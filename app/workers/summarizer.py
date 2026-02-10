import sys
import os
import time
import json
import re
import csv
from datetime import datetime, timezone
from google import genai
from google.genai import types

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
from app.database.models import SessionLocal, Trend, RawNews
from sqlalchemy import desc
from app.config import Config
from app.core.indexing_utils import notify_google # وارد کردن ابزار ایندکس گوگل
from app.core.text_utils import slugify_turkish # اضافه کردن تابع جدید برای سئو

# --- تنظیمات گوگل ---
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
if not GOOGLE_API_KEY:
    print("❌ Error: GOOGLE_API_KEY not found in .env")

client = None
MODEL_NAME = None 
LOG_FILE = "ai_monitor_data.csv"
BASE_SITE_URL = "https://trendiatr.com" # آدرس اصلی سایت برای سئو

# حد نصاب امتیاز برای ارسال به گوگل (برای مدیریت سهمیه Quota)
GOOGLE_INDEXING_THRESHOLD = 30

# لیست کلمات کلیدی برای محدودسازی امتیاز اخبار زرد (فال و طالع‌بینی)
JUNK_KEYWORDS = ['burç', 'fal ', 'günlük burç', 'astroloji', 'horoskop']

# --- ایجاد فایل لاگ اگر وجود ندارد ---
if not os.path.exists(LOG_FILE):
    with open(LOG_FILE, mode='w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "trend_id", "model", "input_tokens", "output_tokens", "duration_sec", "category", "status", "cost_usd"])

def log_to_csv(trend_id, model, in_tok, out_tok, duration, category, status):
    """ذخیره آمار در فایل برای داشبورد"""
    try:
        # محاسبه هزینه تقریبی (بر اساس نرخ Flash Lite)
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
        print(f"⚠️ Log Error: {e}")

def get_best_available_model(client):
    print("🔍 Auto-detecting best Gemini model...")
    try:
        candidates = []
        for m in client.models.list():
            name = m.name.replace('models/', '') 
            if 'flash' in name.lower() and 'image' not in name.lower() and 'audio' not in name.lower():
                candidates.append(name)
        
        print(f"   📋 Candidates found: {candidates}")

        for c in candidates:
            if '1.5-flash' in c and 'latest' not in c: return c
        for c in candidates:
            if 'lite' in c and 'flash' in c:
                print(f"   💡 Switching to LITE model for cost savings: {c}")
                return c
        for c in candidates:
            if 'gemini-flash-latest' in c: return c
        
        if candidates: return candidates[0]
        return 'gemini-flash-latest'
        
    except Exception as e:
        print(f"⚠️ Could not list models: {e}. Using default.")
        return 'gemini-1.5-flash'

if GOOGLE_API_KEY:
    try:
        client = genai.Client(api_key=GOOGLE_API_KEY)
        MODEL_NAME = get_best_available_model(client)
        print(f"✅ Selected AI Model: {MODEL_NAME}")
    except Exception as e:
        print(f"❌ Error initializing Gemini Client: {e}")

CATEGORIES = ["Siyaset", "Ekonomi", "Gündem", "Spor", "Teknoloji", "Sanat"]

# ==========================================
# KEYWORD LISTS (100% Turkish Alphabet - Cleaned from Persian/Arabic)
# ==========================================
SPORTS_KEYWORDS = {
    "high": ["futbol", "süper lig", "şampiyonlar ligi", "avrupa ligi", "konferans ligi", "dünya kupası", "uefa", "fifa", "tff", "ziraat türkiye kupası", "beşiktaş", "fenerbahçe", "galatasaray", "trabzonspor", "başakşehir", "milli takım", "bizim çocuklar", "voleybol", "filenin sultanları", "filenin efeleri", "eczacıbaşı", "vakıfbank", "fenerbahçe opet", "basketbol", "12 dev adam", "anadolu efes", "fenerbahçe beko", "nba", "euroleague", "güreş", "yağlı güreş", "kırkpınar", "başpehlivan", "boks", "kick boks", "tekvando", "karate", "mma", "ufc", "halter", "atıcılık", "okçuluk", "mete gazoz", "yusuf dikeç", "formula 1", "tenis", "atletizm", "real madrid", "barcelona", "manchester city", "liverpool", "bayern münih", "psg", "juventus", "inter", "milan", "teknik direktör"],
    "medium": ["derbi", "penaltı", "frikik", "korner", "ofsayt", "hat-trick", "var incelemesi", "sarı kart", "kırmızı kart", "rövanş", "fikstür", "gol kralı", "asist", "smaç", "blok", "manşet", "servis", "ribaund", "üçlük", "nakavt", "raund", "madalya", "altın madalya", "gümüş madalya", "bronz madalya", "şampiyonluk yarışı"],
    "low": ["maç", "karşılaşma", "müsabaka", "turnuva", "lig", "sezon", "şampiyon", "kupa", "galibiyet", "mağlubiyet", "beraberlik", "skor", "puan", "rekor", "performans", "kadro", "transfer", "sözleşme", "taraftar", "tribün", "takım", "kulüp", "antrenör", "hakem", "oyuncu", "sakatlandı", "ceza aldı", "finale çıktı", "kazandı", "kaybetti", "antrenman"]
}
ECONOMY_KEYWORDS = {
    "high": ["vergi", "bütçe", "açık", "cari açık", "enflasyon", "faiz", "zam", "maaş", "bist 100", "bist 30", "borsa istanbul", "viop", "spk", "kap", "halka arz", "temettü", "nasdaq", "dow jones", "s&p 500", "fed", "powell", "ecb", "imf", "dünya bankası", "merkez bankası", "tcmb", "bitcoin", "btc", "ethereum", "eth", "kripto", "blockchain", "binance", "coinbase", "gram altın", "çeyrek altın", "ons altın", "döviz", "dolar/tl", "euro/tl", "brent petrol", "akaryakıt", "benzin", "motorin", "tüpraş", "thy", "aselsan", "ereğli", "kardemir", "sasa", "hektaş", "banka", "kredi"],
    "medium": ["tüfe", "üfe", "politika faizi", "kur korumalı", "sterlin", "dış ticaret", "ihracat", "ithalat", "gsyh", "büyüme rakamları", "işsizlik oranı", "istihdam", "konut satışları", "kredi notu", "hazine", "kdv", "ötv", "stopaj", "matrah", "fiyat artışı"],
    "low": ["fiyat", "artış", "düşüş", "rekor", "satış", "alış", "yatırım", "tasarruf", "borç", "şirket", "piyasa", "analiz", "beklenti", "hedef", "kar", "zarar", "maliyet", "ücret", "asgari ücret", "emekli", "memur"]
}
TECHNOLOGY_KEYWORDS = {
    "high": ["apple", "google", "microsoft", "amazon", "meta", "facebook", "twitter", "x", "instagram", "tiktok", "openai", "chatgpt", "gemini", "nvidia", "intel", "amd", "samsung", "huawei", "xiaomi", "sony", "tesla", "spacex", "nasa", "tübitak", "aselsan", "baykar", "tusaş", "yapay zeka", "ai", "machine learning", "siber güvenlik", "hacker", "bulut bilişim", "5g", "6g", "uydu", "uzay", "mars", "roket", "astronot", "algoritma", "kodlama"],
    "medium": ["yazılım", "donanım", "işletim sistemi", "android", "ios", "windows", "linux", "macos", "uygulama", "app", "akıllı telefon", "tablet", "laptop", "bilgisayar", "konsol", "playstation", "xbox", "video oyunu", "espor", "işlemci", "ram", "ekran kartı", "batarya", "piksel", "güncelleme", "sürüm", "beta", "elektrikli araç", "otonom", "robot", "drone"],
    "low": ["cihaz", "teknoloji", "dijital", "sanal", "platform", "şifre", "bağlantı", "hız", "ekran", "butona", "tıkla", "indir", "yükle"]
}
POLITICS_KEYWORDS = {
    "high": ["cumhurbaşkanı", "başکان", "erdoğan", "özgür özel", "bahçeli", "imamoğlu", "mansur yavaş", "ak parti", "akp", "chp", "mhp", "iyi parti", "dem parti", "tbmm", "meclis", "parlamento", "bakan", "bakanlığı", "kabine", "hükümet", "muhalefet", "iktidar", "seçim", "sandık", "oy", "ysk", "anayasa", "kararname", "resmi gazete", "nato", "bm", "birleşmiş milletler", "ab", "avrupa birliği", "biden", "trump", "putin", "zelenskiy", "diplomasi", "dışişleri", "içişleri", "mgk", "milli güvenlik kurulu", "belediye", "belediye başkanı", "yerel yönetim", "kayyum"],
    "medium": ["miting", "aday", "ittifak", "genel başkan", "grup toplantısı", "önerge", "yasa", "kanun", "teklif", "komisyon", "büyükelçi", "konsolos", "zirve", "görüşme", "temas", "heyet", "sözcü", "parti", "tüzük", "kurultay", "kongre", "referandum"],
    "low": ["açıklama", "toplantı", "karar", "kriz", "gündem", "lider", "ziyaret", "mesaj", "çağrı", "tepki", "eleştiri", "destek", "protesto"]
}
ART_KEYWORDS = {
    "high": ["sinema", "film", "dizi", "tiyatro", "konser", "festival", "sergi", "müze", "sanat", "kültür", "edebiyat", "kitap", "yazar", "şair", "ressam", "heykeltraş", "oyuncu", "aktris", "aktör", "şarkıcı", "müzisyen", "albüm", "şarkı", "klip", "single", "netflix", "disney", "blutv", "exxen", "altın kelebek", "oscar", "emmy", "grammy", "cannes", "altın portakal", "acun ılıcalı", "tarkan", "sezen aksu", "cem yılmaz", "magazin", "ünlü", "dövme", "tattoo", "estetik", "fenomen", "sosyal medya"],
    "medium": ["vizyon", "gala", "sahne", "performans", "yönetmen", "senarist", "yapımcı", "başrol", "fragman", "bölüm", "sezon finali", "reyting", "dedikodu", "aşk", "ayrılık", "evlilik", "boşanma", "konser takvimi", "bilet", "gişe", "paylaşım", "takipçi"],
    "low": ["izle", "dinle", "eğlence", "şov", "yıldız", "popüler", "trend", "moda", "tarz", "stil", "kırmızı halı"]
}

NEGATIVE_KEYWORDS = {
    "political_dominance": {
        "dominant_category": "Siyaset", 
        "keywords": ["resmi gazete", "kararname", "kanun teklifi", "anayasa mahkemesi", "tbmm genel kurulu", "yargıtay", "danıştay"],
        "penalty": -40, "affects": ["Spor", "Sanat", "Teknoloji", "Gündem"],
        "soft_penalty": -15, "soft_affects": ["Ekonomi"] 
    },
    "sports_dominance": {
        "dominant_category": "Spor", 
        "keywords": ["maç sonucu", "puan durumu", "fikstür", "gol kralı", "sarı kart", "kırmızı kart", "teknik direktör"],
        "penalty": -40, "affects": ["Siyaset", "Ekonomi", "Teknoloji", "Sanat"],
        "soft_penalty": -10, "soft_affects": ["Gündem"] 
    },
    "economic_dominance": {
        "dominant_category": "Ekonomi", 
        "keywords": ["borsa istanbul", "bist 100", "faiz kararı", "enflasyon raporu", "döviz kurları", "çeyrek altın"],
        "penalty": -30, "affects": ["Spor", "Sanat", "Gündem"],
        "soft_penalty": -10, "soft_affects": ["Siyaset", "Teknoloji"] 
    }
}

# ==========================================
# HELPER FUNCTIONS
# ==========================================
def normalize_turkish(text: str) -> str:
    text = text.replace('İ', 'i').replace('I', 'ı').replace('Ğ', 'ğ').replace('Ü', 'ü').replace('Ş', 'ş').replace('Ö', 'ö').replace('Ç', 'ç')
    return text.lower()

def calculate_score(text: str, keywords_dict: dict) -> int:
    text = normalize_turkish(text)
    score = 0
    for word in keywords_dict["high"]:
        if word in text:
            score += 50
            break 
    medium_hits = 0
    for word in keywords_dict["medium"]:
        if word in text:
            score += 15
            medium_hits += 1
            if medium_hits >= 2: break
    for word in keywords_dict["low"]:
        if re.search(r'\b' + re.escape(word) + r'\b', text): score += 5
    return score

def apply_negative_penalties(text: str, scores: dict) -> dict:
    text = normalize_turkish(text)
    text = re.sub(r'\s+', ' ', text)
    current_winner = max(scores, key=scores.get) if scores else None
    
    for group, config in NEGATIVE_KEYWORDS.items():
        if config.get("dominant_category") == current_winner: continue
        keywords = config["keywords"]
        hard_penalty = config["penalty"]
        hard_affects = config["affects"]
        soft_penalty = config.get("soft_penalty", -10)
        soft_affects = config.get("soft_affects", [])
        
        found = False
        for word in keywords:
            if re.search(r'\b' + re.escape(word) + r'\b', text):
                found = True
                break 
        
        if found:
            for cat in hard_affects:
                if cat in scores: scores[cat] = max(0, scores[cat] + hard_penalty)
            for cat in soft_affects:
                if cat in scores: scores[cat] = max(0, scores[cat] + soft_penalty)
    return scores

def decide_final_category(ai_category: str, scores: dict) -> str:
    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    top_cat, top_score = sorted_scores[0]
    
    override_threshold = 70 if top_cat == "Ekonomi" else 60

    if scores.get(ai_category, 0) < 15 and top_score > override_threshold:
        print(f"   🛡️ GUARD: AI chose '{ai_category}' (Low Score) but keywords strongly suggest '{top_cat}' (Score: {top_score}). Overriding.")
        return top_cat, True 

    return ai_category, False

def generate_unique_slug(db, base_title, trend_id):
    """
    تولید اسلاگ یکتا با بررسی در دیتابیس
    """
    base_slug = slugify_turkish(base_title)
    unique_slug = base_slug
    counter = 1
    
    while True:
        # بررسی اینکه آیا این اسلاگ توسط رکورد دیگری (غیر از خودِ این رکورد) اشغال شده یا خیر
        existing = db.query(Trend).filter(Trend.slug == unique_slug, Trend.id != trend_id).first()
        if not existing:
            return unique_slug
        
        unique_slug = f"{base_slug}-{counter}"
        counter += 1

# ==========================================
# GEMINI LOGIC
# ==========================================
def generate_summary_with_gemini(text_cluster, scores_context):
    if not client or not MODEL_NAME:
        return None, 0, 0, 0 

    prompt = f"""
    You are a professional Turkish news editor. Analyze the following raw text data.
    
    SYSTEM SCORES (For Context Only):
    {scores_context}

    CRITICAL RULES FOR CATEGORIZATION (Reasoning Required):
    1. **Ekonomi**: Currency, Stock Market, Inflation, Taxes, Corporate Finance. (NOT Building collapses/Physical damage).
    2. **Teknoloji**: Software, AI, Hardware, Space, Cyber Security. (NOT Social Media Celebrities/Influencers/Tattoos).
    3. **Sanat/Magazin**: Celebrities, Tattoos, Singers, Movies, Social Media Trends.
    4. **Gündem/Yaşam**: Accidents, Earthquakes, Weather, Local News, Building Collapses, Animal Attacks.

    INSTRUCTION:
    First, think step-by-step about WHY this news belongs to a category. Write this in 'category_reasoning'.
    Then, select the final 'category'.

    OUTPUT FORMAT: JSON
    {{
        "detected_language": "TR", 
        "is_relevant_to_turkey": true, 
        "headline": "City Name: Short Catchy Headline",
        "summary": "Neutral summary...",
        "category_reasoning": "Explain why you chose the category here...",
        "category": "Chosen Category"
    }}

    TEXT TO ANALYZE:
    {text_cluster}
    """

    max_retries = 3
    base_delay = 1 

    for attempt in range(max_retries):
        try:
            req_start = time.time()
            
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type='application/json',
                    temperature=0.1,
                    max_output_tokens=500,
                )
            )
            
            req_end = time.time()
            duration = req_end - req_start
            
            in_tok = 0
            out_tok = 0
            if response.usage_metadata:
                in_tok = response.usage_metadata.prompt_token_count
                out_tok = response.usage_metadata.candidates_token_count
                print(f"   🎫 Tokens: Input={in_tok}, Output={out_tok} | Time: {duration:.2f}s | Model: {MODEL_NAME}")
            else:
                print(f"   🏁 Request finished in {duration:.2f}s")
            
            result = json.loads(response.text)
            
            if isinstance(result, list):
                if result and isinstance(result[0], dict):
                    return result[0], in_tok, out_tok, duration
                return None, in_tok, out_tok, duration
                
            return result, in_tok, out_tok, duration

        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                print(f"   ⏳ Rate Limit Hit (429). Sleeping {base_delay}s... (Attempt {attempt+1}/{max_retries})")
                time.sleep(base_delay)
                base_delay *= 2 
                continue 
            else:
                print(f"   ❌ Gemini Error: {e}")
                return None, 0, 0, 0
    
    print("   ❌ Failed after retries.")
    return None, 0, 0, 0

