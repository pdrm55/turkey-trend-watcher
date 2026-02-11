import os
import sys
import logging
import faulthandler
import uuid
import shutil
import requests
import json
from datetime import datetime, timedelta

# ۱. فعال‌سازی ردیاب خطای سیستمی (برای عیب‌یابی SegFaultها در محیط داکر)
faulthandler.enable()

# ۲. تنظیمات حیاتی برای تک‌نخ کردن پردازش‌های سنگین ریاضی (جلوگیری از تداخل حافظه)
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

# ۳. شبیه‌سازی ماژول Posthog برای جلوگیری از لود شدن تله‌متری ناخواسته
from unittest.mock import MagicMock
sys.modules["posthog"] = MagicMock()

# تنظیمات لاگر
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ۴. ایمپورت‌های سنگین یادگیری ماشین (پس از تنظیمات محیطی)
import torch
torch.set_num_threads(1) 

import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer

# --- تنظیمات اتصال و مدل‌ها ---
CHROMA_HOST = os.getenv("CHROMA_HOST", "ttw_chroma")
CHROMA_PORT = os.getenv("CHROMA_PORT", "8000")
OLLAMA_API_URL = os.getenv("OLLAMA_API_URL", "http://ttw_ollama:11434/api/generate")
LOCAL_MODEL_NAME = "qwen2.5:1.5b"

class AIEngine:
    def __init__(self):
        """راه‌اندازی موتور هوش مصنوعی و اتصال به دیتابیس برداری"""
        print("🧠 Loading Multilingual Embedding Model...", flush=True)
        # استفاده از مدل چندزبانه برای پشتیبانی عالی از زبان ترکی
        self.model = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2', device='cpu')
        
        try:
            self.chroma_client = chromadb.HttpClient(
                host=CHROMA_HOST,
                port=int(CHROMA_PORT),
                settings=Settings(anonymized_telemetry=False, allow_reset=True)
            )
            # ایجاد یا فراخوانی کالکشن با فضای محاسباتی کسینوسی (مناسب برای متن)
            self.collection = self.chroma_client.get_or_create_collection(
                name="news_clusters",
                metadata={"hnsw:space": "cosine"}
            )
            print(f"✅ AI Engine Phase 3 Ready (Rolling Cache Enabled)", flush=True)
        except Exception as e:
            print(f"❌ ChromaDB Connection Error: {e}")

    def get_embedding(self, text: str):
        """تبدیل متن به بردار عددی (Embedding)"""
        try:
            if not isinstance(text, str): text = str(text)
            # نرمال‌سازی بردارها برای دقت بیشتر در مقایسه کسینوسی
            vector = self.model.encode(text, convert_to_numpy=True).tolist()
            return vector
        except Exception as e:
            logger.error(f"Embedding Error: {e}")
            raise e

    def ask_local_llm(self, reference_news, candidate_news):
        """تایید نهایی شباهت دو خبر توسط مدل محلی Qwen برای جلوگیری از خوشه‌بندی اشتباه"""
        prompt = f"""
        Act as a strict news editor. Compare these two Turkish news texts.
        Do they report the EXACT SAME specific incident/event occurring at the same time?
        
        If it's a new update about an old event, answer: false.
        If it's the exact same report, answer: true.
        
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
        """دریافت متن مرجع (اصلی‌ترین خبر) یک کلاستر برای مقایسه‌های بعدی"""
        try:
            # جستجو برای سندی که به عنوان مرجع تگ شده است
            result = self.collection.get(
                where={"$and": [{"cluster_id": cluster_id}, {"is_reference": True}]},
                limit=1
            )
            if result['documents'] and len(result['documents']) > 0:
                return result['documents'][0]
            
            # در صورتی که مرجع صریح وجود نداشت، اولین سند کلاستر را برگردان
            fallback = self.collection.get(where={"cluster_id": cluster_id}, limit=1)
            if fallback['documents'] and len(fallback['documents']) > 0:
                return fallback['documents'][0]
        except Exception as e:
            logger.error(f"Reference Doc Fetch Error: {e}")
        return None

    def process_news(self, raw_text: str, source: str, external_id: str):
        """
        پردازش خبر ورودی: وکتوریزه کردن، جستجوی کلاستر مشابه و تصمیم‌گیری برای ایجاد یا الحاق به ترند.
        """
        from app.core.text_utils import clean_text
        cleaned_text = clean_text(raw_text)
        
        # نادیده گرفتن متون بسیار کوتاه یا نامفهوم
        if not cleaned_text or len(cleaned_text) < 25: 
            return None, False

        vector = self.get_embedding(cleaned_text)
        
        # --- فاز ۳: حافظه برداری میان‌مدت (Rolling Cache Filter) ---
        # فقط اخباری که در ۴۸ ساعت گذشته منتشر شده‌اند برای کلاسترسازی بررسی می‌شوند
        time_threshold = (datetime.now() - timedelta(hours=48)).isoformat()
        
        try:
            # جستجو در ChromaDB با فیلتر زمانی برای افزایش دقت و سرعت
            results = self.collection.query(
                query_embeddings=[vector],
                n_results=5,
                where={"timestamp": {"$gte": time_threshold}}, # فیلتر حافظه غلتان
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
                # اگر فاصله کسینوسی بیش از 0.42 باشد، تشابه معنایی ضعیف است
                if distance > 0.42: continue
                
                metadata = results['metadatas'][0][i]
                candidate_cluster_id = metadata['cluster_id']
                
                if candidate_cluster_id in checked_clusters: continue
                checked_clusters.add(candidate_cluster_id)

                # دریافت متن مرجع کلاستر کاندیدا برای مقایسه دقیق‌تر
                target_text = self.get_cluster_reference_doc(candidate_cluster_id) or results['documents'][0][i]
                
                # حالت اول: شباهت برداری بسیار بالا (کپی مستقیم)
                if distance < 0.07:
                    cluster_id = candidate_cluster_id
                    is_duplicate = True
                    break

                # حالت دوم: شباهت در محدوده خاکستری -> تایید با هوش مصنوعی محلی
                if self.ask_local_llm(target_text, cleaned_text):
                    cluster_id = candidate_cluster_id
                    is_duplicate = True
                    break

        # ۵. تصمیم‌گیری برای ایجاد ترند جدید یا اضافه شدن به قبلی
        is_new_reference = False
        if not cluster_id:
            cluster_id = str(uuid.uuid4())
            is_new_reference = True 
            logger.info(f"✨ New Trend Created: {cluster_id[:8]}")
        else:
            logger.info(f"🔗 Appended to Trend: {cluster_id[:8]}")

        # ۶. ذخیره خبر در دیتابیس برداری
        self.collection.add(
            documents=[cleaned_text],
            embeddings=[vector],
            metadatas=[{
                "source": source,
                "cluster_id": cluster_id,
                "external_id": external_id,
                "timestamp": datetime.now().isoformat(),
                "is_reference": is_new_reference
            }],
            ids=[str(uuid.uuid4())]
        )
        
        return cluster_id, is_duplicate

    def get_related_trends(self, cluster_id, limit=4):
        """یافتن ترندهای مرتبط (Related News) بر اساس نزدیکی برداری در کل تاریخچه"""
        try:
            ref_doc = self.get_cluster_reference_doc(cluster_id)
            if not ref_doc: return []

            query_vector = self.get_embedding(ref_doc)

            # در اینجا فیلتر زمانی اعمال نمی‌کنیم تا آرشیو هم بررسی شود
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

# ایجاد نمونه یکتا (Singleton) از موتور
ai_engine = AIEngine()