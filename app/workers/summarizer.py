import sys
import os
import time
import json
import re
import csv
import logging
from datetime import datetime, timezone
from google import genai
from google.genai import types
from google.api_core import exceptions as google_exceptions

# Add project root to sys path for internal imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
from app.database.models import SessionLocal, Trend, RawNews, SystemSettings
from sqlalchemy import desc, or_, and_, func
from app.config import Config
from app.core.indexing_utils import notify_google 
from app.core.text_utils import slugify_turkish 
from app.core.alert_service import alert_service
from app.core.classifier import decide_final_category, normalize_text, CAT_MAP
from app.core.translation import (
    clear_fa_cache, _redis_key_title, _redis_key_summary,
)
from app.core.quota_guard import gemini_quota

logging.basicConfig(level=logging.INFO, format="%(asctime)s - Summarizer - %(levelname)s - %(message)s")
logger = logging.getLogger("Summarizer")
# The genai SDK logs every HTTP request at INFO, which drowns the worker's own
# output in `docker logs`. Keep their warnings, drop the per-request chatter.
for _noisy in ("httpx", "httpcore", "google_genai", "google.genai"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

# --- Redis client (used only for FA translation cache invalidation) ---
try:
    import redis as _redis_lib
    _redis_fa = _redis_lib.from_url(
        f"redis://{os.getenv('REDIS_HOST', 'ttw_redis')}:6379/0",
        decode_responses=True, socket_connect_timeout=2
    )
    _redis_fa.ping()
except Exception:
    _redis_fa = None

# --- Google AI & System Configuration ---
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
if not GOOGLE_API_KEY:
    print("❌ Error: GOOGLE_API_KEY not found in .env. LLM operations will fail.")

# --- NEW: Robust API Calling State ---
client = None
PRIMARY_MODEL_NAME = None
FALLBACK_MODEL_NAME = None
CURRENT_MODEL_NAME = None
FAILOVER_ACTIVE = False
SUCCESSFUL_CYCLES_SINCE_FAILOVER = 0
FAILOVER_RESET_THRESHOLD = 5 # Attempt to reset to primary model after 5 successful fallback calls

# --- Monitoring & System Configuration ---
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

# Per-model pricing (input $/token, output $/token).  Update when Google changes rates.
_MODEL_PRICING: dict = {
    "gemini-2.5-flash-lite": (0.10 / 1_000_000, 0.40 / 1_000_000),
    "gemini-2.5-flash":      (0.15 / 1_000_000, 0.60 / 1_000_000),
    "gemini-2.0-flash-lite": (0.075 / 1_000_000, 0.30 / 1_000_000),
    "gemini-1.5-flash":      (0.075 / 1_000_000, 0.30 / 1_000_000),
}
_DEFAULT_PRICING = (0.10 / 1_000_000, 0.40 / 1_000_000)

def _get_model_pricing(model_name: str):
    name_lower = (model_name or "").lower()
    for key, pricing in _MODEL_PRICING.items():
        if key in name_lower:
            return pricing
    return _DEFAULT_PRICING

def log_to_csv(trend_id, model, in_tok, out_tok, duration, category, status):
    """Logs AI performance and token usage for cost monitoring and analytics"""
    try:
        in_price, out_price = _get_model_pricing(model)
        cost = (in_tok * in_price) + (out_tok * out_price)
        
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
    """Dynamically identifies the best available Gemini models from the API list"""
    print("🔍 Probing for best Gemini models in the current region...")
    try:
        candidates = []
        for m in client.models.list():
            name = m.name.replace('models/', '') 
            # Filter for text-generation flash models
            name_lower = name.lower()
            
            # Blacklist specific restricted models
            if name_lower in ['gemini-2.0-flash', 'gemini-2.0-flash-001']:
                continue
                
            if 'flash' in name_lower and 'image' not in name_lower and 'audio' not in name_lower and 'embedding' not in name_lower:
                candidates.append(name)
        
        primary = None
        fallback = None
        
        # Priority for Primary: Specific lite versions
        primary_priorities = ['2.5-flash-lite', '2.0-flash-lite', 'flash-lite-latest']
        for priority in primary_priorities:
            for c in candidates:
                if priority in c.lower():
                    primary = c
                    break
            if primary:
                break
        
        if not primary and candidates: 
            primary = candidates[0]
        elif not primary:
            return 'gemini-2.0-flash-lite-preview-09-2025', 'gemini-1.5-flash'
            
        # Priority for fallback: Environment stable models
        fallback_priorities = ['flash-latest', '2.5-flash', '3-flash-preview']
        for priority in fallback_priorities:
            for c in candidates:
                if c != primary and priority in c.lower():
                    fallback = c
                    break
            if fallback:
                break
        
        if not fallback:
            for c in candidates:
                if c != primary:
                    fallback = c
                    break
                    
        # If only one model is available, use it for both
        if not fallback:
            fallback = primary
            
        return primary, fallback
        
    except Exception as e:
        print(f"⚠️ Model Discovery Failed: {e}. Using hardcoded fallback.")
        return 'gemini-2.0-flash-lite-preview-09-2025', 'gemini-1.5-flash'

# Initialize the Gemini Client
if GOOGLE_API_KEY:
    try:
        client = genai.Client(api_key=GOOGLE_API_KEY)
        PRIMARY_MODEL_NAME, FALLBACK_MODEL_NAME = get_best_available_model(client)
        CURRENT_MODEL_NAME = PRIMARY_MODEL_NAME
        print(f"✅ AI Context Ready. Primary: {PRIMARY_MODEL_NAME} | Fallback: {FALLBACK_MODEL_NAME}")
    except Exception as e:
        print(f"❌ Gemini Initialization Error: {e}")

# Empty-analysis detector.
#
# Three prompt revisions could not stop the model producing significance-talk
# instead of a concrete fact: each named phrase was dropped and an equivalent
# took its place ("önem taşıyor" was banned first and came back two revisions
# later; "merak konusu" appeared as a fresh substitute). Prompt-side bans are
# probabilistic, so the rule is enforced here instead, where it is deterministic.
#
# A matching analysis is discarded and inject_ai_analysis() then omits the whole
# section — which is the intended outcome: no analysis beats a hollow one.
# Tier 1 — hollow regardless of what else the sentence contains.
_HOLLOW_ALWAYS = [
    # Allow intervening words: "önemli bir gösterge niteliği taşıyor" is the same
    # move as "önem taşıyor" and slipped through an adjacency-only pattern.
    r'önem(i|li)?\b.{0,40}?\b(taşı|arz\s+ed)',
    r'(gösterge|işaret)\s+niteliği\s+taşı',
    r'gözler\s+önüne\s+ser',
    r'ortaya\s+koy(uyor|maktadır)',
    # Only the predictive forms. "üstünlüğünü pekiştirdi" after a race result is
    # a concrete fact; "yetkinliğini pekiştirecektir" is a hollow forecast.
    r'pekiştir(ecek|ir|mektedir)',
    r'yansı(t(ıyor|maktadır)|ması\s+olarak)',
    # "X has now become clear / complete" — states only that the event ended.
    r'(netleş|tamamlan|sona\s+er)(miş|mış)\s+oldu',
    r'merak\s+konusu',
    r'değerlendirilebilir',
    r'analiz\s+sunul',          # reports that reporting happened
]

# Tier 2 — hollow ONLY when nothing specific is attached. These verbs are fine in
# real journalism: "Duruşmanın 14 Ekim'de görülmesi bekleniyor" is a genuine next
# step; "Trafik akışının iyileşmesi bekleniyor" is filler. Blanket-banning the
# verb destroyed the first kind, so the anchor decides, not the verb.
_HOLLOW_IF_UNANCHORED = [
    r'bekleniyor\s*\.?\s*$',
    r'olacaktır\s*\.?\s*$',
    r'işaret\s+ediyor',
    r'fırsat\s+sun(uyor|ar)',
    r'dikkat\s+çek(iyor|mektedir)\s*\.?\s*$',
    r'olduğu\s+belirtiliyor\s*\.?\s*$',
    r'takibi\s+sürüyor\s*\.?\s*$',
]

_HOLLOW_ALWAYS_RX = [re.compile(p, re.IGNORECASE) for p in _HOLLOW_ALWAYS]
_HOLLOW_IF_UNANCHORED_RX = [re.compile(p, re.IGNORECASE) for p in _HOLLOW_IF_UNANCHORED]

_TR_MONTHS = ('ocak', 'şubat', 'mart', 'nisan', 'mayıs', 'haziran',
              'temmuz', 'ağustos', 'eylül', 'ekim', 'kasım', 'aralık')
# Explicit time expressions — the other way a forecast becomes checkable.
_TIME_RX = re.compile(
    r'\b(yarın|bugün|önümüzdeki\s+\w+|gelecek\s+(hafta|ay|yıl)|hafta\s+sonu|'
    r'pazartesi|salı|çarşamba|perşembe|cuma|cumartesi|pazar)\b',
    re.IGNORECASE,
)


def _has_concrete_anchor(text: str) -> bool:
    """
    Whether a forecast is checkable: a number or a date/time.

    Deliberately NOT proper nouns. Anchoring on those was tried and disabled the
    whole tier — practically every news sentence names a club, a party or a
    company, so "Bursaspor'un maçı ... bir fırsat sunuyor" counted as anchored.
    A named subject does not make a prediction verifiable; a date or a figure does.
    """
    if any(ch.isdigit() for ch in text):
        return True
    low = text.lower()
    if any(m in low for m in _TR_MONTHS):
        return True
    return bool(_TIME_RX.search(text))


def is_empty_analysis(text: str) -> bool:
    """True when the analysis says nothing a reader can act on."""
    if not text or not text.strip():
        return True
    t = text.strip()
    if len(t) < 25:          # too short to carry a real fact
        return True
    if any(rx.search(t) for rx in _HOLLOW_ALWAYS_RX):
        return True
    if any(rx.search(t) for rx in _HOLLOW_IF_UNANCHORED_RX):
        return not _has_concrete_anchor(t)
    return False


_AI_HEADING_TR = "### 🤖 Yapay Zeka Analizi"
_AI_HEADING_FA = "### 🤖 تحلیل هوش مصنوعی"
_SOURCES_HEADING_TR = "### 🔗 Kaynaklar"
_SOURCES_HEADING_FA = "### 🔗 منابع"


def inject_ai_analysis(summary: str, analysis: str, heading: str, sources_heading: str) -> str:
    """
    Put the AI-analysis section into a summary, immediately before the sources
    block (or at the end if there is none).

    Asking the model to emit this section inside the markdown did not work: the
    same prompt tells it not to pad thin sections, and it read the analysis
    heading as padding — 6 of 9 live summaries dropped it entirely. The heading
    is a contract (routes.py:287 builds the FAQ JSON-LD from it, and it is the
    reader-facing disclosure that the analysis is AI-written), so it is assembled
    here in code from a dedicated JSON field instead of being requested in prose.
    """
    if not summary:
        return summary
    if not analysis or not analysis.strip():
        return summary
    if heading in summary:          # model emitted it anyway — leave it alone
        return summary

    block = f"{heading}\n{analysis.strip()}"

    idx = summary.find(sources_heading)
    if idx == -1:
        return f"{summary.rstrip()}\n\n{block}"
    return f"{summary[:idx].rstrip()}\n\n{block}\n\n{summary[idx:]}"


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
    """Executes the summarization call to the Gemini API with robust failover and recovery logic."""
    global CURRENT_MODEL_NAME, FAILOVER_ACTIVE, SUCCESSFUL_CYCLES_SINCE_FAILOVER

    if not client or not PRIMARY_MODEL_NAME:
        return None, 0, 0, 0, "No-Client"

    umbrella_instruction = ""
    if is_umbrella and old_title:
        umbrella_instruction = f'### UMBRELLA UPDATE MODE ACTIVATED\nThis is a highly evolving story. The PREVIOUS headline was: "{old_title}". Your task is to create an "Umbrella Title" (تیتر چتری) and comprehensive summary. You MUST combine the core original event WITH the new developments/reactions. Format example: "[Core Event]: [New Developments]".'

    prompt = f"""
### WHO YOU ARE
You are a senior editor in a Turkish newsroom — the person who takes raw wire
copy and turns it into the piece that actually runs. You write the way an
experienced Turkish journalist writes: plain, confident, concrete. You are not
summarizing text for a machine; you are telling a reader what happened.

### STEP 1 — READ THE SOURCES (do this before writing)
- Discard ads (betting, bonuses, promotions), "devamını oku" links, navigation
  text, and "ilgili haberler" snippets about a different story.
- Find the one core event that most sources agree on.
- Check causality: "sakatlık nedeniyle ameliyat oldu", never the reverse.
- Compare numbers across sources (dates, tolls, prices, percentages). If they
  disagree, do NOT silently pick one — say so in your own words, phrased
  differently each time. Never reuse a stock sentence for this.
- NEVER invent a fact, number, name, quote, or source that is not in the raw
  text. If something is unknown, leave it out or say it is not yet clear.

{umbrella_instruction}

### STEP 2 — WRITE IT LIKE A JOURNALIST
This is the part that matters most. The text must read as though a person wrote
it on deadline, not as though a template was filled in.

HOW TO WRITE:
- Open with a real lede: 2–3 sentences of flowing prose that tell the reader
  what happened, to whom, where, and why it matters. NOT a bullet list.
- Active voice. Concrete verbs. Short sentences. Say "polis 3 kişiyi gözaltına
  aldı", not "3 kişinin gözaltına alınması gerçekleştirilmiştir".
- Vary your sentence length and rhythm. Uniform sentences are the clearest
  giveaway of machine text.
- Let the story dictate the structure. A court ruling, a derby result and an
  inflation print should not come out shaped identically.
- Subheadings must be specific to THIS story ("Soruşturma nasıl başladı",
  "Kulüpten ilk açıklama") — never generic filler like "Detaylar" or "Genel Bakış".
- Keep paragraphs to 2–3 sentences for mobile reading.

### LENGTH FOLLOWS THE SOURCES — THIS IS THE MOST VIOLATED RULE
Most of these clusters are thin: the sources carry one fact and nothing more.
When that happens, the correct summary is the lede and nothing else.

- A summary that is `### ⚡ Özet` + lede + `### 🔗 Kaynaklar` is a COMPLETE,
  GOOD summary. Ship it. Do not add a body section to make it look longer.
- Add a body section ONLY when you have facts that do not fit in the lede —
  a sequence of events, a second party's response, background, consequences,
  numbers. If you cannot name the new fact a section would carry, there is no
  section to write.
- HARD TEST, apply to every body sentence before you keep it: does this sentence
  contain information the Özet does not already state? If no, delete it. Saying
  the same fact in different words is not elaboration; it is padding, and it is
  the single most obvious sign that a machine wrote the text.

Concretely, this is the failure to avoid:
  Özet: "AFAD ve TÜBİTAK ortak deprem araştırma çağrısını başlattı."
  Body: "AFAD ve TÜBİTAK arasındaki işbirliği kapsamında duyurulan ortak çağrı,
         deprem çalışmalarını teşvik etmeyi amaçlıyor."
Those are the same sentence twice. The second one should not exist — that story
ends after the lede.

NEVER WRITE THESE (they instantly read as machine text):
- "Bu haber, ... açısından önem taşımaktadır"
- "Sonuç olarak", "Özetle", "Genel olarak bakıldığında"
- "Gelişmeler yakından takip edilmektedir" (unless a source actually says it)
- Any sentence that describes the article instead of the events in it. In
  particular, never follow a quote or a fact with a sentence explaining how
  significant it is ("Bu sözler, ...nin ne kadar büyük olduğunu gösteriyor").
  Report the thing; let the reader judge it.
- Do not end every paragraph with the same passive reporting verb. Stacking
  "bildirildi / belirtildi / vurgulandı / kaydedildi" one per paragraph is the
  most recognisable machine-Turkish pattern there is. Attribute plainly instead
  ("Emniyet Müdürlüğü'ne göre ...", "AA'nın aktardığına göre ..."), and only
  where the attribution actually matters.
- Do not repeat the lede's figures and phrasing verbatim in the body. The body
  advances the story; it does not restate the opening in longer form.
- The same opening construction you would use for every other story.

### STEP 3 — STRUCTURE (markdown, in the `summary` field)
These exact section markers are parsed by the site and the Telegram bot. Do not
rename them, do not translate them, do not change their emoji.

ALWAYS REQUIRED — exactly two sections, nothing else is guaranteed:
1. `### ⚡ Özet` — then the PROSE LEDE described above. Prose, not bullets.
2. `### 🔗 Kaynaklar` — always last. Bulleted list of the agencies/institutions
   actually named in the raw text (`- AA`, `- TÜİK`, `- Reuters`).

CONDITIONAL — add these only when the sources genuinely carry the material:
3. One or more `###` subheadings specific to this story, carrying facts the lede
   does not already state. Omit entirely for thin stories (see the length rule
   above). This is optional, not expected.
4. `### 📊 Önemli İstatistikler` — only if the sources contain several real
   figures worth pulling out. A section holding one number is padding; omit it.
5. `### 💬 Uzman Görüşleri` — only if the sources contain a genuine statement or
   quote. Format: `> **[İsim, Ünvan/Kurum]:** "[alıntı]"`. Never invent a quote.

Do NOT write an analysis section inside `summary` — that goes in its own JSON
field (see below) and is assembled afterwards.

STYLING: `**bold**` for people, organizations and critical figures. `>` blockquotes
for real statements only.

### STEP 3b — THE ANALYSIS (`ai_analysis` and `fa_ai_analysis` fields)
REQUIRED, both languages. 1–2 sentences. Plain prose, no heading, no markdown.

This is the one place you may go beyond reporting — but it must stay CONCRETE.
Pick whichever of these the story actually supports, in this order:

1. WHAT HAPPENS NEXT, specifically. The hearing date, the deadline, the next
   match, the vote, when the programme opens, who has to respond and by when.
2. WHO IS AFFECTED AND HOW, in plain terms. Which people, how many, what
   changes for them in practice.
3. THE NUMBER IN CONTEXT. Compare the figure in the story to a previous one, a
   target, or a comparable case — but only using numbers present in the sources.
4. THE UNANSWERED QUESTION. Name the specific thing the sources do not yet
   settle ("kaç köyün etkilendiği açıklanmadı").
5. THE CONCRETE DETAIL BEHIND THE EVENT — use this when the story is already
   finished and has no "next": who organised it, how long it ran, how many took
   part, what it replaced, which edition it was, what the result was. A completed
   race, a closed festival, a published column: report the fact that anchors it,
   not what it supposedly demonstrates.

An event that has ended is the case you get wrong most often. There is no future
to point at, so do not manufacture one. "Önümüzdeki yıllarda benzer etkinliklerin
düzenlenmesi bekleniyor" and "konumlar netleşmiş oldu" are empty. Give the fact:
who won, how many competed, which edition this was, who is hosting the next one.

BANNED — this is the failure mode to avoid. Do not write sentences whose subject
is an abstraction ("şeffaflık", "dışa bağımlılık", "teknolojik yetkinlik",
"kararlılık") and whose verb is a display verb ("gözler önüne seriyor", "ortaya
koyuyor", "pekiştirecektir", "önem taşıyor"). Real examples of what NOT to write:

  ✗ "Bu durum, projelerin uygulanmasında şeffaflık ve katılımcılık eksikliğini
     gözler önüne seriyor."
  ✗ "Harşena-A05'in başarılı olması, Türkiye'nin teknolojik yetkinliğini
     pekiştirecektir."

Both say nothing a reader can act on. Their concrete replacements:

  ✓ "Köylüler kavşağın yerinin değiştirilmesini istiyor; Karayolları'ndan
     konuyla ilgili bir açıklama gelmedi."
  ✓ "Aracın seri üretime geçip geçmeyeceği ve çiftçiye hangi fiyattan
     sunulacağı henüz belli değil."

ALSO BANNED — do not close on a vague curiosity formula. "... merak konusu",
"... bekleniyor", "... işaret ediyor" and "... önemli bir faktör olacaktır" are
the same empty move as above. If you name an open question, name the SPECIFIC
one (option 4), with the thing that is unknown stated outright.

Never describe the article itself. "Bu konuda bir analiz sunuluyor" or
"...olduğu belirtiliyor" reports that reporting happened; write what happened.

### LEAVING IT OUT IS ALLOWED AND OFTEN CORRECT
Also return `analysis_type`, naming which of the five you used:
"next_step" | "who_affected" | "number_in_context" | "open_question" |
"anchoring_fact" | "none".

If none of the five genuinely fits — the sources carry one fact, the event is
over, nothing follows, nothing is unresolved — return `analysis_type: "none"`
and `ai_analysis: ""` and `fa_ai_analysis: ""`. The section is then omitted
entirely, which is the correct outcome.

An omitted analysis is ALWAYS better than a sentence about significance. You are
not being graded on filling the field. Do not write something in order to have
written something.

### STEP 4 — TELEGRAM CAPTION (`telegram_caption` field)
A standalone piece for the channel. The reader must understand the whole story
without clicking. 2–3 short paragraphs, ~100–150 words, emojis used naturally,
**bold** for names and figures, NO `###` headers. Same journalist voice — this is
the version most people actually read, so it should be the most human of all.

### STEP 5 — PERSIAN EDITION (`fa_headline`, `fa_summary`)
Also produce the Persian version in this same response (this saves an entire
extra API call, so it is required, not optional):
- `fa_headline`: the headline in natural Persian news style.
- `fa_summary`: the full summary in Persian. Keep EVERY markdown marker and emoji
  exactly as-is, translating only the visible text —
  `### ⚡ Özet` → `### ⚡ خلاصه`, `### 📊 Önemli İstatistikler` → `### 📊 آمار مهم`,
  `### 💬 Uzman Görüşleri` → `### 💬 دیدگاه کارشناسان`,
  `### 🤖 Yapay Zeka Analizi` → `### 🤖 تحلیل هوش مصنوعی`,
  `### 🔗 Kaynaklar` → `### 🔗 منابع`.
- Write real Persian journalism, not a word-for-word transfer. Turkish proper
  nouns take their natural Persian spelling. Convert comma-separated counts into
  proper sentences: "2 ölü, 3 yaralı" → "۲ نفر کشته و ۳ نفر زخمی شدند".

### CATEGORY
Choose from [Siyaset, Ekonomi, Gündem, Spor, Teknoloji, Sanat] based only on the
core event, and justify it in `category_reasoning`. Watch the traps: state
budget, pensions, or public infrastructure (DSİ/TOKİ) are never Spor.

### ALSO EXTRACT
- `image_search_query`: 2–3 words naming the main visual subject, tuned for image
  search (e.g. 'Ali Yerlikaya', 'Galatasaray transfer').
- `tags`, and `entities` (people / locations / organizations).

### CONSTRAINTS
- `headline`, `summary`, `telegram_caption`: Turkish only. `fa_*`: Persian only.
- Accurate and factual. No clickbait. The headline states what happened.

### OUTPUT FORMAT (JSON ONLY)
{{
    "headline": "...",
    "summary": "Raw Markdown text here...",
    "ai_analysis": "1-2 sentences of editorial context, Turkish, no heading...",
    "telegram_caption": "...",
    "fa_headline": "...",
    "fa_summary": "Raw Markdown text in Persian...",
    "fa_ai_analysis": "the same analysis in Persian, no heading...",
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

    # --- Smart Failover & Recovery Logic ---
    # 1. Check if we should try to reset from failover mode
    if FAILOVER_ACTIVE and SUCCESSFUL_CYCLES_SINCE_FAILOVER >= FAILOVER_RESET_THRESHOLD:
        print("🔄 Failover reset threshold reached. Attempting to revert to primary model...")
        CURRENT_MODEL_NAME = PRIMARY_MODEL_NAME
        FAILOVER_ACTIVE = False
        SUCCESSFUL_CYCLES_SINCE_FAILOVER = 0
        print(f"✅ Model reset to primary: {CURRENT_MODEL_NAME}")

    model_to_use = CURRENT_MODEL_NAME

    # 1b. Quota breaker: if the project quota is exhausted, do not spend another
    # rejected request on it. Both this worker and the FA sweep share this state.
    if gemini_quota.is_open():
        print(f"   ⏸️  Gemini quota cooling down ({gemini_quota.seconds_remaining()}s left) — skipping call.")
        return None, 0, 0, 0, "Quota-Cooldown"

    try:
        # 2. Main API call attempt
        req_start = time.time()
        response = client.models.generate_content(
            model=model_to_use,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type='application/json',
                # 0.55 (was 0.15): near-deterministic sampling produced uniform,
                # templated sentences — the main reason summaries read as machine
                # text. Higher temperature buys natural variation in rhythm and
                # phrasing; the prompt's "never invent a fact" rules hold accuracy.
                temperature=0.55,
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

        # 3. Success — clear any quota cooldown and the failover counter.
        gemini_quota.record_success()
        if FAILOVER_ACTIVE:
            SUCCESSFUL_CYCLES_SINCE_FAILOVER += 1

        return ai_data, in_tok, out_tok, duration, model_to_use

    except Exception as e:
        error_str = str(e)
        if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str.upper():
            print(f"   ⚠️ Rate limit (429) hit with model {model_to_use}.")

            # If we are already on the fallback model, the whole project quota is
            # gone — open the shared breaker so neither this worker nor the FA
            # sweep keeps firing rejected requests at it.
            if model_to_use == FALLBACK_MODEL_NAME:
                gemini_quota.record_failure(e)
                print(f"   ❌ Both models rate-limited. Gemini paused for {gemini_quota.seconds_remaining()}s.")
                return None, 0, 0, 0, model_to_use

            # Primary is limited but the fallback may have its own quota bucket —
            # one retry on the fallback, then stop. (No breaker yet: this is a
            # per-model limit, not necessarily project-wide exhaustion.)
            FAILOVER_ACTIVE = True
            SUCCESSFUL_CYCLES_SINCE_FAILOVER = 0
            CURRENT_MODEL_NAME = FALLBACK_MODEL_NAME
            print(f"   ↪️ Switched to fallback model: {CURRENT_MODEL_NAME}")

            time.sleep(2)
            return generate_summary_with_gemini(text_cluster, is_umbrella=is_umbrella, old_title=old_title)
        else:
            print(f"   ❌ Unhandled LLM Execution Error on {model_to_use}: {e}")
            return None, 0, 0, 0, model_to_use

# ==========================================
# FIXED LOGIC: Smart Action Filtering
# ==========================================

# Per-trend retry backoff, in memory. Keyed by trend id → earliest retry
# timestamp. Deliberately not persisted: a worker restart clearing it is
# harmless, and it avoids a schema migration on a live database.
_RETRY_AFTER: dict[int, float] = {}
_RETRY_STRIKES: dict[int, int] = {}
# How many trends to consider before picking the 5 to work on. Must be well
# above 5 so that backed-off trends do not block the ones behind them.
BACKOFF_FETCH_WINDOW = 60
# Penalty ladder for a trend that fails to produce a usable summary.
_TREND_BACKOFF_STEPS = [300, 900, 3600, 21600]  # 5m → 15m → 1h → 6h


def _penalise_trend(trend_id: int, reason: str):
    """Push a failing trend down the queue so it stops blocking the others."""
    strikes = _RETRY_STRIKES.get(trend_id, 0)
    delay = _TREND_BACKOFF_STEPS[min(strikes, len(_TREND_BACKOFF_STEPS) - 1)]
    _RETRY_STRIKES[trend_id] = strikes + 1
    _RETRY_AFTER[trend_id] = time.time() + delay
    print(f"   ⏭️  Trend {trend_id} backed off for {delay}s ({reason}, strike {strikes + 1}).")


def _clear_penalty(trend_id: int):
    _RETRY_AFTER.pop(trend_id, None)
    _RETRY_STRIKES.pop(trend_id, None)


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

        # 3. Query with OR logic.
        # Fetch a wide window, not just the top 5: a trend that keeps failing
        # (bad source text, AI validation rejection) used to sit at the head of
        # the queue forever and starve everything behind it — 68 trends were
        # backed up behind the same 5. We now skip trends that are in penalty
        # and take the highest-TPS eligible ones instead.
        candidates = db.query(Trend).filter(
            Trend.is_active == True,
            or_(condition_needs_summary, condition_needs_update, condition_needs_publish)
        ).order_by(desc(Trend.final_tps)).limit(BACKOFF_FETCH_WINDOW).all()

        now_ts = time.time()
        pending_trends = [t for t in candidates if _RETRY_AFTER.get(t.id, 0) <= now_ts][:5]

        if not pending_trends:
            if candidates:
                print(f"⏳ All {len(candidates)} queued trends are in retry backoff. Waiting.")
            return False

        skipped = len(candidates) - len([t for t in candidates if _RETRY_AFTER.get(t.id, 0) <= now_ts])
        suffix = f" ({skipped} in backoff, {len(candidates)} queued)" if skipped else f" ({len(candidates)} queued)"
        print(f"✍️  Processing {len(pending_trends)} Actionable Trends{suffix}...")

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

                # --- Rate Limit & Burst Protection ---
                time.sleep(1.0)

                ai_result, in_tok, out_tok, duration, model_used = generate_summary_with_gemini(cluster_text, is_umbrella=is_umbrella, old_title=trend.title)

                # Protect against API errors (failover is now handled inside the function)
                if ai_result is None:
                    # Failures were previously invisible: nothing was written to
                    # ai_monitor_data.csv, so the dashboard showed a healthy
                    # system while 5000+ calls a day were being rejected.
                    log_to_csv(trend.id, model_used, 0, 0, duration,
                               trend.category or "Unknown", "Error: APIFailure")
                    if gemini_quota.is_open():
                        # Quota is gone — stop the cycle entirely instead of
                        # walking the rest of the batch into the same wall.
                        print(f"   ⏸️  Quota exhausted. Halting cycle for {gemini_quota.seconds_remaining()}s.")
                        return False
                    print(f"   ❌ API call failed for Trend {trend.id}.")
                    _penalise_trend(trend.id, "api failure")
                    continue

                # Add a natural anti-spam delay between successful API calls
                time.sleep(3)
                
                if ai_result.get("is_relevant_to_turkey", True):
                    ai_cat = ai_result.get("category", "Gündem")
                    
                    if "category_reasoning" in ai_result:
                        print(f"   🧠 AI Reasoning: {ai_result['category_reasoning']}")

                    # Verify category through manual keyword analysis
                    final_category, overridden = decide_final_category(ai_cat, cluster_text)
                    
                    # Update Trend Record
                    new_headline = ai_result.get("headline", trend.title)
                    title_changed = new_headline and new_headline != trend.title
                    if title_changed:
                        trend.fa_title = None       # DB: mark for re-translation
                    trend.title = new_headline

                    # Handle cases where AI returns None or empty string
                    extracted_summary = ai_result.get("summary")
                    extracted_tg_caption = ai_result.get("telegram_caption")

                    # STRICT ENFORCEMENT: Reject the AI output completely if telegram_caption is missing
                    if not extracted_summary or extracted_summary.lower() == "none" or not extracted_tg_caption or extracted_tg_caption.lower() == "none":
                        print(f"   ⚠️ AI validation failed: missing summary or telegram_caption.")
                        _penalise_trend(trend.id, "validation failed")
                        continue

                    # Assemble the AI-analysis section from its dedicated field,
                    # but only when it actually says something. A hollow analysis
                    # is dropped in both languages together, so TR and FA never
                    # disagree about whether the section exists.
                    # The decision rests on the text, not on analysis_type. The
                    # model's self-label is unreliable in both directions: it
                    # pads when it should abstain, and it also returned "none"
                    # while writing a perfectly concrete open-question analysis
                    # (trend 280703). The type field still earns its place in the
                    # prompt — it gives the model a sanctioned way to abstain —
                    # but only the content filter decides here.
                    _analysis = ai_result.get("ai_analysis")
                    if is_empty_analysis(_analysis):
                        if _analysis and _analysis.strip():
                            print(f"   ✂️  Dropped hollow analysis for {trend.id}: {_analysis.strip()[:70]}")
                        _analysis = None
                        ai_result["fa_ai_analysis"] = None

                    extracted_summary = inject_ai_analysis(
                        extracted_summary, _analysis,
                        _AI_HEADING_TR, _SOURCES_HEADING_TR,
                    )

                    trend.summary = extracted_summary
                    trend.category = final_category

                    # ── FA translation now arrives in the SAME response ──
                    # It used to be a second Gemini call per trend, which doubled
                    # request count against a quota whose binding limit is
                    # requests-per-day, not tokens. The 30-minute sweep in
                    # gravity_worker still backfills anything missing here.
                    try:
                        fa_title = (ai_result.get("fa_headline") or "").strip()
                        fa_summary = inject_ai_analysis(
                            (ai_result.get("fa_summary") or "").strip(),
                            ai_result.get("fa_ai_analysis"),
                            _AI_HEADING_FA, _SOURCES_HEADING_FA,
                        )
                        trend.fa_title   = fa_title   or None
                        trend.fa_summary = fa_summary or None
                        if fa_title or fa_summary:
                            if _redis_fa:
                                if fa_title:
                                    _redis_fa.setex(_redis_key_title(trend.id), 86400, fa_title)
                                if fa_summary:
                                    _redis_fa.setex(_redis_key_summary(trend.id), 86400, fa_summary)
                            print(f"   🇮🇷 FA included in same call: {trend.id}")
                        else:
                            # No fresh Persian text. The Turkish content just
                            # changed, so any cached FA translation now describes
                            # the OLD story — evict it or the site serves stale
                            # Persian for up to 24h until the sweep catches up.
                            clear_fa_cache(trend.id, _redis_fa)
                            print(f"   ⚠️ FA missing from response for {trend.id} — sweep will retry")
                    except Exception as _fa_err:
                        logger.warning(f"FA handling error for {trend.id}: {_fa_err}")
                        trend.fa_title   = None
                        trend.fa_summary = None
                        clear_fa_cache(trend.id, _redis_fa)
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
                    
                    if not trend.ai_processed_at:
                        trend.ai_processed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                    
                    trend.last_updated = datetime.now(timezone.utc).replace(tzinfo=None)
                    trend.last_summary_msg_count = trend.message_count

                    print(f"   ✅ Summarized/Updated: [{trend.category}] {trend.title} (TPS: {trend.final_tps:.1f})")
                    if trend.slug: print(f"   🚀 SEO Slug: /trend/{trend.slug}")

                    # Save and Log Stats
                    log_to_csv(trend.id, model_used, in_tok, out_tok, duration, trend.category, "Success")
                    db.commit() # Save content immediately
                    _clear_penalty(trend.id)
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
    print(f"🤖 TrendiaTR AI Summary Worker Active. Primary Model: {PRIMARY_MODEL_NAME}")
    while True:
        try:
            # When the quota is exhausted, sleep out the cooldown instead of
            # spinning the query loop every second against a wall.
            if gemini_quota.is_open():
                wait = min(gemini_quota.seconds_remaining() + 5, 300)
                print(f"⏸️  Gemini quota exhausted — sleeping {wait}s before re-checking.")
                time.sleep(wait)
                continue

            has_work = process_pending_trends()
            # Dynamic sleep based on workload
            time.sleep(1 if has_work else 15)
        except KeyboardInterrupt: break
        except Exception as e:
            print(f"❌ Worker Loop Exception: {e}")
            time.sleep(30)

if __name__ == "__main__":
    main()