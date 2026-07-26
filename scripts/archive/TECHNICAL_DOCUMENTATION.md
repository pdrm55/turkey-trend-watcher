# TrendiaTR — Technical Documentation

> **Platform:** AI-powered Turkish news aggregation, clustering, and trend scoring system  
> **Domain:** trendiatr.com | Monitor: monitor.trendiatr.com  
> **Stack:** Python 3.11 · Flask · PostgreSQL · ChromaDB · Redis · Ollama · Gemini API  

---

## Table of Contents

1. [Project Strategy & Architecture](#1-project-strategy--architecture)
2. [Technology Stack](#2-technology-stack)
3. [System Architecture Diagram](#3-system-architecture-diagram)
4. [End-to-End Data Flow](#4-end-to-end-data-flow)
5. [Database Schema](#5-database-schema)
6. [File Reference](#6-file-reference)
   - [Configuration](#61-configuration)
   - [Database Layer](#62-database-layer)
   - [Core Modules](#63-core-modules)
   - [Collectors](#64-collectors)
   - [Workers](#65-workers)
   - [API Layer](#66-api-layer)
   - [Templates](#67-templates)
   - [Infrastructure](#68-infrastructure)
7. [Inter-Module Dependency Map](#7-inter-module-dependency-map)
8. [TPS Scoring Engine](#8-tps-scoring-engine)
9. [AI Pipeline Detail](#9-ai-pipeline-detail)
10. [Configuration Reference](#10-configuration-reference)
11. [Docker Services](#11-docker-services)
12. [Operational Scripts](#12-operational-scripts)

---

## 1. Project Strategy & Architecture

### Mission

TrendiaTR detects breaking Turkish news trends in real time by ingesting content from 50+ RSS feeds and 37 Telegram channels, clustering semantically related articles using vector embeddings, and scoring each cluster with a multi-signal proprietary metric called **TPS (Trend Priority Score)**. The highest-scoring trends are published automatically to a Telegram channel and made available via a B2B REST API.

### Core Design Principles

| Principle | Implementation |
|-----------|---------------|
| **Speed over completeness** | Adaptive polling: 45-second minimum cycle for RSS during breaking news |
| **Dedup before storage** | 48-hour rolling vector cache prevents re-clustering known stories |
| **Async scoring** | Ingestion never blocks on scoring — Redis priority queue decouples them |
| **Multi-signal trust** | TPS combines velocity, engagement, source authority, and novelty |
| **Bot protection on Ollama** | Two-level lock (process semaphore + Redis) serializes all LLM calls |
| **Human-in-the-loop publishing** | Admin approval required unless TPS exceeds auto-publish threshold |

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        DATA SOURCES                             │
│   50 RSS Feeds (Tier 1-3)    37 Telegram Channels    X Trends  │
└────────────┬────────────────────────┬───────────────────┬───────┘
             │                        │                   │
     rss_fetcher.py          telegram_bot.py      social_worker.py
             │                        │                   │
             └────────────────────────┴───────────────────┘
                                      │
                            ┌─────────▼──────────┐
                            │   text_utils.py     │  Clean & Normalize
                            │   classifier.py     │  Categorize
                            └─────────┬──────────┘
                                      │
                            ┌─────────▼──────────┐
                            │   ai_engine.py      │  Embed → Search → Verify
                            │   (ChromaDB+Ollama) │
                            └─────────┬──────────┘
                                      │
                         ┌────────────┴────────────┐
                         │     PostgreSQL           │
                         │  raw_news · trends       │
                         │  trend_arrivals          │
                         └────────────┬────────────┘
                                      │
                            ┌─────────▼──────────┐
                            │  scoring_queue.py   │  Redis Priority Queue
                            └─────────┬──────────┘
                                      │
                            ┌─────────▼──────────┐
                            │  gravity_worker.py  │  TPS Calculation
                            │  scoring.py         │  Decay & Alerts
                            └─────────┬──────────┘
                                      │
               ┌──────────────────────┼──────────────────────┐
               │                      │                      │
      ┌────────▼──────┐    ┌──────────▼──────┐   ┌──────────▼──────┐
      │ Telegram      │    │  Web Dashboard  │   │   B2B REST API  │
      │ Channel Pub.  │    │  (Flask+SSR)    │   │   /api/v1/      │
      └───────────────┘    └─────────────────┘   └─────────────────┘
```

---

## 2. Technology Stack

### Backend Runtime

| Library | Version | Purpose |
|---------|---------|---------|
| Python | 3.11 | Runtime |
| Flask | 3.1.2 | Web framework & API server |
| Gunicorn | 21.2.0 | WSGI production server (4 workers, preload) |
| Werkzeug | 3.1.5 | WSGI utilities |
| Flask-Limiter | latest | Redis-backed rate limiting |

### Database & Storage

| Library | Version | Purpose |
|---------|---------|---------|
| SQLAlchemy | 2.0.46 | ORM for PostgreSQL |
| psycopg2-binary | 2.9.11 | PostgreSQL adapter |
| redis | 7.1.0 | Queue, cache, distributed locks, page view counters |
| chromadb | 1.4.1 | Vector database for semantic clustering |

### AI & Machine Learning

| Library | Version | Purpose |
|---------|---------|---------|
| torch | 2.10.0 | PyTorch — required by sentence-transformers |
| sentence-transformers | 5.2.2 | `multilingual-e5-large` (1024-dim embeddings) |
| google-genai | 1.61.0 | Gemini API — summarization, merge verification, X content |
| Ollama (external) | — | Local Qwen 2.5:1.5b LLM — cross-validation, comment moderation |

### Collectors & Scrapers

| Library | Version | Purpose |
|---------|---------|---------|
| feedparser | 6.0.12 | RSS/Atom feed parsing |
| beautifulsoup4 | 4.12.2 | HTML scraping (X trend sites) |
| requests | 2.32.5 | HTTP client with retry wrapper |
| Telethon | 1.42.0 | Telegram MTProto client (channel listener) |
| pyTelegramBotAPI | 4.26.0 | Telegram Bot API (notifications + interactive bot) |

### Processing & Utilities

| Library | Version | Purpose |
|---------|---------|---------|
| pandas | 2.3.3 | CSV analytics in dashboard |
| Pillow | 12.1.0 | Image processing for X draft cards |
| python-dotenv | 1.2.1 | `.env` file loading |
| python-dateutil | 2.9.0 | RSS date parsing |
| rapidfuzz | 3.6.2 | Fast fuzzy string matching (merge pre-filter) |
| psutil | 6.0.0 | System resource monitoring in dashboard |
| docker | 7.1.0 | Docker SDK — container stats in dashboard |
| yfinance | 0.2.54 | Financial market data |

### Frontend & UI

| Technology | Purpose |
|------------|---------|
| streamlit 1.53.1 | Admin dashboard & demo dashboard |
| Jinja2 (via Flask) | Server-side HTML rendering |
| Tailwind CSS (CDN) | Styling — all templates |
| marked.js (CDN) | Markdown rendering in modal |
| Google Analytics 4 (`G-3B9YSY997H`) | Visitor tracking |
| Schema.org JSON-LD | SEO structured data |

---

## 3. System Architecture Diagram

```
┌─────────────────── Docker Network: ttw_network ──────────────────────┐
│                                                                        │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                │
│  │ ttw_postgres │  │  ttw_redis   │  │  ttw_chroma  │                │
│  │ :5432→5433   │  │    :6379     │  │    :8000     │                │
│  │ PostgreSQL15 │  │  Redis Alpine│  │ ChromaDB 1.4 │                │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘                │
│         │                 │                  │                        │
│  ┌──────▼─────────────────▼──────────────────▼───────┐               │
│  │                   ttw_ollama                        │               │
│  │              Qwen 2.5:1.5b  :11434                 │               │
│  └───────────────────────────────────────────────────-┘               │
│                                                                        │
│  ┌──────────────────────────────────────────────────────┐             │
│  │                    ttw_api  :5000                     │             │
│  │     Flask + Gunicorn (4 workers, preload)             │             │
│  │     web_server.py → routes.py + api_v1.py            │             │
│  └──────────────────────────────────────────────────────┘             │
│                                                                        │
│  ┌──────────────────┐  ┌─────────────────────────────────┐            │
│  │ ttw_dashboard    │  │ ttw_demo  :8080                  │            │
│  │ Streamlit :8501  │  │ Streamlit (public demo)          │            │
│  └──────────────────┘  └─────────────────────────────────┘            │
│                                                                        │
│  ─────────── Workers (docker-compose profile: workers) ──────────     │
│  ttw_rss  ttw_telegram  ttw_summarizer  ttw_image_worker               │
│  ttw_gravity  ttw_merge  ttw_social  ttw_x_worker                     │
│  ttw_market  ttw_interactive_bot                                       │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 4. End-to-End Data Flow

### Phase 1 — Ingestion

```
RSS / Telegram / X Trend
        │
        ▼
1. Text cleaning       text_utils.clean_text()
2. Boilerplate strip   text_utils.clean_text_for_embedding()
3. Category detect     classifier.fast_classify()
4. Embedding           ai_engine → SentenceTransformer (multilingual-e5-large)
5. Vector search       ChromaDB.query(n_results=1, where={"ts": {$gte: 48h_ago}})
6. Distance decision:
   ├── dist < auto_merge_thresh  → join existing cluster (skip Ollama)
   ├── dist < uncertain_thresh   → ask Ollama: "same event?"
   │       ├── YES → join cluster
   │       └── NO  → create new cluster
   └── dist > uncertain_thresh   → always new cluster
7. PostgreSQL write    raw_news + trends (create or update)
8. Queue              scoring_queue.enqueue(trend_id, BREAKING|NORMAL)
```

### Phase 2 — Scoring (Async)

```
scoring_queue (Redis)
        │
        ▼ gravity_worker.process_pending_scores() — every 5s
1. Fetch trend + related raw_news + arrivals (5 DB queries total)
2. TPSCalculator.run_tps_cycle()
   ├── V signal: article velocity over 15min/1h/3h windows
   ├── E signal: comment count
   ├── S signal: source tier weights (1.25x / 1.0x / 0.75x)
   └── N signal: first-seen recency decay
3. Critical keyword boost (×1.6 for deprem/patlama/darbe etc.)
4. Store in trend_score_history
5. Threshold check:
   ├── TPS ≥ 20  → alert_service.send_admin_alert() (Telegram inline keyboard)
   └── TPS ≥ 35  → alert_service.publish_to_channel() (auto-publish)
```

### Phase 3 — Post-Processing (Background)

```
Hourly  → merge_worker: Gemini verifies cluster pairs, merges duplicates
Daily   → gravity_worker: decay (politics −2%/h, sports −15%/h, general −8%/h)
Every 6h → gravity_worker: cleanup inactive media, prune score history >48h
On demand → summarizer: Gemini generates Turkish summaries
On demand → image_processor: download cover images, generate AI cards
On demand → x_worker: generate X (Twitter) draft content
```

### Phase 4 — Publication

```
Admin approves via Telegram bot inline keyboard
        │
        ├── publish_to_channel() → Telegram public channel
        ├── indexing_utils.notify_google() → Google Indexing API (instant SEO)
        └── Web dashboard → SSR trend detail page cached in Redis (600s TTL)
```

---

## 5. Database Schema

### `raw_news`

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL PK | Auto-increment |
| source_type | VARCHAR | `rss` / `telegram` / `x` |
| source_name | VARCHAR | Feed name or channel username |
| source_tier | INTEGER | 1=official, 2=reputable, 3=other |
| external_id | VARCHAR UNIQUE | RSS GUID or Telegram message URL |
| content | TEXT | Cleaned article text |
| published_at | TIMESTAMP | Original publish time |
| created_at | TIMESTAMP | Ingestion time |
| trend_id | FK → trends | Cluster assignment |
| media_status | INTEGER | 0=pending, 1=downloading, 2=ready, −1=error |
| media_url | VARCHAR | Source image URL |
| media_path | VARCHAR | Local storage path |
| media_meta | JSON | Width, height, format |
| video_path | VARCHAR | Local video path |

### `trends`

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL PK | Auto-increment |
| cluster_id | VARCHAR UNIQUE | ChromaDB vector document ID |
| slug | VARCHAR UNIQUE | SEO URL slug (`{id}-{title-slug}`) |
| title | TEXT | Best headline (updated by scoring) |
| summary | TEXT | AI-generated Turkish summary |
| category | VARCHAR | Siyaset/Ekonomi/Gündem/Spor/Teknoloji/Sanat |
| message_count | INTEGER | Total articles in cluster |
| score | FLOAT | Raw signal score |
| tps_signal | FLOAT | Current TPS value |
| tps_confidence | FLOAT | Confidence multiplier |
| final_tps | FLOAT | `tps_signal × tps_confidence` |
| previous_tps | FLOAT | Last cycle's TPS (for trajectory) |
| trajectory | VARCHAR | `up` / `down` / `steady` |
| needs_scoring | BOOLEAN | Flag for gravity_worker queue |
| is_active | BOOLEAN | Visible to public (TPS > 3.0) |
| is_published | BOOLEAN | Sent to Telegram channel |
| has_social_signal | BOOLEAN | Confirmed by X trend or Telegram wire |
| first_seen | TIMESTAMP | Cluster creation time |
| last_updated | TIMESTAMP | Most recent article ingested |
| ai_processed_at | TIMESTAMP | Last summary generation |
| cover_image | VARCHAR | Static media path |
| video_path | VARCHAR | Video media path |
| tags | JSON | Array of keyword tags |
| entities | JSON | People, locations, organizations; also stores cross_validated flag |
| radar_phase_triggered | BOOLEAN | X Radar draft generated |
| radar_tweet_id | VARCHAR | Published tweet ID |

### `trend_arrivals`

Stores one row per article ingested into a cluster. Used by `TPSCalculator` to compute the velocity signal (arrival rate over 15min/1h/3h windows).

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL PK | |
| trend_id | FK → trends | |
| raw_news_id | FK → raw_news | |
| timestamp | TIMESTAMP | Arrival time (UTC, no TZ) |

### `trend_score_history`

Time-series of TPS scores for charts and trend analysis.

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL PK | |
| trend_id | FK → trends | |
| tps_score | FLOAT | Score at this moment |
| timestamp | TIMESTAMP | |
| event_type | VARCHAR | `scoring` / `decay` / `merge` |

### `system_settings`

Dynamic key-value configuration overrideable without restart.

| Column | Type | Description |
|--------|------|-------------|
| key | VARCHAR UNIQUE | Setting name |
| value | VARCHAR | Setting value |
| updated_at | TIMESTAMP | |

Notable keys: `auto_publish_threshold`, `admin_alert_threshold`

### `market_assets` / `market_history`

Financial data for the market ticker widget.

| Asset | Symbol | Type |
|-------|--------|------|
| US Dollar | USDTRY | currency |
| Euro | EURTRY | currency |
| Gold | GC=F | commodity |
| Bitcoin | BTC-USD | crypto |
| Borsa Istanbul | BIST100 | index |

### `x_drafts`

Staged Twitter/X content pending admin approval.

| Column | Type | Description |
|--------|------|-------------|
| trend_id | FK → trends | Source trend |
| hook_text | TEXT | First 280-char tweet |
| long_caption | TEXT | Thread continuation |
| image_short_text | TEXT | Text overlay for card image |
| tps_score | FLOAT | TPS at time of draft creation |
| image_path | VARCHAR | Generated GIF/image path |
| status | VARCHAR | `draft` / `sent` |
| draft_type | VARCHAR | `standard` / `radar` |
| tweet_id | VARCHAR | After publication |

### `comments` / `comment_votes`

User comment system with AI moderation and vote deduplication (session fingerprint).

### `api_clients`

B2B API client registry with per-plan monthly call quotas.

---

## 6. File Reference

### 6.1 Configuration

---

#### `app/config.py`

Central configuration class. Reads all settings from environment variables with sensible defaults.

**Key sections:**
- Database: PostgreSQL connection (host, port, user, pass, db name)
- Telegram: API credentials, bot token, admin chat ID, public channel ID
- Polling intervals: RSS (45–600s), Social (120–1800s) with prime-hours acceleration
- Scoring queue: max size 5000, batch 50, retries 2
- HTTP resilience: 3 retries, 0.7s→8s exponential backoff
- Source tiers: Tier-1 list (AA, TRT, DHA, IHA), Tier-2 list (Hürriyet, Sözcü, etc.)
- TPS thresholds: alert at 20, auto-publish at 35
- Multi-source validation: min score 15, min platforms 2
- AI: Ollama URL, model name (`qwen2.5:1.5b`)

**Consumed by:** every module in the project via `from app.config import Config`

---

### 6.2 Database Layer

---

#### `app/database/models.py`

SQLAlchemy ORM definitions for all tables. Exports:
- All model classes: `RawNews`, `Trend`, `TrendArrivals`, `TrendScoreHistory`, `SystemSettings`, `MarketAsset`, `MarketHistory`, `EntityImageCache`, `XDraft`, `Comment`, `CommentVote`, `APIClient`
- `SessionLocal` — scoped session factory
- `engine` — SQLAlchemy engine
- `init_db()` — creates all tables on startup

**Dependencies:** `app/config.py` (for `SQLALCHEMY_DATABASE_URI`)  
**Consumed by:** all workers, collectors, API routes, and core modules

---

### 6.3 Core Modules

---

#### `app/core/ai_engine.py`

The central AI processing pipeline. This is the most complex module in the project.

**Responsibilities:**

1. **Text Embedding** — `multilingual-e5-large` via SentenceTransformer (lazy-loaded on first use). Produces 1024-dimensional vectors. Strips boilerplate before embedding via `text_utils.clean_text_for_embedding()`.

2. **Vector Storage & Search** — ChromaDB collection `news_cluster_v2`. Documents are stored with metadata `{"ts": unix_timestamp, "source": source_type}`. Queries use a 48-hour rolling window (`ts >= now - 172800`) to limit search space for fresh trends.

3. **Distance Thresholds** (cosine distance):

   | Threshold | Value (RSS) | Value (X-Trend) | Action |
   |-----------|-------------|-----------------|--------|
   | `auto_merge_thresh` | 0.08 | 0.12 | Merge without LLM |
   | `uncertain_thresh` | 0.35 | 0.20 | Ask Ollama |
   | Above uncertain | — | — | New cluster |

   Clusters older than 24h use a stricter auto-merge threshold (0.12) to prevent stale merges.

4. **Ollama LLM Verification** — `verify_cross_trend()` and `ask_local_llm()` call the local Qwen 2.5:1.5b model with Turkish prompts. Protected by:
   - **Level 1**: `threading.Semaphore(1)` — non-blocking, per-process
   - **Level 2**: Redis `SET NX EX 35` distributed lock — cross-container serialization
   - **Circuit Breaker**: 3 consecutive failures → 120-second cooldown

5. **Cluster Operations** — `process_news()` is the main entry point. Returns `(cluster_id, is_new)`. Also provides `merge_clusters()` for the merge_worker, `get_related_trends()` for the API, and `moderate_comment()` for comment moderation.

6. **Fast Dedup Cache** — In-memory LRU dict (180s TTL) of recently seen `external_id` values. Prevents redundant ChromaDB lookups for duplicate feed items.

**Key method signatures:**
```python
process_news(text, source_type, external_id) → (cluster_id, is_new_cluster)
verify_cross_trend(trend_name, headline) → bool
ask_local_llm(text1, text2) → bool
get_related_trends(cluster_id, n=5) → List[dict]
merge_clusters(cluster_id_keep, cluster_id_drop) → bool
moderate_comment(comment_text) → str  # "approved"|"rejected"|"shadow_banned"
```

**Dependencies:** `app/config.py`, `app/core/text_utils.py`, ChromaDB, Ollama, SentenceTransformer, Redis

---

#### `app/core/scoring.py`

TPS scoring engine. See [Section 8](#8-tps-scoring-engine) for full detail.

**Exports:** `TPSCalculator` class, `get_source_tier()`, `CRITICAL_KEYWORDS`

**Dependencies:** `app/database/models.py`, `app/core/ai_engine.py`, `app/config.py`, `app/core/observability.py`

---

#### `app/core/scoring_queue.py`

Redis-backed async priority queue decoupling ingestion from scoring.

**Design:**
- Two priority lanes stored as Redis Lists: `scoring_queue:breaking` and `scoring_queue:normal`
- Pending set (`scoring_queue:pending`) for deduplication — prevents a trend from being scored twice simultaneously
- On enqueue: `RPUSH queue_key trend_id` + `SADD pending_key trend_id`
- On consume: `LPOP` (breaking first, then normal) + remove from pending set
- **Backpressure**: When `len(normal_queue) > SCORING_QUEUE_MAX_SIZE`, new NORMAL jobs are dropped with a warning

**Exports:** `ScoringQueue` class (singleton `scoring_queue`), priority constants `BREAKING` / `NORMAL`

**Dependencies:** `app/config.py`, Redis

---

#### `app/core/classifier.py`

Keyword-based category classifier. No ML model — pure lexical scoring.

**Categories:** Siyaset (Politics), Ekonomi (Economy), Spor (Sports), Teknoloji (Technology), Sanat (Arts & Culture), Gündem (General — default)

**Algorithm (`fast_classify`):**
1. Score each category using weighted keyword sets (high=60pts, medium=20pts, low=5pts)
2. Apply negative rules: Sports-vs-Politics conflict subtracts 100 from competitor
3. Winner must score ≥60 AND be 1.8× Gündem's score; otherwise defaults to Gündem
4. Density check: keyword count / text length must exceed category-specific minimum

**Dependencies:** none (standalone)

---

#### `app/core/text_utils.py`

Text cleaning and normalization utilities.

**Key functions:**

| Function | Input | Output | Description |
|----------|-------|--------|-------------|
| `clean_text(text)` | raw HTML/text | cleaned string | Strips HTML tags, URLs, mentions, special chars, excessive whitespace |
| `clean_text_for_embedding(text, source)` | cleaned text | embedding-ready string | Removes boilerplate phrases that would cause false clustering (arrest templates, operation templates, etc.) |
| `slugify_turkish(text)` | Turkish text | URL slug | Maps Turkish chars (ç→c, ş→s, ı→i, ğ→g, ö→o, ü→u), lowercases, replaces spaces with hyphens |
| `normalize_turkish(text)` | Turkish text | normalized | Lowercase + Turkish-specific normalization |
| `is_noise(text)` | text | bool | Detects subscription prompts, template news, junk content |

**Exports:** `JUNK_KEYWORDS` list (used by collectors to filter junk trends)

**Dependencies:** none (stdlib only)

---

#### `app/core/multi_source_validator.py`

Phase 2 validation: before sending an X trend to Ollama for LLM verification, first search existing DB content for corroborating signals.

**Source Weight Table:**

| Source Type | Weight | Example |
|-------------|--------|---------|
| telegram_wire | 10 | AA, IHA, DHA official Telegram |
| rss_disaster | 9 | RSS with category=afet |
| telegram_intl | 8 | Al Jazeera TR, Sputnik TR |
| rss_tv | 7 | TRT, NTV, CNN Türk RSS |
| rss_major | 5 | Hürriyet, Milliyet RSS |
| telegram_major | 4 | Sabah, Cumhuriyet Telegram |
| rss_standard | 3 | Tier-3 RSS sources |
| telegram_analysis | 2 | Opinion channels |
| telegram_minor | 1 | Unknown/small channels |

**Logic:**
1. Extract search words from keyword (strip `#`, keep words >3 chars, max 3 words)
2. Query `raw_news` WHERE `source_type='telegram'` AND `created_at >= now-30min` AND `content ILIKE '%word%'`
3. Same for `source_type='rss'`
4. Each unique source name contributes one signal (deduped)
5. Returns `ValidationResult(total_score, platform_count, signals)`

**Override rule** (in social_worker.py): If Ollama says NO but `total_score ≥ 15` AND `platform_count ≥ 2`, override to validated.

**Dependencies:** `app/database/models.py`, collectors' `rss_sources.txt`

---

#### `app/core/page_tracker.py`

Redis-based page view tracker for the Visitor Analytics tab.

**Redis key schema:**

| Key | TTL | Purpose |
|-----|-----|---------|
| `ttw:pv:daily:{YYYY-MM-DD}` | 35 days | Page view counter (INCR) |
| `ttw:pv:hourly:{YYYY-MM-DD}:{HH}` | 3 days | Hourly counter (INCR) |
| `ttw:pv:uniq:daily:{YYYY-MM-DD}` | 35 days | Unique visitor HyperLogLog (PFADD) |

**Bot filtering** (`is_bot(user_agent)`): 30-token blocklist covering Googlebot, bingbot, Yandex, SEO crawlers (Semrush, Ahrefs), social preview bots, CLI tools (curl, wget, python-requests), headless browsers (Puppeteer, Playwright). Empty User-Agent → treated as bot. IPs are SHA-256 hashed before storage.

**Called from:** `routes.py` `@api_bp.after_request` hook — fires on all GET 200 HTML responses not starting with `/api/`, `/static/`, `/admin/`, `/sitemap`, `/robots`, `/favicon`.

**Dependencies:** `app/config.py`, Redis

---

#### `app/core/alert_service.py`

Telegram notification service for publishing trends.

**Key methods:**

| Method | Description |
|--------|-------------|
| `send_admin_alert(trend)` | Sends TPS alert to admin chat with inline keyboard (✅ Publish / ❌ Reject / 🔍 Details) |
| `publish_to_channel(trend)` | Formats and sends to public Telegram channel. Fallback: video → image → text-only |
| `send_system_status()` | Daily summary of ingestion stats |

**Smart formatting:** HTML tag balancer before truncation prevents malformed Telegram messages. Summaries truncated at sentence boundaries, not mid-word.

**Dependencies:** `app/config.py`, `app/core/http_resilience.py`, `app/database/models.py`

---

#### `app/core/tg_notifier.py`

Lightweight Telegram notifier for X draft creation events.

**Method:** `notify_admin_x_draft(draft_id, trend_title, tps)` — sends inline keyboard to admin to review/approve/reject X drafts.

**Dependencies:** `app/config.py`, `requests`

---

#### `app/core/x_ai_service.py`

Gemini-powered X (Twitter) content generator.

**Functions:**
- `generate_x_content(trend)` → `{hook_text, long_caption, image_short_text}` — generates engaging Turkish tweet content using Gemini 2.5 Flash Lite
- `generate_x_thread(trend)` → multi-part thread format

Uses dynamic model selection — probes Gemini API for best available `flash-lite` model at startup.

**Dependencies:** `app/config.py`, Gemini API (`GOOGLE_API_KEY`)

---

#### `app/core/x_image_gen.py`

Generates animated GIF cards for X drafts using Pillow.

**Output:** 1200×675px animated GIF with:
- TrendiaTR branding
- Headline text (Roboto Bold)
- TPS score badge
- Category color coding
- Subtle animation (3 frames)

Fonts loaded from `/app/app/static/assets/` (Roboto-Bold.ttf, Roboto-Regular.ttf).

**Dependencies:** Pillow, `app/static/assets/`

---

#### `app/core/api_auth.py`

API key authentication decorator for B2B endpoints.

**`@require_api_key` decorator:**
1. Extracts key from `Authorization: Bearer`, `X-API-Key` header, or `?api_key=` query param
2. Queries `api_clients` table (no row lock)
3. Checks `is_active`, monthly limit, resets counter if 30-day window expired
4. Increments `calls_used`, updates `last_seen_at`
5. Sets `g.api_client` for use in route handlers

**Dependencies:** `app/database/models.py`

---

#### `app/core/api_serializer.py`

Serializes `Trend` and `RawNews` objects to JSON-safe dicts for the B2B API.

**Functions:** `serialize_trend(trend, articles)`, `serialize_media(news)`

Converts relative media paths to absolute URLs using `BASE_SITE_URL`.

**Dependencies:** `app/database/models.py`

---

#### `app/core/indexing_utils.py`

Google Indexing API integration for instant SEO indexing after publication.

**Function:** `notify_google(url, action="URL_UPDATED")` — authenticates with `google_credentials.json` service account and POSTs to `https://indexing.googleapis.com/v3/urlNotifications:publish`.

Called from `gravity_worker.py` when a trend is published to the Telegram channel.

**Dependencies:** `google-auth`, `google-api-python-client`, `google_credentials.json`

---

#### `app/core/http_resilience.py`

Centralized HTTP client with retry logic.

**Function:** `request_with_retry(method, url, *, attempts, backoff_base, backoff_max, jitter, metric_name, **kwargs)`

- Exponential backoff: `delay = backoff_base × 2^attempt + random(0, jitter)`
- Default: 3 attempts, 0.7s base, 8s max, 0.3s jitter
- Emits latency metric via `observability.emit_metric()`

**Dependencies:** `requests`, `app/core/observability.py`

---

#### `app/core/observability.py`

Lightweight structured logging for metrics and tracing.

**Exports:**
- `emit_metric(name, value, tags)` — logs `METRIC name=value tags` to stdout
- `@traced_span(name)` — context manager that logs start/end + duration

**Dependencies:** stdlib only

---

#### `app/core/limiter.py`

Flask-Limiter instance configured with Redis storage backend.

**Strategy:** Fixed window. Key function: `get_remote_address` (respects X-Forwarded-For).

**Used at:** Contact form (5/min, 20/day), API routes

**Dependencies:** `flask-limiter[redis]`, `app/config.py`

---

### 6.4 Collectors

---

#### `app/collectors/rss_fetcher.py`

Polls 50 RSS/Atom feeds on an adaptive schedule.

**Source config file:** `app/collectors/rss_sources.txt`  
Format: `name, url, tier, category, speed` (speed: 1=breaking, 2=fast, 3=standard)

**Processing loop:**
1. Group sources by speed tier; breaking sources polled every cycle
2. For each feed: `feedparser.parse(url)` → filter by `published_at` (skip items older than lookback window)
3. `text_utils.clean_text()` → `classifier.fast_classify()` → `ai_engine.process_news()`
4. On new cluster: create `Trend` + `RawNews` + `TrendArrivals` in a single DB transaction (batch size 25)
5. Enqueue to `scoring_queue` (BREAKING priority for Tier-1 sources, NORMAL otherwise)
6. Sleep: if new trends found → `min_interval`, else → adaptive backoff up to `max_interval`

**Error handling:** `http_resilience.request_with_retry()` for feed fetches. Per-feed exception isolation — one failing feed doesn't block others.

**Adaptive polling config (from Config):**
- Base: 180s | Prime hours (7–23): 90s | Min: 45s | Max: 600s | Jitter: 15%

**Dependencies:** `app/core/ai_engine.py`, `app/core/text_utils.py`, `app/core/classifier.py`, `app/core/scoring_queue.py`, `app/core/http_resilience.py`, `app/core/observability.py`, `app/database/models.py`, `app/config.py`

---

#### `app/collectors/telegram_bot.py`

Async Telegram channel listener using Telethon (MTProto client).

**Session file:** `ttw_session.session` (persistent, survives restarts)

**Channel config:** `app/collectors/channels.txt` (one username per line)  
Dynamic reload: re-reads file every 60 seconds, auto-joins new channels.

**Event handling:**
1. `NewMessage` event fires for each new post
2. Extract text content, optionally download media thumbnail
3. Same pipeline as RSS: `clean_text()` → `fast_classify()` → `ai_engine.process_news()`
4. Creates `RawNews` with `source_type='telegram'` and `source_tier` based on channel weight
5. Stores media references; actual download handled by `image_processor`
6. Enqueues to `scoring_queue`

**Dependencies:** Telethon, `app/core/*`, `app/database/models.py`, `app/config.py`

---

### 6.5 Workers

---

#### `app/workers/gravity_worker.py`

The primary async processing daemon. Runs three independent cycles.

**Cycle 1 — Score Processing** (every 5s)
- Consumes `scoring_queue` (Redis list, LPOP with blocking fallback to DB scan)
- Calls `TPSCalculator.run_tps_cycle(trend_id)` for each dequeued trend
- On threshold breach: `alert_service.send_admin_alert()` or `publish_to_channel()`
- On publish: calls `indexing_utils.notify_google()` for instant SEO indexing

**Cycle 2 — Gravity Decay** (every 30 min)
- Applies category-specific hourly decay to all active trends
- Decay rates: Politics 2%/h, Economy 5%/h, General 8%/h, Tech 10%/h, Sports 15%/h, Arts 12%/h
- Trends below `is_active` threshold (TPS < 3.0) are deactivated after `inactivity_hours`
- Batch size: 100 trends per cycle to prevent long DB locks

**Cycle 3 — Cleanup** (every 6h)
- Deletes video files from `static/media/` for deactivated trends (disk space management)
- Prunes `trend_score_history` records older than 48 hours

**Dependencies:** `app/core/scoring.py`, `app/core/alert_service.py`, `app/core/scoring_queue.py`, `app/core/indexing_utils.py`, `app/database/models.py`, `app/config.py`

---

#### `app/workers/merge_worker.py`

Hourly cluster consolidation using Gemini LLM verification.

**Algorithm:**
1. Query ChromaDB for all trend vectors from the last 72 hours
2. Find pairs with cosine distance in range `[0.16, 0.40]` (too-close pairs are auto-merged by ingestion; too-far pairs are unrelated)
3. Pre-filter pairs:
   - Same category (or both General)
   - `rapidfuzz.fuzz.partial_ratio(title1, title2) > 40` (keyword overlap)
   - Time window: within 24h for distant pairs (0.30–0.40), 72h for close pairs
4. Send up to 60 verified candidate pairs to Gemini: "Are these the same news event?"
5. For confirmed merges: update PostgreSQL (reassign `raw_news`, sum `message_count`, keep higher-TPS trend) + merge ChromaDB vectors

**Gemini config:** `gemini-2.5-flash-lite`, `thinking_budget=0`, `max_output_tokens=200`  
Fast and cheap — no thinking mode needed for binary merge decisions.

**Dependencies:** `app/core/ai_engine.py`, `app/database/models.py`, Gemini API (`GOOGLE_API_KEY`)

---

#### `app/workers/summarizer.py`

Generates AI-powered Turkish summaries for trends.

**Cost logging:** `log_to_csv()` appends to `ai_monitor_data.csv` with model-aware pricing:

| Model | Input | Output |
|-------|-------|--------|
| gemini-2.0-flash-lite | $0.075/1M | $0.30/1M |
| gemini-2.5-flash-lite | $0.10/1M | $0.40/1M |
| gemini-2.5-flash | $0.15/1M | $0.60/1M |

Uses dynamic model selection — picks best available Gemini flash model at startup.

**Trigger:** Runs on trends in `needs_scoring=True` state that lack a summary. Processes in batch, respects rate limits.

**Dependencies:** `app/database/models.py`, Gemini API (`GOOGLE_API_KEY`)

---

#### `app/workers/social_worker.py`

Monitors X (Twitter) trends for Turkey and injects them into the pipeline.

**Trend sources (with fallback):**
1. Primary: `getdaytrends.com/turkey/` — lower Cloudflare protection
2. Fallback: `trends24.in/turkey/`

**Extended validation pipeline** (unique to X trends, not RSS/Telegram):
1. `fetch_google_context(keyword)` → Google News RSS for the trend keyword → returns `(best_title, ui_content, ai_content)`
2. `multi_source_validator.validate(keyword, db, lookback=30min)` → checks if trend appears in recent DB content
3. `ai_engine.verify_cross_trend(keyword, best_title)` → Ollama verifies Google News headline explains the trend
4. Phase-2 override: if Ollama says NO but multi-source score ≥ 15 across ≥ 2 platforms → accept anyway
5. Stores validation metadata in `trend.entities` JSON: `cross_validated`, `multi_source_score`, `multi_source_platforms`

**Adaptive polling:** 120–1800s with prime-hours (8–23) acceleration to 180s.

**Dependencies:** `app/core/ai_engine.py`, `app/core/multi_source_validator.py`, `app/core/scoring_queue.py`, `app/core/http_resilience.py`, `app/database/models.py`, `app/config.py`

---

#### `app/workers/image_processor.py`

Downloads and processes cover images for trends.

**Pipeline:**
1. Poll `raw_news` for items with `media_status=0` (pending)
2. Download from `media_url` using `http_resilience.request_with_retry()`
3. Resize to standard dimensions, convert format
4. Save to `/app/app/static/media/{trend_id}/`
5. Update `media_status=2` (ready) + `media_path`
6. For trends with no image: generate AI image card via Gemini Imagen or DuckDuckGo image search fallback

**Special session:** Uses `ttw_image.session` for Telegram media downloads.

**Dependencies:** Pillow, `app/database/models.py`, `app/core/http_resilience.py`, Gemini Imagen API (`IMAGEN_API_KEY`)

---

#### `app/workers/x_worker.py`

Generates X (Twitter) draft content for high-TPS trends.

**Trigger:** Called by `gravity_worker` when `final_tps >= X_DRAFT_THRESHOLD` and `radar_phase_triggered=False`.

**Steps:**
1. `x_ai_service.generate_x_content(trend)` → hook text + long caption (Gemini)
2. `x_image_gen.generate_x_image(...)` → animated GIF card (Pillow)
3. Creates `XDraft` record, sets `trend.radar_phase_triggered=True`
4. `tg_notifier.notify_admin_x_draft()` → Telegram notification to admin

**Dependencies:** `app/core/x_ai_service.py`, `app/core/x_image_gen.py`, `app/core/tg_notifier.py`, `app/database/models.py`

---

#### `app/workers/market_worker.py`

Fetches live financial market data every 5 minutes.

**Data source:** `yfinance` for USDTRY, EURTRY, GC=F (Gold), BTC-USD, BIST100  
**Storage:** `market_history` table + Redis key `market_ticker` (JSON, 30s TTL)  
**UI consumption:** `/api/market/live` endpoint reads Redis first, falls back to DB

**Dependencies:** yfinance, Redis, `app/database/models.py`

---

#### `app/workers/telegram_bot_worker.py`

Interactive Telegram bot for admin commands.

**Commands:**
- `/start` — welcome
- `/status` — system stats (active trends, queue depth, last error)
- View/approve/reject pending trends
- Manual trend publishing

Handles callback queries from `alert_service.send_admin_alert()` inline keyboards.

**Dependencies:** pyTelegramBotAPI, `app/database/models.py`, `app/core/alert_service.py`

---

#### `app/workers/dashboard.py`

Admin-only Streamlit dashboard (port 8501, monitor.trendiatr.com).

**Authentication:** Username/password gate (hardcoded in Streamlit session state).

**Tabs:**

| Tab | Content |
|-----|---------|
| 🤖 AI Token Monitor | Cost analytics with model-aware pricing recalculation, 30-day filter, daily/hourly charts, success rate, projected monthly cost, pricing drift warning |
| 👥 Visitor Analytics | Today KPIs with yesterday delta, 30-day daily line chart (views + unique visitors), 48-hour hourly bar chart, daily breakdown table |
| 🛠️ Workers & System Live Monitor | Host CPU/RAM (2s live refresh), Docker container stats table, live log viewer |

**Bot detection note:** Visitor counts exclude known crawlers via `page_tracker.is_bot()`.

**Dependencies:** streamlit, pandas, docker SDK, psutil, redis, `app/core/page_tracker.py`, `ai_monitor_data.csv`

---

#### `app/workers/demo_dashboard.py`

Public-facing read-only Streamlit dashboard (port 8080). Shows aggregate stats, trending charts, and sample trend content for demonstration purposes. No authentication.

---

#### `app/workers/reprocess_trends.py`

Utility script for manual reprocessing of historical trends (re-score, re-embed, re-categorize). Run ad-hoc, not part of normal operation.

---

### 6.6 API Layer

---

#### `web_server.py`

Flask application factory. Creates the app instance, registers blueprints, initializes rate limiter, verifies DB schema on startup.

**Blueprints registered:**
- `api_bp` (from `routes.py`) — web UI + utility endpoints
- `api_v1_bp` (from `api_v1.py`) — B2B REST API
- `api_admin_b2b_bp` (from `api_admin.py`) — admin API client management

**Gunicorn command:** `gunicorn --workers 4 --preload --bind 0.0.0.0:5000 --timeout 120 web_server:app`  
`--preload` shares model weights and ChromaDB connection across workers via COW memory.

---

#### `app/api/routes.py`

Main Flask blueprint. Handles all HTML pages, utility JSON endpoints, admin panel, and site infrastructure.

**HTML Routes:**

| Route | Template | Description |
|-------|----------|-------------|
| `GET /` | index.html | SPA homepage with trend timeline |
| `GET /category/<cat>` | index.html | Category-filtered view (SSR meta tags) |
| `GET /trend/<identifier>` | trend_detail.html | Single trend SSR page (Redis cached 600s) |
| `GET /admin` | admin.html | Admin panel (requires auth) |
| `GET /admin/editorial` | editorial.html | Editorial queue |
| `GET /admin/x-studio` | x_studio.html | X draft editor |
| `GET /api_clients` | api_clients.html | API documentation |

**JSON API Routes:**

| Route | Description |
|-------|-------------|
| `GET /api/trends` | Paginated trends list with filters (query, category, sort, date) |
| `GET /api/trends/<id>` | Full trend detail (cached in Redis) |
| `POST /api/trends/<id>/summarize` | Manual summarization trigger |
| `POST /api/comments` | Submit user comment |
| `POST /api/comments/<id>/vote` | Like/dislike comment |
| `GET /api/stats` | System-wide counts |
| `GET /api/market/live` | Live market ticker (Redis-first) |
| `POST /api/contact` | Contact form (Telegram forwarding) |
| `GET /sitemap.xml` | Dynamic XML sitemap (only trends with summary) |
| `GET /robots.txt` | Served from `app/static/robots.txt` |

**Error Handlers:** 404 (JSON for /api/, HTML otherwise), 500 (same), 429 (rate limit)

**Page View Tracking:** `@api_bp.after_request` hook calls `page_tracker.track()` for all GET 200 HTML responses.

**Admin Routes (require HTTP Basic Auth):**  
`/api/admin/trends/*` — publish, reject, update, delete trends, manage comments, media

**Cache Strategy:** Trend detail pages cached in Redis with key `ssr_trend_{identifier}` (600s TTL). Invalidated on any admin action via `invalidate_trend_caches()`.

**Dependencies:** Almost every core module, `app/database/models.py`, `app/config.py`, Redis

---

#### `app/api/api_v1.py`

B2B REST API blueprint. All endpoints require `@require_api_key`.

**Base path:** `/api/v1`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check (no auth) |
| GET | `/trends` | List trends (filters: min_tps, category, trajectory, limit 1–100, offset) |
| GET | `/trends/<id>` | Full trend with all articles and media |
| GET | `/trends/<id>/media` | Media assets for a trend |
| GET | `/usage` | Client's monthly usage stats |

**Response format:** JSON with `data`, `meta` (pagination), `error` fields.

**Rate limiting:** Per-client monthly quota enforced in `api_auth.require_api_key`.

**Dependencies:** `app/core/api_auth.py`, `app/core/api_serializer.py`, `app/database/models.py`

---

#### `app/api/api_admin.py`

Admin API for B2B client management.

**Base path:** `/api/admin/b2b` (requires HTTP Basic Auth via `requires_auth`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/clients` | List all API clients |
| POST | `/clients` | Create client + generate API key |
| PATCH | `/clients/<id>` | Update plan/limits |
| POST | `/clients/<id>/reset-key` | Rotate API key |

**Dependencies:** `app/database/models.py`, `app/core/api_auth.py`

---

### 6.7 Templates

| File | Description |
|------|-------------|
| `index.html` | SPA homepage. Loads timeline via `fetch('/api/trends')`, opens modals for trend detail. Uses `window.history.pushState` for URL changes. GA4 virtual page_view events on category switch, trend open, and modal close. |
| `trend_detail.html` | Server-side rendered trend page for SEO. Full content, Schema.org Article markup, Open Graph tags, canonical URL. Used when accessing `/trend/<slug>` directly. |
| `admin.html` | Admin dashboard — trend queue, publish/reject controls, score history charts |
| `editorial.html` | Editorial workflow — bulk review and categorization |
| `x_studio.html` | X draft viewer/editor — preview hook text and GIF cards before publishing |
| `api_clients.html` | B2B API documentation with interactive key management |
| `404.html` | Custom 404 page with GA4 tracking |
| `500.html` | Custom 500 page with GA4 tracking |

**All templates include:**
- Google Analytics 4 (`G-3B9YSY997H`)
- Tailwind CSS (CDN)
- Schema.org JSON-LD structured data (index.html, trend_detail.html)

---

### 6.8 Infrastructure

---

#### `docker-compose.yml`

Defines all 14 services on a shared bridge network `ttw_network`. Workers are gated behind the `workers` profile (`--profile workers` to start them). The project root is volume-mounted as `.:/app` in all containers so code changes do not require a rebuild.

#### `Dockerfile`

Single image used by all Python services. Based on `python:3.11-slim`. Installs `requirements.txt`, sets `WORKDIR /app`.

#### `scripts/entrypoint.sh`

Shared entrypoint for all containers. Sets threading environment variables (`OMP_NUM_THREADS=1`), clears stale ChromaDB lock files, waits 2 seconds for infrastructure, then `exec "$@"`.

#### `scripts/safe_restart.sh` / `scripts/hard_reset.sh`

Operational scripts for graceful restart and full data reset respectively.

#### `app/static/robots.txt`

```
Disallow: /admin/          (admin panel)
Disallow: /api/admin/      (admin API)
Disallow: /api/v1/         (authenticated B2B API — prevents 401 to Googlebot)
Disallow: /api/trends      (JSON endpoint)
Disallow: /api/stats       (JSON endpoint)
Disallow: /api/market/     (JSON endpoint)
Disallow: /api/contact     (form endpoint)
Disallow: /?q=*            (search results)
Disallow: /api/trends?q=*  (API search)
GPTBot: Disallow /
CCBot: Disallow /
```

#### `app/collectors/rss_sources.txt`

50 RSS sources in CSV format: `name, url, tier, category, speed`

Tier-1 sources include: `anadolu_tr` (Anadolu Agency), `trthaber` (TRT), `bbc_turkce`, `dw_turkce`  
Categories include: gundem, spor, ekonomi, teknoloji, dunya, afet (disaster)

#### `app/collectors/channels.txt`

37 Telegram channels: wire agencies (`anadoluajansi`, `ihaturkiye`, `dhaturkiye`), international (`sputnik_tr`, `aljazeera_tr`), TV (`cnnturkhaber`, `trthaberdijital`), major papers (`sabahgazete`, `cumhuriyet`), specialist (`depremturkey`, `kandillisondepremler`, `bloomberghttv`)

#### `tests/test_b2b_api.py`

Integration tests for the B2B API endpoints. Tests authentication, trend listing with filters, usage endpoint.

#### `ai_monitor_data.csv`

Append-only log of every Gemini API call made by `summarizer.py`.  
Columns: `timestamp, trend_id, model, input_tokens, output_tokens, duration_sec, category, status, cost_usd`  
Read by the Admin Dashboard Token Monitor tab. As of May 2026: ~158K rows.

---

## 7. Inter-Module Dependency Map

```
app/config.py ◄──────────────── imported by almost every module
      │
app/database/models.py ◄─────── imported by all workers, collectors, API routes
      │
      ├── app/core/text_utils.py ◄── no dependencies (pure stdlib)
      │
      ├── app/core/classifier.py ◄── no dependencies (pure stdlib)
      │
      ├── app/core/observability.py ◄── no dependencies (pure stdlib)
      │
      ├── app/core/http_resilience.py ◄── observability
      │
      ├── app/core/limiter.py ◄── config
      │
      ├── app/core/scoring_queue.py ◄── config, redis
      │
      ├── app/core/page_tracker.py ◄── config, redis
      │
      ├── app/core/ai_engine.py ◄── config, text_utils, redis, chromadb, ollama, sentence-transformers
      │
      ├── app/core/scoring.py ◄── models, ai_engine, config, observability, http_resilience
      │
      ├── app/core/api_serializer.py ◄── models
      │
      ├── app/core/api_auth.py ◄── models
      │
      ├── app/core/alert_service.py ◄── config, http_resilience, models
      │
      ├── app/core/tg_notifier.py ◄── config, requests
      │
      ├── app/core/x_ai_service.py ◄── config, google-genai
      │
      ├── app/core/x_image_gen.py ◄── Pillow, static assets
      │
      ├── app/core/indexing_utils.py ◄── google-auth, google-api-python-client
      │
      ├── app/core/multi_source_validator.py ◄── models, rss_sources.txt
      │
      │── COLLECTORS
      ├── app/collectors/rss_fetcher.py ◄── ai_engine, text_utils, classifier, scoring_queue,
      │                                      http_resilience, observability, models, config
      │
      ├── app/collectors/telegram_bot.py ◄── ai_engine, text_utils, classifier, scoring_queue,
      │                                       models, config, Telethon
      │
      │── WORKERS
      ├── app/workers/gravity_worker.py ◄── scoring, scoring_queue, alert_service,
      │                                      indexing_utils, models, config
      │
      ├── app/workers/merge_worker.py ◄── ai_engine, models, config, google-genai
      │
      ├── app/workers/summarizer.py ◄── models, config, google-genai
      │
      ├── app/workers/social_worker.py ◄── ai_engine, multi_source_validator, scoring_queue,
      │                                     http_resilience, text_utils, classifier, models, config
      │
      ├── app/workers/image_processor.py ◄── models, http_resilience, Pillow
      │
      ├── app/workers/x_worker.py ◄── x_ai_service, x_image_gen, tg_notifier, models
      │
      ├── app/workers/market_worker.py ◄── models, yfinance, redis
      │
      ├── app/workers/telegram_bot_worker.py ◄── models, alert_service, pyTelegramBotAPI
      │
      ├── app/workers/dashboard.py ◄── page_tracker (via Redis keys), docker SDK, psutil
      │
      │── API LAYER
      ├── web_server.py ◄── routes.py, api_v1.py, api_admin.py, limiter, models
      │
      ├── app/api/routes.py ◄── almost all core modules, models, config, redis
      │
      ├── app/api/api_v1.py ◄── api_auth, api_serializer, models
      │
      └── app/api/api_admin.py ◄── models, api_auth
```

---

## 8. TPS Scoring Engine

TPS (Trend Priority Score) is a weighted multi-signal score computed per trend by `app/core/scoring.py`.

### Formula

```
TPS = (V_score × 0.35 + E_score × 0.30 + S_score × 0.20 + N_score × 0.15)
      × criticality_boost
      × confidence_multiplier
```

### Signal Definitions

**V — Velocity (35% weight)**

Measures article arrival rate over three time windows. Each window contributes a sub-score based on articles per hour. The `trend_arrivals` table provides timestamps. Windows: 15min (×3 weight), 1h (×2), 3h (×1). Sub-scores are summed and normalized.

**E — Engagement (30% weight)**

Count of approved comments on the trend. Logarithmic scaling prevents viral outliers from dominating.

**S — Source Authority (20% weight)**

Average source tier weight of all articles in the cluster:
- Tier 1 (AA, TRT, DHA, IHA, ANKA): weight 1.25
- Tier 2 (Hürriyet, Sözcü, Habertürk, etc.): weight 1.00
- Tier 3 (all others, X trends, Telegram non-wire): weight 0.75

**N — Novelty (15% weight)**

Recency bonus based on `first_seen` timestamp. Full score for trends < 1h old; decays to zero over 6h.

### Criticality Boost

A `1.6×` multiplier applied when the trend title or summary contains high-priority keywords:

| Level | Keywords |
|-------|---------|
| High (×1.6) | deprem (earthquake), patlama (explosion), istifa (resignation), darbe (coup), şehit (martyr), terör (terror) |
| Sports Gold (×1.4) | tarihi zafer (historic victory), şampiyon (champion), rekor (record) |
| Medium (×1.2) | son dakika (breaking), faiz kararı (interest rate decision), seçim (election) |

### Decay Schedule

Applied every 30 minutes by `gravity_worker.apply_gravity_decay()`:

| Category | Hourly Decay | Half-life |
|----------|-------------|-----------|
| Siyaset (Politics) | −2% | ~35h |
| Ekonomi (Economy) | −5% | ~14h |
| Gündem (General) | −8% | ~9h |
| Teknoloji (Tech) | −10% | ~7h |
| Sanat (Arts) | −12% | ~6h |
| Spor (Sports) | −15% | ~5h |

### Thresholds

| Threshold | Value | Action |
|-----------|-------|--------|
| `THRESHOLD_ADMIN_ALERT` | 20.0 | Telegram notification to admin with inline keyboard |
| `THRESHOLD_AUTO_PUBLISH` | 35.0 | Auto-publish to channel if no admin response |
| `is_active` deactivation | < 3.0 | Hide from public timeline |

Thresholds are overrideable via `system_settings` table without restart.

---

## 9. AI Pipeline Detail

### Embedding Model

**Model:** `intfloat/multilingual-e5-large`  
**Dimensions:** 1024  
**Languages:** Turkish, English, and 100+ others  
**Loading:** Lazy (loaded on first `process_news()` call, shared via Gunicorn preload COW)

**Why multilingual-e5-large:**  
Turkish news contains mixed Turkish/English content (brand names, technical terms). The multilingual model handles this natively without translation.

### ChromaDB Collection

**Collection name:** `news_cluster_v2`  
**Distance metric:** Cosine  
**Document ID:** `cluster_id` (UUID)  
**Metadata stored per document:** `ts` (Unix timestamp), `source` (source_type)

**Query example:**
```python
results = collection.query(
    query_embeddings=[embedding],
    n_results=1,
    where={"ts": {"$gte": time.time() - 172800}},  # 48h rolling window
    include=["distances", "metadatas"]
)
```

### Ollama / Qwen 2.5:1.5b

**Purpose:** Binary semantic verification — "Are these two news texts about the same event?"

**Use cases:**
1. `ask_local_llm(text1, text2)` — during ingestion for uncertain-zone pairs
2. `verify_cross_trend(x_trend, headline)` — validates X trend against Google News
3. `moderate_comment(text)` — approves/rejects/shadow-bans user comments

**Prompt language:** Turkish (to match content language and reduce hallucination)

**Circuit breaker:** 3 failures → 120s cooldown. Redis lock TTL: 35s. Acquire timeout: 20s.

### Gemini API

| Use Case | Module | Model |
|----------|--------|-------|
| Trend summarization | `summarizer.py` | gemini-2.5-flash-lite (dynamic) |
| Merge verification | `merge_worker.py` | gemini-2.5-flash-lite |
| X content generation | `x_ai_service.py` | gemini-2.5-flash-lite (dynamic) |

**Two separate API keys:**
- `GOOGLE_API_KEY` — Gemini text (paid) for summarizer, merge_worker, x_ai_service
- `IMAGEN_API_KEY` — Gemini Imagen (free tier) for image_processor only

---

## 10. Configuration Reference

All settings read from `.env` via `app/config.py`. Below are the most operationally significant:

```bash
# Database
POSTGRES_USER=admin
POSTGRES_PASSWORD=secretpassword
POSTGRES_HOST=localhost
POSTGRES_PORT=5433
POSTGRES_DB=trend_watcher_db

# Telegram
TELEGRAM_API_ID=<your_api_id>
TELEGRAM_API_HASH=<your_api_hash>
TELEGRAM_PHONE=<your_phone>
TELEGRAM_BOT_TOKEN=<bot_token>
ADMIN_CHAT_ID=<admin_chat_id>
PUBLIC_CHANNEL_ID=<channel_id>

# AI
GOOGLE_API_KEY=<gemini_api_key>          # Text models (paid)
IMAGEN_API_KEY=<imagen_api_key>          # Image generation (free)
OLLAMA_API_URL=http://ttw_ollama:11434/api/generate

# Infrastructure
REDIS_HOST=ttw_redis
BASE_SITE_URL=https://trendiatr.com
SECRET_KEY=<flask_secret_key>

# Polling tuning
RSS_PRIME_INTERVAL_SECONDS=90            # During 7:00–23:00
RSS_MIN_POLL_INTERVAL_SECONDS=45         # Floor (breaking news mode)
SOCIAL_POLL_INTERVAL_SECONDS=300         # X trend polling base

# Scoring thresholds
# Override via system_settings table at runtime (no restart needed)
THRESHOLD_ADMIN_ALERT=20.0
THRESHOLD_AUTO_PUBLISH=35.0

# Multi-source validation
VALIDATION_MULTI_SOURCE_MIN_SCORE=15     # Score to override Ollama rejection
VALIDATION_MULTI_SOURCE_MIN_PLATFORMS=2  # Must appear on ≥2 platforms

# Queue
SCORING_QUEUE_MAX_SIZE=5000
INGEST_WRITE_BATCH_SIZE=25
```

---

## 11. Docker Services

| Service | Container | Port | Profile | Description |
|---------|-----------|------|---------|-------------|
| postgres | ttw_postgres | 5433→5432 | always | PostgreSQL 15 Alpine |
| redis | ttw_redis | 6379 | always | Redis Alpine |
| chromadb | ttw_chroma | 8000 | always | ChromaDB 1.4 vector store |
| ollama | ttw_ollama | 11434 | always | Qwen 2.5:1.5b local LLM |
| db_init | ttw_init | — | always (once) | Runs init_db() + pulls Qwen model |
| api_server | ttw_api | 5000 | always | Flask + Gunicorn (4 workers) |
| dashboard | ttw_dashboard | 8501 | always | Admin Streamlit dashboard |
| demo_dashboard | ttw_demo | 8080 | always | Public demo Streamlit |
| telegram_worker | ttw_telegram | — | workers | Telethon channel listener |
| rss_worker | ttw_rss | — | workers | RSS feed poller |
| summarizer | ttw_summarizer | — | workers | Gemini summarization |
| image_worker | ttw_image_worker | — | workers | Media download & processing |
| gravity_worker | ttw_gravity | — | workers | TPS scoring & decay |
| market_worker | ttw_market | — | workers | Financial data |
| social_worker | ttw_social | — | workers | X trend monitoring |
| x_worker | ttw_x_worker | — | workers | X draft generation |
| telegram_bot_worker | ttw_interactive_bot | — | workers | Interactive Telegram bot |
| merge_worker | ttw_merge | — | workers | Cluster consolidation |

**Start commands:**
```bash
# Infrastructure + web only
docker compose up -d

# Full system with all workers
docker compose --profile workers up -d

# Restart single worker
docker compose restart ttw_gravity
```

---

## 12. Operational Scripts

| Script | Purpose |
|--------|---------|
| `scripts/entrypoint.sh` | Universal container entrypoint — env setup, lock cleanup, `exec "$@"` |
| `scripts/safe_restart.sh` | Graceful rolling restart of workers |
| `scripts/hard_reset.sh` | Full data reset (drops DB, clears ChromaDB, removes media) |
| `reset_platform.py` | Python-level platform reset (truncates tables, keeps schema) |
| `sync_chromadb_clusters.py` | Re-syncs ChromaDB vectors from PostgreSQL cluster_ids after hard reset |
| `reprocess_trends.py` | Manually re-runs embedding/scoring on historical trends |
| `audit_fix_clusters.py` | Diagnostic: finds and fixes misassigned raw_news cluster memberships |
| `fix_misplaced_news.py` | Moves raw_news items from wrong trend clusters |
| `reset_x_images.py` | Clears and regenerates X draft GIF images |
| `reset_placeholders.py` | Resets placeholder/test data |
| `run.sh` | Local development startup script |

---

*Documentation generated from codebase as of May 2026. Commit: `0c2f7da`*
