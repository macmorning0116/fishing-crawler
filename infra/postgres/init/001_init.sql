CREATE TABLE IF NOT EXISTS articles (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL DEFAULT 'naver_cafe',
    cafe_id BIGINT NOT NULL,
    article_id BIGINT NOT NULL,
    board_key TEXT NOT NULL,
    board_name TEXT NOT NULL,
    menu_id BIGINT NOT NULL,
    title TEXT NOT NULL,
    body_text TEXT NOT NULL,
    author_name TEXT,
    published_at DATE,
    date_text TEXT,
    category_label TEXT,
    access_status TEXT NOT NULL,
    access_reason TEXT NOT NULL,
    species TEXT,
    species_reason TEXT,
    external_open BOOLEAN,
    external_open_reason TEXT,
    region TEXT,
    region_reason TEXT,
    place TEXT,
    place_reason TEXT,
    url TEXT NOT NULL,
    page_url TEXT NOT NULL,
    page_title TEXT,
    content_hash TEXT,
    raw_payload JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source, cafe_id, article_id)
);

CREATE INDEX IF NOT EXISTS idx_articles_board_key ON articles (board_key);
CREATE INDEX IF NOT EXISTS idx_articles_menu_id ON articles (menu_id);
CREATE INDEX IF NOT EXISTS idx_articles_published_at ON articles (published_at);
CREATE INDEX IF NOT EXISTS idx_articles_access_status ON articles (access_status);
CREATE INDEX IF NOT EXISTS idx_articles_species ON articles (species);
CREATE INDEX IF NOT EXISTS idx_articles_region ON articles (region);
CREATE INDEX IF NOT EXISTS idx_articles_place ON articles (place);

CREATE TABLE IF NOT EXISTS crawl_checkpoints (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL DEFAULT 'naver_cafe',
    board_key TEXT NOT NULL,
    cafe_id BIGINT NOT NULL,
    menu_id BIGINT NOT NULL,
    until_date DATE NOT NULL,
    next_page INTEGER NOT NULL DEFAULT 1,
    last_seen_article_id BIGINT,
    last_seen_date DATE,
    status TEXT NOT NULL DEFAULT 'pending',
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source, board_key, until_date)
);

CREATE INDEX IF NOT EXISTS idx_crawl_checkpoints_status ON crawl_checkpoints (status);
