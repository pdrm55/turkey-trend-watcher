import re

# کلمات کلیدی اسپم (شرط‌بندی، تبلیغات، کلاهبرداری)
SPAM_KEYWORDS = [
    'bet', 'casino', 'bonus', 'çevrimsiz', 'yatırımsız', 
    'deneme bonusu', 'yasal bahis', 'slot', 'rulet', 
    'reklam', 'tıkla', 'linkte', 'kazan'
]

def normalize_turkish(text: str) -> str:
    """
    نرمال‌سازی حروف خاص ترکی استانبولی.
    در پایتون lower() معمولی برای I و İ ترکی درست کار نمی‌کند.
    """
    if not text:
        return ""
    
    # تبدیل دستی حروف بزرگ ترکی به کوچک
    text = text.replace('İ', 'i').replace('I', 'ı')
    return text.lower()

def is_spam(text: str) -> bool:
    """
    بررسی می‌کند آیا متن حاوی کلمات اسپم است یا خیر.
    """
    if not text:
        return True
        
    text_lower = normalize_turkish(text)
    
    # اگر طول پیام خیلی کم باشد (مثلا فقط یک لینک)
    if len(text.strip()) < 15:
        return True

    # بررسی کلمات کلیدی
    for keyword in SPAM_KEYWORDS:
        if keyword in text_lower:
            return True
            
    return False

def clean_text(text: str) -> str:
    """
    حذف نویزها از متن خبر برای آماده‌سازی جهت وکتور شدن.
    """
    if not text:
        return ""
        
    # 1. حذف لینک‌ها (URL)
    text = re.sub(r'http\S+|www\.\S+', '', text)
    
    # 2. حذف منشن‌ها (@username) و هشتگ‌ها (#tag)
    text = re.sub(r'@\w+', '', text)
    text = re.sub(r'#\w+', '', text)
    
    # 3. حذف کاراکترهای غیر الفبایی (ایموجی‌ها و نمادها)
    # نگه داشتن حروف ترکی: çğıöşüÇĞİÖŞÜ
    text = re.sub(r'[^\w\sçğıöşüÇĞİÖŞÜ,.?!-]', ' ', text)
    
    # 4. حذف فاصله‌های اضافی
    text = re.sub(r'\s+', ' ', text).strip()
    
    return text

def slugify_turkish(text: str) -> str:
    """
    تبدیل عنوان ترکی به اسلاگ خوانا برای سئو.
    مثال: "Türkiye'de Ekonomi" -> "turkiyede-ekonomi"
    """
    if not text:
        return ""
    
    # استفاده از نرمال‌سازی موجود برای مدیریت درست حروف I و İ
    text = normalize_turkish(text)
    
    # تبدیل حروف خاص ترکی به معادل‌های انگلیسی برای URL
    mapping = {
        "ş": "s", "ğ": "g", "ü": "u", "ö": "o", "ç": "c"
    }
    for search, replace in mapping.items():
        text = text.replace(search, replace)
    
    # حذف کاراکترهای غیرمجاز (فقط حروف انگلیسی، اعداد، فضا و خط تیره)
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    
    # تبدیل فضاهای خالی به خط تیره و حذف خط تیره‌های تکراری یا اضافی در ابتدا و انتها
    text = re.sub(r'\s+', '-', text).strip('-')
    
    return text
