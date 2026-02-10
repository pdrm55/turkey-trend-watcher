import os
import sys
import logging
import faulthandler

# 1. فعال‌سازی ردیاب خطای سیستمی
faulthandler.enable()

# 2. تنظیمات حیاتی برای تک‌نخ کردن پردازش‌ها (جلوگیری از SegFault)
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

# 3. کشتن کامل ماژول Posthog قبل از لود شدن
from unittest.mock import MagicMock
sys.modules["posthog"] = MagicMock()

import uuid
import shutil
import requests
import json
from datetime import datetime

# تنظیم لاگر
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ایمپورت‌های سنگین (بعد از تنظیمات محیطی)
import torch
torch.set_num_threads(1) 

import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer

# --- تنظیمات اتصال داکر ---
CHROMA_HOST = os.getenv("CHROMA_HOST", "ttw_chroma")
CHROMA_PORT = os.getenv("CHROMA_PORT", "8000")
OLLAMA_API_URL = os.getenv("OLLAMA_API_URL", "http://ttw_ollama:11434/api/generate")
LOCAL_MODEL_NAME = "qwen2.5:1.5b"

class AIEngine:
    def __init__(self):
        print("🧠 Loading Embedding Model (SentenceTransformer)...", flush=True)
        self.model = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2', device='cpu')
        
        try:
            self.chroma_client = chromadb.HttpClient(
                host=CHROMA_HOST,
                port=int(CHROMA_PORT),
                settings=Settings(anonymized_telemetry=False, allow_reset=True)
            )
            self.collection = self.chroma_client.get_or_create_collection(
                name="news_clusters",
                metadata={"hnsw:space": "cosine"}
            )
            print(f"✅ AI Engine Ready. Strategy: Client-Server Hybrid", flush=True)
        except Exception as e:
            print(f"❌ ChromaDB Connection Error: {e}")

    def get_embedding(self, text: str):
        try:
            if not isinstance(text, str): text = str(text)
            vector = self.model.encode(text, convert_to_numpy=True).tolist()
            return vector
        except Exception as e:
            logger.error(f"Embedding Error: {e}")
            raise e

    def ask_local_llm(self, reference_news, candidate_news):
        prompt = f"""
        Act as a strict news editor. Compare these two news texts.
        Do they report the EXACT SAME specific event/incident?
        
        Ref News: "{reference_news[:600]}"
        New News: "{candidate_news[:600]}"
        
        Answer ONLY JSON: {{"match": true}} or {{"match": false}}
        """
        payload = {
            "model": LOCAL_MODEL_NAME, "prompt": prompt, "stream": False, "format": "json",
            "options": {"temperature": 0.0, "num_ctx": 2048}
        }
        try:
            response = requests.post(OLLAMA_API_URL, json=payload, timeout=8)
            result = response.json()
            return json.loads(result['response']).get("match", False)
        except: return False 

    def get_cluster_reference_doc(self, cluster_id):
        """
        دریافت متن مرجع برای یک کلاستر.
        تلاش می‌کند ابتدا سندی با تگ 'is_reference' پیدا کند، در غیر این صورت اولین سند موجود را برمی‌گرداند.
        """
        try:
            # تلاش برای یافتن خبر مرجع اصلی
            result = self.collection.get(
                where={"$and": [{"cluster_id": cluster_id}, {"is_reference": True}]},
                limit=1
            )
            if result['documents'] and len(result['documents']) > 0:
                return result['documents'][0]
            
            # در صورتی که مرجع پیدا نشد (برای کلاسترهای قدیمی)، اولین خبر کلاستر را بگیر
            fallback_result = self.collection.get(
                where={"cluster_id": cluster_id},
                limit=1
            )
            if fallback_result['documents'] and len(fallback_result['documents']) > 0:
                return fallback_result['documents'][0]
        except Exception as e:
            logger.error(f"Error fetching reference doc for {cluster_id}: {e}")
        return None

    def get_related_trends(self, cluster_id, limit=4):
        """
        یافتن ترندهای مشابه با استفاده از جستجوی برداری در ChromaDB.
        """
        try:
            ref_doc = self.get_cluster_reference_doc(cluster_id)
            if not ref_doc:
                return []

            query_vector = self.get_embedding(ref_doc)

            # جستجوی بردارهای نزدیک
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

    def process_news(self, raw_text: str, source: str, external_id: str):
        from app.core.text_utils import clean_text
        cleaned_text = clean_text(raw_text)
        if not cleaned_text or len(cleaned_text) < 20: return None, False

        vector = self.get_embedding(cleaned_text)
        
        try:
            results = self.collection.query(query_embeddings=[vector], n_results=5, include=["metadatas", "distances", "documents"])
        except: return None, False

        cluster_id = None
        is_duplicate = False
        checked_clusters = set()

        if results['distances'] and results['distances'][0]:
            for i, distance in enumerate(results['distances'][0]):
                if distance > 0.40: continue
                candidate_cluster_id = results['metadatas'][0][i]['cluster_id']
                if candidate_cluster_id in checked_clusters: continue
                checked_clusters.add(candidate_cluster_id)

                target_text = self.get_cluster_reference_doc(candidate_cluster_id) or results['documents'][0][i]
                
                if distance < 0.08:
                    cluster_id = candidate_cluster_id
                    is_duplicate = True
                    break

                if self.ask_local_llm(target_text, cleaned_text):
                    cluster_id = candidate_cluster_id
                    is_duplicate = True
                    break

        is_new_reference = False
        if not cluster_id:
            cluster_id = str(uuid.uuid4())
            is_new_reference = True 

        self.collection.add(
            documents=[cleaned_text], embeddings=[vector],
            metadatas=[{"source": source, "cluster_id": cluster_id, "external_id": external_id, "timestamp": datetime.now().isoformat(), "is_reference": is_new_reference}],
            ids=[str(uuid.uuid4())]
        )
        return cluster_id, is_duplicate

ai_engine = AIEngine()
