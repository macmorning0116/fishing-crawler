from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import psycopg
from opensearchpy import OpenSearch

from crawler.env import load_dotenv
from crawler.naver_cafe import ArticleResult, BoardConfig, parse_date_text


DEFAULT_INDEX_NAME = "fishing_articles_v2"
POSTGRES_SCHEMA_PATH = Path("infra/postgres/init/001_init.sql")
OPENSEARCH_INDEX_PATH = Path("infra/opensearch/articles-index.json")
logger = logging.getLogger(__name__)


def _require_env(name: str) -> str:
    load_dotenv()
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"환경변수 {name} 가 비어 있습니다. .env 를 확인해주세요.")
    return value


def get_postgres_config() -> dict[str, Any]:
    return {
        "host": _require_env("POSTGRES_HOST"),
        "port": int(_require_env("POSTGRES_PORT")),
        "dbname": _require_env("POSTGRES_DB"),
        "user": _require_env("POSTGRES_USER"),
        "password": _require_env("POSTGRES_PASSWORD"),
    }


def get_opensearch_host() -> str:
    return _require_env("OPENSEARCH_HOST")


def postgres_connection() -> psycopg.Connection:
    return psycopg.connect(**get_postgres_config())


def opensearch_client() -> OpenSearch:
    raw_host = get_opensearch_host()
    parsed = urlparse(raw_host if "://" in raw_host else f"http://{raw_host}")
    use_ssl = parsed.scheme == "https"
    host = parsed.hostname or raw_host
    port = parsed.port or (443 if use_ssl else 80)

    return OpenSearch(
        hosts=[{"host": host, "port": port}],
        use_ssl=use_ssl,
        verify_certs=False,
        ssl_assert_hostname=False,
        ssl_show_warn=False,
        timeout=30,
    )


def init_postgres_schema(schema_path: Path = POSTGRES_SCHEMA_PATH) -> None:
    sql = schema_path.read_text(encoding="utf-8")
    with postgres_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    logger.info("Postgres 스키마 초기화 완료 schema_path=%s", schema_path)


def ensure_opensearch_index(
    index_name: str = DEFAULT_INDEX_NAME,
    mapping_path: Path = OPENSEARCH_INDEX_PATH,
) -> bool:
    client = opensearch_client()
    if client.indices.exists(index=index_name):
        logger.info("OpenSearch 인덱스가 이미 존재합니다 index=%s", index_name)
        return False

    body = json.loads(mapping_path.read_text(encoding="utf-8"))
    client.indices.create(index=index_name, body=body)
    logger.info("OpenSearch 인덱스 생성 완료 index=%s mapping_path=%s", index_name, mapping_path)
    return True


def test_storage_connections(index_name: str = DEFAULT_INDEX_NAME) -> dict[str, Any]:
    postgres_ok = False
    postgres_version = ""
    with postgres_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT version()")
            row = cur.fetchone()
            postgres_version = row[0] if row else ""
        postgres_ok = True

    client = opensearch_client()
    os_ok = bool(client.ping())
    os_info = client.info() if os_ok else {}
    os_index_exists = client.indices.exists(index=index_name) if os_ok else False

    return {
        "postgres_ok": postgres_ok,
        "postgres_version": postgres_version,
        "opensearch_ok": os_ok,
        "opensearch_info": os_info,
        "opensearch_index_exists": os_index_exists,
        "opensearch_index_name": index_name,
    }


