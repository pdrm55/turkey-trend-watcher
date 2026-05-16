import os
import json
import logging
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
MODEL_NAME = "gemini-2.5-flash-lite"  # Default fallback

client = None

def _initialize_client():
    """Initializes the Gemini client and selects the best available flash model."""
    global client, MODEL_NAME
    if not GOOGLE_API_KEY:
        logger.error("GOOGLE_API_KEY not found in environment variables.")
        return

    try:
        client = genai.Client(api_key=GOOGLE_API_KEY)

        try:
            candidates = []
            for m in client.models.list():
                name = m.name.replace('models/', '')
                if 'flash' in name.lower() and 'image' not in name.lower() and 'audio' not in name.lower():
                    candidates.append(name)

            found = False
            for c in candidates:
                if '2.5' in c and 'lite' in c and 'flash' in c:
                    MODEL_NAME = c
                    found = True
                    break

            if not found:
                for c in candidates:
                    if 'lite' in c and 'flash' in c:
                        MODEL_NAME = c
                        found = True
                        break

            if not found:
                for c in candidates:
                    if '1.5-flash' in c and 'latest' not in c:
                        MODEL_NAME = c
                        found = True
                        break

            if not found and candidates:
                MODEL_NAME = candidates[0]

            logger.info(f"X-AI Service initialized with model: {MODEL_NAME}")
        except Exception as e:
            logger.warning(f"Model discovery failed, using default {MODEL_NAME}: {e}")

    except Exception as e:
        logger.error(f"Failed to initialize Gemini client: {e}")

_initialize_client()


# ── Content-type detection ─────────────────────────────────────────────────────

_SPORTS_KEYWORDS = {"futbol", "spor", "basketbol", "voleybol", "formula", "tenis", "yüzme", "atletizm"}
_DISASTER_KEYWORDS = {"deprem", "sel", "yangın", "kaza", "afet", "fırtına", "çığ", "heyelan", "trafik"}

def _detect_content_type(category: str) -> str:
    cat = (category or "").lower()
    if any(k in cat for k in _SPORTS_KEYWORDS):
        return "breaking"
    if any(k in cat for k in _DISASTER_KEYWORDS):
        return "disaster"
    return "politics"


# ── Per-type content rules ─────────────────────────────────────────────────────

_CONTENT_RULES = {
    "breaking": {
        "label": "BREAKING (Spor / Anlık Haber)",
        "hook_rule": """
HOOK TYPE — Seç birini:
- Şok rakam: "90+3'te gol. 90+7'de kırmızı. Sonuç: beraberlik 😤"
- Keskin kontrast: "Galatasaray coştu — TFF koltuğu boş bıraktı 🪑"
- Çarpıcı gerçek: "3 maçta 0 gol. Teknik direktör hâlâ görevde."
TONE: Hızlı, çarpıcı, duygusal. Analiz yok.
YASAK: Açıklayıcı girişler, "Bu gelişme...", jeopolitik analiz.""",
        "body_rule": "2 cümle max. Sadece sert gerçek. Özellikle beklenmedik detay varsa onu öne çıkar.",
        "question_rule": "Sonuca veya karara odaklı binary soru. Takım taraftarını yorum yapmaya zorla."
    },
    "politics": {
        "label": "POLİTİK / JEOPOLİTİK",
        "hook_rule": """
HOOK TYPE — Seç birini:
- Gizli gerilim: "Kimse konuşmuyor ama bu karar her şeyi değiştirebilir 🧵"
- Çelişki: "NATO'ya evet, BM'ye hayır. Bu tutarsızlığın bedeli ne?"
- Keskin soru: "Anlaşma imzalandı. Peki kazanan kim?"
TONE: Analitik ama anlaşılır. Taraf tutan değil, düşündüren.
YASAK: "bölgesel istikrarsızlık", "piyasalarda dalgalanma", "istikrarsızlığı tetikleyecek" — bunlar jenerik dolgu ifadedir, KESİNLİKLE kullanma.""",
        "body_rule": "Cümle 1: Somut gerçek (kim, ne yaptı). Cümle 2: Spesifik sonuç veya beklenmedik detay — genel değerlendirme değil.",
        "question_rule": "İki gerçek senaryo arasında seçim yaptıran soru. Okuyucuyu taraf seçmeye zorla."
    },
    "disaster": {
        "label": "AFET / ACİL DURUM",
        "hook_rule": """
HOOK TYPE — Önce insan gerçeği, sonra rakam:
- "Enkaz altında 14 saat. İşte o an 🔴"
- "7 yaşında bir çocuk. 43 saat sonra kurtarıldı."
- "Yangın 6 saatte 3 köyü yuttu."
TONE: Ağır, saygılı, gerçekçi. Abartı veya yapay duygusallık kesinlikle yok.
YASAK: "yürekler acısı", "büyük felaket", melodramatik ifadeler.""",
        "body_rule": "Cümle 1: İnsan boyutu (kaç kişi, hangi durum). Cümle 2: Operasyonun mevcut durumu — somut rakam ile.",
        "question_rule": "Okuyucunun tepkisini veya beklentisini soran soru. Müdahale hızı veya yeterliliği hakkında."
    }
}


# ── Main content generator ─────────────────────────────────────────────────────

