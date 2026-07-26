-- Hot-path indexes for TrendiaTR.
--
-- All of these are CREATE INDEX CONCURRENTLY: they do not take a write lock, so
-- they are safe to run against the live database while every worker keeps
-- running. They are purely additive — no data is modified. Reversible with
-- DROP INDEX CONCURRENTLY <name>.
--
-- Run one statement at a time (CONCURRENTLY cannot run inside a transaction):
--   sudo docker exec ttw_postgres psql -U admin -d trend_watcher_db \
--     -c 'CREATE INDEX CONCURRENTLY ...'
--
-- Measured before (raw_news, 492k rows, no index on trend_id):
--   SELECT * FROM raw_news WHERE trend_id = X ORDER BY published_at DESC LIMIT 10
--   → Parallel Seq Scan, 53,290 blocks read, 160 ms, to return 2 rows.
--   This query runs on every scoring cycle (every 5s) and on every page render.

-- 1. THE BIG ONE. raw_news.trend_id has no index; Postgres does not create one
--    for a foreign key. Filtered in scoring.py:309 (every 5s), routes.py
--    227/392/455/1708, gravity_worker.py:102, merge_worker.py:129/151,
--    api_serializer.py:84. Composite so it serves the ORDER BY too.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_raw_news_trend_published
    ON raw_news (trend_id, published_at DESC);

-- 2. multi_source_validator filters raw_news.created_at >= cutoff on every
--    X-trend, every cycle (~60-100 scans of the table every few minutes).
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_raw_news_created_at
    ON raw_news (created_at DESC);

-- 3-5. trends listing / worker queries. Partial on is_active because every one
--      of these paths filters is_active = true, and inactive rows are the
--      majority of the 234k-row table.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_trends_active_tps
    ON trends (final_tps DESC) WHERE is_active;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_trends_active_first_seen
    ON trends (first_seen DESC) WHERE is_active;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_trends_active_updated
    ON trends (last_updated DESC) WHERE is_active;

-- 6. Queried and ordered on every scoring cycle (scoring.py:387-392) and pruned
--    by timestamp in gravity_worker.py:116. 84k rows.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_tsh_trend_time
    ON trend_score_history (trend_id, timestamp DESC);

-- 7. Counted on every trend detail render (routes.py 267/418/462/1718).
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_comments_trend_status
    ON comments (trend_id, status);

-- 8. NOT IN subquery at routes.py:1024 and x_worker.py:71.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_x_drafts_trend
    ON x_drafts (trend_id);

-- Verify afterwards:
--   SELECT indexname, pg_size_pretty(pg_relation_size(indexname::regclass))
--   FROM pg_indexes WHERE indexname LIKE 'idx_%' ORDER BY indexname;
--
-- And re-check the plan:
--   EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM raw_news
--   WHERE trend_id = 280500 ORDER BY published_at DESC LIMIT 10;
