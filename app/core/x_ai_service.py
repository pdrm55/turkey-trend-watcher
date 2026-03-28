import os
import json
import logging
import random
from google import genai
from google.genai import types

# Configure module logger
logger = logging.getLogger(__name__)

# --- Configuration ---
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
MODEL_NAME = "gemini-2.0-flash-lite-preview-09-2025"  # Default fallback

client = None

def _initialize_client():
    """Initializes the Gemini client and selects the best model."""
    global client, MODEL_NAME
    if not GOOGLE_API_KEY:
        logger.error("GOOGLE_API_KEY not found in environment variables.")
        return

    try:
        client = genai.Client(api_key=GOOGLE_API_KEY)
        
        # Dynamic Model Selection Logic (Replicated from Summarizer)
        try:
            candidates = []
            for m in client.models.list():
                name = m.name.replace('models/', '')
                # Filter for text-generation flash models
                if 'flash' in name.lower() and 'image' not in name.lower() and 'audio' not in name.lower():
                    candidates.append(name)
            
            # Priority 1: Flash Lite (Best value/performance)
            found = False
            for c in candidates:
                if 'lite' in c and 'flash' in c: 
                    MODEL_NAME = c
                    found = True
                    break
            
            # Priority 2: Stable 1.5 Flash
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

# Initialize on module load
_initialize_client()

def generate_x_content(trend_title, cluster_text, category, phase_type="standard"):
    """
    Generates optimized X (Twitter) Premium content using Gemini.
    """
    if not client:
        logger.error("Gemini client is not initialized.")
        return None

    phase_instructions = ""
    if phase_type == "radar":
        phase_instructions = "PHASE 1 (RADAR / BREAKING): The event is just breaking. Tone: Urgent but investigating. Start with phrases like 'İlk bilgilere göre' or 'Gelen ilk raporlara göre'. Focus ONLY on the core claim triggering the trend. Do not make definitive geopolitical conclusions yet."
    elif phase_type == "confirmed":
        phase_instructions = "PHASE 2 (CONFIRMED): The event is fully verified. Tone: Authoritative, definitive, and factual. State exactly what happened with absolute certainty. Then provide 1 highly specific, non-obvious strategic insight."

    prompt = f"""
    You are an "Expert Data Journalist" for the news platform 'TrendiaTR'. 
    Create highly engaging, professional content for X (Twitter).

    CRITICAL RULE: ALL GENERATED TEXT MUST BE STRICTLY IN THE TURKISH LANGUAGE (TÜRKÇE).

    ### INPUT DATA
    - Headline: {trend_title}
    - Context: {cluster_text}

    ### JOURNALISM RULES (STRICTLY ENFORCED)
    1. THE INVERTED PYRAMID: Your summary MUST start with the hard facts (Who, What, Where, When).
    2. ANTI-CLICHÉ PROTOCOL: YOU ARE STRICTLY FORBIDDEN from using generic geopolitical or economic filler phrases (e.g., "bölgesel çatışma riskini artıracak", "piyasalarda dalgalanma yaratacak", "istikrarsızlığı tetikleyecek"). 
    3. SPECIFIC INSIGHT ONLY: If you add an analysis, it must be a concrete, data-driven detail or a specific strategic consequence (e.g., "The targeted facility produces 40% of the fuel" NOT "this will affect the economy").
    4. CURRENT STATUS: {phase_instructions}

    ### TASK
    Generate a JSON response with exactly these 4 keys:

    1. "ai_summary": 
       - 2 sentences max.
       - Sentence 1: The hard fact (What exactly happened?).
       - Sentence 2: The specific insight or current status based on the phase.
       - CRITICAL: DO NOT ASK ANY QUESTIONS in this section.

    2. "interaction_question":
       - IDENTIFY the core tension or uncertainty in the news.
       - SYNTHESIZE a polarizing BINARY question.
       - CRITICAL JSON FORMATTING: You MUST use the exact string characters "\\n\\n" (double backslash) to create line breaks.
       - STRICT FORMAT: "[Your specific question?]\\n\\nA) [Scenario A]\\nB) [Scenario B]\\n\\nA veya B? Yorumlarda nedenini belirtin! 👇"

    3. "hashtags":
       - Exactly 2 relevant hashtags (without the #). Example: ["Siyaset", "Ortadoğu"]

    4. "image_short_text":
       - A heavily compressed, single-sentence summary UNDER 130 CHARACTERS. NO EMOJIS.
    """

    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type='application/json',
                temperature=0.8,
            )
        )
        
        try:
            result = json.loads(response.text)
            if isinstance(result, list) and len(result) > 0:
                result = result[0]
        except json.JSONDecodeError:
            logger.error("Failed to decode JSON from AI response.")
            return None
        
        required_keys = ["ai_summary", "interaction_question", "hashtags", "image_short_text"]
        if not all(k in result for k in required_keys):
            logger.error(f"AI response missing required keys.")
            return None
            
        return result

    except Exception as e:
        logger.error(f"X Content Generation Failed: {e}")
        return None

def generate_x_thread(trend_title, cluster_text, category):
    """
    Generates a viral 4-part Twitter thread (flood) using Gemini.
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
