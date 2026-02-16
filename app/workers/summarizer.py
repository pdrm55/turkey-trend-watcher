import sys
import os
import time
import json
import re
import csv
from datetime import datetime, timezone
from google import genai
from google.genai import types

# Add project root to sys path for internal imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
from app.database.models import SessionLocal, Trend, RawNews, SystemSettings
from sqlalchemy import desc
from app.config import Config
from app.core.indexing_utils import notify_google 
from app.core.text_utils import slugify_turkish 
from app.core.alert_service import alert_service

# --- Google AI & System Configuration ---
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
if not GOOGLE_API_KEY:
    print("❌ Error: GOOGLE_API_KEY not found in .env. LLM operations will fail.")

client = None
MODEL_NAME = None 
LOG_FILE = "ai_monitor_data.csv"

# دریافت آدرس سایت از محیط؛ در صورت عدم وجود از دامنه واقعی استفاده می‌شود تا تلگرام خطا ندهد
BASE_SITE_URL = os.getenv("BASE_SITE_URL", "https://trendiatr.com") 

# Scoring threshold for instant Google Indexing (SEO Step)
GOOGLE_INDEXING_THRESHOLD = 25

# Junk keywords for final filtering (Safety Layer)
JUNK_KEYWORDS = ['burç', 'fal ', 'günlük burç', 'astroloji', 'horoskop', 'astrolog']

# --- Monitoring & Logging Infrastructure ---
if not os.path.exists(LOG_FILE):
    with open(LOG_FILE, mode='w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "trend_id", "model", "input_tokens", "output_tokens", "duration_sec", "category", "status", "cost_usd"])

def log_to_csv(trend_id, model, in_tok, out_tok, duration, category, status):
    """Logs AI performance and token usage for cost monitoring and analytics"""
    try:
        # Cost calculation based on Gemini 2.0 Flash Lite pricing
        # Input: $0.075 / 1M | Output: $0.30 / 1M
        cost = (in_tok * 0.000000075) + (out_tok * 0.00000030)
        
        with open(LOG_FILE, mode='a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                trend_id,
                model,
                in_tok,
                out_tok,
                f"{duration:.2f}",
                category,
                status,
                f"{cost:.8f}"
            ])
    except Exception as e:
        print(f"⚠️ Monitoring Log Error: {e}")

def get_best_available_model(client):
    """Dynamically identifies the best available Gemini model from the API list"""
    print("🔍 Probing for best Gemini model in the current region...")
    try:
        candidates = []
        for m in client.models.list():
            name = m.name.replace('models/', '') 
            # Filter for text-generation flash models
            if 'flash' in name.lower() and 'image' not in name.lower() and 'audio' not in name.lower():
                candidates.append(name)
        
        # Priority 1: Flash Lite (Best value/performance)
        for c in candidates:
            if 'lite' in c and 'flash' in c: return c
        # Priority 2: Stable 1.5 Flash
        for c in candidates:
            if '1.5-flash' in c and 'latest' not in c: return c
        
        if candidates: return candidates[0]
        return 'gemini-2.0-flash-lite-preview-09-2025'
        
    except Exception as e:
        print(f"⚠️ Model Discovery Failed: {e}. Using hardcoded fallback.")
        return 'gemini-2.0-flash-lite-preview-09-2025'

# Initialize the Gemini Client
if GOOGLE_API_KEY:
    try:
        client = genai.Client(api_key=GOOGLE_API_KEY)
        MODEL_NAME = get_best_available_model(client)
        print(f"✅ AI Context Ready: Using {MODEL_NAME}")
    except Exception as e:
        print(f"❌ Gemini Initialization Error: {e}")

# ==========================================
# Turkish Categorical Keyword Optimization
# ==========================================

SPORTS_KEYWORDS = {
    "high": ["futbol", "süper lig", "şampiyonlar ligi", "avrupa ligi", "beşiktaş", "fenerbahçe", "galatasaray", "trabzonspor", "milli takım", "voleybol", "basketbol", "derbi", "puan durumu", "teknik direktör", "gol kralı", "fikstür"],
    "medium": ["penaltı", "transfer", "kadro", "madalya", "şampiyon", "kupa", "bonservis", "sarı kart", "kırmızı kart", "ofsayt", "var incelemesi"],
    "low": ["maç", "skor", "takım", "kulüp", "hakem", "oyuncu", "antrenman", "karşılaşma"]
}

