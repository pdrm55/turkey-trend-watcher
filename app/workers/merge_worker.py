import sys
import os
import time
import json
import logging
from datetime import datetime, timezone, timedelta

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from app.database.models import SessionLocal, Trend, RawNews, TrendArrivals, TrendScoreHistory
from app.core.ai_engine import ai_engine
from app.config import Config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("MergeWorker")

# --- Configuration ---
MERGE_INTERVAL_SECONDS = 3600      # Run full cycle every 1 hour
# 0.12 is ai_engine's *lowest* auto-merge threshold, not its usual one: it merges
# under 0.15 normally but drops to 0.12 when the reference document is older than
# 24h (and for X-Trend). This used to be 0.16, one comment-sized assumption above
# the real floor, which left 0.12-0.16 as a dead band that neither side inspected.
# CLUSTER_STATS measured 10% of new trends landing there, and the one genuinely
# duplicated pair found by hand — the Ahbap investigation, trends 281471 and
# 281402 — sat at 0.1556. Gemini still verifies every pair, so widening the
# search cannot merge anything on distance alone.
SEARCH_DISTANCE_MIN = 0.12         # ai_engine's floor; below this it merges at ingest
SEARCH_DISTANCE_MAX = 0.40         # Above this, clusters are semantically different
MAX_GEMINI_CALLS_PER_CYCLE = 60    # Rate-limit guard for Gemini API
MAX_TIME_DIFF_HOURS = 72           # Don't merge events more than 3 days apart
GEMINI_VERIFY_MODEL = "gemini-2.5-flash-lite"

# --- Smart Pre-filter ---
# Pairs with distance > this threshold also require title keyword overlap to reach Gemini
# Set equal to SEARCH_DISTANCE_MIN so keyword filter applies to ALL candidate pairs
SMART_FILTER_DISTANCE_THRESHOLD = 0.12
# Tighter time window for distant pairs (distance > threshold)
SMART_FILTER_MAX_TIME_HOURS = 24.0

_TURKISH_STOPWORDS = {
    've', 'ile', 'için', 'bir', 'bu', 'da', 'de', 'ki', 'ne', 'ya',
    'mi', 'mu', 'mü', 'mı', 'o', 'şu', 'ise', 'var', 'yok', 'olan',
    'oldu', 'olacak', 'etti', 'edildi', 'olarak', 'kadar', 'sonra',
    'önce', 'gibi', 'üzere', 'ama', 'fakat', 'ancak', 'veya', 'hem',
    'daha', 'çok', 'en', 'her', 'hiç', 'bazı', 'tüm', 'bütün',
}

# --- Gemini Client (lightweight, for verification only) ---
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
_gemini_client = None

def _get_gemini_client():
    global _gemini_client
    if _gemini_client is None and GOOGLE_API_KEY:
        from google import genai
        _gemini_client = genai.Client(api_key=GOOGLE_API_KEY)
    return _gemini_client


def _title_keyword_overlap(title_a: str, title_b: str) -> int:
    """Count shared meaningful tokens between two cluster titles."""
    def tokens(t: str) -> set:
        return {
            w.lower().strip("':,.()")
            for w in (t or '').split()
            if len(w) > 3 and w.lower() not in _TURKISH_STOPWORDS
        }
    return len(tokens(title_a) & tokens(title_b))


def _categories_compatible(cat_a: str, cat_b: str) -> bool:
    """Return False only for clearly incompatible category pairs (e.g. Spor vs Siyaset)."""
    if not cat_a or not cat_b or cat_a == cat_b:
        return True
    # Gündem is a catch-all — always compatible
    if 'Gündem' in (cat_a, cat_b):
        return True
    # Spor clusters must not merge with non-sport clusters
    if 'Spor' in (cat_a, cat_b):
        return False
    return True


def verify_same_event(text_a: str, text_b: str) -> bool:
    """
    Determines if two news snippets describe the same real-world event.
    Uses Gemini as primary verifier; falls back to local Qwen if unavailable.
    """
    client = _get_gemini_client()

    if client:
        from google.genai import types
        prompt = f"""You are a strict news deduplication engine for a Turkish news platform.

Compare the two Turkish news snippets below and decide if they describe THE EXACT SAME specific real-world event.

Rules:
- Same general topic (e.g. economy, football) is NOT enough.
- Same location + same specific incident (e.g. same earthquake, same match, same decree) = SAME EVENT.
- Day 1 report vs Day 2 reaction/update about the same incident = SAME EVENT (umbrella story).
- Completely different incidents even if related = DIFFERENT.

Snippet A: "{text_a[:600]}"
Snippet B: "{text_b[:600]}"

Return JSON only: {{"same_event": true}} or {{"same_event": false}}"""

        try:
            response = client.models.generate_content(
                model=GEMINI_VERIFY_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type='application/json',
                    temperature=0.0,
                    max_output_tokens=200,
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                )
            )
            raw = response.text.strip().strip("` \n")
            if raw.startswith("json"):
                raw = raw[4:].strip()
            result = json.loads(raw)
            return bool(result.get("same_event", False))
        except Exception as e:
            logger.warning(f"Gemini verification failed — skipping merge (no Ollama fallback): {e}")
            return False  # Skip merge; don't hammer Ollama as fallback


