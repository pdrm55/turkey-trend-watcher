import os
import json
import logging
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
    Generates optimized X (Twitter) Premium content using Gemini.
    
    Args:
        trend_title (str): The headline of the trend.
        cluster_text (str): The raw text content/summary of the news.
        category (str): The category of the news.
        
    Returns:
        dict: A dictionary with 'ai_summary', 'interaction_question', 'hashtags', and 'image_short_text', or None if failed.
    """
    if not client:
        logger.error("Gemini client is not initialized.")
        return None

    prompt = f"""
    ### SYSTEM ROLE
    You are an "Expert Turkish Social Media Editor" for the news platform 'TrendiaTR'. 
    Your goal is to create highly engaging, professional, and viral content for X (Twitter) based on a "Zero-Click" strategy, where the user gets all the key info directly in the tweet.

    ### INPUT DATA
    - Headline: {trend_title}
    - Category: {category}
    - Context: {cluster_text}

    ### TASK
    Analyze the context and generate a JSON response with exactly these 4 keys:

    1. "ai_summary": 
       - A 1-line highly engaging summary of the news.
       - Must include 1 or 2 relevant emojis.

    2. "interaction_question":
       - An open-ended, thought-provoking question related to the news to encourage user comments and interaction.

    3. "hashtags":
       - A list of exactly 2 relevant hashtags (without the # symbol). Example: ["Siyaset", "Ekonomi"]

    4. "image_short_text":
       - A heavily compressed, single-sentence summary of the news.
       - STRICTLY UNDER 130 CHARACTERS.
       - This text will be printed physically on an image, so it must be concise.
       - NO EMOJIS in this specific field.

    ### OUTPUT FORMAT (JSON ONLY)
    {{
        "ai_summary": "...",
        "interaction_question": "...",
        "hashtags": ["tag1", "tag2"],
        "image_short_text": "..."
    }}
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
        required_keys = ["ai_summary", "interaction_question", "hashtags", "image_short_text"]
        if not all(k in result for k in required_keys):
            logger.error(f"AI response missing required keys. Got: {list(result.keys())}")
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
        cluster_text (str): The raw text content/summary of the news.
        category (str): The category of the news.
        
    Returns:
        dict: A dictionary with tweet parts and image text, or None if failed.
    """
    if not client:
        logger.error("Gemini client is not initialized.")
        return None

    prompt = f"""
    You are an Expert Turkish Social Media Editor. Create a viral 5-part Twitter thread (flood) based on this news.

    CRITICAL RULE: ALL GENERATED TEXT MUST BE STRICTLY IN THE TURKISH LANGUAGE (TÜRKÇE).

    Headline: {trend_title}
    Context: {cluster_text}

    Generate a JSON response with exactly these 6 keys:
    - 'tweet_1_hook': A shocking or highly engaging opening line with emojis (in Turkish). No hashtags.
    - 'tweet_2_context': 2-3 lines explaining the core of the event simply (in Turkish).
    - 'tweet_3_data': 2 or 3 bullet points with hard facts or data from the context (in Turkish).
    - 'tweet_4_insight': A short AI prediction or analysis of the consequences (in Turkish).
    - 'tweet_5_cta': An engaging question to the audience and exactly 2 hashtags (in Turkish).
    - 'image_short_text': A highly compressed, single-sentence summary strictly under 130 chars (NO emojis) to be printed on the image (in Turkish).
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