def generate_x_content(trend_title: str, cluster_text: str, category: str) -> dict | None:
    """
    Generates a single-phase X (Twitter) post using a category-aware template.
    Returns: {full_tweet, reply_hook, hashtags, image_short_text}
    """
    if not client:
        logger.error("Gemini client is not initialized.")
        return None

    content_type = _detect_content_type(category)
    rules = _CONTENT_RULES[content_type]

    prompt = f"""
Sen TrendiaTR platformu için Türkçe X (Twitter) içerik uzmanısın.
Hedef kitle: Türkçe konuşan global kullanıcılar.
Amaç: Marka bilinirliği ve takipçi büyümesi — viral, insan gibi yazılmış içerik.

## GİRİŞ
- Başlık: {trend_title}
- Bağlam: {cluster_text}
- İçerik Türü: {rules['label']}

## HOOK KURALI
{rules['hook_rule']}

## BODY KURALI
{rules['body_rule']}

## SORU KURALI
{rules['question_rule']}

## EVRENSELLİŞ KURALLAR (İSTİSNASIZ)
1. İlk cümle (hook) MAKSIMUM 10 kelime
2. Toplam 3-4 kısa paragraf — her paragraf max 2 satır
3. Mobil okumaya uygun — kısa, nefes alabilir
4. ASLA yazma: "Güven Endeksi", "Gündem Gücü", "Normalden Xx daha hızlı"
5. ASLA yazma: "🚨 DOĞRULANDI", "Son dakika:", "Önemli gelişme:"
6. Soru SADECE en sona, binary format — A/B şeklinde
7. Hashtag sayısı: tam olarak 2 (3. olarak #TrendiaTR x_worker ekler)

## ÇIKTI FORMATI — SADECE JSON, başka hiçbir şey yazma

{{
  "full_tweet": "hook\\n\\nbody cümle 1. Body cümle 2.\\n\\nSoru?\\n\\nA) Seçenek A\\nB) Seçenek B\\n\\nA veya B? Yorumlarda nedenini belirtin! 👇\\n\\n#Hashtag1 #Hashtag2",
  "reply_hook": "Konunun [spesifik detay] ve resmi açıklamalar için 👇",
  "hashtags": ["Hashtag1", "Hashtag2"],
  "image_short_text": "Max 130 karakter, emoji yok, tek cümle özet"
}}

UYARI — reply_hook için:
- Jenerik YAZMA: "Olayın tüm detayları için 👇" — bu değersiz
- SPESİFİK YAZ: "TFF'nin resmi açıklaması ve kulüp tepkileri için 👇" veya "Maçın VAR kararları ve teknik analiz için 👇"
- reply_hook içinde URL YAZMA — x_worker ekler
"""

    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type='application/json',
                temperature=0.85,
            )
        )
        try:
            result = json.loads(response.text)
            if isinstance(result, list) and len(result) > 0:
                result = result[0]
        except json.JSONDecodeError:
            logger.error("Failed to decode JSON from AI response.")
            return None

        required_keys = ["full_tweet", "reply_hook", "hashtags", "image_short_text"]
        if not all(k in result for k in required_keys):
            logger.error(f"AI response missing required keys. Got: {list(result.keys())}")
            return None

        logger.info(f"Generated {content_type} content for: {trend_title[:50]}")
        return result

    except Exception as e:
        logger.error(f"X Content Generation Failed: {e}")
        return None


def generate_x_thread(trend_title, cluster_text, category):
    """
    Generates a viral 4-part Twitter thread (flood) using Gemini.
    Used for manual thread generation from the studio UI.
    """
    if not client:
        logger.error("Gemini client is not initialized.")
        return None

    prompt = f"""
    You are an "Expert News Analyst" for 'TrendiaTR'. Create a highly scannable, viral 4-part Twitter thread based on this event.

    CRITICAL RULE: ALL GENERATED TEXT MUST BE STRICTLY IN THE TURKISH LANGUAGE (TÜRKÇE).

    Headline: {trend_title}
    Context: {cluster_text}

    Generate a JSON response with exactly these 5 keys:
    - 'tweet_1_hook': 🚨 Start with a shocking emoji and 1-2 punchy, assertive sentences summarizing the core event. CRITICAL: DO NOT ASK ANY QUESTIONS. Make it a powerful, declarative statement. Do not add hashtags here.
    - 'tweet_2_facts': 📌 Provide 2 or 3 extremely short bullet points with the most important hard facts or quotes.
    - 'tweet_3_ai_insight': 🤖 Start with 'AI Analizi:'. Provide 2 short bullet points explaining the consequences, future scenarios, or hidden impact.
    - 'tweet_4_cta' (CRITICAL - LOGICAL CHAIN-OF-THOUGHT):
       - DO NOT write a placeholder question.
       - CRITICAL: DO NOT use the 💬 emoji in your output.
       - FOLLOW THIS LOGICAL PROCESS:
         a. IDENTIFY the core tension or uncertainty in the news.
         b. SYNTHESIZE a polarizing BINARY question.
         c. Scenario A and Scenario B MUST BE the direct grammatical answers to the question.
       - CRITICAL JSON FORMATTING: You MUST use the exact string characters "\\n\\n" (double backslash) to create line breaks inside the JSON value.
       - YOU MUST strictly adhere to this exact output string format:
       "[Your intelligent question?]\\n\\nA) [Scenario A - 4 to 8 words]\\nB) [Scenario B - 4 to 8 words]\\n\\nA veya B? Yorumlarda nedenini belirtin! 👇"
    - 'image_short_text': A highly compressed, single-sentence summary strictly under 130 chars (NO emojis).
    """

    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type='application/json', temperature=0.7)
        )

        try:
            result = json.loads(response.text)
            if isinstance(result, list) and len(result) > 0:
                result = result[0]
        except json.JSONDecodeError:
            logger.error("Failed to decode JSON from AI response.")
            return None

        required_keys = ["tweet_1_hook", "tweet_2_facts", "tweet_3_ai_insight", "tweet_4_cta", "image_short_text"]
        if not all(k in result for k in required_keys):
            logger.error(f"AI response missing keys.")
            return None

        return result

    except Exception as e:
        logger.error(f"X Thread Generation Failed: {e}")
        return None
