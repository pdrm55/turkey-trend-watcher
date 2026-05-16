# TrendiaTR B2B API — Documentation

**Version:** 1.0
**Base URL:** `https://trendiatr.com/api/v1`
**Last Updated:** 2026-05-16

---

## Overview

The TrendiaTR B2B API provides programmatic access to real-time Turkish news trend clusters. Each trend represents a cluster of related articles aggregated from RSS feeds, Telegram channels, and social media, scored by the proprietary TPS (Trend Power Score) algorithm. External clients — media agencies, brands, analytics platforms — can receive filtered trend data, full article clusters, and media assets.

---

## Authentication

Every request to a protected endpoint must include a valid API key. Three methods are accepted:

### Method 1 — Authorization: Bearer (recommended)
```http
GET /api/v1/trends
Authorization: Bearer ttr_your_api_key_here
```

### Method 2 — X-API-Key header
```http
GET /api/v1/trends
X-API-Key: ttr_your_api_key_here
```

### Method 3 — Query parameter
```http
GET /api/v1/trends?api_key=ttr_your_api_key_here
```

API keys begin with the prefix `ttr_` followed by a cryptographically random string.

---

## Rate Limiting

Each plan has a monthly call limit. The counter resets 30 days after account creation.

When the limit is exceeded, the API returns `429 Too Many Requests`:

```json
{
  "error": "rate_limit_exceeded",
  "message": "Monthly limit of 1000 calls reached.",
  "limit": 1000,
  "used": 1000,
  "resets_at": "2026-06-16T10:00:00Z"
}
```

Check remaining quota at any time with `GET /api/v1/usage`.

---

## Plans & Thresholds

| Plan | Monthly Calls | Min TPS Threshold | Description |
|------|--------------|-------------------|-------------|
| **starter** | 1,000 | 70.0 | High-signal trends only |
| **pro** | 10,000 | 50.0 | Broader coverage |
| **enterprise** | Unlimited | 30.0 | Full access, all active trends |

**TPS Threshold** is the minimum Trend Power Score a trend must have to appear in your results. You may request a *higher* threshold with `min_tps`, but you cannot go below your plan's minimum.

---

## Endpoints

---

### GET /health

Health check — no authentication required. Use for uptime monitoring.

**Response:**
```json
{
  "status": "ok",
  "service": "TrendiaTR B2B API",
  "version": "1.0"
}
```

Returns `503` if the database is unreachable.

---

### GET /trends

List active trends above your plan's TPS threshold, ordered by TPS score descending.

**Authentication:** Required

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `min_tps` | float | plan threshold | Minimum TPS score. Cannot go below your plan's threshold. |
| `category` | string | — | Filter by category (case-insensitive, partial match). E.g. `Spor`, `Gündem`, `Siyaset` |
| `trajectory` | string | — | Filter by trend direction: `up`, `down`, or `steady` |
| `limit` | int | 20 | Number of results (max 100) |
| `offset` | int | 0 | Pagination offset |

**Example Request:**
```http
GET /api/v1/trends?min_tps=75&trajectory=up&limit=10
X-API-Key: ttr_your_key
```

**Example Response:**
```json
{
  "data": [
    {
      "id": 198634,
      "title": "Samsun'da Sokak Ortasına İHA Düştü: 3 Evde Hasar Oluştu",
      "category": "Gündem",
      "tps_score": 95.81,
      "tps_signal": 82.4,
      "tps_confidence": 0.94,
      "trajectory": "up",
      "article_count": 11,
      "first_seen": "2026-05-16T08:09:58Z",
      "last_updated": "2026-05-16T10:30:00Z",
      "url": "https://trendiatr.com/trend/samsunda-sokak-ortasina-iha-dustu",
      "cover_image": "https://trendiatr.com/static/media/198634_cover.jpg",
      "has_video": false
    }
  ],
  "meta": {
    "total": 42,
    "limit": 10,
    "offset": 0,
    "min_tps_applied": 75.0,
    "plan_threshold": 50.0
  }
}
```

---

### GET /trends/{id}

Full trend detail including summary, tags, entities, and the complete article cluster.

**Authentication:** Required

**Path Parameters:**
- `id` — integer trend ID

**Example Request:**
```http
GET /api/v1/trends/198634
X-API-Key: ttr_your_key
```