ECONOMY_KEYWORDS = {
    "high": ["enflasyon", "faiz", "zam", "maaş", "borsa istanbul", "bist 100", "tcmb", "merkez bankası", "dolar/tl", "euro/tl", "akaryakıt", "halka arz", "asgari ücret", "emekli zammı", "vergi artışı"],
    "medium": ["tüfe", "üfe", "ihracat", "ithalat", "gsyh", "kredi", "vergi", "bütçe", "cari açık", "döviz kuru", "altın fiyatları", "temettü", "spk", "kap"],
    "low": ["fiyat", "artış", "yatırım", "borç", "şirket", "piyasa", "kar", "zarar", "maliyet", "tüketici", "alım gücü"]
}

TECHNOLOGY_KEYWORDS = {
    "high": ["apple", "google", "microsoft", "openai", "chatgpt", "yapay zeka", "ai", "siber güvenlik", "baykar", "tusaş", "aselsan", "uzay", "roket", "savunma sanayii", "togg", "insansız hava aracı"],
    "medium": ["yazılım", "donanım", "ios", "android", "akıllı telefon", "işlemci", "güncelleme", "robot", "drone", "uygulama", "blockchain", "kripto para", "bulut bilişim"],
    "low": ["cihaz", "teknoloji", "dijital", "platform", "şifre", "bağlantı", "hız", "ekran", "fiber", "internet"]
}

POLITICS_KEYWORDS = {
    "high": ["cumhurbaşkanı", "erdoğan", "özgür özel", "bahçeli", "imamoğlu", "ak parti", "chp", "mhp", "tbmm", "meclis", "başkan", "kabine", "seçim", "ysk", "anayasa", "bakanlığı"],
    "medium": ["miting", "aday", "ittifak", "yasa", "kanun", "zirve", "diplomasi", "nato", "bm", "birleşmiş milletler", "istifa", "gözaltı", "tutuklama", "önerge"],
    "low": ["açıklama", "toplantı", "karar", "kriz", "gündem", "lider", "tepki", "eleştiri", "ziyaret", "diplomatik"]
}

ART_KEYWORDS = {
    "high": ["sinema", "film", "dizi", "konser", "festival", "sergi", "kitap", "yazar", "oyuncu", "albüm", "tarkan", "sezen aksu", "magazin", "ünlü", "cem yılmaz"],
    "medium": ["vizyon", "gala", "sahne", "yönetmen", "fragman", "reyting", "aşk", "ayrılık", "boşanma", "evlilik", "fenomen", "sosyal medya", "instagram"],
    "low": ["izle", "dinle", "eğlence", "moda", "tarz", "trend", "stil", "kırmızı halı", "tiktok", "paylaşım"]
}

GUNDEM_KEYWORDS = {
    "high": ["deprem", "yangın", "kaza", "sel", "cinayet", "operasyon", "patlama", "afad", "polis", "jandarma", "meteoroloji", "şiddetli fırtına"],
    "medium": ["vefat", "kayıp", "arama kurtarma", "trafik kazası", "gözaltı", "adliye", "asayiş", "uyarı", "don", "sağanak"],
    "low": ["haber", "olay", "hava durumu", "sıcaklık", "belediye", "valilik", "hizmet", "duyuru"]
}

# Cross-category penalties for classification refinement
NEGATIVE_KEYWORDS = {
    "political_vs_sports": {
        "dominant_category": "Spor",
        "keywords": ["galatasaray", "fenerbahçe", "beşiktaş", "trabzonspor", "süper lig", "maç", "gol", "transfer"],
        "penalty": -60, "affects": ["Siyaset"]
    },
    "political_vs_accident": {
        "dominant_category": "Gündem",
        "keywords": ["deprem", "yangın", "sel", "kaza", "can kaybı", "patlama"],
        "penalty": -40, "affects": ["Siyaset", "Ekonomi"]
    },
    "politics_exclusive": {
        "dominant_category": "Siyaset", 
        "keywords": ["resmi gazete", "kararname", "kanun teklifi", "tbmm", "anayasa mahkemesi", "genel kurul", "grup toplantısı"],
        "penalty": -50, "affects": ["Spor", "Sanat", "Teknoloji", "Gündem"],
        "soft_penalty": -20, "soft_affects": ["Ekonomi"] 
    },
    "economy_exclusive": {
        "dominant_category": "Ekonomi",
        "keywords": ["borsa istanbul", "bist 100", "döviz kuru", "faiz kararı", "enflasyon rakamları", "temettü", "kap bildirimi"],
        "penalty": -40, "affects": ["Spor", "Sanat", "Teknoloji"],
        "soft_penalty": -15, "soft_affects": ["Siyaset", "Gündem"]
    }
}

