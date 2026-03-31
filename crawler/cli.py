from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from crawler.classifiers import ClassificationResult, DetailAccessResult
from crawler.naver_cafe import (
    BOARD_REGISTRY,
    ArticleResult,
    BoardConfig,
    crawl_backfill,
    crawl_incremental,
    DEFAULT_MENU_URL,
    DEFAULT_OUTPUT_PATH,
    DEFAULT_PROFILE_DIR,
    crawl_first_page,
    summarize_results,
    test_auto_login,
)
from crawler.storage import (
    DEFAULT_INDEX_NAME,
    ensure_opensearch_index,
    init_postgres_schema,
    load_existing_article_ids_for_board,
    test_storage_connections,
    upsert_articles_to_opensearch,
    upsert_articles_to_postgres,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Crawl the first page of a logged-in Naver Cafe board.")
    parser.add_argument(
        "--test-login",
        action="store_true",
        help="Only test Naver auto login using NAVER_ID/NAVER_PASSWORD from .env, without crawling articles.",
    )
    parser.add_argument(
        "--test-storage",
        action="store_true",
        help="Test Postgres/OpenSearch connections using .env values.",
    )
    parser.add_argument(
        "--init-storage",
        action="store_true",
        help="Initialize Postgres schema and create OpenSearch index if missing.",
    )
    parser.add_argument(
        "--opensearch-index",
        default=DEFAULT_INDEX_NAME,
        help="OpenSearch index name for init/test commands.",
    )
    parser.add_argument(
        "--store-postgres",
        action="store_true",
        help="After crawling, upsert results into Postgres.",
    )
    parser.add_argument(
        "--store-opensearch",
        action="store_true",
        help="After crawling, upsert results into OpenSearch.",
    )
    parser.add_argument(
        "--mode",
        choices=["first_page", "backfill", "incremental"],
        default="first_page",
        help="Crawl mode. 'backfill' iterates pages until the cutoff date. 'incremental' stops when it meets an already stored article.",
    )
    parser.add_argument(
        "--from-date",
        default=None,
        help="Inclusive upper bound in YYYY-MM-DD for backfill mode. Newer articles are skipped until this date range starts.",
    )
    parser.add_argument(
        "--until-date",
        default=None,
        help="Inclusive lower bound in YYYY-MM-DD for backfill mode. Crawling stops when older articles appear.",
    )
    parser.add_argument("--page-size", type=int, default=50, help="List page size for backfill mode")
    parser.add_argument("--start-page", type=int, default=1, help="Start page for backfill mode")
    parser.add_argument("--max-pages", type=int, default=1000, help="Safety cap for backfill page iteration")
    parser.add_argument(
        "--stop-after-existing-streak",
        type=int,
        default=10,
        help="Incremental mode only. Stop when already stored articles appear this many times in a row.",
    )
    parser.add_argument(
        "--stop-after-existing-ratio",
        type=float,
        default=0.8,
        help="Incremental mode only. Stop when a page is mostly already stored articles.",
    )
    parser.add_argument(
        "--limit-results",
        type=int,
        default=None,
        help="Trim crawled or loaded results to the first N items before saving/storing.",
    )
    parser.add_argument(
        "--board-key",
        choices=sorted(BOARD_REGISTRY.keys()),
        default="bass_walking",
        help="Predefined board config key.",
    )
    parser.add_argument("--board-name", default=None, help="Override board name for saved output")
    parser.add_argument("--menu-url", default=None, help="Override target Naver Cafe board URL")
    parser.add_argument("--input-json", type=Path, default=None, help="Load previously saved crawl JSON instead of crawling")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="Output JSON path")
    parser.add_argument("--profile-dir", type=Path, default=DEFAULT_PROFILE_DIR, help="Persistent browser profile dir")
    parser.add_argument(
        "--browser-channel",
        default="chrome",
        help="Browser channel to launch. Default is 'chrome' so it uses regular Google Chrome instead of Chrome for Testing.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run headless. Leave this off for the first run so you can log in manually.",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Crawler log level.",
    )
    return parser