**Example Response:**
```json
{
  "data": {
    "id": 198634,
    "title": "Samsun'da Sokak Ortasına İHA Düştü: 3 Evde Hasar Oluştu",
    "category": "Gündem",
    "tps_score": 95.81,
    "tps_signal": 82.4,
    "tps_confidence": 0.94,
    "trajectory": "up",
    "article_count": 11,
    "first_seen": "2026-05-16T08:09:58Z",
    "last_updated": "2026-05-16T10:30:00Z",
    "url": "https://trendiatr.com/trend/samsunda-sokak-ortasina-iha-dustu",
    "cover_image": "https://trendiatr.com/static/media/198634_cover.jpg",
    "has_video": false,
    "summary": "Samsun'un Atakum ilçesinde bir insansız hava aracı (İHA) sokak ortasına düştü...",
    "tags": ["İHA", "Samsun", "Kaza"],
    "entities": ["Samsun Valiliği", "AFAD"],
    "video_url": null,
    "cluster": {
      "article_count": 11,
      "articles": [
        {
          "id": 84521,
          "title": "Samsun'da düşen İHA 3 evde hasar bıraktı",
          "content_preview": "Samsun'un Atakum ilçesinde bir insansız hava aracı...",
          "source_name": "Sabah",
          "source_type": "rss",
          "source_tier": 1,
          "published_at": "2026-05-16T08:05:00Z",
          "media": [
            {
              "type": "image",
              "url": "https://trendiatr.com/static/media/84521.jpg",
              "source_url": "https://sabah.com.tr/img/xyz.jpg",
              "width": 1200,
              "height": 630
            }
          ]
        }
      ]
    }
  }
}
```

Returns `404` if the trend doesn't exist or is below your plan's TPS threshold.

---

### GET /trends/{id}/media

All media assets (images and videos) from a trend's article cluster, including trend-level cover image and video.

**Authentication:** Required

**Example Request:**
```http
GET /api/v1/trends/198634/media
X-API-Key: ttr_your_key
```

**Example Response:**
```json
{
  "trend_id": 198634,
  "trend_title": "Samsun'da Sokak Ortasına İHA Düştü: 3 Evde Hasar Oluştu",
  "media_count": 4,
  "data": [
    {
      "source": "trend_cover",
      "type": "image",
      "url": "https://trendiatr.com/static/media/198634_cover.jpg"
    },
    {
      "source": "article",
      "type": "image",
      "url": "https://trendiatr.com/static/media/84521.jpg",
      "source_url": "https://sabah.com.tr/img/xyz.jpg",
      "width": 1200,
      "height": 630,
      "article_id": 84521,
      "source_name": "Sabah"
    }
  ]
}
```

---

### GET /usage

Current API usage statistics for the authenticated client.

**Authentication:** Required

**Example Request:**
```http
GET /api/v1/usage
X-API-Key: ttr_your_key
```

**Example Response:**
```json
{
  "plan": "pro",
  "tps_threshold": 50.0,
  "monthly_limit": 10000,
  "calls_used": 247,
  "calls_remaining": 9753,
  "resets_at": "2026-06-16T10:00:00Z",
  "last_seen_at": "2026-05-16T11:04:22Z"
}
```

---

## Response Schema

### Trend Object (Summary)

Returned by `GET /trends`.

| Field | Type | Description |
|-------|------|-------------|
| `id` | integer | Unique trend ID |
| `title` | string | AI-generated Turkish headline |
| `category` | string | News category (e.g. Gündem, Spor, Siyaset) |
| `tps_score` | float | Final Trend Power Score (0–100+) |
| `tps_signal` | float | Raw signal component of TPS |
| `tps_confidence` | float | Source credibility multiplier (0.0–1.0) |
| `trajectory` | string | Trend direction: `up`, `down`, or `steady` |
| `article_count` | integer | Number of articles in the cluster |
| `first_seen` | ISO 8601 | When the first article was detected |
| `last_updated` | ISO 8601 | Last cluster update time |
| `url` | string | Canonical page on trendiatr.com |
| `cover_image` | string\|null | Absolute URL of best available image |
| `has_video` | boolean | Whether a video is available |

### Trend Object (Full)

Returned by `GET /trends/{id}`. Includes all Summary fields plus:

| Field | Type | Description |
|-------|------|-------------|
| `summary` | string\|null | AI-generated paragraph summary (Turkish) |
| `tags` | array | Keyword tags extracted by AI |
| `entities` | array | Named entities (people, orgs, places) |
| `video_url` | string\|null | Absolute URL of trend video |
| `cluster` | object | `{ article_count, articles[] }` |

### Article Object

| Field | Type | Description |
|-------|------|-------------|
| `id` | integer | Raw news ID |
| `title` | string\|null | First 120 chars of content |
| `content_preview` | string\|null | First 500 chars of content |
| `source_name` | string | Publication name |
| `source_type` | string | `rss`, `telegram`, or `x` |
| `source_tier` | integer | Credibility tier: 1=official, 2=trusted, 3=unknown |
| `published_at` | ISO 8601 | Original publish time |
| `media` | array | List of Media Objects |