def process_pending_trends():
    db = SessionLocal()
    try:
        pending_trends = db.query(Trend).filter(
            (Trend.summary == None) | (Trend.summary == ""),
            Trend.message_count >= 1 
        ).order_by(desc(Trend.last_updated)).limit(10).all()

        if not pending_trends: return False

        print(f"✍️  Processing {len(pending_trends)} trends with {MODEL_NAME}...")

        for trend in pending_trends:
            time.sleep(0.1) 
            
            news_items = db.query(RawNews).filter(RawNews.trend_id == trend.id).limit(10).all()
            if not news_items: continue

            combined_text = ""
            for n in news_items:
                clean_content = n.content.replace("\n", " ").strip()
                combined_text += f"- {clean_content[:800]}\n"

            scores = {
                "Spor": calculate_score(combined_text, SPORTS_KEYWORDS),
                "Ekonomi": calculate_score(combined_text, ECONOMY_KEYWORDS),
                "Teknoloji": calculate_score(combined_text, TECHNOLOGY_KEYWORDS),
                "Siyaset": calculate_score(combined_text, POLITICS_KEYWORDS),
                "Sanat": calculate_score(combined_text, ART_KEYWORDS)
            }
            scores = apply_negative_penalties(combined_text, scores)
            
            top_score = max(scores.values())
            
            scores_str = "\n".join([f"- {k}: {v}" for k, v in scores.items()])
            system_pre_analysis = f"ÖN ANALİZ (SİSTEM PUANLARI):\n{scores_str}"

            print(f"   📊 Trend {trend.id}: Top Score={top_score}")

            ai_result, in_tok, out_tok, duration = generate_summary_with_gemini(combined_text, system_pre_analysis)
            
            status = "Success"
            
            if ai_result:
                is_relevant = ai_result.get("is_relevant_to_turkey", True)
                detected_lang = ai_result.get("detected_language", "EN").upper()
                ai_category = ai_result.get("category", "Gündem")
                
                final_category, overridden = decide_final_category(ai_category, scores)
                
                if overridden:
                    status = "Guard Override"

                if final_category == "Teknoloji":
                    text_check = normalize_turkish(combined_text)
                    tech_terms = ["yazılım", "yapay zeka", "dijital", "siber", "platform", "uygulama", "algoritma", "kodlama", "internet", "veri", "bilişim", "inovasyon", "apple", "google", "microsoft", "tesla", "togg", "baykar", "aselsan", "robot", "otomasyon", "uzay", "uydu", "kripto", "blockchain", "teknoloji", "cihaz", "telefon", "bilgisayar"]
                    has_strong_tech = any(re.search(r'\b' + re.escape(w) + r'\b', text_check) for w in tech_terms)
                    if not has_strong_tech:
                        if ai_category == "Sanat":
                            final_category = "Sanat"
                        else:
                            final_category = "Gündem"
                        status = "Tech Guard"

                if trend.score > 20: is_relevant = True
                if "TR" in detected_lang: is_relevant = True

                if is_relevant:
                    raw_title = ai_result.get("headline", trend.title)
                    if raw_title and len(raw_title) > 250:
                        raw_title = raw_title[:250] + "..."
                    
                    trend.title = raw_title
                    trend.summary = ai_result.get("summary", "")
                    trend.category = final_category 
                    
                    # --- بخش سئو: تولید اسلاگ یکتا ---
                    trend.slug = generate_unique_slug(db, trend.title, trend.id)
                    
                    trend.last_updated = datetime.now(timezone.utc).replace(tzinfo=None)

                    # --- منطق پنالتی برای اخبار فال ---
                    title_norm = normalize_turkish(trend.title)
                    if any(word in title_norm for word in JUNK_KEYWORDS):
                        print(f"   ⚠️ Junk Content Detected ({trend.title}). Limiting score to 10.")
                        trend.score = min(trend.score, 10)

                    print(f"   ✅ Summarized: [{trend.category}] {trend.title}")
                    
                    try:
                        # LOGGING
                        log_to_csv(trend.id, MODEL_NAME, in_tok, out_tok, duration, final_category, status)
                        db.commit()

                        # --- سئو: اطلاع‌رسانی آنی به گوگل بر اساس حد نصاب امتیاز ---
                        if trend.slug:
                            if trend.score >= GOOGLE_INDEXING_THRESHOLD:
                                target_url = f"{BASE_SITE_URL}/trend/{trend.slug}"
                                success, err_msg = notify_google(target_url)
                                if success:
                                    print(f"   🚀 Google Indexing API: Success for {trend.slug}")
                                else:
                                    print(f"   ⚠️ Google Indexing API Error for {trend.slug}: {err_msg}")
                            else:
                                print(f"   💤 Indexing Skipped (Score {trend.score} < {GOOGLE_INDEXING_THRESHOLD})")
                                
                    except Exception as e:
                        db.rollback()
                        print(f"   ❌ DB Commit Error for Trend {trend.id}: {e}")
                        continue
                    
                else:
                    trend.is_active = False
                    trend.summary = "Filtered."
                    print(f"   🗑️  Filtered Out.")
                    log_to_csv(trend.id, MODEL_NAME, in_tok, out_tok, duration, "Filtered", "Irrelevant")
                    try:
                        db.commit()
                    except:
                        db.rollback()
                
            else:
                # --- FALLBACK ---
                print(f"   ⚠️ AI Failed for Trend {trend.id}. Setting fallback.")
                fallback_category = max(scores, key=scores.get) if top_score >= 20 else "Gündem"
                first_news = news_items[0].content.strip()
                fallback_summary = ' '.join(first_news.split()[:40]) + "..."
                
                trend.summary = fallback_summary
                trend.title = trend.title or fallback_summary[:100]
                trend.category = fallback_category
                trend.last_updated = datetime.now(timezone.utc).replace(tzinfo=None)
                
                # تولید اسلاگ برای حالت فال‌بک
                trend.slug = generate_unique_slug(db, trend.title, trend.id)
                
                print(f"   🔰 Fallback Applied: [{fallback_category}]")
                log_to_csv(trend.id, MODEL_NAME, 0, 0, 0, fallback_category, "Fallback (AI Fail)")
                try:
                    db.commit()
                except:
                    db.rollback()

        return True
    finally:
        db.close()

def main():
    print("🤖 Cloud AI Worker Starting (Turbo Mode + CSV Logging + SEO Indexing)...")
    while True:
        try:
            did_work = process_pending_trends()
            sleep_time = 1 if did_work else 10
            if not did_work: print("💤 Waiting...", end='\r')
            time.sleep(sleep_time)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"❌ Global Worker Error: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()