def load_results_from_json(path: Path) -> list:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [
        ArticleResult(
            board_name=item["board_name"],
            article_id=item["article_id"],
            title=item["title"],
            url=item["url"],
            author=item.get("author", ""),
            date_text=item.get("date_text", ""),
            category_label=item.get("category_label", ""),
            page_title=item.get("page_title", ""),
            page_url=item.get("page_url", ""),
            body_text=item.get("body_text", ""),
            access=DetailAccessResult(**item["access"]),
            classification=ClassificationResult(**item["classification"]),
        )
        for item in payload
    ]


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    selected_board = BOARD_REGISTRY[args.board_key]
    board = BoardConfig(
        board_name=args.board_name or selected_board.board_name,
        menu_url=args.menu_url or selected_board.menu_url,
        fixed_species=selected_board.fixed_species,
    )

    if args.test_login:
        result = asyncio.run(
            test_auto_login(
                profile_dir=args.profile_dir,
                headless=args.headless,
                browser_channel=args.browser_channel,
            )
        )
        print(f"자동 로그인 성공: {result['success']}")
        print(f"현재 URL: {result['page_url']}")
        print(f"현재 제목: {result['page_title']}")
        print(f"NID 쿠키: {', '.join(result['nid_cookies']) if result['nid_cookies'] else '(없음)'}")
        return

    if args.init_storage:
        init_postgres_schema()
        created = ensure_opensearch_index(index_name=args.opensearch_index)
        print("Postgres 스키마 초기화 완료")
        print(
            f"OpenSearch 인덱스 {'생성 완료' if created else '이미 존재함'}: {args.opensearch_index}"
        )
        return

    if args.test_storage:
        result = test_storage_connections(index_name=args.opensearch_index)
        print(f"Postgres 연결 성공: {result['postgres_ok']}")
        print(f"Postgres 버전: {result['postgres_version']}")
        print(f"OpenSearch 연결 성공: {result['opensearch_ok']}")
        print(f"OpenSearch 인덱스 존재: {result['opensearch_index_exists']}")
        print(f"OpenSearch 인덱스 이름: {result['opensearch_index_name']}")
        cluster_name = (result.get('opensearch_info') or {}).get('cluster_name', '')
        version_number = ((result.get('opensearch_info') or {}).get('version') or {}).get('number', '')
        if cluster_name:
            print(f"OpenSearch 클러스터: {cluster_name}")
        if version_number:
            print(f"OpenSearch 버전: {version_number}")
        return

    if args.input_json:
        results = load_results_from_json(args.input_json)
    elif args.mode == "backfill":
        if not args.until_date:
            parser.error("--mode backfill 에서는 --until-date YYYY-MM-DD 가 필요합니다.")
        until_date = datetime.strptime(args.until_date, "%Y-%m-%d").date()
        from_date = datetime.strptime(args.from_date, "%Y-%m-%d").date() if args.from_date else None
        results = asyncio.run(
            crawl_backfill(
                board=board,
                until_date=until_date,
                from_date=from_date,
                output_path=args.output,
                profile_dir=args.profile_dir,
                headless=args.headless,
                browser_channel=args.browser_channel,
                page_size=args.page_size,
                start_page=args.start_page,
                max_pages=args.max_pages,
            )
        )
    elif args.mode == "incremental":
        existing_article_ids = load_existing_article_ids_for_board(board=board)

        results = asyncio.run(
            crawl_incremental(
                board=board,
                existing_article_ids=existing_article_ids,
                output_path=args.output,
                profile_dir=args.profile_dir,
                headless=args.headless,
                browser_channel=args.browser_channel,
                page_size=args.page_size,
                start_page=args.start_page,
                max_pages=args.max_pages,
                stop_after_existing_streak=args.stop_after_existing_streak,
                stop_after_existing_ratio=args.stop_after_existing_ratio,
            )
        )
    else:
        results = asyncio.run(
            crawl_first_page(
                board=board,
                output_path=args.output,
                profile_dir=args.profile_dir,
                headless=args.headless,
                browser_channel=args.browser_channel,
            )
        )

    if args.limit_results is not None:
        results = results[: args.limit_results]

    if args.store_postgres:
        init_postgres_schema()
        inserted = upsert_articles_to_postgres(results=results, board=board, board_key=args.board_key)
        print(f"Postgres upsert 완료: {inserted}건")

    if args.store_opensearch:
        ensure_opensearch_index(index_name=args.opensearch_index)
        inserted = upsert_articles_to_opensearch(
            results=results,
            board=board,
            board_key=args.board_key,
            index_name=args.opensearch_index,
        )
        print(f"OpenSearch upsert 완료: {inserted}건")

    print(summarize_results(results))
    print(f"\n저장 위치: {args.output}")


if __name__ == "__main__":
    main()
