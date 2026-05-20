"""
Multi-Source Validator — Phase 2
Enriches X-trend validation by searching Telegram and RSS signals
already stored in raw_news before the LLM cross-validation step.

Flow:
  fetch_google_context()
    → MultiSourceValidator.validate()   ← this module
    → ai_engine.verify_cross_trend()    ← existing Qwen LLM check
    → process_news()
"""

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import List

from sqlalchemy import or_
from sqlalchemy.orm import Session

logger = logging.getLogger("MultiSourceValidator")

# ─── RSS metadata file path ────────────────────────────────────────────────
_RSS_FILE = os.path.join(
    os.path.dirname(__file__), '..', 'collectors', 'rss_sources.txt'
)

# ─── Source weight table ──────────────────────────────────────────────────
# Higher weight = more authoritative signal for validation scoring
SOURCE_WEIGHTS: dict = {
    "telegram_wire":     10,  # Official agency Telegram (AA, IHA, DHA)
    "rss_disaster":       9,  # RSS with category='afet'
    "telegram_intl":      8,  # International Turkish media (Al Jazeera, Sputnik)
    "rss_tv":             7,  # TV/agency RSS (TRT, NTV, CNN Türk, AA, BBC)
    "rss_major":          5,  # Major newspaper RSS (Hürriyet, Milliyet, etc.)
    "telegram_major":     4,  # Major Turkish news Telegram channels
    "rss_standard":       3,  # Standard RSS (tier 3)
    "telegram_analysis":  2,  # Opinion/analysis Telegram channels
    "telegram_minor":     1,  # Smaller / unknown Telegram channels
}

# ─── Telegram channel type mapping ────────────────────────────────────────
# source_name (channel username as stored in raw_news.source_name) → type key
TELEGRAM_CHANNEL_TYPES: dict = {
    # Official wire/agencies
    "anadoluajansi":      "wire",
    "ihaturkiye":         "wire",
    "dhaturkiye":         "wire",
    "trthaberdijital":    "tv",
    # International Turkish media
    "sputnik_tr":         "intl",
    "aljazeera_tr":       "intl",
    # TV channels / major media
    "cnnturkhaber":       "tv",
    "tele1haberler":      "major",
    "haberler":           "major",
    "sabahgazete":        "major",
    "cumhuriyet":         "major",
    "solcugazete":        "major",
    "bianet_org":         "major",
    # Economics
    "bloomberghttv":      "major",
    "ekonomimcom":        "standard",
    "borsagundemtr":      "standard",
    # Analysis / commentary
    "conflict_tr":        "analysis",
    "ibrahimhaskologlu":  "analysis",
    "aliosmanonder":      "analysis",
    "gundemedairhs":      "analysis",
    "pusholder":          "analysis",
    # Sports
    "beinsportsturkiye":  "standard",
    "galatasaray":        "standard",
    "fenerbahcekulubu":   "standard",
    "trabzonsporsevgisi": "standard",
    # Tech
    "technopatnet":       "standard",
    "webtekno":           "standard",
    # Earthquake/disaster
    "depremturkey":       "wire",
    "kandillisondepremler": "wire",
    # Minor
    "buzznews_tr":        "minor",
    "bpthaber":           "minor",
    "habermha":           "minor",
}

# ─── RSS source metadata (loaded once at module import) ───────────────────
# Maps source_name → (tier, category, speed)
_RSS_SOURCE_METADATA: dict = {}