def _article_content_hash(article: ArticleResult) -> str:
    payload = "\n".join(
        [
            article.article_id,
            article.title,
            article.body_text,
            article.author,
            article.date_text,
            article.category_label,
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _article_row(article: ArticleResult, board: BoardConfig, board_key: str) -> dict[str, Any]:
    published_at = parse_date_text(article.date_text)
    return {
        "source": "naver_cafe",
        "cafe_id": board.cafe_id,
        "article_id": int(article.article_id),
        "board_key": board_key,
        "board_name": article.board_name,
        "menu_id": board.menu_id,
        "title": article.title,
        "body_text": article.body_text,
        "author_name": article.author or None,
        "published_at": published_at,
        "date_text": article.date_text,
        "category_label": article.category_label or None,
        "access_status": article.access.status,
        "access_reason": article.access.reason,
        "species": article.classification.species,
        "species_reason": article.classification.species_reason,
        "external_open": article.classification.external_open,
        "external_open_reason": article.classification.external_open_reason,
        "region": article.classification.region,
        "region_reason": article.classification.region_reason,
        "place": article.classification.place,
        "place_reason": article.classification.place_reason,
        "url": article.url,
        "page_url": article.page_url,
        "page_title": article.page_title,
        "content_hash": _article_content_hash(article),
        "raw_payload": json.dumps(
            {
                "article": {
                    "board_name": article.board_name,
                    "article_id": article.article_id,
                    "title": article.title,
                    "url": article.url,
                    "author": article.author,
                    "date_text": article.date_text,
                    "category_label": article.category_label,
                    "page_title": article.page_title,
                    "page_url": article.page_url,
                    "body_text": article.body_text,
                },
                "access": asdict(article.access),
                "classification": asdict(article.classification),
            },
            ensure_ascii=False,
        ),
    }


def upsert_articles_to_postgres(
    *,
    results: Iterable[ArticleResult],
    board: BoardConfig,
    board_key: str,
) -> int:
    rows = [_article_row(article, board, board_key) for article in results]
    if not rows:
        return 0

    sql = """
    INSERT INTO articles (
        source,
        cafe_id,
        article_id,
        board_key,
        board_name,
        menu_id,
        title,
        body_text,
        author_name,
        published_at,
        date_text,
        category_label,
        access_status,
        access_reason,
        species,
        species_reason,
        external_open,
        external_open_reason,
        region,
        region_reason,
        place,
        place_reason,
        url,
        page_url,
        page_title,
        content_hash,
        raw_payload
    )
    VALUES (
        %(source)s,
        %(cafe_id)s,
        %(article_id)s,
        %(board_key)s,
        %(board_name)s,
        %(menu_id)s,
        %(title)s,
        %(body_text)s,
        %(author_name)s,
        %(published_at)s,
        %(date_text)s,
        %(category_label)s,
        %(access_status)s,
        %(access_reason)s,
        %(species)s,
        %(species_reason)s,
        %(external_open)s,
        %(external_open_reason)s,
        %(region)s,
        %(region_reason)s,
        %(place)s,
        %(place_reason)s,
        %(url)s,
        %(page_url)s,
        %(page_title)s,
        %(content_hash)s,
        %(raw_payload)s::jsonb
    )
    ON CONFLICT (source, cafe_id, article_id) DO UPDATE SET
        board_key = EXCLUDED.board_key,
        board_name = EXCLUDED.board_name,
        menu_id = EXCLUDED.menu_id,
        title = EXCLUDED.title,
        body_text = EXCLUDED.body_text,
        author_name = EXCLUDED.author_name,
        published_at = EXCLUDED.published_at,
        date_text = EXCLUDED.date_text,
        category_label = EXCLUDED.category_label,
        access_status = EXCLUDED.access_status,
        access_reason = EXCLUDED.access_reason,
        species = EXCLUDED.species,
        species_reason = EXCLUDED.species_reason,
        external_open = EXCLUDED.external_open,
        external_open_reason = EXCLUDED.external_open_reason,
        region = EXCLUDED.region,
        region_reason = EXCLUDED.region_reason,
        place = EXCLUDED.place,
        place_reason = EXCLUDED.place_reason,
        url = EXCLUDED.url,
        page_url = EXCLUDED.page_url,
        page_title = EXCLUDED.page_title,
        content_hash = EXCLUDED.content_hash,
        raw_payload = EXCLUDED.raw_payload,
        updated_at = NOW()
    """
    with postgres_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)
        conn.commit()
    logger.info("Postgres upsert 완료 board=%s board_key=%s count=%s", board.board_name, board_key, len(rows))
    return len(rows)


def _opensearch_doc(article: ArticleResult, board: BoardConfig, board_key: str) -> dict[str, Any]:
    published_at = parse_date_text(article.date_text)
    return {
        "source": "naver_cafe",
        "cafe_id": board.cafe_id,
        "article_id": article.article_id,
        "board_key": board_key,
        "board_name": article.board_name,
        "menu_id": board.menu_id,
        "title": article.title,
        "body_text": article.body_text,
        "author_name": article.author or None,
        "published_at": published_at.isoformat() if published_at else None,
        "date_text": article.date_text,
        "category_label": article.category_label or None,
        "access_status": article.access.status,
        "access_reason": article.access.reason,
        "species": article.classification.species,
        "species_reason": article.classification.species_reason,
        "external_open": article.classification.external_open,
        "external_open_reason": article.classification.external_open_reason,
        "region": article.classification.region,
        "region_reason": article.classification.region_reason,
        "place": article.classification.place,
        "place_reason": article.classification.place_reason,
        "url": article.url,
        "page_url": article.page_url,
        "page_title": article.page_title,
        "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }


def upsert_articles_to_opensearch(
    *,
    results: Iterable[ArticleResult],
    board: BoardConfig,
    board_key: str,
    index_name: str = DEFAULT_INDEX_NAME,
) -> int:
    client = opensearch_client()
    count = 0
    for article in results:
        document = _opensearch_doc(article, board, board_key)
        doc_id = f"naver_cafe:{board.cafe_id}:{article.article_id}"
        client.index(index=index_name, id=doc_id, body=document, refresh=True)
        count += 1
    logger.info(
        "OpenSearch upsert 완료 board=%s board_key=%s index=%s count=%s",
        board.board_name,
        board_key,
        index_name,
        count,
    )
    return count


def find_existing_article_ids(*, board: BoardConfig, article_ids: Iterable[str]) -> set[str]:
    normalized_ids = []
    for article_id in article_ids:
        text = str(article_id).strip()
        if text.isdigit():
            normalized_ids.append(int(text))

    if not normalized_ids:
        return set()

    sql = """
    SELECT article_id
    FROM articles
    WHERE source = 'naver_cafe'
      AND cafe_id = %(cafe_id)s
      AND menu_id = %(menu_id)s
      AND article_id = ANY(%(article_ids)s)
    """
    with postgres_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                {
                    "cafe_id": board.cafe_id,
                    "menu_id": board.menu_id,
                    "article_ids": normalized_ids,
                },
            )
            rows = cur.fetchall()

    return {str(row[0]) for row in rows}


def load_existing_article_ids_for_board(*, board: BoardConfig) -> set[str]:
    sql = """
    SELECT article_id
    FROM articles
    WHERE source = 'naver_cafe'
      AND cafe_id = %(cafe_id)s
      AND menu_id = %(menu_id)s
    """
    with postgres_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                {
                    "cafe_id": board.cafe_id,
                    "menu_id": board.menu_id,
                },
            )
            rows = cur.fetchall()

    return {str(row[0]) for row in rows}
