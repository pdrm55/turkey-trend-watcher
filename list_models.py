import os
import sys
from dotenv import load_dotenv

# تلاش برای ایمپورت کتابخانه جدید
try:
    from google import genai
except ImportError:
    print("❌ Error: 'google-genai' library is not installed.")
    print("👉 Run: pip install google-genai")
    sys.exit(1)

# بارگذاری متغیرها
load_dotenv()

api_key = os.getenv("GOOGLE_API_KEY")

if not api_key:
    print("❌ Error: GOOGLE_API_KEY not found in .env")
    sys.exit(1)

def main():
    print("📡 Connecting to Google API...")
    try:
        client = genai.Client(api_key=api_key)
        
        print("\n✅ Connection Successful! Here are your available models:\n")
        print(f"{'Model Name':<30} | {'Display Name'}")
        print("-" * 50)
        
        # دریافت لیست مدل‌ها
        # نکته: ممکن است نام‌ها با 'models/' شروع شوند
        count = 0
        for model in client.models.list():
            # فیلتر کردن مدل‌هایی که قابلیت تولید محتوا دارند
            # برخی مدل‌ها فقط برای امبدینگ هستند
            model_name = model.name
            display_name = getattr(model, 'display_name', 'N/A')
            
            # مدل‌های جمنای را هایلایت می‌کنیم
            prefix = "👉 " if "gemini" in model_name.lower() and "flash" in model_name.lower() else "   "
            
            print(f"{prefix}{model_name:<27} | {display_name}")
            count += 1

        print("-" * 50)
        print(f"\nTotal models found: {count}")
        print("\n💡 Please copy the exact name of the 'Flash' model (e.g., 'gemini-1.5-flash')")
        print("   and update the MODEL_NAME in 'app/workers/summarizer.py'.")

    except Exception as e:
        print(f"\n❌ Error calling Google API: {e}")
        print("Possible reasons:")
        print("1. Your API Key might be invalid or expired.")
        print("2. Your region might be restricted.")
        print("3. You haven't enabled the API in Google AI Studio.")

if __name__ == "__main__":
    main()