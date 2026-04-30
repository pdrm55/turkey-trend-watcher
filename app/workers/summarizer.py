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
from app.core.classifier import decide_final_category, normalize_text, CAT_MAP

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

def generate_summary_with_gemini(text_cluster, is_umbrella=False, old_title=None):
    """Executes the summarization call to the Gemini API with structured response"""
    if not client or not MODEL_NAME: return None, 0, 0, 0 

    umbrella_instruction = ""
    if is_umbrella and old_title:
        umbrella_instruction = f'### UMBRELLA UPDATE MODE ACTIVATED\nThis is a highly evolving story. The PREVIOUS headline was: "{old_title}". Your task is to create an "Umbrella Title" (تیتر چتری) and comprehensive summary. You MUST combine the core original event WITH the new developments/reactions. Format example: "[Core Event]: [New Developments]".'

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
       - Generate a 2 to 3 word 'image_search_query' representing the main visual subject of the news, highly optimized for Bing/Google Image search (e.g., 'Ali Yerlikaya', 'Galatasaray transfer').

    5. CONFLICT RESOLUTION (Layer 6):
       - Compare numerical data (dates, death tolls, prices, percentages) across all input sources.
       - If sources provide contradicting facts or numbers, DO NOT pick one at random.
       - Report the discrepancy in the summary using phrases like "Sources report varying figures between X and Y" or "While some sources claim X, others report Y".
       - Ensure the summary reflects the consensus of Tier 1 sources but acknowledges minority reports if they are significant.

    6. CATEGORY AUDIT:
       - Analyze the core news event. Compare it strictly against [Siyaset, Ekonomi, Spor, Teknoloji, Sanat, Gündem].
       - Provide the final category AND a new field "category_reasoning" explaining your choice.
       - Special focus on TRAPS: If the news is about government budget, retirement, or state infrastructure (DSİ/TOKİ), it is NOT Spor. justify this in 'category_reasoning'.

    {umbrella_instruction}

    ### EVOLUTIONARY CONTEXT:
    The provided text may contain updates to an older story. If the new data contains a definitive outcome, final score, or major update that makes the previous context obsolete, overwrite the previous headline and summary with the most recent and important 'Core Event'.

    ### SMART FORMATTING RULES (MARKDOWN):
    - You MUST return the 'summary' field in raw Markdown.
    - Structure:
        1. Start with `### ⚡ Özet` and provide 3 key takeaways as bullet points.
        2. Use `###` for logical sub-headings (e.g., "Olayın Özeti", "Arka Plan").
        3. Keep paragraphs strictly short (maximum 2-3 sentences per block) for mobile readability.
        4. GEO DATA (Statistics): If the raw text contains specific numbers, percentages, or financial data, extract them into a section: `### 📊 Önemli İstatistikler`.
        5. GEO DATA (Quotes): If the text contains expert opinions or official statements, create a section: `### 💬 Uzman Görüşleri`. Format them precisely as: `> **[Name, Title/Organization]:** "[Direct Quote]"`
        6. Conclude with a final section: `### 🤖 Yapay Zeka Analizi`. Under this heading, you MUST write exactly 1-2 sentences explaining the deeper context, why this news matters, or its potential future impact.
        7. GEO DATA (Citations): At the very end of the summary, add `### 🔗 Kaynaklar` and list the names of the news agencies, institutes, or sources mentioned in the raw text as a bulleted list (e.g., `- Reuters`, `- TÜİK`, `- AA`).
    - Styling:
        - Use `**bold**` for key entity names (people, organizations) and critical numbers/dates.
        - Use `> blockquotes` for official statements, direct quotes, or crucial announcements.

    ### TELEGRAM CAPTION RULES:
    - Generate a standalone summary specifically for the Telegram channel in the `telegram_caption` field.
    - Value Proposition: The reader MUST fully understand the core event and its importance WITHOUT needing to click the link. Provide a complete but condensed story.
    - Length: 2 to 3 short paragraphs (approx 100-150 words).
    - Format: Use emojis naturally. Use **bold** for names and key figures.
    - Restriction: DO NOT use Markdown headers like ###. Use a journalistic, engaging, and highly readable tone.

    ### CONSTRAINTS
    - Language: Turkish (TR) only.
    - Style: Strictly professional, neutral, and journalistic. No clickbait.
    - Category Accuracy: Determine the category ONLY based on the "Core Event".
    - Headline: Catchy, SEO-optimized, and factually accurate.
    - Category List: [Siyaset, Ekonomi, Gündem, Spor, Teknoloji, Sanat].

    ### OUTPUT FORMAT (JSON ONLY)
    {{
        "headline": "...",
        "summary": "Raw Markdown text here...",
        "telegram_caption": "...",
        "category": "...",
        "category_reasoning": "...",
        "fact_check": "Brief validation of logic...",
        "image_search_query": "...",
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
                max_output_tokens=8192,
            )
        )
        duration = time.time() - req_start
        
        meta = response.usage_metadata
        in_tok = meta.prompt_token_count if meta else 0
        out_tok = meta.candidates_token_count if meta else 0
        
        # --- Bulletproof JSON Parsing & Auto-Recovery ---
        raw_text = response.text.strip()
        
        # Strip markdown code blocks if AI hallucinated them despite mime_type
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        elif raw_text.startswith("```"):
            raw_text = raw_text[3:]
            
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]
            
        raw_text = raw_text.strip()
        
        # Fix trailing commas
        raw_text = re.sub(r',\s*}', '}', raw_text)
        raw_text = re.sub(r',\s*\]', ']', raw_text)

        # Auto-recovery for slight truncations
        if raw_text.startswith("{") and not raw_text.endswith("}"):
            raw_text += "}"
        elif raw_text.startswith("[") and not raw_text.endswith("]"):
            raw_text += "]"
            
        raw_result = json.loads(raw_text)
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
        # A) Needs Content: High score (>15) OR it's a Social Trend (X-Watcher), and no summary
        condition_needs_summary = and_(
            or_(Trend.final_tps >= 15, Trend.has_social_signal == True),
            or_(Trend.summary == None, Trend.summary == "")
        )
        
        # B) Needs Update: Significant new messages since last summary (Event Evolution)
        condition_needs_update = and_(
            Trend.final_tps >= 15,
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

            is_umbrella = False
            time_alive_hours = 0
            if trend.first_seen:
                time_alive_hours = (datetime.now(timezone.utc).replace(tzinfo=None) - trend.first_seen).total_seconds() / 3600.0
            
            new_msg_count = trend.message_count - (trend.last_summary_msg_count or 0)
            if time_alive_hours >= 12.0 and new_msg_count >= 15:
                is_umbrella = True

            if needs_content:
                print(f"   🧠 Generating/Updating summary for: {trend.title[:30]}...")
                
                # Use latest 10 news items for consensus (Layer 2) to prioritize recent updates
                news_items = db.query(RawNews).filter(RawNews.trend_id == trend.id).order_by(desc(RawNews.published_at)).limit(10).all()
                if not news_items: continue

                # --- Layer 2: Semantic Intersection Heuristic ---
                if len(news_items) > 1:
                    # 1. Build Global Frequency Map
                    word_doc_freq = {}
                    for n in news_items:
                        words = set(normalize_text(n.content[:500]).split())
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
                            overlap = len(set(normalize_text(p).split()).intersection(consensus_vocab))
                            if overlap >= 2: filtered_lines.append(f"- {p}")
                    
                    cluster_text = "\n".join(filtered_lines) if len(filtered_lines) > 0 else "\n".join([f"- {n.content[:500]}" for n in news_items])
                else:
                    cluster_text = "\n".join([f"- {n.content[:500]}" for n in news_items])

                ai_result, in_tok, out_tok, duration = generate_summary_with_gemini(cluster_text, is_umbrella=is_umbrella, old_title=trend.title)
                
                # NEW: Protect against 429 Rate Limit Errors
                if ai_result is None:
                    print(f"   ⏳ API Limit Reached for Trend {trend.id}. Pausing for 15 seconds...")
                    time.sleep(15) # Cooldown before next loop
                    continue # Skip without discarding the trend
                    
                # Add a natural anti-spam delay between successful API calls
                time.sleep(3)
                
                if ai_result.get("is_relevant_to_turkey", True):
                    ai_cat = ai_result.get("category", "Gündem")
                    
                    if "category_reasoning" in ai_result:
                        print(f"   🧠 AI Reasoning: {ai_result['category_reasoning']}")

                    # Verify category through manual keyword analysis
                    final_category, overridden = decide_final_category(ai_cat, cluster_text)
                    
                    # Update Trend Record
                    trend.title = ai_result.get("headline", trend.title)
                    
                    # Handle cases where AI returns None or empty string
                    extracted_summary = ai_result.get("summary")
                    extracted_tg_caption = ai_result.get("telegram_caption")
                    
                    # STRICT ENFORCEMENT: Reject the AI output completely if telegram_caption is missing
                    if not extracted_summary or extracted_summary.lower() == "none" or not extracted_tg_caption or extracted_tg_caption.lower() == "none":
                        print(f"   ⚠️ AI validation failed: missing summary or telegram_caption. Retrying next cycle.")
                        continue

                    trend.summary = extracted_summary
                    trend.category = final_category 
                    trend.tags = ai_result.get("tags")
                    
                    entities_dict = ai_result.get("entities", {})
                    if isinstance(entities_dict, dict):
                        entities_dict["image_search_query"] = ai_result.get("image_search_query")
                        # NEW: Save Telegram caption in the JSON column
                        entities_dict["telegram_caption"] = ai_result.get("telegram_caption")
                    trend.entities = entities_dict
                    
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
                
                # Add UTM parameters for tracking
                utm_params = "utm_source=telegram&utm_medium=channel&utm_campaign=hot_trends"
                separator = "&" if "?" in target_url else "?"
                target_url = f"{target_url}{separator}{utm_params}"
                
                # STRICT ENFORCEMENT: Extract dedicated telegram caption, NO FALLBACK
                tg_caption = None
                if isinstance(trend.entities, dict):
                    tg_caption = trend.entities.get("telegram_caption")
                
                if not tg_caption:
                    print(f"   ⚠️ Publish skipped: 'telegram_caption' missing for legacy Trend {trend.id}. Marking as published to unblock queue.")
                    trend.is_published = True
                    db.commit()
                    continue
                
                # Call the service and check return value
                success = alert_service.publish_to_channel(
                    title=trend.title,
                    summary=tg_caption, # MUST use the dedicated caption here!
                    category=trend.category,
                    url=target_url,
                    image_path=trend.cover_image,
                    video_path=trend.video_path
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