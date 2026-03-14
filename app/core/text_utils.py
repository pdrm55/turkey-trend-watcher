import re
import html
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
    r'yayınlanma tarihi', r'devamı için', r'tıkla', r'ilgili haberler', 
    r'galeri için', r'video için', r'paylaş', r'yorum yap', r'yazdır', 
    r'haberleri', r'son dakika haberi', r'gelişmeler için', r'kaynak\s*:'
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
    
    # 1. Check for lines that consist ONLY of numbers, dates, or social media handles
    # Remove handles, dates, numbers and separators to see if any content remains
    temp = re.sub(r'@\w+', '', norm_line)
    temp = re.sub(r'\d+[\./-]\d+[\./-]\d+', '', temp)
    temp = re.sub(r'[\d\s\.,:/-]+', '', temp)
    if not temp.strip():
        return True

    # 2. Filter out lines that are just "reklam" or "advertisement"
    if norm_line in ['reklam', 'advertisement', 'ilan']:
        return True
    
    # 3. Word Count heuristic: If < 5 words AND contains noise keywords
    if len(norm_line.split()) < 5:
        for pattern in NOISE_PATTERNS:
            if re.search(pattern, norm_line):
                return True
            
    # 4. Short line heuristics (< 40 chars) with irrelevant symbols
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
        
    # Decode HTML entities first so BeautifulSoup can properly identify and remove tags
    text = html.unescape(text)

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
            
        text = soup.get_text(separator=" | ")
    except Exception:
        # اگر BeautifulSoup خطا داد، از رگکس ساده استفاده کن
        text = re.sub(r'<[^>]+>', '', text)

    # ۲. فیلتر کردن خطوط مزاحم (Layer 1 Noise Filtering)
    lines = re.split(r'[|\n]', text)
    cleaned_lines = []
    for line in lines:
        if not is_noise_line(line):
            # Strip artifacts (dots, dashes) often left after removing links
            line = line.strip(' .-_')
            line = re.sub(r'\s+', ' ', line).strip()
            if line:
                cleaned_lines.append(line)
    
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