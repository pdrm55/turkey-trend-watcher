<div align="center">

# TrendiaTR

**AI-powered Turkish news aggregation, clustering, and real-time trend scoring**

🌐 [trendiatr.com](https://trendiatr.com) · 📊 [monitor.trendiatr.com](https://monitor.trendiatr.com)

`Python 3.11` · `Flask` · `PostgreSQL` · `ChromaDB` · `Redis` · `Ollama` · `Gemini API`

</div>

---

## What it does

TrendiaTR detects breaking Turkish news trends in real time. It ingests content from **50+ RSS feeds**, **37 Telegram channels**, and **X (Twitter) trends**, clusters semantically related articles using vector embeddings, and scores each cluster with a proprietary multi-signal metric called **TPS (Trend Priority Score)**. The highest-scoring trends are auto-published to a Telegram channel and exposed via a B2B REST API. A full **Persian (Farsi) edition** of the site is served at `/fa/` with on-demand, cached AI translation.

## Architecture at a glance

```
        ┌─── DATA SOURCES ───┐
   RSS feeds   Telegram   X-Trends
        │          │          │
   rss_fetcher  telegram   social_worker
        └──────────┼──────────┘
                   ▼
        text_utils → classifier         clean & categorize
                   ▼
        ai_engine (ChromaDB + Ollama)   embed → search → verify → cluster
                   ▼
        PostgreSQL (raw_news · trends · trend_arrivals)
                   ▼
        scoring_queue (Redis priority queue)
                   ▼
        gravity_worker + scoring.py     TPS calculation, decay, alerts
                   ▼
   ┌───────────────┼────────────────┐
 Telegram     Web Dashboard      B2B REST API
 channel      (Flask SSR +       (/api/v1/)
 publish      FA edition)
```

### TPS Scoring

`TPS = (Velocity·0.35 + Engagement·0.30 + SourceAuthority·0.20 + Novelty·0.15) × criticality_boost × confidence`

Category-aware decay (politics decays slowest, sports fastest) is applied every 30 minutes. Critical keywords (`deprem`, `patlama`, `darbe`, …) trigger a ×1.6 boost.

## Tech stack

| Layer | Technology |
|-------|-----------|
| Web / API | Flask 3.1 · Gunicorn (4 workers, preload) · Flask-Limiter |
| Database | PostgreSQL 15 (SQLAlchemy 2.0) · Redis (queue, cache, locks) |
| Vector search | ChromaDB 1.4 · `intfloat/multilingual-e5-large` (1024-dim) |
| LLM | Ollama (Qwen 2.5:1.5b, local) · Google Gemini (flash-lite) |
| Collectors | feedparser · Telethon · BeautifulSoup |
| Dashboard | Streamlit · pandas · Docker SDK · psutil |

## Quick start

```bash
# 1. Configure environment
cp .env.example .env        # then fill in credentials (see below)

# 2. Start infrastructure + web (Postgres, Redis, ChromaDB, Ollama, API)
sudo docker compose up -d

# 3. Start the full system including all background workers
sudo docker compose --profile workers up -d
```

| URL | Service |
|-----|---------|
| `http://localhost:5000` | Public site (Turkish) |
| `http://localhost:5000/fa/` | Persian edition |
| `http://localhost:8501` | Admin dashboard (Streamlit) |
| `http://localhost:8080` | Public demo dashboard |

### Required environment variables

```bash
# Database
POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB
POSTGRES_HOST=ttw_postgres        # Docker network hostname
REDIS_HOST=ttw_redis
CHROMA_HOST=ttw_chroma

# Telegram
TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_PHONE
TELEGRAM_BOT_TOKEN, ADMIN_CHAT_ID, PUBLIC_CHANNEL_ID

# AI
GOOGLE_API_KEY                    # Gemini text (summarization + FA translation)
IMAGEN_API_KEY                    # Gemini Imagen (free tier, image cards)
OLLAMA_API_URL=http://ttw_ollama:11434/api/generate

# Misc
SECRET_KEY                        # Flask session signing (required)
BASE_SITE_URL=https://trendiatr.com
```

## Services

Service names differ from container names — use the **service name** in `docker compose` commands:

| Service | Container | Profile | Role |
|---------|-----------|---------|------|
| `api_server` | ttw_api | always | Flask/Gunicorn web + API; runs `init_db()` on startup |
| `dashboard` | ttw_dashboard | always | Admin Streamlit (token monitor, analytics) |
| `db_init` | ttw_init | always (once) | Schema sync + pulls Qwen model |
| `rss_worker` | ttw_rss | workers | RSS poller |
| `telegram_worker` | ttw_telegram | workers | Telethon channel listener |
| `social_worker` | ttw_social | workers | X-trend monitor |
| `summarizer` | ttw_summarizer | workers | Gemini summarization + FA translation |
| `gravity_worker` | ttw_gravity | workers | TPS scoring, decay, FA sweep |
| `merge_worker` | ttw_merge | workers | Cluster consolidation (Gemini) |
| `image_worker` | ttw_image_worker | workers | Cover image download / AI cards |
| `x_worker` | ttw_x_worker | workers | X draft generation |
| `market_worker` | ttw_market | workers | Financial ticker data |
| `telegram_bot_worker` | ttw_interactive_bot | workers | Interactive admin bot |

```bash
# Restart a single service (use the SERVICE name, not container name)
sudo docker compose restart api_server
sudo docker compose restart summarizer gravity_worker

# Tail logs
sudo docker compose logs api_server --tail=50 -f
```

## Persian (FA) edition

The `/fa/` routes mirror the Turkish site with an RTL layout. Translation follows a 3-layer lookup — **Redis (24h TTL) → `trends.fa_title` / `trends.fa_summary` DB columns → Gemini API** — so each cluster is translated at most once and persists across restarts.

- **On creation**: `summarizer.py` translates title + summary in one Gemini call the moment Turkish content is generated, preserving the original Markdown structure (`### ⚡ خلاصه`, bullet lists, emoji icons).
- **Self-healing**: `gravity_worker` runs a sweep every 30 minutes that finds any trend with a missing/incomplete translation and retries it (highest-TPS first).
- **Invalidation**: when a Turkish title or summary changes, `fa_title`/`fa_summary` are set to `NULL` and the Redis cache is cleared, forcing a fresh translation.

All Gemini token usage (summarization **and** translation) is logged to `ai_monitor_data.csv` and visualized in the admin dashboard, with a filter to break down cost by call type.

## Testing

```bash
# B2B API integration tests (requires a live database)
python3 -m pytest tests/test_b2b_api.py -v

# Run a single test class
python3 -m pytest tests/test_b2b_api.py::TestAuthentication -v
```

## Database migrations

There is no Alembic. Schema changes are applied by `init_db()` in `app/database/models.py`, which runs automatically when `api_server` starts. It only **adds** columns — it never drops data. To add a column:

1. Add the `Column(...)` to the ORM model.
2. Add an idempotent migration block in `init_db()`:
   ```python
   if 'new_column' not in trend_columns:
       conn.execute(text("ALTER TABLE trends ADD COLUMN new_column TYPE"))
   ```
3. `sudo docker compose restart api_server` to apply.

## Further reading

- [`CLAUDE.md`](CLAUDE.md) — architecture notes and conventions for working in this repo
- [`TECHNICAL_DOCUMENTATION.md`](TECHNICAL_DOCUMENTATION.md) — full file-by-file reference
- [`API_DOCUMENTATION.md`](API_DOCUMENTATION.md) — B2B REST API reference