def _get_ref_doc(trend: Trend, db) -> str | None:
    """Fetches the best reference document for a trend (ChromaDB first, DB fallback)."""
    ref_doc, _ = ai_engine.get_cluster_reference_doc(trend.cluster_id)
    if ref_doc:
        return ref_doc

    # Fallback: pick longest recent news from DB
    news_items = (
        db.query(RawNews)
        .filter(RawNews.trend_id == trend.id)
        .order_by(RawNews.published_at.desc())
        .limit(3)
        .all()
    )
    if not news_items:
        return None
    return max(news_items, key=lambda n: len(n.content or "")).content


def _do_merge(source: Trend, target: Trend, db) -> bool:
    """
    Merges source cluster into target cluster in both ChromaDB and PostgreSQL.
    Source is deactivated; target absorbs all news items, arrivals, and message count.
    Returns True on success.
    """
    # 1. Move all ChromaDB vectors. ChromaDB is not in the Postgres transaction,
    #    so this half can succeed while the other half fails. Bail out before
    #    touching Postgres if the move did not land, and keep what was moved so
    #    it can be put back if the commit below fails.
    moved_vectors = ai_engine.merge_clusters(source.cluster_id, target.cluster_id)
    if moved_vectors is None:
        logger.error(
            f"❌ Merge aborted ({source.id} → {target.id}): ChromaDB move failed, "
            f"Postgres left untouched"
        )
        return False

    try:
        # 2. Reassign RawNews rows
        db.query(RawNews).filter(RawNews.trend_id == source.id).update(
            {"trend_id": target.id}, synchronize_session=False
        )

        # 3. Reassign TrendArrivals rows
        db.query(TrendArrivals).filter(TrendArrivals.trend_id == source.id).update(
            {"trend_id": target.id}, synchronize_session=False
        )

        # 4. Update target counters and flags
        target.message_count += source.message_count
        target.final_tps = max(target.final_tps, source.final_tps)
        target.score = target.final_tps
        target.needs_scoring = True
        target.last_updated = datetime.now(timezone.utc).replace(tzinfo=None)

        # 5. Deactivate source
        source.is_active = False

        db.commit()
        logger.info(
            f"🔀 Merged [{source.id}] '{(source.title or '')[:40]}' "
            f"→ [{target.id}] '{(target.title or '')[:40]}'"
        )
        return True

    except Exception as e:
        db.rollback()
        # A Postgres rollback does not unmove the ChromaDB vectors. Without this
        # compensation the source trend stays active with its rows intact but no
        # vectors of its own: invisible to every later merge cycle, and new
        # matching news clusters to the target instead. Put them back.
        if not ai_engine.restore_vector_metadata(moved_vectors):
            logger.error(
                f"🚨 SPLIT BRAIN ({source.id} → {target.id}): Postgres rolled back but "
                f"{len(moved_vectors)} ChromaDB vectors are still on the target cluster"
            )
        logger.error(f"❌ Merge DB error ({source.id} → {target.id}): {e}")
        return False


