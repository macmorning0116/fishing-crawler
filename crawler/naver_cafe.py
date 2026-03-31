from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

from playwright.async_api import BrowserContext, Error, Frame, Page, async_playwright

from crawler.classifiers import (
    ArticleApiFlags,
    ClassificationResult,
    DetailAccessResult,
    classify_article,
    detect_detail_access_from_api,
    normalize_title,
)
from crawler.env import load_dotenv


DEFAULT_MENU_URL = "https://cafe.naver.com/f-e/cafes/29836679/menus/3?viewType=L"
DEFAULT_OUTPUT_PATH = Path("output/first_page.json")
DEFAULT_PROFILE_DIR = Path(".playwright/naver-profile")
NAVER_LOGIN_URL = "https://nid.naver.com/nidlogin.login"
ARTICLE_API_BASE = "https://article.cafe.naver.com/gw/v4/cafes/{cafe_id}/articles/{article_id}"
logger = logging.getLogger(__name__)


class HtmlTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.block_tags = {"p", "div", "br", "li", "tr"}

    def handle_data(self, data: str) -> None:
        if data:
            self.parts.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag in self.block_tags:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.block_tags:
            self.parts.append("\n")

    def get_text(self) -> str:
        text = unescape("".join(self.parts))
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n", text)
        return text.strip()


@dataclass
class BoardConfig:
    board_name: str
    menu_url: str
    fixed_species: Optional[str] = None

    @property
    def menu_id(self) -> int:
        match = re.search(r"/menus/(\d+)", self.menu_url)
        if not match:
            raise ValueError(f"menu_url 에서 menu id 를 추출하지 못했습니다: {self.menu_url}")
        return int(match.group(1))

    @property
    def cafe_id(self) -> int:
        match = re.search(r"/cafes/(\d+)", self.menu_url)
        if not match:
            raise ValueError(f"menu_url 에서 cafe id 를 추출하지 못했습니다: {self.menu_url}")
        return int(match.group(1))


BOARD_REGISTRY: dict[str, BoardConfig] = {
    "bass_walking": BoardConfig(
        board_name="배스 조행기(워킹조행)",
        menu_url="https://cafe.naver.com/f-e/cafes/29836679/menus/3?viewType=L",
        fixed_species="배스",
    ),
    "bass_boating": BoardConfig(
        board_name="배스 조행기(보팅조행)",
        menu_url="https://cafe.naver.com/f-e/cafes/29836679/menus/87",
        fixed_species="배스",
    ),
    "freshwater_guest": BoardConfig(
        board_name="민물 조행기(손님고기)",
        menu_url="https://cafe.naver.com/f-e/cafes/29836679/menus/88",
        fixed_species=None,
    ),
}


@dataclass
class ArticleSummary:
    article_id: str
    title: str
    url: str
    author: str
    date_text: str
    write_date: Optional[date] = None


@dataclass
class ArticleResult:
    board_name: str
    article_id: str
    title: str
    url: str
    author: str
    date_text: str
    category_label: str
    page_title: str
    page_url: str
    body_text: str
    access: DetailAccessResult
    classification: ClassificationResult


def article_id_from_url(url: str) -> Optional[str]:
    match = re.search(r"/articles/(\d+)", url)
    if match:
        return match.group(1)

    match = re.search(r"[?&]articleid=(\d+)", url, re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def build_board_page_url(menu_url: str, page: int, size: int) -> str:
    parsed = urlparse(menu_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["page"] = str(page)
    query["size"] = str(size)
    if "viewType" not in query:
        query["viewType"] = "L"
    new_query = urlencode(query)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))


