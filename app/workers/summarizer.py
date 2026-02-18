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
from sqlalchemy import desc, or_, and_, func
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
    "high": ["futbol", "beşiktaş", "fenerbahçe", "galatasaray", "trabzonspor", "samsunspor", "antalyaspor", "kasımpaşa", "başakşehir", "sivasspor", "adana demirspor", "konyaspor", "alanyaspor", "göztepe", "eyüpspor", "bodrum fk", "rizespor", "uefa", "şampiyonlar ligi", "avrupa ligi", "konferans ligi", "milli takım", "voleybol", "basketbol", "derbi", "puan durumu", "teknik direktör", "transfer haberi", "bonservis", "euroleague", "nba", "wimbledon", "grand slam", "olimpiyat", "altın ayakkabı"],
    "medium": ["penaltı", "gol kralı", "kadro", "madalya", "şampiyon", "kupa", "sarı kart", "kırmızı kart", "ofsayt", "var incelemesi", "stadyum", "idman", "deplasman", "fikstür", "kura çekimi", "antrenör", "scout"],
    "low": ["maç", "skor", "takım", "kulüp", "hakem", "oyuncu", "antrenman", "karşılaşma", "lig"]
}

ECONOMY_KEYWORDS = {
    "high": ["enflasyon", "tcmb", "merkez bankası", "faiz kararı", "borsa istanbul", "bist 100", "dolar/tl", "euro/tl", "akaryakıt", "halka arz", "asgari ücret", "emekli zammı", "vergi artışı", "cari açık", "gsyh", "kripto para", "bitcoin", "ethereum", "moody's", "fitch", "s&p"],
    "medium": ["tüfe", "üfe", "ihracat", "ithalat", "kredi notu", "mevduat", "swap", "altın fiyatları", "temettü", "kap bildirimi", "resesyon", "konkordato", "vergi paketi", "bütçe", "alım gücü"],
    "low": ["fiyat", "artış", "yatırım", "borç", "şirket", "piyasa", "kar", "zarar", "maliyet", "tüketici", "zam", "maas"]
}

TECHNOLOGY_KEYWORDS = {
    "high": ["yapay zeka", "ai", "openai", "chatgpt", "deepfake", "siber güvenlik", "baykar", "tusaş", "aselsan", "savunma sanayii", "togg", "iha", "siha", "uzay", "roket", "spacex", "starlink", "kuantum", "yarı iletken", "yazılım güncellemesi"],
    "medium": ["apple", "google", "microsoft", "tesla", "meta", "instagram", "tiktok", "x twitter", "threads", "5g", "6g", "bulut bilişim", "robot", "drone", "uygulama", "blockchain", "donanım", "çip krizi", "siber saldırı", "veri sızıntısı"],
    "low": ["dijital", "internet", "platform", "fiber", "akıllı telefon", "işlemci", "otonom"]
}

POLITICS_KEYWORDS = {
    "high": ["cumhurbaşkanı", "erdoğan", "özgür özel", "bahçeli", "imamoğlu", "mansur yavaş", "ak parti", "chp", "mhp", "dem partisi", "iyi parti", "zafer partisi", "tbmm", "meclis", "kabine", "ysk", "anayasa", "beyaz saray", "kremlin", "pentagon"],
    "medium": ["seçim anketi", "erken seçim", "ittifak", "yasa", "kanun", "zirve", "diplomasi", "nato", "bm", "birleşmiş milletler", "istifa", "gözaltı", "tutuklama", "önerge", "koalisyon", "gensoru", "torba yasa"],
    "low": ["açıklama", "toplantı", "karar", "bakanlık", "lider", "tepki", "eleştiri", "ziyaret", "gündem"]
}

ART_KEYWORDS = {
    "high": ["sinema", "film", "dizi", "konser", "festival", "sergi", "kitap", "yazar", "albüm", "tarkan", "sezen aksu", "cem yılmaz", "oscar", "altın portakal", "cannes", "bienal", "netflix", "disney+", "bluetv"],
    "medium": ["vizyon", "gala", "sahne", "yönetmen", "fragman", "reyting", "aşk", "ayrılık", "boşanma", "evlilik", "fenomen", "influencer", "gişe rekoru", "tiyatro", "prömiyer"],
    "low": ["izle", "dinle", "eğlence", "magazin", "ünlü", "moda", "tarz", "viral", "ödül töreni"]
}

