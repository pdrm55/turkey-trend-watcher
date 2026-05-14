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
MERGE_INTERVAL_SECONDS = 7200      # Run full cycle every 2 hours
SEARCH_DISTANCE_MIN = 0.23         # Below this, ai_engine already merges at ingest time
SEARCH_DISTANCE_MAX = 0.50         # Above this, clusters are semantically different
MAX_GEMINI_CALLS_PER_CYCLE = 40    # Rate-limit guard for Gemini API
MAX_TIME_DIFF_HOURS = 72           # Don't merge events more than 3 days apart
GEMINI_VERIFY_MODEL = "gemini-2.0-flash-lite"

# --- Gemini Client (lightweight, for verification only) ---
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
_gemini_client = None

def _get_gemini_client():
    global _gemini_client
    if _gemini_client is None and GOOGLE_API_KEY:
        from google import genai
        _gemini_client = genai.Client(api_key=GOOGLE_API_KEY)
    return _gemini_client


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
                    max_output_tokens=20,
                )
            )
            result = json.loads(response.text.strip())
            return bool(result.get("same_event", False))
        except Exception as e:
            logger.warning(f"Gemini verification failed, falling back to local LLM: {e}")

    # Fallback: local Qwen via ai_engine
    return ai_engine.ask_local_llm(text_a, text_b)


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
    try:
        # 1. Move all ChromaDB vectors
        ai_engine.merge_clusters(source.cluster_id, target.cluster_id)

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
        logger.error(f"❌ Merge DB error ({source.id} → {target.id}): {e}")
        return False


def run_merge_cycle() -> int:
    """
    Main retroactive merge cycle.
    Scans active trends, finds semantically similar cluster pairs via ChromaDB,
    verifies with Gemini, and merges confirmed duplicates.
    Returns the number of merges performed.
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

        checked_pairs: set[tuple] = set()

        for trend in active_trends:
            # A trend that was merged away in this cycle is now inactive — skip it
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

                # Time-window guard: don't merge events far apart in time
                time_diff_hours = abs(
                    (trend.first_seen - candidate_trend.first_seen).total_seconds() / 3600.0
                )
                if time_diff_hours > MAX_TIME_DIFF_HOURS:
                    continue

                # Rate-limit guard
                if gemini_calls >= MAX_GEMINI_CALLS_PER_CYCLE:
                    logger.info("⚠️ [MergeWorker] Gemini call limit reached, stopping this cycle.")
                    return merged_count

                candidate_doc = _get_ref_doc(candidate_trend, db)
                if not candidate_doc:
                    continue

                gemini_calls += 1
                time.sleep(0.5)  # polite rate limiting

                if not verify_same_event(ref_doc[:600], candidate_doc[:600]):
                    continue

                # Merge smaller into larger (by message count)
                if trend.message_count >= candidate_trend.message_count:
                    target, source = trend, candidate_trend
                else:
                    target, source = candidate_trend, trend

                if _do_merge(source, target, db):
                    merged_count += 1
                    # If this trend was the source, it's now inactive — stop inner loop
                    if source is trend:
                        break

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
