import sys
import os
import time
import math
from datetime import datetime, timezone

# اضافه کردن مسیر ریشه پروژه به sys.path برای دسترسی به ماژول‌های داخلی
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from app.database.models import SessionLocal, Trend

# --- تنظیمات میرایی داینامیک بر اساس دسته‌بندی (Dynamic Decay Configuration) ---
# هرچه عدد به 1 نزدیک‌تر باشد، خبر دیرتر سرد می‌شود (ماندگاری بیشتر).
# هرچه عدد کوچک‌تر باشد، خبر با سرعت بیشتری از لیست داغ حذف می‌شود.

CATEGORY_DECAY_FACTORS = {
    "Siyaset": 0.98,    # سیاست: بسیار ماندگار (فقط ۲٪ کاهش در هر ساعت)
    "Ekonomi": 0.97,    # اقتصاد: ماندگار (۳٪ کاهش در هر ساعت)
    "Teknoloji": 0.94, # تکنولوژی: میان‌رده
    "Gündem": 0.92,    # عمومی/حوادث: میرایی نسبتاً سریع
    "Spor": 0.85,      # ورزش: میرایی بسیار سریع (۱۵٪ کاهش در هر ساعت)
    "Sanat": 0.88,     # هنر و مجله: میرایی سریع
    "Default": 0.93    # نرخ پیش‌فرض برای دسته‌های ناشناخته
}

# حداقل آستانه امتیاز: اگر امتیاز از این مقدار کمتر شود، روند میرایی متوقف یا ترند غیرفعال می‌شود.
MIN_TPS_THRESHOLD = 3.0
# بازه زمانی اجرای ورکر (ثانیه): هر ۳۰ دقیقه یکبار اجرا می‌شود.
CHECK_INTERVAL = 1800 

def apply_gravity_decay():
    """
    اعمال نرخ میرایی برداری/نمایی بر روی امتیاز TPS ترندهای فعال.
    این تابع تفاوت بین دسته‌بندی‌ها را در سرعت "سرد شدن" اخبار لحاظ می‌کند.
    """
    db = SessionLocal()
    try:
        # واکشی ترندهای فعال که دارای امتیاز مثبت هستند
        active_trends = db.query(Trend).filter(
            Trend.is_active == True,
            Trend.final_tps > MIN_TPS_THRESHOLD
        ).all()

        if not active_trends:
            return

        print(f"📉 [Gravity] شروع چرخه میرایی برای {len(active_trends)} ترند فعال...", flush=True)
        
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        decay_count = 0
        deactivated_count = 0

        for trend in active_trends:
            # محاسبه زمان سپری شده از آخرین آپدیت (به ساعت)
            time_diff = now - trend.last_updated
            hours_passed = time_diff.total_seconds() / 3600.0

            # میرایی فقط زمانی اعمال می‌شود که حداقل ۱ ساعت از آخرین سیگنال گذشته باشد
            if hours_passed >= 1.0:
                # تعیین نرخ میرایی بر اساس دسته بندی ترند
                category = trend.category if trend.category else "Default"
                decay_factor = CATEGORY_DECAY_FACTORS.get(category, CATEGORY_DECAY_FACTORS["Default"])
                
                old_score = trend.final_tps
                
                # فرمول میرایی نمایی: TPS_new = TPS_old * (Decay_Factor ^ Hours_Passed)
                # این فرمول باعث می‌شود اخبار قدیمی با گذشت زمان، قدرت خود را به صورت تصاعدی از دست بدهند.
                new_score = old_score * math.pow(decay_factor, hours_passed)
                
                # اعمال امتیاز جدید
                trend.final_tps = new_score
                trend.score = new_score # همگام‌سازی با فیلد قدیمی جهت سازگاری
                
                # اگر امتیاز به زیر حد بحرانی رسید، ترند را از حالت فعال خارج کن (Archive)
                if new_score < 2.0:
                    trend.is_active = False
                    deactivated_count += 1
                
                decay_count += 1
                logger_msg = f"   🔹 Trend {trend.id} ({category}): {old_score:.1f} -> {new_score:.1f}"
                print(logger_msg, flush=True)

        db.commit()
        print(f"✅ [Gravity] پایان چرخه. تغییرات: {decay_count} مورد | غیرفعال شده: {deactivated_count}", flush=True)

    except Exception as e:
        db.rollback()
        print(f"❌ [Gravity] خطای ورکر: {e}", flush=True)
    finally:
        db.close()

def main():
    """
    حلقه اصلی سرویس Gravity Decay
    """
    print("🪐 TrendiaTR Dynamic Gravity Worker Started.")
    print(f"⚙️ Configuration: Multi-Category Decay Active | Interval={CHECK_INTERVAL}s")
    
    while True:
        try:
            apply_gravity_decay()
        except KeyboardInterrupt:
            print("\n🛑 سرویس متوقف شد.")
            break
        except Exception as e:
            print(f"❌ خطای بحرانی در حلقه اصلی: {e}")
        
        # وقفه تا چرخه بعدی
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()