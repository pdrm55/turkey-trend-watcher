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
        "Start EXACTLY with '🤖 AI Özeti:' and use a formal, analytical tone.",
        "Start EXACTLY with '🚨 SON DAKİKA:' and create a sense of urgency. Do not use the word AI.",
        "Start EXACTLY with '🤔 Analiz:' and explain the core event in a storytelling style.",
        "Start EXACTLY with '⚡ Kısaca:' and provide a very short, punchy, direct summary.",
        "Start EXACTLY with '📌 Öne Çıkan Detay:' and focus on the most striking fact or quote."
    ]
    selected_style = random.choice(styles)

    prompt = f"""
    You are an "Expert Turkish Social Media Editor" for the news platform 'TrendiaTR'. 
    Create highly engaging, professional, and viral content for X (Twitter).

    CRITICAL RULE: ALL GENERATED TEXT MUST BE STRICTLY IN THE TURKISH LANGUAGE (TÜRKÇE).

    ### INPUT DATA
    - Headline: {trend_title}
    - Context: {cluster_text}

    ### TASK
    Generate a JSON response with exactly these 4 keys:

    1. "ai_summary": 
       - A 1-line highly engaging summary.
       - MANDATORY STYLE RULE: {selected_style}

    2. "interaction_question":
       - An open-ended, thought-provoking question related to the news.
       - MUST start with a conversational emoji like 💬, ❓, or 🗣️.

    3. "hashtags":
       - A list of exactly 2 relevant hashtags (without the # symbol). Example: ["Siyaset", "Ekonomi"]

    4. "image_short_text":
       - A heavily compressed, single-sentence summary.
       - STRICTLY UNDER 130 CHARACTERS.
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
    Generates a 5-part Twitter thread (flood) using Gemini.
    
    Args:
        trend_title (str): The headline of the trend.
        cluster_text (str): The raw text content/summary of the news, including timeline of dependent events.
        category (str): The category of the news.
        
    Returns:
        dict: A dictionary with tweet parts and image text, or None if failed.
    """
    if not client:
        logger.error("Gemini client is not initialized.")
        return None

    prompt = f"""
    You are an "Expert News Analyst and Investigative Journalist" for the news platform 'TrendiaTR'. 
    Create a viral, highly analytical 5-part Twitter thread (flood) based on the provided timeline of a main event and its dependent/related news.

    CRITICAL RULE: ALL GENERATED TEXT MUST BE STRICTLY IN THE TURKISH LANGUAGE (TÜRKÇE).

    Headline: {trend_title}
    Context & Timeline: {cluster_text}

    Analyze the flow of these connected events and generate a JSON response with exactly these 6 keys:
    - 'tweet_1_hook': A compelling hook highlighting the scale, evolution, or hidden truth of the ongoing story (with emojis). No hashtags.
    - 'tweet_2_context': Summarize the chronological flow of events (how the story started and how the dependent events unfolded).
    - 'tweet_3_data': Extract the critical connections, contradictions, or key data points between the main news and its dependent news items.
    - 'tweet_4_insight': Provide a deep analytical insight, consequence, or prediction based on the trajectory of these connected events.
    - 'tweet_5_cta': Conclude the analysis with a strong summary and ask an engaging question to the audience. Include exactly 2 hashtags.
    - 'image_short_text': A highly compressed, single-sentence summary strictly under 130 chars (NO emojis) to be printed on the image.
    """

    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type='application/json',
                temperature=0.7,
            )
        )
        
        try:
            result = json.loads(response.text)
            if isinstance(result, list) and len(result) > 0:
                result = result[0]
        except json.JSONDecodeError:
            logger.error("Failed to decode JSON from AI response.")
            return None
        
        # Basic validation
        required_keys = ["tweet_1_hook", "tweet_2_context", "tweet_3_data", "tweet_4_insight", "tweet_5_cta", "image_short_text"]
        if not all(k in result for k in required_keys):
            logger.error(f"AI response missing required keys. Got: {list(result.keys())}")
            return None
            
        return result

    except Exception as e:
        logger.error(f"X Thread Generation Failed: {e}")
        return None