def parse_date_text(value: str) -> Optional[date]:
    value = (value or "").strip()
    if not value:
        return None

    for fmt in ("%Y.%m.%d", "%Y.%m.%d.", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def format_write_timestamp(value: Any) -> str:
    if value in (None, ""):
        return ""

    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return ""

    if timestamp <= 0:
        return ""

    return datetime.fromtimestamp(timestamp / 1000).date().isoformat()


def article_result_date(article: ArticleResult) -> Optional[date]:
    return parse_date_text(article.date_text)


def html_to_text(html: str) -> str:
    parser = HtmlTextExtractor()
    parser.feed(html or "")
    return parser.get_text()


async def _main_frame(page: Page) -> Frame | Page:
    iframe = page.frame(name="cafe_main")
    if iframe is not None:
        return iframe

    element = await page.query_selector("iframe#cafe_main")
    if element:
        content_frame = await element.content_frame()
        if content_frame is not None:
            return content_frame

    return page


async def _has_login_cookies(context: BrowserContext) -> bool:
    cookies = await context.cookies()
    cookie_names = {cookie["name"] for cookie in cookies}
    return "NID_SES" in cookie_names or "NID_AUT" in cookie_names


async def _wait_until_logged_in(context: BrowserContext, page: Page, menu_url: str, timeout_ms: int = 300000) -> None:
    deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000)
    while asyncio.get_running_loop().time() < deadline:
        if await _has_login_cookies(context):
            await page.goto(menu_url, wait_until="domcontentloaded")
            return
        await page.wait_for_timeout(1000)

    raise RuntimeError("로그인 쿠키를 확인하지 못했습니다. 로그인 완료 후 다시 시도해주세요.")


def _get_login_credentials() -> tuple[str, str]:
    load_dotenv()
    user_id = os.environ.get("NAVER_ID", "").strip()
    password = os.environ.get("NAVER_PASSWORD", "").strip()
    return user_id, password


async def attempt_auto_login(context: BrowserContext, page: Page, user_id: str, password: str) -> bool:
    logger.info("자동 로그인 시도")
    await page.goto(NAVER_LOGIN_URL, wait_until="domcontentloaded")

    id_input = page.locator("#id")
    password_input = page.locator("#pw")
    login_button = page.locator("#log\\.login")

    await id_input.wait_for(timeout=10000)
    await password_input.wait_for(timeout=10000)

    await page.evaluate(
        """
        ({ userId, password }) => {
          const idInput = document.querySelector('#id');
          const pwInput = document.querySelector('#pw');
          for (const [element, value] of [[idInput, userId], [pwInput, password]]) {
            if (!element) continue;
            element.focus();
            element.value = value;
            element.dispatchEvent(new Event('input', { bubbles: true }));
            element.dispatchEvent(new Event('change', { bubbles: true }));
          }
        }
        """,
        {"userId": user_id, "password": password},
    )
    await page.wait_for_timeout(400)
    await login_button.click()

    try:
        await _wait_until_logged_in(context, page, DEFAULT_MENU_URL, timeout_ms=20000)
        logger.info("자동 로그인 성공")
        return True
    except RuntimeError:
        logger.warning("자동 로그인 실패")
        return False


async def ensure_login(context: BrowserContext, menu_url: str) -> Page:
    page = await context.new_page()
    await page.goto(menu_url, wait_until="domcontentloaded")

    if await _has_login_cookies(context):
        logger.info("기존 로그인 세션 확인 완료")
        return page

    user_id, password = _get_login_credentials()
    if user_id and password:
        logger.info("저장된 NAVER_ID/NAVER_PASSWORD로 자동 로그인을 시도합니다")
        if await attempt_auto_login(context, page, user_id, password):
            await page.goto(menu_url, wait_until="domcontentloaded")
            return page
        logger.warning("자동 로그인에 실패했습니다. 수동 로그인으로 전환합니다")

    logger.info("로그인 세션이 없어서 수동 로그인 대기 상태로 전환합니다")
    logger.info("브라우저에서 네이버 로그인을 완료하면 게시판 크롤링을 시작합니다: %s", NAVER_LOGIN_URL)
    await page.goto(NAVER_LOGIN_URL, wait_until="domcontentloaded")
    await _wait_until_logged_in(context, page, menu_url)
    return page


