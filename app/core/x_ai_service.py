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

def generate_x_content(trend_title, cluster_text, category):
    """
    Generates optimized X (Twitter) Premium content using Gemini with Spintax Rotation.
    """
    if not client:
        logger.error("Gemini client is not initialized.")
        return None

    styles = [
        "Investigative Journalist: Start with 🚨. Reveal the hidden layer or real motive behind the news.",
        "Data-Driven Analyst: Start with 📊. Focus on the scale, impact, and unprecedented nature of the event.",
        "Geopolitical/Macro Strategist: Start with 🌍. Frame the news around shifting power dynamics or market impact.",
        "Devil's Advocate: Start with ⚖️. Challenge the mainstream narrative of the event immediately.",
        "Future-Caster: Start with 🔮. Directly address how this specific event changes the future landscape."
    ]
    selected_style = random.choice(styles)

    prompt = f"""
    You are an "Expert Turkish Social Media Editor" for the news platform 'TrendiaTR'. 
    Create highly engaging, professional, and viral content for X (Twitter).

    CRITICAL RULE: ALL GENERATED TEXT MUST BE STRICTLY IN THE TURKISH LANGUAGE (TÜRKÇE).

    ### INPUT DATA
    - Headline: {trend_title}
    - Context: {cluster_text}

    ### TONE & CONTEXT RULES
    Analyze the nature of the news. 
    - IF the news is about natural disasters, weather (e.g., fog, rain), standard traffic updates, or non-political daily events: YOU MUST strictly avoid artificial controversy or sensationalism. Default to factual, urgent, or minimalist tones, regardless of the randomly selected style.
    - IF the news is political, economic, or a major crisis: Apply the MANDATORY STYLE RULE fully.

    ### TASK
    Generate a JSON response with exactly these 4 keys:

    1. "ai_summary": 
       - A 1 or 2-line highly engaging, assertive summary.
       - CRITICAL: DO NOT ASK ANY QUESTIONS in this section. Make it a powerful, declarative statement or a shocking revelation.
       - You MUST include the core factual reason or specific trigger (e.g., '2-4 weeks duration').
       - MANDATORY STYLE RULE: {selected_style}

    2. "interaction_question" (CRITICAL - LOGICAL CHAIN-OF-THOUGHT):
       - DO NOT write a placeholder question.
       - CRITICAL: DO NOT use the 💬 emoji in your output.
       - FOLLOW THIS LOGICAL PROCESS:
         a. IDENTIFY the core tension or uncertainty in the news.
         b. SYNTHESIZE a polarizing BINARY question.
         c. Scenario A and Scenario B MUST BE the direct grammatical answers to the question.
       - CRITICAL JSON FORMATTING: You MUST use the exact string characters "\\n\\n" (double backslash) to create line breaks inside the JSON value.
       - YOU MUST strictly adhere to this exact output string format:
       "[Your intelligent question?]\\n\\nA) [Scenario A - 4 to 8 words]\\nB) [Scenario B - 4 to 8 words]\\n\\nA veya B? Yorumlarda nedenini belirtin! 👇"

    3. "hashtags":
       - A list of exactly 2 relevant hashtags (without the # symbol). Example: ["Siyaset", "Ekonomi"]

    4. "image_short_text":
       - A heavily compressed, single-sentence summary.
       - STRICTLY UNDER 130 CHARACTERS. NO EMOJIS.
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
