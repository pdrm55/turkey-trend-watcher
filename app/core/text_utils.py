import re
from bs4 import BeautifulSoup

# Spam keywords for filtering out advertisements and fraud
SPAM_KEYWORDS = [
    'bet', 'casino', 'bonus', 'çevrimsiz', 'yatırımsız', 
    'deneme bonusu', 'yasal bahis', 'slot', 'rulet', 
    'reklam', 'tıkla', 'linkte', 'kazan'
]

# Junk keywords for mandatory low-scoring (Astrology/Horoscopes/Spam)
JUNK_KEYWORDS = [
    'burç', 'fal ', 'günlük burç', 'astroloji', 'horoskop', 'astrolog'
]

# Layer 1: Structural Noise Patterns (Turkish News Clutter)
NOISE_PATTERNS = [
    r'ilgili haber', r'son dakika', r'tıklayın', r'abone ol', 
    r'takip et', r'daha fazlası için', r'ilginizi çekebilir', 
    r'haberin devamı', r'gelen aramalar', r'okuma süresi', 
    r'yayınlanma tarihi'
]

def normalize_turkish(text: str) -> str:
    """
    Normalizes specific Turkish characters for consistent text processing.
    Standard lower() in Python often fails with Turkish 'I' and 'İ'.
    """
    if not text:
        return ""
    
    # Manual replacement for Turkish specific casing
    text = text.replace('İ', 'i').replace('I', 'ı')
    return text.lower()

def is_spam(text: str) -> bool:
    """
    Checks if the given text contains any spam-related keywords.
    """
    if not text:
        return True
        
    text_lower = normalize_turkish(text)
    
    # Filter very short messages (usually just links or noise)
    if len(text.strip()) < 15:
        return True

    # Check against the spam list
    for keyword in SPAM_KEYWORDS:
        if keyword in text_lower:
            return True
            
    return False

def is_noise_line(line: str) -> bool:
    """
    Helper to identify structural noise lines in news content.
    """
    if not line or not line.strip():
        return True
        
    norm_line = normalize_turkish(line).strip()
    
    # 1. Check against noise patterns
    for pattern in NOISE_PATTERNS:
        if re.search(pattern, norm_line):
            return True
            
    # 2. Short line heuristics (< 40 chars) with irrelevant symbols
    if len(norm_line) < 40:
        if re.search(r'[>|/\\_]', norm_line):
            return True
            
    return False

def clean_text(text: str) -> str:
    """
    پاکسازی پیشرفته متن (بروزرسانی شده برای حذف کدهای HTML)
    این تابع تمام تگ‌های HTML، آدرس‌ها، منشن‌ها و فضاهای خالی اضافی را حذف می‌کند.
    """
    if not text:
        return ""
        
    # ۱. حذف تگ‌های HTML با استفاده از BeautifulSoup
    # این بخش مشکل نمایش کدهای سایت‌هایی مثل Milliyet را حل می‌کند
    try:
        soup = BeautifulSoup(text, "html.parser")
        # حذف بخش‌هایی که معمولاً حاوی کدهای مزاحم هستند مثل 'İlginizi Çekebilir'
        for section in soup.find_all(["section", "script", "style", "iframe", "noscript"], class_=lambda x: x and "mceNonEditable" in x):
            section.decompose()
        # Remove all script and style tags
        for tag in soup(["script", "style", "iframe", "noscript"]):
            tag.decompose()
            
        text = soup.get_text(separator="\n")
    except Exception:
        # اگر BeautifulSoup خطا داد، از رگکس ساده استفاده کن
        text = re.sub(r'<[^>]+>', '', text)

    # ۲. فیلتر کردن خطوط مزاحم (Layer 1 Noise Filtering)
    lines = text.split('\n')
    cleaned_lines = []
    for line in lines:
        if not is_noise_line(line):
            cleaned_lines.append(line.strip())
    
    text = " ".join(cleaned_lines)

    # ۳. حذف URLها
    text = re.sub(r'http\S+|www\.\S+', '', text)
    
    # ۴. حذف منشن‌ها و هشتگ‌ها
    text = re.sub(r'@\w+', '', text)
    text = re.sub(r'#\w+', '', text)
    
    # ۵. حذف کاراکترهای غیرمجاز (فقط حروف ترکی، اعداد و علائم نگارشی پایه)
    text = re.sub(r'[^\w\sçğıöşüÇĞİÖŞÜ,.?!-]', ' ', text)
    
    # ۶. پاکسازی فضاهای خالی اضافی
    text = re.sub(r'\s+', ' ', text).strip()
    
    return text

def slugify_turkish(text: str) -> str:
    """
    Converts a Turkish title into an SEO-friendly URL slug.
    Example: "Türkiye'de Ekonomi" -> "turkiyede-ekonomi"
    """
    if not text:
        return ""
    
    text = normalize_turkish(text)
    
    # Map special Turkish characters to English equivalents for URLs
    mapping = {
        "ş": "s", "ğ": "g", "ü": "u", "ö": "o", "ç": "c"
    }
    for search, replace in mapping.items():
        text = text.replace(search, replace)
    
    # Remove all non-alphanumeric characters except dashes and spaces
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    
    # Replace spaces with dashes and clean duplicates
    text = re.sub(r'\s+', '-', text).strip('-')
    
    return text