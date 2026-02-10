import os
import json
import requests
from google.oauth2 import service_account
from google.auth.transport.requests import Request

# مسیر فایل کلید امنیتی در ریشه پروژه
KEY_FILE = "google_credentials.json"
# نقطه اتصال تایید شده نسخه ۳ گوگل
ENDPOINT = "https://indexing.googleapis.com/v3/urlNotifications:publish"

def notify_google(url, action="URL_UPDATED"):
    """
    ارسال دستور ایندکس آنی بر اساس رفرنس V3
    تست شده و موفق در محیط سرور
    """
    if not os.path.exists(KEY_FILE):
        return False, "Google credentials file missing."

    try:
        # ۱. احراز هویت و دریافت توکن دسترسی (Scope ثابت است)
        scopes = ["https://www.googleapis.com/auth/indexing"]
        credentials = service_account.Credentials.from_service_account_file(KEY_FILE, scopes=scopes)
        
        # تازه سازی توکن
        credentials.refresh(Request())
        token = credentials.token
        
        # ۲. آماده‌سازی هدرها و بدنه درخواست
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        data = {
            "url": url,
            "type": action
        }
        
        # ۳. ارسال درخواست POST (حذف هدر Host برای جلوگیری از اختلال شبکه داکر)
        response = requests.post(ENDPOINT, headers=headers, json=data, timeout=20)
        
        # ۴. تحلیل وضعیت پاسخ
        if response.status_code == 200:
            return True, "Success"
        else:
            try:
                error_info = response.json()
                msg = error_info.get('error', {}).get('message', f"Status {response.status_code}")
            except:
                msg = f"HTTP {response.status_code}"
            return False, msg

    except Exception as e:
        return False, f"System Error: {str(e)}"
