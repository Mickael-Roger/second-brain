-- Per-feed metadata kept separately so favicons (Fever returns them as
-- base64 data URIs, ~0.5–4 KB each) don't get duplicated onto every
-- article row. Articles still carry feed_id / feed_title / feed_group
-- denormalised for query simplicity; we LEFT JOIN this table when the
-- API needs to surface the favicon.
--
-- Re-fetched on every fetch pass — title/group changes in FreshRSS
-- propagate here, and favicons rotate without manual intervention.

CREATE TABLE news_feeds (
    id                TEXT    PRIMARY KEY,    -- Fever feed_id
    title             TEXT,
    feed_group        TEXT,                    -- folder/category name (denormalised)
    site_url          TEXT,
    favicon_data_uri  TEXT,                    -- e.g. "data:image/png;base64,..."
    updated_at        TEXT    NOT NULL
);
