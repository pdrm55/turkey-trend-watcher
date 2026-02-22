import requests
from bs4 import BeautifulSoup
import urllib.parse
import json
import re
import time
import random

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Safari/605.1.15"
]

def test_bing_search(raw_query):
    print("\n" + "="*50)
    print(f"🔍 مرحله ۱: دریافت کوئری خام")
    print(f"متن ورودی: {raw_query}")

    # شبیه‌سازی منطق پاک‌سازی ربات شما
    toxic_words = [
        'sosyal medya', 'gündem', 'gündeme', 'viral', 'tepki', 'tepkiler', 
        'trend', 'trendler', 'olay oldu', 'şok', 'şaşkına', 'çevirdi', 
        'video', 'izle', 'kullanıcılar', 'sosyal medyada', '𝕏', '📰', '#'
    ]
    clean_query = raw_query.lower()
    for toxic in toxic_words:
        clean_query = clean_query.replace(toxic, ' ')

    clean_query = re.sub(r'[^\w\s]', ' ', clean_query)
    words = [w for w in clean_query.split() if len(w) > 2]
    clean_query = " ".join(words[:3]).strip()
    
    print(f"🧹 متن پاک‌سازی شده (فقط ۳ کلمه اصلی): {clean_query}")

    if not clean_query:
        print("❌ خطا: بعد از پاک‌سازی کلمه‌ای باقی نماند!")
        return

    trusted_sites = "site:ntv.com.tr OR site:haberturk.com OR site:sabah.com.tr OR site:hurriyet.com.tr OR site:ntvspor.net OR site:trthaber.com"
    final_query = f"{clean_query} ({trusted_sites})"
    print(f"🎯 مرحله ۲: ساختار نهایی جستجو در بینگ:\n{final_query}")

    encoded_query = urllib.parse.quote_plus(clean_query + f" ({trusted_sites})")
    url = f"https://www.bing.com/images/search?q={encoded_query}&cc=TR&setmkt=tr-TR&setlang=tr&qft=+filterui:photo-photo+filterui:aspect-wide"

    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "tr-TR,tr;q=0.9",
        "Referer": "https://www.bing.com/",
        "Cookie": "SRCHHPGUSR=ADLT=OFF&NRSLT=-1&CW=1366&CH=768&DPR=1&UTC=180&WLS=2&SRCHLANG=tr"
    }

    print("\n" + "="*50)
    print(f"🌐 مرحله ۳: اتصال به بینگ (با جعل موقعیت ترکیه)...")
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        print(f"📡 وضعیت پاسخ بینگ: {resp.status_code}")
        
        soup = BeautifulSoup(resp.content, 'html.parser')
        elements = soup.find_all('a', class_='iusc')
        print(f"🖼️ تعداد {len(elements)} عکس در صفحه نتایج پیدا شد.")
        print("="*50 + "\n")

        for i, el in enumerate(elements[:10]): # بررسی 10 عکس اول
            try:
                m_data = json.loads(el.get('m', '{}'))
                img_url = m_data.get('murl')
                
                if not img_url:
                    continue

                print(f"--- بررسی عکس شماره #{i+1} ---")
                print(f"🔗 لینک اصلی عکس: {img_url}")

                # تلاش برای دانلود عکس دقیقاً با همان متد ربات
                img_resp = requests.get(img_url, headers={"User-Agent": headers["User-Agent"], "Referer": "https://www.bing.com/"}, timeout=7)
                content_type = img_resp.headers.get('Content-Type', '')

                print(f"📥 وضعیت دانلود: {img_resp.status_code} | نوع فایل: {content_type}")

                if img_resp.status_code == 200 and 'image' in content_type:
                    print(f"✅ موفقیت! این دقیقاً همان عکسی است که ربات روی سایت می‌گذارد.")
                    filename = f"test_result_{i+1}.jpg"
                    with open(filename, "wb") as f:
                        f.write(img_resp.content)
                    print(f"💾 عکس در فایل '{filename}' ذخیره شد. لطفاً آن را باز کنید و ببینید!")
                    break # خروج از حلقه بعد از اولین موفقیت
                else:
                    print(f"❌ سرورِ سایت اجازه دانلود نداد (یا فایل عکس نبود). عبور و رفتن به عکس بعدی...\n")
                    
            except Exception as e:
                print(f"⚠️ خطای پردازش در عکس #{i+1}: {e}\n")

    except Exception as e:
        print(f"❌ خطای کلی در اتصال: {e}")

if __name__ == '__main__':
    while True:
        user_input = input("\n📝 یک تیتر خبر یا کلمه کلیدی وارد کنید (یا بنویسید exit برای خروج): ")
        if user_input.lower() == 'exit':
            break
        test_bing_search(user_input)