import os
from google import genai

def list_available_models():
    # خواندن کلید وب‌سرویس از متغیرهای محیطی
    api_key = os.getenv("GOOGLE_API_KEY")
    
    if not api_key:
        print("❌ Error: GOOGLE_API_KEY is not set in the environment.")
        print("Please run: export GOOGLE_API_KEY='your_api_key_here'")
        return

    print("🔍 Connecting to Google GenAI and fetching available models...\n")
    
    try:
        # ساخت کلاینت دقیقا مشابه پروژه اصلی
        client = genai.Client(api_key=api_key)
        
        count = 0
        flash_count = 0
        
        print("="*60)
        print(f"{'MODEL NAME (ID)'.ljust(40)} | {'VERSION'}")
        print("="*60)
        
        # دریافت لیست تمام مدل‌ها
        for model in client.models.list():
            # حذف پیشوند models/ برای خوانایی بهتر
            clean_name = model.name.replace("models/", "")
            version = model.version if hasattr(model, 'version') else 'N/A'
            
            # پرینت نام مدل
            print(f"🟢 {clean_name.ljust(37)} | {version}")
            
            # شمارش مدل‌های سری flash برای پروژه شما
            if 'flash' in clean_name.lower():
                flash_count += 1
                
            count += 1
            
        print("="*60)
        print(f"✅ Total Models Found: {count}")
        print(f"⚡ Total 'Flash' Models Available: {flash_count}")
        print("="*60)

    except Exception as e:
        print(f"\n❌ Execution Failed: {e}")
        print("Make sure your API key is valid and has permissions for this project.")

if __name__ == "__main__":
    list_available_models()