async def _wait_for_article_links(page: Page) -> Frame | Page:
    frame_or_page = await _main_frame(page)
    for _ in range(20):
        anchors = await frame_or_page.query_selector_all("a[href]")
        for anchor in anchors:
            href = await anchor.get_attribute("href")
            if href and article_id_from_url(href):
                return frame_or_page
        await page.wait_for_timeout(500)
        frame_or_page = await _main_frame(page)
    raise RuntimeError("게시글 링크를 찾지 못했습니다. 페이지 구조를 확인해주세요.")


async def _extract_list_page_articles(frame_or_page: Frame | Page) -> list[ArticleSummary]:
    raw_articles: list[dict[str, Any]] = await frame_or_page.evaluate(
        """
        () => {
          const normalize = (value) => (value || "").replace(/\\s+/g, " ").trim();
          const anchors = Array.from(document.querySelectorAll("a[href]"));
          const results = [];
          const seen = new Set();

          for (const anchor of anchors) {
            const href = anchor.href || "";
            const match = href.match(/\\/articles\\/(\\d+)/i) || href.match(/[?&]articleid=(\\d+)/i);
            if (!match) continue;

            const articleId = match[1];
            if (seen.has(articleId)) continue;

            const row = anchor.closest("tr, li, div[class*='article'], div[class*='item'], div");
            const title = normalize(anchor.textContent);
            if (!title || title.length < 2) continue;

            const rowText = row ? normalize(row.innerText) : title;
            const dateMatch = rowText.match(/\\d{4}\\.\\d{2}\\.\\d{2}\\.?|\\d{2}:\\d{2}/);

            let author = "";
            if (row) {
              const authorCandidate = row.querySelector("[class*='nick'], [class*='name'], [class*='writer'], .td_name");
              author = normalize(authorCandidate ? authorCandidate.textContent : "");
            }

            results.push({
              article_id: articleId,
              title,
              url: href,
              author,
              date_text: dateMatch ? dateMatch[0] : "",
            });
            seen.add(articleId);
          }

          return results;
        }
        """
    )

    return [
        ArticleSummary(
            article_id=item["article_id"],
            title=item["title"],
            url=item["url"],
            author=item.get("author", ""),
            date_text=item.get("date_text", ""),
            write_date=parse_date_text(item.get("date_text", "")),
        )
        for item in raw_articles
    ]


async def _cookie_header_for_url(context: BrowserContext, url: str) -> str:
    cookies = await context.cookies(url)
    return "; ".join(f"{cookie['name']}={cookie['value']}" for cookie in cookies)


async def _fetch_article_api(api_page: Page, board: BoardConfig, article_id: str) -> dict[str, Any]:
    query = urlencode(
        {
            "query": "",
            "menuId": board.menu_id,
            "useCafeId": "true",
            "requestFrom": "A",
        }
    )
    api_url = ARTICLE_API_BASE.format(cafe_id=board.cafe_id, article_id=article_id) + f"?{query}"
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = await api_page.goto(api_url, wait_until="domcontentloaded")
            if response is None:
                raise RuntimeError(f"상세 API 응답을 받지 못했습니다: {api_url}")

            payload_text = await api_page.text_content("body")
            if not payload_text:
                raise RuntimeError(f"상세 API 본문이 비어 있습니다: {api_url}")

            return json.loads(payload_text)
        except Exception as exc:
            last_error = exc
            logger.warning(
                "article API 호출 실패, 재시도합니다 article_id=%s attempt=%s error=%s",
                article_id,
                attempt + 1,
                exc,
            )
            if attempt == 2:
                break
            await api_page.wait_for_timeout(1000 * (attempt + 1))

    raise RuntimeError(f"상세 API 호출에 반복 실패했습니다: {api_url}") from last_error


