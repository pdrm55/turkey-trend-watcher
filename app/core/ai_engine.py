import os
import sys
import logging
import faulthandler
import uuid
import shutil
import requests
import json
import hashlib
import threading
import time
from datetime import datetime, timedelta

# 1. Enable system error tracking (for debugging SegFaults in Docker)
faulthandler.enable()

# 2. Vital settings for single-threading (prevents memory conflicts in ML models)
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

# 3. Kill Posthog module before it loads (Telemetry blocking)
from unittest.mock import MagicMock
sys.modules["posthog"] = MagicMock()

# Logger configuration
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 4. Heavy ML Imports (After environment settings)
import torch
torch.set_num_threads(1) 

import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer
from app.config import Config

# --- Connection Settings ---
CHROMA_HOST = os.getenv("CHROMA_HOST", "ttw_chroma")
CHROMA_PORT = os.getenv("CHROMA_PORT", "8000")
OLLAMA_API_URL = os.getenv("OLLAMA_API_URL", "http://ttw_ollama:11434/api/generate")
LOCAL_MODEL_NAME = "qwen2.5:1.5b"

# Budgets for moderation called from a Flask request. Kept small deliberately:
# a comment POST must not hold one of four gunicorn workers while it queues
# behind the ingestion pipeline for the shared Ollama lock. Anything that does
# not fit in this budget becomes "pending" and is re-moderated by the worker.
MODERATION_REQUEST_TIMEOUT = 3
MODERATION_REQUEST_LOCK_WAIT = 1
# What the background sweep uses: it can afford to wait.
MODERATION_SWEEP_TIMEOUT = 15
MODERATION_SWEEP_LOCK_WAIT = 20