def run_merge_cycle() -> int:
    """
    Main retroactive merge cycle — two-phase with smart pre-filter.

    Phase 1 (no Gemini): scan all active trends via ChromaDB, collect every
    candidate pair with its cosine distance. No API calls, just embeddings.

    Phase 2 (Gemini): sort pairs by distance ASC so the most similar pairs are
    verified first. Apply two cheap pre-filters before each Gemini call:
      - Category compatibility (Spor clusters never merge with non-Spor)
      - Keyword overlap guard: if distance > SMART_FILTER_DISTANCE_THRESHOLD and
        the two titles share zero meaningful words, skip Gemini entirely.

    This ensures the 40-call budget is spent on the strongest merge candidates
    across all clusters, not just the first few high-TPS trends.
    """
    db = SessionLocal()
    merged_count = 0
    gemini_calls = 0

    try:
        active_trends = (
            db.query(Trend)
            .filter(Trend.is_active == True, Trend.cluster_id.isnot(None))
            .order_by(Trend.final_tps.desc())
            .all()
        )

        if len(active_trends) < 2:
            return 0

        logger.info(f"🔍 [MergeWorker] Scanning {len(active_trends)} active trends...")

        # ── Phase 1: collect all candidate pairs (no Gemini) ──────────────────
        checked_pairs: set[tuple] = set()
        # (distance, trend, candidate_trend, ref_doc_of_trend)
        candidate_pairs: list = []

        for trend in active_trends:
            if not trend.is_active:
                continue

            ref_doc = _get_ref_doc(trend, db)
            if not ref_doc:
                continue

            try:
                query_vector = ai_engine.get_embedding(ref_doc, is_query=True)
                results = ai_engine.collection.query(
                    query_embeddings=[query_vector],
                    n_results=20,
                    include=["metadatas", "distances"]
                )
            except Exception as e:
                logger.error(f"ChromaDB query error for trend {trend.id}: {e}")
                continue

            if not results['distances'] or not results['distances'][0]:
                continue

            for i, distance in enumerate(results['distances'][0]):
                if distance < SEARCH_DISTANCE_MIN or distance > SEARCH_DISTANCE_MAX:
                    continue

                meta = results['metadatas'][0][i]
                candidate_cluster_id = meta.get('cluster_id')
                if not candidate_cluster_id or candidate_cluster_id == trend.cluster_id:
                    continue

                pair_key = tuple(sorted([trend.cluster_id, candidate_cluster_id]))
                if pair_key in checked_pairs:
                    continue
                checked_pairs.add(pair_key)

                candidate_trend = (
                    db.query(Trend)
                    .filter(Trend.cluster_id == candidate_cluster_id, Trend.is_active == True)
                    .first()
                )
                if not candidate_trend:
                    continue

                time_diff_hours = abs(
                    (trend.first_seen - candidate_trend.first_seen).total_seconds() / 3600.0
                )
                # Tighter time window for semantically distant pairs
                max_hours = MAX_TIME_DIFF_HOURS if distance < SMART_FILTER_DISTANCE_THRESHOLD else SMART_FILTER_MAX_TIME_HOURS
                if time_diff_hours > max_hours:
                    continue

                candidate_pairs.append((distance, trend, candidate_trend, ref_doc))

        logger.info(f"📋 [MergeWorker] {len(candidate_pairs)} candidate pairs collected before pre-filter")

        # ── Phase 2: sort by distance, pre-filter, verify with Gemini ─────────
        candidate_pairs.sort(key=lambda x: x[0])

        skipped_category = 0
        skipped_keyword = 0

        for distance, trend, candidate_trend, ref_doc in candidate_pairs:
            # Skip if either side was merged away earlier in this cycle
            if not trend.is_active or not candidate_trend.is_active:
                continue

            # Pre-filter 1: incompatible categories (free — no API call)
            if not _categories_compatible(trend.category, candidate_trend.category):
                skipped_category += 1
                continue

            # Pre-filter 2: for distant pairs, require at least one shared keyword
            if distance > SMART_FILTER_DISTANCE_THRESHOLD:
                if _title_keyword_overlap(trend.title, candidate_trend.title) == 0:
                    skipped_keyword += 1
                    continue

            # Rate-limit guard
            if gemini_calls >= MAX_GEMINI_CALLS_PER_CYCLE:
                logger.info("⚠️ [MergeWorker] Gemini call limit reached, stopping this cycle.")
                break

            candidate_doc = _get_ref_doc(candidate_trend, db)
            if not candidate_doc:
                continue

            gemini_calls += 1
            time.sleep(0.5)

            if not verify_same_event(ref_doc[:600], candidate_doc[:600]):
                continue

            if trend.message_count >= candidate_trend.message_count:
                target, source = trend, candidate_trend
            else:
                target, source = candidate_trend, trend

            if _do_merge(source, target, db):
                merged_count += 1

        logger.info(
            f"📊 [MergeWorker] Pre-filter saved: {skipped_category} category + "
            f"{skipped_keyword} keyword mismatches. "
            f"Gemini calls: {gemini_calls}/{MAX_GEMINI_CALLS_PER_CYCLE}"
        )
        return merged_count

    except Exception as e:
        logger.error(f"❌ [MergeWorker] Cycle error: {e}")
        return merged_count
    finally:
        db.close()


def main():
    logger.info("🔀 TrendiaTR Retroactive Merge Worker started.")
    logger.info(
        f"⚙️  Config: interval={MERGE_INTERVAL_SECONDS}s, "
        f"distance=[{SEARCH_DISTANCE_MIN}, {SEARCH_DISTANCE_MAX}], "
        f"max_gemini_calls={MAX_GEMINI_CALLS_PER_CYCLE}, "
        f"max_time_diff={MAX_TIME_DIFF_HOURS}h"
    )

    while True:
        try:
            start = time.time()
            merges = run_merge_cycle()
            elapsed = time.time() - start
            logger.info(f"✅ [MergeWorker] Cycle done in {elapsed:.1f}s — {merges} merges performed.")
        except KeyboardInterrupt:
            logger.info("🛑 MergeWorker stopped manually.")
            break
        except Exception as e:
            logger.error(f"❌ [MergeWorker] Unexpected error: {e}")

        logger.info(f"💤 [MergeWorker] Next cycle in {MERGE_INTERVAL_SECONDS // 60} minutes.")
        time.sleep(MERGE_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