### Media Object

| Field | Type | Description |
|-------|------|-------------|
| `type` | string | `image` or `video` |
| `url` | string | Absolute URL (downloaded local copy when available) |
| `source_url` | string | Original source URL |
| `width` | integer\|null | Image width in pixels (when known) |
| `height` | integer\|null | Image height in pixels (when known) |
| `status` | string | Only present when not yet downloaded: `pending_download` or `processing` |

---

## Error Responses

| HTTP Code | `error` field | When |
|-----------|--------------|------|
| 400 | `invalid_param` | Invalid query parameter (e.g. `limit=abc`) |
| 401 | `unauthorized` | Missing or invalid API key |
| 404 | `not_found` | Trend ID doesn't exist or is below your threshold |
| 429 | `rate_limit_exceeded` | Monthly call limit reached |
| 503 | `error` | Database unreachable |

All errors follow this structure:
```json
{
  "error": "error_code",
  "message": "Human-readable explanation."
}
```

---

## Code Examples

### Python

```python
import requests

API_KEY = "ttr_your_key_here"
BASE_URL = "https://trendiatr.com/api/v1"

headers = {"X-API-Key": API_KEY}

# List top trends
response = requests.get(f"{BASE_URL}/trends", headers=headers, params={
    "min_tps": 60,
    "trajectory": "up",
    "limit": 20
})
data = response.json()

for trend in data["data"]:
    print(f"[{trend['tps_score']}] {trend['title']}")

# Get full detail
trend_id = data["data"][0]["id"]
detail = requests.get(f"{BASE_URL}/trends/{trend_id}", headers=headers).json()
print(detail["data"]["summary"])

# Check usage
usage = requests.get(f"{BASE_URL}/usage", headers=headers).json()
print(f"{usage['calls_used']} / {usage['monthly_limit']} calls used")
```

### JavaScript / Node.js

```javascript
const API_KEY = "ttr_your_key_here";
const BASE_URL = "https://trendiatr.com/api/v1";

const headers = { "X-API-Key": API_KEY };

// List trending topics
const response = await fetch(`${BASE_URL}/trends?min_tps=60&trajectory=up&limit=20`, { headers });
const { data, meta } = await response.json();

console.log(`Found ${meta.total} trends`);
data.forEach(t => console.log(`[${t.tps_score}] ${t.title}`));

// Get all media for a trend
const trendId = data[0].id;
const mediaRes = await fetch(`${BASE_URL}/trends/${trendId}/media`, { headers });
const mediaData = await mediaRes.json();
console.log(`${mediaData.media_count} media items available`);
```

### cURL

```bash
# Health check (no auth)
curl https://trendiatr.com/api/v1/health

# List trends (Bearer token)
curl -H "Authorization: Bearer ttr_your_key" \
  "https://trendiatr.com/api/v1/trends?min_tps=70&limit=10"

# List trends filtered by category
curl -H "X-API-Key: ttr_your_key" \
  "https://trendiatr.com/api/v1/trends?category=Spor&trajectory=up"

# Get full trend detail
curl -H "X-API-Key: ttr_your_key" \
  "https://trendiatr.com/api/v1/trends/198634"

# Get media assets
curl -H "X-API-Key: ttr_your_key" \
  "https://trendiatr.com/api/v1/trends/198634/media"

# Check usage
curl -H "X-API-Key: ttr_your_key" \
  "https://trendiatr.com/api/v1/usage"

# Paginate results
curl -H "X-API-Key: ttr_your_key" \
  "https://trendiatr.com/api/v1/trends?limit=20&offset=40"
```

---

## Admin API (Internal)

These endpoints are for internal use only — no client-facing auth required. Restrict access via firewall/VPN.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/admin/b2b/clients` | List all API clients |
| `POST` | `/api/admin/b2b/clients` | Create a new client |
| `PATCH` | `/api/admin/b2b/clients/{id}` | Update client (plan, threshold, active status) |
| `POST` | `/api/admin/b2b/clients/{id}/reset-key` | Rotate API key |

**Create client example:**
```bash
curl -X POST https://trendiatr.com/api/admin/b2b/clients \
  -H "Content-Type: application/json" \
  -d '{"name": "Hürriyet Digital", "email": "api@hurriyet.com.tr", "plan": "pro"}'
```

Response includes `api_key` — shown **once only**, store it securely.

---

## Webhook (Coming in Phase 2)

Phase 2 will add webhook support: push new high-TPS trends to your endpoint in real time as they are detected, eliminating the need to poll `/trends`. Contact support to join the beta.