# ==========================================
# Scoring & Categorization Logic
# ==========================================

def normalize_turkish_local(text: str) -> str:
    """Strict Turkish character normalization for consistent matching"""
    text = text.replace('İ', 'i').replace('I', 'ı').replace('Ğ', 'ğ').replace('Ü', 'ü').replace('Ş', 'ş').replace('Ö', 'ö').replace('Ç', 'ç')
    return text.lower()

def calculate_keyword_score(text: str, keywords_dict: dict) -> int:
    """Calculates weighting for a specific category based on text frequency"""
    text = normalize_turkish_local(text)
    score = 0
    for word in keywords_dict["high"]:
        if word in text: score += 60 
    for word in keywords_dict["medium"]:
        if word in text: score += 20
    for word in keywords_dict["low"]:
        if word in text: score += 5
    return score

def apply_negative_logic(scores: dict, text: str) -> dict:
    """Applies cross-categorical penalties to avoid classification bias"""
    text = normalize_turkish_local(text)
    for rule_name, config in NEGATIVE_KEYWORDS.items():
        found = any(word in text for word in config["keywords"])
        if found:
            for target in config["affects"]:
                if target in scores: scores[target] = max(0, scores[target] + config["penalty"])
            if "soft_affects" in config:
                for target in config["soft_affects"]:
                    if target in scores: scores[target] = max(0, scores[target] + config["soft_penalty"])
    return scores

def decide_final_category(ai_category: str, text: str) -> str:
    """Safety guard: Confirms Gemini's category choice against keyword density"""
    scores = {
        "Spor": calculate_keyword_score(text, SPORTS_KEYWORDS),
        "Ekonomi": calculate_keyword_score(text, ECONOMY_KEYWORDS),
        "Teknoloji": calculate_keyword_score(text, TECHNOLOGY_KEYWORDS),
        "Siyaset": calculate_keyword_score(text, POLITICS_KEYWORDS),
        "Sanat": calculate_keyword_score(text, ART_KEYWORDS),
        "Gündem": calculate_keyword_score(text, GUNDEM_KEYWORDS)
    }
    
    scores = apply_negative_logic(scores, text)
    top_cat = max(scores, key=scores.get)
    top_score = scores[top_cat]

    # Rule: If Gemini choice is weak in keywords but another category is extremely dominant
    if scores.get(ai_category, 0) < 15 and top_score > 65:
        return top_cat, True 

    return ai_category, False

def generate_unique_slug(db, base_title, trend_id):
    """Ensures slug uniqueness in the database for SEO integrity"""
    if not base_title: return None
    base_slug = slugify_turkish(base_title)
    unique_slug = base_slug
    counter = 1
    while True:
        existing = db.query(Trend).filter(Trend.slug == unique_slug, Trend.id != trend_id).first()
        if not existing: return unique_slug
        unique_slug = f"{base_slug}-{counter}"
        counter += 1

# ==========================================
# Gemini Integration Layer
# ==========================================

def generate_summary_with_gemini(text_cluster):
    """Executes the summarization call to the Gemini API with structured response"""
    if not client or not MODEL_NAME: return None, 0, 0, 0 

    prompt = f"""
    You are a professional Turkish news editor. Summarize the following news cluster into a high-quality news post.
    
    CONSTRAINTS:
    - Language: Turkish (TR) only.
    - Style: Journalistic, professional, neutral.
    - Headline: Catchy and SEO optimized.
    - Category: Choose from [Siyaset, Ekonomi, Gündem, Spor, Teknoloji, Sanat].

    OUTPUT FORMAT (JSON ONLY):
    {{
        "headline": "...",
        "summary": "...",
        "category": "...",
        "is_relevant_to_turkey": true
    }}

    RAW TEXT DATA:
    {text_cluster}
    """

    try:
        req_start = time.time()
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type='application/json',
                temperature=0.15,
                max_output_tokens=1000,
            )
        )
        duration = time.time() - req_start
        
        meta = response.usage_metadata
        in_tok = meta.prompt_token_count if meta else 0
        out_tok = meta.candidates_token_count if meta else 0
        
        raw_result = json.loads(response.text)
        if isinstance(raw_result, list) and len(raw_result) > 0:
            ai_data = raw_result[0]
        else:
            ai_data = raw_result

        return ai_data, in_tok, out_tok, duration
    except Exception as e:
        print(f"   ❌ LLM Execution Error: {e}")
        return None, 0, 0, 0