def load_rss_source_metadata() -> dict:
    """
    Reads rss_sources.txt and returns {source_name: (tier, category, speed)}.
    Called once at module load; result stored in _RSS_SOURCE_METADATA.
    """
    metadata = {}
    if not os.path.exists(_RSS_FILE):
        logger.warning(f"RSS source file not found at {_RSS_FILE}")
        return metadata
    try:
        with open(_RSS_FILE, 'r', encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = [p.strip() for p in line.split(',')]
                if len(parts) >= 5:
                    name, _, tier, category, speed = (
                        parts[0], parts[1], parts[2], parts[3], parts[4]
                    )
                    try:
                        metadata[name] = (int(tier), category, speed)
                    except ValueError:
                        metadata[name] = (3, 'gundem', '3')
                elif len(parts) >= 2:
                    metadata[parts[0]] = (3, 'gundem', '3')
    except Exception as exc:
        logger.warning(f"Could not load RSS source metadata: {exc}")
    return metadata


# Load at import time so every call to validate() is fast
_RSS_SOURCE_METADATA = load_rss_source_metadata()


# ─── Dataclasses ─────────────────────────────────────────────────────────

@dataclass
class ValidationSignal:
    source_type: str       # 'telegram' or 'rss'
    source_name: str       # channel username or RSS source name
    source_weight: int
    matched_content: str   # short snippet of matching content
    matched_at: datetime


@dataclass
class ValidationResult:
    signals: List[ValidationSignal] = field(default_factory=list)
    total_score: int = 0
    platform_count: int = 0  # distinct platforms (telegram, rss)

    def calculate(self) -> "ValidationResult":
        seen_platforms: set = set()
        score = 0
        for sig in self.signals:
            score += sig.source_weight
            seen_platforms.add(sig.source_type)
        self.total_score = score
        self.platform_count = len(seen_platforms)
        return self


# ─── Validator class ──────────────────────────────────────────────────────

class MultiSourceValidator:
    """
    Searches recently stored Telegram and RSS news (raw_news table)
    for content matching an X-trend keyword and returns a ValidationResult
    with a weighted confidence score.

    No external HTTP requests are made — all signals come from the DB.
    """

    def _get_rss_source_type(self, source_name: str) -> str:
        """Map RSS source_name to a SOURCE_WEIGHTS key."""
        meta = _RSS_SOURCE_METADATA.get(source_name)
        if not meta:
            return "rss_standard"
        tier, category, _ = meta
        if category == 'afet':
            return "rss_disaster"
        if tier == 1:
            return "rss_tv"
        if tier == 2:
            return "rss_major"
        return "rss_standard"

    def _get_telegram_source_type(self, source_name: str) -> str:
        """Map Telegram channel username to a SOURCE_WEIGHTS key."""
        ch_type = TELEGRAM_CHANNEL_TYPES.get(source_name.lower())
        if ch_type == "wire":
            return "telegram_wire"
        if ch_type == "intl":
            return "telegram_intl"
        if ch_type == "tv":
            return "telegram_wire"   # TV agencies treated as wire
        if ch_type == "major":
            return "telegram_major"
        if ch_type == "analysis":
            return "telegram_analysis"
        if ch_type == "minor":
            return "telegram_minor"
        return "telegram_minor"

    @staticmethod
    def _build_search_words(keyword: str) -> list:
        """
        Extract searchable words from an X-trend name.
        Strips # prefix and keeps words longer than 3 characters (cap 3 words).
        Falls back to the full cleaned keyword if no long words found.
        """
        clean = keyword.lstrip('#').strip()
        words = [w for w in clean.split() if len(w) > 3]
        if not words:
            words = [clean] if len(clean) > 2 else []
        return words[:3]

    def _search_telegram(
        self,
        keyword: str,
        db: Session,
        lookback_minutes: int,
    ) -> List[ValidationSignal]:
        """
        Query raw_news for Telegram messages mentioning the keyword
        within the lookback window.  Returns at most one signal per channel.
        """
        from app.database.models import RawNews

        signals: List[ValidationSignal] = []
        words = self._build_search_words(keyword)
        if not words:
            return signals

        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            minutes=lookback_minutes
        )

        try:
            conditions = or_(*[RawNews.content.ilike(f'%{w}%') for w in words])
            rows = (
                db.query(RawNews)
                .filter(
                    RawNews.source_type == 'telegram',
                    RawNews.created_at >= cutoff,
                    conditions,
                )
                .order_by(RawNews.created_at.desc())
                .limit(50)
                .all()
            )
        except Exception as exc:
            logger.error(f"Telegram search error for '{keyword}': {exc}")
            return signals

        seen_sources: set = set()
        for row in rows:
            if row.source_name in seen_sources:
                continue
            seen_sources.add(row.source_name)
            weight_key = self._get_telegram_source_type(row.source_name or '')
            signals.append(
                ValidationSignal(
                    source_type='telegram',
                    source_name=row.source_name or 'unknown',
                    source_weight=SOURCE_WEIGHTS[weight_key],
                    matched_content=(row.content or '')[:120],
                    matched_at=row.created_at or cutoff,
                )
            )

        return signals

    def _search_rss_db(
        self,
        keyword: str,
        db: Session,
        lookback_minutes: int,
    ) -> List[ValidationSignal]:
        """
        Query raw_news for RSS articles mentioning the keyword
        within the lookback window.  Returns at most one signal per source.
        """
        from app.database.models import RawNews

        signals: List[ValidationSignal] = []
        words = self._build_search_words(keyword)
        if not words:
            return signals

        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            minutes=lookback_minutes
        )

        try:
            conditions = or_(*[RawNews.content.ilike(f'%{w}%') for w in words])
            rows = (
                db.query(RawNews)
                .filter(
                    RawNews.source_type == 'rss',
                    RawNews.created_at >= cutoff,
                    conditions,
                )
                .order_by(RawNews.created_at.desc())
                .limit(50)
                .all()
            )
        except Exception as exc:
            logger.error(f"RSS search error for '{keyword}': {exc}")
            return signals

        seen_sources: set = set()
        for row in rows:
            if row.source_name in seen_sources:
                continue
            seen_sources.add(row.source_name)
            weight_key = self._get_rss_source_type(row.source_name or '')
            signals.append(
                ValidationSignal(
                    source_type='rss',
                    source_name=row.source_name or 'unknown',
                    source_weight=SOURCE_WEIGHTS[weight_key],
                    matched_content=(row.content or '')[:120],
                    matched_at=row.created_at or cutoff,
                )
            )

        return signals

    def validate(
        self,
        keyword: str,
        db: Session,
        lookback_minutes: int = 30,
    ) -> ValidationResult:
        """
        Search Telegram and RSS DB for signals matching keyword.
        Returns a ValidationResult with total_score and platform_count.
        """
        telegram_signals = self._search_telegram(keyword, db, lookback_minutes)
        rss_signals = self._search_rss_db(keyword, db, lookback_minutes)

        result = ValidationResult(signals=telegram_signals + rss_signals)
        result.calculate()

        logger.info(
            f"[MultiSourceValidator] '{keyword}' → "
            f"score={result.total_score}, platforms={result.platform_count}, "
            f"signals={len(result.signals)} "
            f"(tg={len(telegram_signals)}, rss={len(rss_signals)})"
        )
        return result


# Singleton used across workers
multi_source_validator = MultiSourceValidator()