def _build_result_from_api(board: BoardConfig, summary: ArticleSummary, payload: dict[str, Any]) -> Optional[ArticleResult]:
    result = payload.get("result") or {}
    article = result.get("article") or {}
    writer = article.get("writer") or {}
    authority = result.get("authority") or {}

    if article.get("isNotice") is True:
        return None

    actual_menu = article.get("menu") or {}
    menu_name = (actual_menu.get("name") or "").strip()
    if "공지" in menu_name:
        return None

    title = normalize_title(article.get("subject") or summary.title or "")
    content_html = article.get("contentHtml") or ""
    body_text = html_to_text(content_html)
    category_label = (article.get("head") or "").strip()
    page_url = summary.url
    page_title = f"{title} : 네이버 카페" if title else "네이버 카페"

    flags = ArticleApiFlags(
        is_notice=bool(article.get("isNotice")),
        is_search_open=article.get("isSearchOpen"),
        is_enable_external=article.get("isEnableExternal"),
        is_sharable=authority.get("isSharable"),
        is_readable=article.get("isReadable"),
    )
    access = detect_detail_access_from_api(
        title=page_title,
        body_text=body_text,
        page_url=page_url,
        flags=flags,
    )
    classification = classify_article(
        title=title,
        body_text=body_text,
        access_result=access,
        category_label=category_label,
        fixed_species=board.fixed_species,
    )

    date_text = summary.date_text or format_write_timestamp(article.get("writeDate"))

    return ArticleResult(
        board_name=board.board_name,
        article_id=str(article.get("id") or summary.article_id),
        title=title,
        url=page_url,
        author=(writer.get("nick") or summary.author or "").strip(),
        date_text=date_text,
        category_label=category_label,
        page_title=page_title,
        page_url=page_url,
        body_text=body_text,
        access=access,
        classification=classification,
    )


