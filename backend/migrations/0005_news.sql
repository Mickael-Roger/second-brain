-- News & Events. Articles are fetched from external sources (FreshRSS/Fever
-- today; X / Bluesky later) on a cron schedule. A separate cluster pass
-- groups same-topic articles into events. Events are what the UI shows as
-- bubbles; hovering an event reveals its underlying articles.

CREATE TABLE news_articles (
    id            TEXT    PRIMARY KEY,            -- source + ':' + external_id
    source        TEXT    NOT NULL,               -- 'freshrss' for now
    external_id   TEXT    NOT NULL,               -- Fever item id (string for portability)
    feed_id       TEXT,
    feed_title    TEXT,
    url           TEXT,
    title         TEXT    NOT NULL,
    description   TEXT,                            -- LLM-synthesised summary from the feed
    author        TEXT,
    published_at  TEXT    NOT NULL,                -- ISO-8601 UTC
    fetched_at    TEXT    NOT NULL,                -- ISO-8601 UTC
    event_id      TEXT,                            -- nullable; set by the cluster pass
    UNIQUE (source, external_id)
);

CREATE INDEX idx_news_articles_published ON news_articles(published_at DESC);
CREATE INDEX idx_news_articles_event     ON news_articles(event_id);
CREATE INDEX idx_news_articles_unclustered ON news_articles(published_at) WHERE event_id IS NULL;

CREATE TABLE news_events (
    id              TEXT    PRIMARY KEY,
    title           TEXT    NOT NULL,
    summary         TEXT,
    occurred_on     TEXT    NOT NULL,              -- YYYY-MM-DD; the day the event clusters around
    article_count   INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL
);

CREATE INDEX idx_news_events_occurred ON news_events(occurred_on DESC);

CREATE TABLE news_fetch_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT    NOT NULL,                 -- 'fetch' | 'cluster'
    source       TEXT,                              -- only set for fetch runs
    started_at   TEXT    NOT NULL,
    finished_at  TEXT,
    status       TEXT    NOT NULL,                 -- 'running' | 'ok' | 'error'
    fetched      INTEGER NOT NULL DEFAULT 0,
    inserted     INTEGER NOT NULL DEFAULT 0,
    clustered    INTEGER NOT NULL DEFAULT 0,
    error        TEXT
);

CREATE INDEX idx_news_runs_started ON news_fetch_runs(started_at DESC);
