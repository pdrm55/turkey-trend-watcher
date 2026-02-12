import os
import sys
import logging
import faulthandler
import uuid
import shutil
import requests
import json
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

# --- Connection Settings ---
CHROMA_HOST = os.getenv("CHROMA_HOST", "ttw_chroma")
CHROMA_PORT = os.getenv("CHROMA_PORT", "8000")
OLLAMA_API_URL = os.getenv("OLLAMA_API_URL", "http://ttw_ollama:11434/api/generate")
LOCAL_MODEL_NAME = "qwen2.5:1.5b"

class AIEngine:
    def __init__(self):
        """Initialize AI Engine and connect to Vector Database"""
        print("🧠 Loading Multilingual Embedding Model (Phase 3 Fixed)...", flush=True)
        # Using a powerful multilingual model for Turkish market
        self.model = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2', device='cpu')
        
        try:
            self.chroma_client = chromadb.HttpClient(
                host=CHROMA_HOST,
                port=int(CHROMA_PORT),
                settings=Settings(anonymized_telemetry=False, allow_reset=True)
            )
            # Create or get collection with cosine space
            self.collection = self.chroma_client.get_or_create_collection(
                name="news_clusters",
                metadata={"hnsw:space": "cosine"}
            )
            print(f"✅ AI Engine Phase 3 Ready. Rolling Cache: Numeric Timestamps.", flush=True)
        except Exception as e:
            print(f"❌ ChromaDB Connection Error: {e}")

    def get_embedding(self, text: str):
        """Convert text to numerical vector (Embedding)"""
        try:
            if not isinstance(text, str): text = str(text)
            vector = self.model.encode(text, convert_to_numpy=True).tolist()
            return vector
        except Exception as e:
            logger.error(f"Embedding Error: {e}")
            raise e

    def ask_local_llm(self, reference_news, candidate_news):
        """Final semantic verification using local Qwen model"""
        prompt = f"""
        Act as a strict news editor. Compare these two Turkish news texts.
        Do they report the EXACT SAME specific incident/event occurring at the same time?
        
        If it is a new update about an old event, answer: false.
        If it is the exact same report, answer: true.
        
        Ref News: "{reference_news[:700]}"
        New News: "{candidate_news[:700]}"
        
        Answer ONLY JSON: {{"match": true}} or {{"match": false}}
        """
        payload = {
            "model": LOCAL_MODEL_NAME, "prompt": prompt, "stream": False, "format": "json",
            "options": {"temperature": 0.0, "num_ctx": 2048}
        }
        try:
            response = requests.post(OLLAMA_API_URL, json=payload, timeout=10)
            result = response.json()
            return json.loads(result['response']).get("match", False)
        except Exception as e:
            logger.error(f"Local LLM Verification Failed: {e}")
            return False 

    def get_cluster_reference_doc(self, cluster_id):
        """Fetch the primary reference document for a cluster"""
        try:
            # Try to find the document explicitly tagged as reference
            result = self.collection.get(
                where={"$and": [{"cluster_id": cluster_id}, {"is_reference": True}]},
                limit=1
            )
            if result['documents'] and len(result['documents']) > 0:
                return result['documents'][0]
            
            # Fallback: get the first available document in the cluster
            fallback = self.collection.get(where={"cluster_id": cluster_id}, limit=1)
            if fallback['documents'] and len(fallback['documents']) > 0:
                return fallback['documents'][0]
        except Exception as e:
            logger.error(f"Reference Doc Fetch Error: {e}")
        return None

    def process_news(self, raw_text: str, source: str, external_id: str):
        """
        Main processing pipeline: Vectorization -> Rolling Search -> LLM Verification -> Clustering.
        """
        from app.core.text_utils import clean_text
        cleaned_text = clean_text(raw_text)
        
        # Discard very short or irrelevant noise
        if not cleaned_text or len(cleaned_text) < 25: 
            return None, False

        vector = self.get_embedding(cleaned_text)
        
        # --- FIXED Phase 3: Rolling Cache (Numeric Unix Timestamp) ---
        # Current time as Unix timestamp (Float)
        now_ts = datetime.now().timestamp()
        # Filter: only check clusters from the last 48 hours
        time_threshold_ts = (datetime.now() - timedelta(hours=48)).timestamp()
        
        try:
            # Vector query with numeric metadata filtering
            results = self.collection.query(
                query_embeddings=[vector],
                n_results=5,
                where={"timestamp": {"$gte": time_threshold_ts}}, # Numeric comparison fixed
                include=["metadatas", "distances", "documents"]
            )
        except Exception as e:
            logger.error(f"Vector Search Query Error: {e}")
            return None, False

        cluster_id = None
        is_duplicate = False
        checked_clusters = set()

        if results['distances'] and results['distances'][0]:
            for i, distance in enumerate(results['distances'][0]):
                # Cosine distance threshold (0.0 is exact match, 1.0 is opposite)
                if distance > 0.42: continue
                
                metadata = results['metadatas'][0][i]
                candidate_cluster_id = metadata['cluster_id']
                
                if candidate_cluster_id in checked_clusters: continue
                checked_clusters.add(candidate_cluster_id)

                target_text = self.get_cluster_reference_doc(candidate_cluster_id) or results['documents'][0][i]
                
                # Case 1: Extremely high similarity (Direct copy/repost)
                if distance < 0.07:
                    cluster_id = candidate_cluster_id
                    is_duplicate = True
                    break

                # Case 2: Semantic similarity -> Ask Local LLM
                if self.ask_local_llm(target_text, cleaned_text):
                    cluster_id = candidate_cluster_id
                    is_duplicate = True
                    break

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
            embeddings=[vector],
            metadatas=[{
                "source": source,
                "cluster_id": cluster_id,
                "external_id": external_id,
                "timestamp": now_ts, # Stored as float for $gte support
                "is_reference": is_new_reference
            }],
            ids=[str(uuid.uuid4())]
        )
        
        return cluster_id, is_duplicate

    def get_related_trends(self, cluster_id, limit=4):
        """Find related trends using vector proximity across the entire archive"""
        try:
            ref_doc = self.get_cluster_reference_doc(cluster_id)
            if not ref_doc: return []

            query_vector = self.get_embedding(ref_doc)

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

# Singleton instance
ai_engine = AIEngine()