GUNDEM_KEYWORDS = {
    "high": ["deprem", "yangın", "sel", "cinayet", "kaza", "patlama", "afad", "polis", "jandarma", "meteoroloji", "şiddetli fırtına", "son dakika", "flaş haber", "acil durum"],
    "medium": ["vefat", "kayıp", "arama kurtarma", "trafik kazası", "gözaltı", "adliye", "asayiş", "uyarı", "sağanak", "ağır ceza", "müebbet", "dolandırıcılık", "gasp", "hırsızlık"],
    "low": ["haber", "olay", "hava durumu", "sıcaklık", "belediye", "valilik", "hizmet", "duyuru", "trafik yoğunluğu"]
}

# Cross-category penalties for classification refinement
NEGATIVE_KEYWORDS = {
    "political_vs_sports": {
        "dominant_category": "Spor",
        "keywords": ["galatasaray", "fenerbahçe", "beşiktaş", "trabzonspor", "süper lig", "maç", "gol", "transfer"],
        "penalty": -100, "affects": ["Siyaset"]
    },
    "political_vs_accident": {
        "dominant_category": "Gündem",
        "keywords": ["deprem", "yangın", "sel", "kaza", "can kaybı", "patlama"],
        "penalty": -80, "affects": ["Siyaset", "Ekonomi"]
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

def decide_final_category(ai_category: str, text: str) -> tuple:
    """
    Layer 4: Density-Based Categorization & Safety Guard.
    Calculates Keyword Density (Score / Word Count) * 100 to prevent noise-based misclassification.
    """
    # 1. Calculate Word Count
    words = text.split()
    word_count = len(words) if len(words) > 0 else 1

    # 2. Calculate Raw Scores
    scores = {
        "Spor": calculate_keyword_score(text, SPORTS_KEYWORDS),
        "Ekonomi": calculate_keyword_score(text, ECONOMY_KEYWORDS),
        "Teknoloji": calculate_keyword_score(text, TECHNOLOGY_KEYWORDS),
        "Siyaset": calculate_keyword_score(text, POLITICS_KEYWORDS),
        "Sanat": calculate_keyword_score(text, ART_KEYWORDS),
        "Gündem": calculate_keyword_score(text, GUNDEM_KEYWORDS)
    }
    
    # 3. Apply Negative Logic
    scores = apply_negative_logic(scores, text)
    
    # 4. Calculate Density Scores (Scaled by 100)
    density_scores = {k: (v / word_count) * 100 for k, v in scores.items()}
    
    top_cat = max(density_scores, key=density_scores.get)
    top_density = density_scores[top_cat]
    gundem_density = density_scores["Gündem"]

    # Map for high-keyword check
    cat_keywords = {
        "Spor": SPORTS_KEYWORDS,
        "Ekonomi": ECONOMY_KEYWORDS,
        "Teknoloji": TECHNOLOGY_KEYWORDS,
        "Siyaset": POLITICS_KEYWORDS,
        "Sanat": ART_KEYWORDS,
        "Gündem": GUNDEM_KEYWORDS
    }

    # 5. Gündem Safety Rule
    # If the top category isn't Gündem, it must be significantly denser (1.8x) than Gündem
    if top_cat != "Gündem":
        # Rule A: Density Threshold (1.8x)
        if top_density < (1.8 * gundem_density):
            return "Gündem", True
            
        # Rule B: High-Weight Keyword Count (< 2 matches forces Gündem)
        text_norm = normalize_turkish_local(text)
        kw_config = cat_keywords.get(top_cat)
        if kw_config:
            high_matches = sum(1 for w in kw_config["high"] if w in text_norm)
            if high_matches < 2:
                return "Gündem", True

    # 6. AI Validation
    if ai_category == top_cat:
        return top_cat, False
        
    # If AI disagrees, check if AI's choice has reasonable density
    # Thresholds adjusted for *100 scale: 0.1 -> 10, 0.5 -> 50
    if density_scores.get(ai_category, 0) < 10 and top_density > 50:
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
    ### SYSTEM ROLE
    You are a "Professional News Editor, Semantic Gatekeeper, and SEO Specialist". Your goal is to filter out content poisoning, extract the single true story, and optimize metadata for search engines.
    
    ### INTERNAL PROCESS (Follow these steps before generating output)
    1. SCRUTINIZE: Analyze the provided RAW TEXT DATA below. Identify and mentally discard:
       - Advertisements (betting, bonuses, sales).
       - "Read more" links or navigational text.
       - "Related News" snippets that discuss a completely different topic.
    
    2. IDENTIFY CORE EVENT: Find the "Main Event" that is consistent across the majority of source snippets.
    
    3. LOGICAL CAUSALITY (Layer 5: Self-Correction): 
       - Verify cause-and-effect relationships (e.g., "Surgery was performed due to injury", NOT "Injury occurred due to surgery").
       - Perform a brief internal fact-check to ensure the summary logically follows the consensus of the sources.

    4. SEO EXTRACTION:
       - Extract relevant tags (keywords) for search indexing.
       - Identify structured entities (People, Locations, Organizations).

    5. CONFLICT RESOLUTION (Layer 6):
       - Compare numerical data (dates, death tolls, prices, percentages) across all input sources.
       - If sources provide contradicting facts or numbers, DO NOT pick one at random.
       - Report the discrepancy in the summary using phrases like "Sources report varying figures between X and Y" or "While some sources claim X, others report Y".
       - Ensure the summary reflects the consensus of Tier 1 sources but acknowledges minority reports if they are significant.

    ### EVOLUTIONARY CONTEXT:
    The provided text may contain updates to an older story. If the new data contains a definitive outcome, final score, or major update that makes the previous context obsolete, overwrite the previous headline and summary with the most recent and important 'Core Event'.

    ### CONSTRAINTS
    - Language: Turkish (TR) only.
    - Style: Strictly professional, neutral, and journalistic. No clickbait.
    - Category Accuracy: Determine the category ONLY based on the "Core Event".
    - Headline: Catchy, SEO-optimized, and factually accurate.
    - Category List: [Siyaset, Ekonomi, Gündem, Spor, Teknoloji, Sanat].

    ### OUTPUT FORMAT (JSON ONLY)
    {{
        "headline": "...",
        "summary": "...",
        "category": "...",
        "fact_check": "Brief validation of logic...",
        "tags": ["tag1", "tag2"],
        "entities": {{"people": [], "locations": [], "organizations": []}},
        "has_conflicting_data": false,
        "conflict_details": "Description of conflict if any...",
        "is_relevant_to_turkey": true
    }}

    ### RAW TEXT DATA
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

# ==========================================
# FIXED LOGIC: Smart Action Filtering
# ==========================================

def process_pending_trends():
    """
    Fetches high-TPS trends, processes them with AI if needed, or publishes them if ready.
    Uses OR logic to capture both unsummarized trends AND unpublished high-score trends.
    """
    db = SessionLocal()
    try:
        # 1. Fetch current publishing threshold
        threshold_setting = db.query(SystemSettings).filter(SystemSettings.key == "auto_publish_threshold").first()
        publish_threshold = float(threshold_setting.value) if threshold_setting else 35.0

        # 2. Define Action Conditions
        # A) Needs Content: High score (>20) but no summary
        condition_needs_summary = and_(
            Trend.final_tps >= 20,
            or_(Trend.summary == None, Trend.summary == "")
        )
        
        # B) Needs Update: Significant new messages since last summary (Event Evolution)
        condition_needs_update = and_(
            Trend.final_tps >= 20,
            Trend.summary != None,
            (Trend.message_count - func.coalesce(Trend.last_summary_msg_count, 0)) >= 5
        )
        
        # C) Needs Publishing: High score (>Threshold) but not yet published to Telegram
        condition_needs_publish = and_(
            Trend.final_tps >= publish_threshold,
            Trend.is_published == False
        )

        # 3. Query with OR logic
        pending_trends = db.query(Trend).filter(
            Trend.is_active == True,
            or_(condition_needs_summary, condition_needs_update, condition_needs_publish)
        ).order_by(desc(Trend.final_tps)).limit(5).all()

        if not pending_trends: return False

        print(f"✍️  Processing {len(pending_trends)} Actionable Trends...")

        for trend in pending_trends:
            # --- Phase 1: Content Generation (If summary missing OR needs update) ---
            needs_content = False
            if not trend.summary:
                needs_content = True
            elif (trend.message_count - (trend.last_summary_msg_count or 0)) >= 5:
                needs_content = True

            if needs_content:
                print(f"   🧠 Generating/Updating summary for: {trend.title[:30]}...")
                
                # Use latest 7 news items for consensus (Layer 2) to prioritize recent updates
                news_items = db.query(RawNews).filter(RawNews.trend_id == trend.id).order_by(desc(RawNews.published_at)).limit(7).all()
                if not news_items: continue

                # --- Layer 2: Semantic Intersection Heuristic ---
                if len(news_items) > 1:
                    # 1. Build Global Frequency Map
                    word_doc_freq = {}
                    for n in news_items:
                        words = set(normalize_turkish_local(n.content[:500]).split())
                        for w in words:
                            if len(w) > 3: word_doc_freq[w] = word_doc_freq.get(w, 0) + 1
                    
                    # 2. Identify Consensus Vocabulary (> 50% of sources)
                    threshold = len(news_items) / 2
                    consensus_vocab = {w for w, count in word_doc_freq.items() if count >= threshold}
                    
                    # 3. Filter Paragraphs
                    filtered_lines = []
                    for n in news_items:
                        for p in n.content[:500].split('.'): # Split by sentences for better granularity
                            p = p.strip()
                            if len(p) < 40: continue
                            overlap = len(set(normalize_turkish_local(p).split()).intersection(consensus_vocab))
                            if overlap >= 2: filtered_lines.append(f"- {p}")
                    
                    cluster_text = "\n".join(filtered_lines) if len(filtered_lines) > 0 else "\n".join([f"- {n.content[:500]}" for n in news_items])
                else:
                    cluster_text = "\n".join([f"- {n.content[:500]}" for n in news_items])

                ai_result, in_tok, out_tok, duration = generate_summary_with_gemini(cluster_text)
                
                if ai_result and ai_result.get("is_relevant_to_turkey", True):
                    ai_cat = ai_result.get("category", "Gündem")
                    
                    # Verify category through manual keyword analysis
                    final_category, overridden = decide_final_category(ai_cat, cluster_text)
                    
                    # Update Trend Record
                    trend.title = ai_result.get("headline", trend.title)
                    trend.summary = ai_result.get("summary", "")
                    trend.category = final_category 
                    trend.tags = ai_result.get("tags")
                    trend.entities = ai_result.get("entities")
                    
                    # Handle Conflict Data (Layer 6)
                    if ai_result.get("has_conflicting_data"):
                        print(f"   ⚠️ Conflict Detected: {ai_result.get('conflict_details')}")
                    
                    # SEO CRITICAL: Only set slug if it's missing (Preserve original slug during updates)
                    if not trend.slug:
                        trend.slug = generate_unique_slug(db, trend.title, trend.id)
                    
                    trend.last_updated = datetime.now(timezone.utc).replace(tzinfo=None)
                    trend.last_summary_msg_count = trend.message_count

                    print(f"   ✅ Summarized/Updated: [{trend.category}] {trend.title} (TPS: {trend.final_tps:.1f})")
                    if not trend.slug: print(f"   🚀 SEO Slug: /trend/{trend.slug}")
                    
                    # Save and Log Stats
                    log_to_csv(trend.id, MODEL_NAME, in_tok, out_tok, duration, trend.category, "Success")
                    db.commit() # Save content immediately
                else:
                    # Mark irrelevant or failed content as inactive
                    trend.is_active = False 
                    db.commit()
                    print(f"   🗑️  Discarded Trend {trend.id} (Irrelevant Content)")
                    continue

            # --- Phase 2: Publishing (If score met & unpublished) ---
            # Check logic again in case TPS changed or threshold wasn't met in previous loop
            if trend.final_tps >= publish_threshold and not trend.is_published and trend.summary:
                target_url = f"{BASE_SITE_URL}/trend/{trend.slug}"
                
                # Call the service and check return value
                success = alert_service.publish_to_channel(
                    title=trend.title,
                    summary=trend.summary,
                    category=trend.category,
                    url=target_url,
                    image_path=trend.cover_image
                )
                
                if success:
                    trend.is_published = True  # CRITICAL: Mark as published to stop loop
                    db.commit() # Commit state change immediately
                    print(f"   📢 PUBLISHED to Telegram: {trend.title}")
                else:
                    print(f"   ⚠️ Publish failed, will retry next cycle.")

                # Notify Google for instant indexing
                if trend.final_tps >= GOOGLE_INDEXING_THRESHOLD:
                    notify_google(target_url)

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