class AIEngine:
    # Shared circuit breaker + within-process mutex (per-container guard)
    _ollama_lock = threading.Semaphore(1)        # Only 1 Ollama request at a time within this process
    _cb_failures = 0                             # Consecutive failure counter
    _cb_open_until = 0.0                         # Epoch timestamp: circuit open until
    _CB_THRESHOLD = 3                            # Failures before circuit opens
    _CB_COOLDOWN = 120                           # Seconds to keep circuit open (doubled: 60→120)

    # Redis-based global Ollama lock — serializes calls across all containers
    _REDIS_LOCK_KEY = "ttw:ollama:global_lock"
    _REDIS_LOCK_TTL = 35                         # seconds: max HTTP timeout (25s) + overhead
    _REDIS_LOCK_ACQUIRE_TIMEOUT = 20             # seconds to wait before giving up

    # Lua script: atomically releases the lock only if we own it
    _LUA_RELEASE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""

    def __init__(self):
        """Initialize AI Engine and connect to Vector Database"""
        # Lazy load the model to save RAM on workers that don't need embedding
        self.model = None
        # Redis client: None = not yet initialised, False = unavailable
        self._redis = None

        try:
            self.chroma_client = chromadb.HttpClient(
                host=CHROMA_HOST,
                port=int(CHROMA_PORT),
                settings=Settings(anonymized_telemetry=False, allow_reset=True)
            )
            # v2: new collection for multilingual-e5-large (1024-dim, incompatible with old 384-dim)
            self.collection = self.chroma_client.get_or_create_collection(
                name="news_clusters_v2",
                metadata={"hnsw:space": "cosine"}
            )
            print(f"✅ AI Engine Phase 3 Ready. Rolling Cache: Numeric Timestamps.", flush=True)
        except Exception as e:
            print(f"❌ ChromaDB Connection Error: {e}")

        self.fast_dedup_ttl = max(10, getattr(Config, "AI_FAST_DEDUP_TTL_SECONDS", 180))
        self.fast_dedup_cache = {}

        # Eagerly connect to Redis so the global lock is ready before the first Ollama call.
        # _get_redis() is safe to call here: it catches all exceptions internally.
        self._get_redis()

    def _get_redis(self):
        """Return a connected Redis client, or None if unavailable (graceful degradation)."""
        if self._redis is not None:
            return self._redis if self._redis is not False else None
        try:
            import redis as _redis_lib
            client = _redis_lib.Redis.from_url(
                Config.REDIS_URL,
                socket_connect_timeout=2,
                socket_timeout=2,
                decode_responses=True,
            )
            client.ping()
            self._redis = client
            logger.info("✅ Ollama Redis global lock connected.")
        except Exception as exc:
            logger.warning(f"⚠️ Redis unavailable — Ollama will use local semaphore only: {exc}")
            self._redis = False
        return self._redis if self._redis is not False else None

    def _acquire_redis_lock(self, lock_value: str, acquire_timeout: float) -> bool:
        """
        Spin-acquire the global Ollama Redis lock.
        Returns True if acquired, False if timed out.
        Falls back to True (allow call) when Redis is unavailable.
        """
        r = self._get_redis()
        if r is None:
            return True  # graceful degradation: no Redis → proceed anyway
        deadline = time.monotonic() + acquire_timeout
        while True:
            if r.set(self._REDIS_LOCK_KEY, lock_value, nx=True, ex=self._REDIS_LOCK_TTL):
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(0.3, remaining))

    def _release_redis_lock(self, lock_value: str) -> None:
        """Atomically release the Redis lock only if we own it."""
        r = self._get_redis()
        if r is None:
            return
        try:
            r.eval(self._LUA_RELEASE, 1, self._REDIS_LOCK_KEY, lock_value)
        except Exception as exc:
            logger.warning(f"Redis lock release error (TTL will expire): {exc}")

    def get_embedding(self, text: str, is_query: bool = False):
        """Convert text to numerical vector using multilingual-e5-large.
        Prefix strategy: 'query: ' for search lookups, 'passage: ' for indexed documents.
        """
        try:
            if self.model is None:
                print("🧠 Loading multilingual-e5-large (Lazy Load)...", flush=True)
                self.model = SentenceTransformer('intfloat/multilingual-e5-large', device='cpu')

            if not isinstance(text, str):
                text = str(text)
            prefix = "query: " if is_query else "passage: "
            vector = self.model.encode(prefix + text, convert_to_numpy=True).tolist()
            return vector
        except Exception as e:
            logger.error(f"Embedding Error: {e}")
            raise e

    def _ollama_post(self, payload: dict, timeout: int, lock_wait: int = None) -> dict:
        """Send a single request to Ollama with two-level locking + circuit-breaker.

        Level 1 — local semaphore: prevents two threads within the same container
                  from calling Ollama simultaneously (non-blocking: skips immediately).
        Level 2 — Redis global lock: serializes calls across all containers so that
                  ttw_rss, ttw_social, and ttw_merge never hammer Ollama concurrently.
        Circuit breaker: after _CB_THRESHOLD consecutive failures the circuit opens for
                  _CB_COOLDOWN seconds; all callers get {} until it resets.
        Graceful degradation: if Redis is unavailable the call proceeds with local guard only.

        Return value distinguishes two very different outcomes, because callers were
        treating them alike and inventing data from the second one:
          None — the call was never attempted (circuit open, or another caller holds
                 a lock). Nothing is known about the input.
          {}   — the call was attempted and failed.
        Both are falsy, so `if not result` keeps working for callers that do not care.
        """
        now = time.monotonic()
        if now < AIEngine._cb_open_until:
            logger.warning(
                f"Ollama circuit OPEN — skipping call "
                f"(resets in {AIEngine._cb_open_until - now:.0f}s)"
            )
            return None

        # Level 1: local within-process guard (non-blocking — skip if already in use)
        acquired_local = AIEngine._ollama_lock.acquire(blocking=False)
        if not acquired_local:
            return None

        lock_value = str(uuid.uuid4())
        acquired_redis = False
        try:
            # Level 2: Redis cross-container lock.
            # lock_wait lets a caller in the request path give up quickly instead
            # of queueing behind the workers: a Flask request cannot afford to
            # hold a gunicorn worker for the 20s a background job happily waits.
            budget = timeout if lock_wait is None else lock_wait
            acquire_timeout = min(budget, self._REDIS_LOCK_ACQUIRE_TIMEOUT)
            acquired_redis = self._acquire_redis_lock(lock_value, acquire_timeout)
            if not acquired_redis:
                logger.warning("Ollama Redis global lock timed out — skipping call")
                return None

            response = requests.post(OLLAMA_API_URL, json=payload, timeout=timeout)
            response.raise_for_status()
            AIEngine._cb_failures = 0
            return response.json()
        except Exception as e:
            AIEngine._cb_failures += 1
            if AIEngine._cb_failures >= AIEngine._CB_THRESHOLD:
                AIEngine._cb_open_until = time.monotonic() + AIEngine._CB_COOLDOWN
                logger.error(
                    f"Ollama circuit OPENED after {AIEngine._cb_failures} failures "
                    f"— pausing LLM calls for {AIEngine._CB_COOLDOWN}s. Last error: {e}"
                )
            else:
                logger.error(f"Local LLM Verification Failed: {e}")
            return {}
        finally:
            if acquired_redis:
                self._release_redis_lock(lock_value)
            AIEngine._ollama_lock.release()

    def ask_local_llm(self, reference_news, candidate_news):
        """Final semantic verification using local Qwen model with robust JSON parsing"""
        prompt = f"""
        Act as a strict news editor. Compare these two Turkish news texts.
        Do these two texts refer to the **EXACT SAME specific event**?

        - Same broad topic (e.g. "Earthquake") is NOT enough. It must be the SAME location and time.
        - Same sports team is NOT enough. It must be the SAME match or transfer.
        - Same politician is NOT enough. It must be the SAME speech or decree.

        Answer **true** ONLY if they describe the exact same specific event.
        Answer **false** if they are different events, even if related.

        Ref News: "{reference_news[:700]}"
        New News: "{candidate_news[:700]}"

        OUTPUT FORMAT: Return ONLY a raw JSON object. Do NOT use markdown formatting.
        Example: {{"match": true}}
        """
        payload = {
            "model": LOCAL_MODEL_NAME, "prompt": prompt, "stream": False, "format": "json",
            "options": {"temperature": 0.0, "num_ctx": 2048}
        }
        try:
            result = self._ollama_post(payload, timeout=25)
            if not result:
                return False
            raw_text = result.get('response', '{}').strip()
            raw_text = raw_text.strip("` \n")
            if raw_text.startswith("json"):
                raw_text = raw_text[4:].strip()
            return json.loads(raw_text).get("match", False)
        except Exception as e:
            logger.error(f"Local LLM Verification Failed: {e}")
            return False

    def verify_cross_trend(self, x_trend: str, google_title: str) -> bool:
        """
        AI verifies if the Twitter (X) trend logically matches and is explained by the Google News headline.
        """
        prompt = f"""
        Act as a strict news verification AI.
        Twitter (X) Trend: "{x_trend}"
        Google News Headline: "{google_title}"
        
        Does this news headline explicitly explain or relate directly to the Twitter trend?
        - Answer 'true' if the headline is clearly the reason why this word is trending.
        - Answer 'false' if they are unrelated, or if the trend is just a generic spam/daily word not uniquely explained by the headline.
        
        OUTPUT FORMAT: Return ONLY a raw JSON object. Do NOT use markdown formatting.
        Example: {{"match": true}}
        """
        payload = {
            "model": LOCAL_MODEL_NAME, "prompt": prompt, "stream": False, "format": "json",
            "options": {"temperature": 0.0, "num_ctx": 1024}
        }
        try:
            result = self._ollama_post(payload, timeout=8)
            if not result:
                return False
            raw_text = result.get('response', '{}').strip()
            raw_text = raw_text.strip("` \n")
            if raw_text.startswith("json"):
                raw_text = raw_text[4:].strip()
            return json.loads(raw_text).get("match", False)
        except Exception as e:
            logger.error(f"Cross-Trend Verification Failed: {e}")
            return False

    def moderate_comment(self, text: str, *, timeout: int = None, lock_wait: int = None) -> str:
        """AI Auto-Moderation for user comments using local Qwen model.

        Called from the comment POST handler, so it runs inside a gunicorn
        worker. With the previous defaults the worst case was a 5s wait for the
        global Ollama lock followed by a 5s inference — ten seconds of a worker
        held by one comment, and there are only four workers. Requests default to
        a much smaller budget and fall back to "pending"; the gravity worker
        re-moderates anything that lands there, so bailing out early costs a few
        minutes of delay rather than the comment's visibility.
        """
        timeout = MODERATION_REQUEST_TIMEOUT if timeout is None else timeout
        lock_wait = MODERATION_REQUEST_LOCK_WAIT if lock_wait is None else lock_wait
        # 0. Basic Blacklist Check (Fallback & Speed)
        # Simple list of Turkish profanity/spam keywords
        blacklist = ["küfür", "hakaret", "aptal", "gerizekalı", "salak", "bet", "casino", "http", "www", ".com"]
        if any(bad in text.lower() for bad in blacklist):
            return "rejected"
            
        prompt = f"""
        Act as a strict community moderator for a Turkish news website.
        Analyze the following user comment. Check for:
        1. Profanity, swear words, or severe insults (Küfür, hakaret).
        2. Racism, hate speech, or discrimination (Irkçılık, nefret söylemi).
        3. Spam, advertisements, or gibberish (Spam, reklam, anlamsız metin).

        If the comment contains ANY of the above, status is "rejected".
        If the comment is completely clean and normal, status is "approved".
        If you are unsure, status is "pending".

        Comment: "{text}"
        
        OUTPUT FORMAT: Return ONLY a raw JSON object. Do NOT use markdown formatting.
        Example: {{"status": "approved"}}
        """
        payload = {
            "model": LOCAL_MODEL_NAME, "prompt": prompt, "stream": False, "format": "json",
            "options": {"temperature": 0.0, "num_ctx": 1024}
        }
        try:
            result = self._ollama_post(payload, timeout=timeout, lock_wait=lock_wait)
            if not result:
                return "pending"
            raw_text = result.get('response', '{}').strip()
            raw_text = raw_text.strip("` \n")
            if raw_text.startswith("json"):
                raw_text = raw_text[4:].strip()
            status = json.loads(raw_text).get("status", "pending")
            if status not in ["approved", "rejected", "pending"]:
                return "pending"
            
            # If AI is unsure (pending), but text is short and clean, approve it
            if status == "pending" and len(text) < 20 and not any(bad in text.lower() for bad in blacklist):
                return "approved"
                
            return status
        except Exception as e:
            logger.error(f"AI Moderation Failed: {e}")
            # Fallback: If AI fails, approve unless it hits blacklist (checked at start)
            return "approved"

    def get_cluster_reference_doc(self, cluster_id):
        """Fetch the primary reference document for a cluster"""
        try:
            # Try to find the document explicitly tagged as reference
            result = self.collection.get(
                where={"$and": [{"cluster_id": cluster_id}, {"is_reference": True}]},
                limit=1,
                include=["documents", "metadatas"]
            )
            if result['documents'] and len(result['documents']) > 0:
                return result['documents'][0], result['metadatas'][0]
            
            # Fallback: get the first available document in the cluster
            fallback = self.collection.get(where={"cluster_id": cluster_id}, limit=1, include=["documents", "metadatas"])
            if fallback['documents'] and len(fallback['documents']) > 0:
                return fallback['documents'][0], fallback['metadatas'][0]
        except Exception as e:
            logger.error(f"Reference Doc Fetch Error: {e}")
        return None, None

    def process_news(self, raw_text: str, source: str, external_id: str):
        """
        Main processing pipeline: Vectorization -> Rolling Search -> LLM Verification -> Clustering.
        """
        from app.core.text_utils import clean_text, clean_text_for_embedding
        cleaned_text = clean_text(raw_text)

        # Discard very short or irrelevant noise
        if not cleaned_text or len(cleaned_text) < 25:
            return None, False

        # Embedding uses a deeper-cleaned version that strips event-template
        # phrases (arrests, operations, boilerplate) so the vector represents
        # WHAT happened, not HOW it was reported. Display/storage still uses
        # the original cleaned_text.
        embedding_text = clean_text_for_embedding(raw_text)
        if not embedding_text or len(embedding_text) < 15:
            embedding_text = cleaned_text  # fallback if over-stripped

        now_ts = datetime.now().timestamp()

        # Fast-path dedup uses embedding_text hash so identical boilerplate
        # with different event details doesn't collide.
        text_hash = hashlib.sha1(embedding_text.encode("utf-8")).hexdigest()
        cache_entry = self.fast_dedup_cache.get(text_hash)
        if cache_entry and cache_entry["expires_at"] > now_ts:
            return cache_entry["cluster_id"], True

        # Two vectors: passage for storage, query for search (e5-large prefix strategy).
        # Both use embedding_text (template-stripped) so distance reflects event
        # similarity rather than shared reporting boilerplate.
        passage_vector = self.get_embedding(embedding_text, is_query=False)
        query_vector = self.get_embedding(embedding_text, is_query=True)

        # --- FIXED Phase 3: Rolling Cache (Numeric Unix Timestamp) ---
        # Filter: only check clusters from the last 48 hours
        time_threshold_ts = (datetime.now() - timedelta(hours=48)).timestamp()

        try:
            # Vector query with numeric metadata filtering
            results = self.collection.query(
                query_embeddings=[query_vector],
                n_results=12,
                where={"timestamp": {"$gte": time_threshold_ts}},
                include=["metadatas", "distances", "documents"]
            )
        except Exception as e:
            logger.error(f"Vector Search Query Error: {e}")
            return None, False

        cluster_id = None
        is_duplicate = False
        checked_clusters = set()

        # Instrumentation only — no behaviour change. Measured in production:
        # clustering issues ~3.4 LLM calls per article (rss 3.9) and accounts for
        # 77% of all Ollama traffic, which sits at ~95% duty. The loop below asks
        # the LLM once per candidate in the uncertain band and only breaks on a
        # "yes", so an article that matches nothing is the most expensive one.
        # Capping the number of asks is the obvious fix, but the cap is only safe
        # if real merges land on the first ask. These counters answer that.
        llm_asks = 0
        merged_on_ask = None
        merge_distance = None
        merge_kind = "new"

        if results['distances'] and results['distances'][0]:
            for i, distance in enumerate(results['distances'][0]):
                # Cosine distance: 0.0 = exact match. Skip anything clearly unrelated.
                # Use 0.35 as the hard cap; X-Trend has its own tighter threshold applied below.
                if distance > 0.35: continue
                
                metadata = results['metadatas'][0][i]
                candidate_cluster_id = metadata['cluster_id']
                
                if candidate_cluster_id in checked_clusters: continue
                checked_clusters.add(candidate_cluster_id)

                target_text, ref_meta = self.get_cluster_reference_doc(candidate_cluster_id)
                if not target_text:
                    target_text = results['documents'][0][i]
                    ref_meta = results['metadatas'][0][i]

                # Dynamic Thresholds with Time-Decay
                # X-Trend posts share a template prefix ("𝕏 Sosyal Medya Trendi:")
                # which inflates embedding similarity — use tighter thresholds to prevent
                # different trending topics from being merged into one cluster.
                if source == "X-Trend":
                    # X-Trend posts share template prefix → very tight
                    auto_merge_thresh = 0.12
                    uncertain_thresh = 0.20
                else:
                    # RSS/Telegram: embedding_text is now template-stripped,
                    # so stricter thresholds are safe — boilerplate inflation removed.
                    auto_merge_thresh = 0.15
                    uncertain_thresh = 0.25

                age_hours = (now_ts - ref_meta['timestamp']) / 3600.0
                if age_hours > 24.0 and source != "X-Trend":
                    auto_merge_thresh = 0.12
                    uncertain_thresh = 0.20
                
                # Case 1: High similarity (Auto-Merge Zone)
                if distance < auto_merge_thresh:
                    cluster_id = candidate_cluster_id
                    is_duplicate = True
                    merge_kind = "auto"
                    merge_distance = distance
                    break

                # Case 2: Uncertain Zone -> Ask Local LLM
                if distance < uncertain_thresh:
                    llm_asks += 1
                    if self.ask_local_llm(target_text, cleaned_text):
                        cluster_id = candidate_cluster_id
                        is_duplicate = True
                        merge_kind = "llm"
                        merged_on_ask = llm_asks
                        merge_distance = distance
                        break

        # One machine-parseable line per article. Grep CLUSTER_STATS to get the
        # distribution of merges by ask ordinal, which is what decides the cap.
        logger.info(
            "CLUSTER_STATS source=%s kind=%s asks=%d merged_on=%s dist=%s",
            source, merge_kind, llm_asks,
            merged_on_ask if merged_on_ask is not None else "-",
            f"{merge_distance:.4f}" if merge_distance is not None else "-",
        )

        is_new_reference = False
        if not cluster_id:
            cluster_id = str(uuid.uuid4())
            is_new_reference = True 
            logger.info(f"✨ New Trend Created: {cluster_id[:8]}")
        else:
            logger.info(f"🔗 Appended to Trend: {cluster_id[:8]}")

        # Store in ChromaDB with numeric timestamp for future filtering
        self.collection.add(
            documents=[cleaned_text],
            embeddings=[passage_vector],
            metadatas=[{
                "source": source,
                "cluster_id": cluster_id,
                "external_id": external_id,
                "timestamp": now_ts, # Stored as float for $gte support
                "is_reference": is_new_reference
            }],
            ids=[str(uuid.uuid4())]
        )

        self.fast_dedup_cache[text_hash] = {
            "cluster_id": cluster_id,
            "expires_at": now_ts + self.fast_dedup_ttl
        }
        
        return cluster_id, is_duplicate

    def get_related_trends(self, cluster_id, limit=4):
        """Find related trends using vector proximity across the entire archive"""
        try:
            ref_result = self.get_cluster_reference_doc(cluster_id)
            ref_doc = ref_result[0] if isinstance(ref_result, tuple) else ref_result
            
            if not ref_doc: return []

            query_vector = self.get_embedding(ref_doc, is_query=True)

            # No time filter here as related news can be from the past
            results = self.collection.query(
                query_embeddings=[query_vector],
                n_results=limit + 10,
                include=["metadatas"]
            )

            related_clusters = []
            seen_ids = {cluster_id}

            if results['metadatas'] and results['metadatas'][0]:
                for metadata in results['metadatas'][0]:
                    cid = metadata['cluster_id']
                    if cid not in seen_ids:
                        related_clusters.append(cid)
                        seen_ids.add(cid)
                    if len(related_clusters) >= limit:
                        break
            
            return related_clusters
        except Exception as e:
            logger.error(f"Related Trends Error: {e}")
            return []

    def merge_clusters(self, source_cluster_id, target_cluster_id):
        """Moves all vectors from source cluster to target cluster in ChromaDB"""
        try:
            results = self.collection.get(where={"cluster_id": source_cluster_id})
            if not results or not results.get('ids'): 
                return
            
            new_metadatas = []
            for meta in results['metadatas']:
                meta['cluster_id'] = target_cluster_id
                meta['is_reference'] = False # Demote source references
                new_metadatas.append(meta)
                
            self.collection.update(
                ids=results['ids'],
                metadatas=new_metadatas
            )
            logger.info(f"🔗 ChromaDB: Merged {len(results['ids'])} vectors from {source_cluster_id[:8]} to {target_cluster_id[:8]}")
        except Exception as e:
            logger.error(f"❌ ChromaDB Merge Error: {e}")

# Singleton instance
ai_engine = AIEngine()