def process_pending_trends():
    """Fetches high-TPS trends, processes them with AI, and updates SEO slugs"""
    db = SessionLocal()
    try:
        # Fetching high-priority trends for summarization
        pending_trends = db.query(Trend).filter(
            Trend.is_active == True,
            Trend.final_tps >= 20,
            Trend.is_published == False
        ).order_by(desc(Trend.final_tps)).limit(5).all()

        if not pending_trends: return False

        print(f"✍️  Processing {len(pending_trends)} Trends for Summary/Publishing...")

        for trend in pending_trends:
            # 1. Content Generation Phase (Only if summary is missing)
            if not trend.summary:
                # Aggregate news context for the trend
                news_items = db.query(RawNews).filter(RawNews.trend_id == trend.id).limit(15).all()
                if not news_items: continue

                cluster_text = "\n".join([f"- {n.content[:1000]}" for n in news_items])

                # Generate AI Content
                ai_result, in_tok, out_tok, duration = generate_summary_with_gemini(cluster_text)
                
                if ai_result and ai_result.get("is_relevant_to_turkey", True):
                    ai_cat = ai_result.get("category", "Gündem")
                    
                    # Verify category through manual keyword analysis
                    final_category, overridden = decide_final_category(ai_cat, cluster_text)
                    
                    # Update Trend Record
                    trend.title = ai_result.get("headline", trend.title)
                    trend.summary = ai_result.get("summary", "")
                    trend.category = final_category 
                    
                    # SEO CRITICAL: Upgrade temporary slug to professional slug
                    trend.slug = generate_unique_slug(db, trend.title, trend.id)
                    trend.last_updated = datetime.now(timezone.utc).replace(tzinfo=None)

                    print(f"   ✅ Summarized: [{trend.category}] {trend.title} (TPS: {trend.final_tps:.1f})")
                    print(f"   🚀 SEO Slug: /trend/{trend.slug}")
                    
                    # Save and Log Stats
                    log_to_csv(trend.id, MODEL_NAME, in_tok, out_tok, duration, trend.category, "Success")
                    
                    # Notify Google for instant indexing (Only on creation)
                    if trend.final_tps >= GOOGLE_INDEXING_THRESHOLD:
                        target_url = f"{BASE_SITE_URL}/trend/{trend.slug}"
                        success, msg = notify_google(target_url)
                        if success: print(f"   🔗 Pushed to Google Indexing API.")
                        else: print(f"   ⚠️ SEO Indexing Warning: {msg}")

                else:
                    # Mark irrelevant or failed content as inactive
                    trend.is_active = False 
                    db.commit()
                    print(f"   🗑️  Discarded Trend {trend.id} (Irrelevant Content)")
                    continue

            # 2. Publishing Phase (Check if ready to publish)
            if trend.summary:
                # دریافت آستانه از تنظیمات سیستم
                threshold_setting = db.query(SystemSettings).filter(SystemSettings.key == "auto_publish_threshold").first()
                publish_threshold = float(threshold_setting.value) if threshold_setting else 35.0
                
                if trend.final_tps >= publish_threshold:
                    target_url = f"{BASE_SITE_URL}/trend/{trend.slug}"
                    alert_service.publish_to_channel(
                        title=trend.title,
                        summary=trend.summary,
                        category=trend.category,
                        url=target_url
                    )
                    trend.is_published = True
                    print(f"   📢 Automatically published to Public Channel.")

            db.commit()

        return True
    finally:
        db.close()

def main():
    """Continuous worker loop for AI Summarization Service"""
    print(f"🤖 TrendiaTR AI Summary Worker Active. Current Model: {MODEL_NAME}")
    while True:
        try:
            has_work = process_pending_trends()
            # Dynamic sleep based on workload
            time.sleep(1 if has_work else 15)
        except KeyboardInterrupt: break
        except Exception as e:
            print(f"❌ Worker Loop Exception: {e}")
            time.sleep(30)

if __name__ == "__main__":
    main()