async def crawl_first_page(
    board: BoardConfig,
    output_path: Path = DEFAULT_OUTPUT_PATH,
    profile_dir: Path = DEFAULT_PROFILE_DIR,
    headless: bool = False,
    browser_channel: str = "chrome",
) -> list[ArticleResult]:
    logger.info("first_page crawl 시작 board=%s menu_id=%s", board.board_name, board.menu_id)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    profile_dir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as playwright:
        launch_kwargs = {
            "user_data_dir": str(profile_dir),
            "headless": headless,
            "viewport": {"width": 1440, "height": 1100},
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        if browser_channel:
            launch_kwargs["channel"] = browser_channel

        try:
            context = await playwright.chromium.launch_persistent_context(**launch_kwargs)
        except Error as exc:
            if browser_channel:
                raise RuntimeError(
                    f"브라우저 채널 '{browser_channel}' 실행에 실패했습니다. "
                    "Mac에 Google Chrome이 설치되어 있는지 확인하거나 "
                    "`--browser-channel chromium`으로 다시 실행해보세요."
                ) from exc
            raise

        try:
            list_page = await ensure_login(context, board.menu_url)
            frame_or_page = await _wait_for_article_links(list_page)
            articles = await _extract_list_page_articles(frame_or_page)
            logger.info("first_page 목록 수집 완료 board=%s articles=%s", board.board_name, len(articles))

            results: list[ArticleResult] = []
            api_page = await context.new_page()
            for index, summary in enumerate(articles, start=1):
                payload = await _fetch_article_api(api_page, board, summary.article_id)
                article_result = _build_result_from_api(board, summary, payload)
                if article_result is None:
                    continue
                results.append(article_result)
                if index == len(articles) or index % 10 == 0:
                    logger.info(
                        "first_page 진행중 board=%s processed=%s collected=%s",
                        board.board_name,
                        index,
                        len(results),
                    )

            serialized = [
                {
                    "board_name": item.board_name,
                    "article_id": item.article_id,
                    "title": item.title,
                    "url": item.url,
                    "author": item.author,
                    "date_text": item.date_text,
                    "category_label": item.category_label,
                    "page_title": item.page_title,
                    "page_url": item.page_url,
                    "body_text": item.body_text,
                    "access": asdict(item.access),
                    "classification": asdict(item.classification),
                }
                for item in results
            ]
            output_path.write_text(json.dumps(serialized, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info("first_page crawl 완료 board=%s collected=%s output=%s", board.board_name, len(results), output_path)
            return results
        finally:
            await context.close()


async def crawl_backfill(
    board: BoardConfig,
    until_date: date,
    from_date: Optional[date] = None,
    output_path: Path = DEFAULT_OUTPUT_PATH,
    profile_dir: Path = DEFAULT_PROFILE_DIR,
    headless: bool = False,
    browser_channel: str = "chrome",
    page_size: int = 50,
    start_page: int = 1,
    max_pages: int = 1000,
) -> list[ArticleResult]:
    logger.info(
        "backfill 시작 board=%s from_date=%s until_date=%s start_page=%s max_pages=%s page_size=%s",
        board.board_name,
        from_date,
        until_date,
        start_page,
        max_pages,
        page_size,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    profile_dir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as playwright:
        launch_kwargs = {
            "user_data_dir": str(profile_dir),
            "headless": headless,
            "viewport": {"width": 1440, "height": 1100},
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        if browser_channel:
            launch_kwargs["channel"] = browser_channel

        try:
            context = await playwright.chromium.launch_persistent_context(**launch_kwargs)
        except Error as exc:
            if browser_channel:
                raise RuntimeError(
                    f"브라우저 채널 '{browser_channel}' 실행에 실패했습니다. "
                    "Mac에 Google Chrome이 설치되어 있는지 확인하거나 "
                    "`--browser-channel chromium`으로 다시 실행해보세요."
                ) from exc
            raise

        try:
            page = await ensure_login(context, board.menu_url)
            api_page = await context.new_page()

            results: list[ArticleResult] = []
            should_stop = False
            for page_number in range(start_page, start_page + max_pages):
                list_url = build_board_page_url(board.menu_url, page=page_number, size=page_size)
                logger.info("backfill 페이지 조회 board=%s page=%s url=%s", board.board_name, page_number, list_url)
                await page.goto(list_url, wait_until="domcontentloaded")
                frame_or_page = await _wait_for_article_links(page)
                articles = await _extract_list_page_articles(frame_or_page)
                logger.info("backfill 목록 파싱 완료 board=%s page=%s articles=%s", board.board_name, page_number, len(articles))
                if not articles:
                    logger.info("backfill 목록이 비어서 종료 board=%s page=%s", board.board_name, page_number)
                    break

                page_collected = 0
                skipped_newer = 0
                for summary in articles:
                    if summary.write_date and summary.write_date < until_date:
                        logger.info(
                            "backfill 하한 날짜 도달로 종료 board=%s page=%s article_id=%s article_date=%s until_date=%s",
                            board.board_name,
                            page_number,
                            summary.article_id,
                            summary.write_date,
                            until_date,
                        )
                        should_stop = True
                        break

                    payload = await _fetch_article_api(api_page, board, summary.article_id)
                    article_result = _build_result_from_api(board, summary, payload)
                    if article_result is None:
                        continue

                    actual_write_date = article_result_date(article_result)
                    if from_date and actual_write_date and actual_write_date > from_date:
                        skipped_newer += 1
                        continue
                    if actual_write_date and actual_write_date < until_date:
                        logger.info(
                            "backfill 상세 날짜 기준 하한 도달로 종료 board=%s page=%s article_id=%s article_date=%s until_date=%s",
                            board.board_name,
                            page_number,
                            article_result.article_id,
                            actual_write_date,
                            until_date,
                        )
                        should_stop = True
                        break

                    results.append(article_result)
                    page_collected += 1

                logger.info(
                    "backfill 페이지 처리 완료 board=%s page=%s page_collected=%s total_collected=%s skipped_newer=%s should_stop=%s",
                    board.board_name,
                    page_number,
                    page_collected,
                    len(results),
                    skipped_newer,
                    should_stop,
                )

                if should_stop:
                    break

            serialized = [
                {
                    "board_name": item.board_name,
                    "article_id": item.article_id,
                    "title": item.title,
                    "url": item.url,
                    "author": item.author,
                    "date_text": item.date_text,
                    "category_label": item.category_label,
                    "page_title": item.page_title,
                    "page_url": item.page_url,
                    "body_text": item.body_text,
                    "access": asdict(item.access),
                    "classification": asdict(item.classification),
                }
                for item in results
            ]
            output_path.write_text(json.dumps(serialized, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info("backfill 완료 board=%s collected=%s output=%s", board.board_name, len(results), output_path)
            return results
        finally:
            await context.close()


async def crawl_incremental(
    board: BoardConfig,
    existing_article_ids: set[str],
    output_path: Path = DEFAULT_OUTPUT_PATH,
    profile_dir: Path = DEFAULT_PROFILE_DIR,
    headless: bool = False,
    browser_channel: str = "chrome",
    page_size: int = 50,
    start_page: int = 1,
    max_pages: int = 1000,
    stop_after_existing_streak: int = 10,
    stop_after_existing_ratio: float = 0.8,
) -> list[ArticleResult]:
    logger.info(
        "incremental 시작 board=%s existing_ids=%s start_page=%s max_pages=%s page_size=%s streak=%s ratio=%.2f",
        board.board_name,
        len(existing_article_ids),
        start_page,
        max_pages,
        page_size,
        stop_after_existing_streak,
        stop_after_existing_ratio,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    profile_dir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as playwright:
        launch_kwargs = {
            "user_data_dir": str(profile_dir),
            "headless": headless,
            "viewport": {"width": 1440, "height": 1100},
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        if browser_channel:
            launch_kwargs["channel"] = browser_channel

        try:
            context = await playwright.chromium.launch_persistent_context(**launch_kwargs)
        except Error as exc:
            if browser_channel:
                raise RuntimeError(
                    f"브라우저 채널 '{browser_channel}' 실행에 실패했습니다. "
                    "Mac에 Google Chrome이 설치되어 있는지 확인하거나 "
                    "`--browser-channel chromium`으로 다시 실행해보세요."
                ) from exc
            raise

        try:
            page = await ensure_login(context, board.menu_url)
            api_page = await context.new_page()

            results: list[ArticleResult] = []
            should_stop = False
            existing_streak = 0
            for page_number in range(start_page, start_page + max_pages):
                list_url = build_board_page_url(board.menu_url, page=page_number, size=page_size)
                logger.info("incremental 페이지 조회 board=%s page=%s url=%s", board.board_name, page_number, list_url)
                await page.goto(list_url, wait_until="domcontentloaded")
                frame_or_page = await _wait_for_article_links(page)
                articles = await _extract_list_page_articles(frame_or_page)
                logger.info("incremental 목록 파싱 완료 board=%s page=%s articles=%s", board.board_name, page_number, len(articles))
                if not articles:
                    logger.info("incremental 목록이 비어서 종료 board=%s page=%s", board.board_name, page_number)
                    break

                existing_count_in_page = 0
                page_new = 0
                for summary in articles:
                    if summary.article_id in existing_article_ids:
                        existing_streak += 1
                        existing_count_in_page += 1
                        if existing_streak >= stop_after_existing_streak:
                            logger.info(
                                "incremental 연속 중복 기준 충족으로 종료 board=%s page=%s article_id=%s existing_streak=%s",
                                board.board_name,
                                page_number,
                                summary.article_id,
                                existing_streak,
                            )
                            should_stop = True
                            break
                        continue

                    existing_streak = 0

                    payload = await _fetch_article_api(api_page, board, summary.article_id)
                    article_result = _build_result_from_api(board, summary, payload)
                    if article_result is None:
                        continue
                    results.append(article_result)
                    page_new += 1

                if articles:
                    existing_ratio = existing_count_in_page / len(articles)
                    if existing_ratio >= stop_after_existing_ratio:
                        logger.info(
                            "incremental 페이지 중복 비율 기준 충족으로 종료 board=%s page=%s existing_ratio=%.2f threshold=%.2f",
                            board.board_name,
                            page_number,
                            existing_ratio,
                            stop_after_existing_ratio,
                        )
                        should_stop = True
                    logger.info(
                        "incremental 페이지 처리 완료 board=%s page=%s page_new=%s existing_in_page=%s existing_streak=%s existing_ratio=%.2f total_collected=%s",
                        board.board_name,
                        page_number,
                        page_new,
                        existing_count_in_page,
                        existing_streak,
                        existing_ratio,
                        len(results),
                    )

                if should_stop:
                    break

            serialized = [
                {
                    "board_name": item.board_name,
                    "article_id": item.article_id,
                    "title": item.title,
                    "url": item.url,
                    "author": item.author,
                    "date_text": item.date_text,
                    "category_label": item.category_label,
                    "page_title": item.page_title,
                    "page_url": item.page_url,
                    "body_text": item.body_text,
                    "access": asdict(item.access),
                    "classification": asdict(item.classification),
                }
                for item in results
            ]
            output_path.write_text(json.dumps(serialized, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info("incremental 완료 board=%s collected=%s output=%s", board.board_name, len(results), output_path)
            return results
        finally:
            await context.close()


async def test_auto_login(
    profile_dir: Path = DEFAULT_PROFILE_DIR,
    browser_channel: str = "chrome",
    headless: bool = False,
) -> dict[str, Any]:
    profile_dir.mkdir(parents=True, exist_ok=True)
    user_id, password = _get_login_credentials()
    if not user_id or not password:
        raise RuntimeError(".env 또는 환경변수에 NAVER_ID, NAVER_PASSWORD를 먼저 설정해주세요.")

    async with async_playwright() as playwright:
        launch_kwargs = {
            "user_data_dir": str(profile_dir),
            "headless": headless,
            "viewport": {"width": 1440, "height": 1100},
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        if browser_channel:
            launch_kwargs["channel"] = browser_channel

        try:
            context = await playwright.chromium.launch_persistent_context(**launch_kwargs)
        except Error as exc:
            if browser_channel:
                raise RuntimeError(
                    f"브라우저 채널 '{browser_channel}' 실행에 실패했습니다. "
                    "Mac에 Google Chrome이 설치되어 있는지 확인하거나 "
                    "`--browser-channel chromium`으로 다시 실행해보세요."
                ) from exc
            raise

        try:
            page = await context.new_page()
            success = await attempt_auto_login(context, page, user_id, password)
            cookies = await context.cookies()
            cookie_names = sorted(cookie["name"] for cookie in cookies if cookie["name"].startswith("NID_"))
            return {
                "success": success,
                "page_url": page.url,
                "page_title": await page.title(),
                "nid_cookies": cookie_names,
            }
        finally:
            await context.close()


def summarize_results(results: Iterable[ArticleResult]) -> str:
    lines = []
    for item in results:
        open_value = (
            "허용"
            if item.classification.external_open is True
            else "비허용"
            if item.classification.external_open is False
            else "미확인"
        )
        species_value = item.classification.species or "미확인"
        region_value = item.classification.region or "미확인"
        place_value = item.classification.place or "미확인"
        lines.append(
            f"- [{item.article_id}] {item.title} | 접근={item.access.status} | 어종={species_value} "
            f"| 외부공개={open_value} | 지역={region_value} | 장소={place_value}"
        )
    return "\n".join(lines)
