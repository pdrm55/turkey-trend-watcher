import streamlit as st
import sys
import os
import pandas as pd

# اضافه کردن مسیر ریشه پروژه به sys.path برای ایمپورت صحیح ماژول‌های app
# این کار باعث می‌شود اگر اسکریپت را از هر مسیری اجرا کردید، ماژول app به درستی پیدا شود
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, "../../"))
if project_root not in sys.path:
    sys.path.append(project_root)

# ایمپورت‌های دیتابیس بر اساس models.py پروژه شما
from app.database.models import SessionLocal, Trend, RawNews

# ==========================================
# تنظیمات صفحه Streamlit
# ==========================================
st.set_page_config(
    page_title="TrendiaTR Demo Dashboard",
    page_icon="🔥",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ==========================================
# توابع اتصال به دیتابیس
# ==========================================
@st.cache_resource(ttl=60) # کش کردن نتیجه برای ۶۰ ثانیه تا در هر بار رفرش UI به دیتابیس فشار نیاید
def check_db_connection():
    """تست اتصال به دیتابیس و دریافت آمار اولیه"""
    db = SessionLocal()
    try:
        trends_count = db.query(Trend).count()
        raw_news_count = db.query(RawNews).count()
        return True, {"trends": trends_count, "raw_news": raw_news_count}
    except Exception as e:
        return False, str(e)
    finally:
        db.close()

def get_db_session():
    """ایجاد یک نشست جدید دیتابیس (برای استفاده در سایر بخش‌ها)"""
    return SessionLocal()

# ==========================================
# سایدبار (Sidebar) - ساختار ناوبری
# ==========================================
st.sidebar.image("https://via.placeholder.com/300x100.png?text=TrendiaTR+Logo", use_container_width=True)
st.sidebar.title("کنترل پنل پرزنت")
st.sidebar.markdown("---")

# انتخابگر حالت (Live vs Replay)
display_mode = st.sidebar.radio(
    "انتخاب حالت نمایش:",
    ["Live (زنده)", "Replay (بازپخش)"],
    index=0,
    help="حالت زنده داده‌های فعلی را نشان می‌دهد. حالت بازپخش برای نمایش دمو و تکامل یک خبر در طول زمان است."
)

st.sidebar.markdown("---")
st.sidebar.caption("TrendiaTR v7.6 - AI Presentation Dashboard")

# ==========================================
# بدنه اصلی داشبورد (Main Body)
# ==========================================
st.title("🔥 TrendiaTR - داشبورد مانیتورینگ هوشمند")

# ------------------------------------------
# بخش تست دیتابیس (نمایش وضعیت اتصال)
# ------------------------------------------
st.markdown("### 🔌 وضعیت سیستم و دیتابیس")
is_connected, db_data = check_db_connection()

if is_connected:
    st.success("اتصال به دیتابیس PostgreSQL موفقیت‌آمیز بود. ✅")
    
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric(label="کل ترندهای پردازش شده", value=f"{db_data['trends']:,}")
    with col2:
        st.metric(label="کل اخبار خام (Raw News)", value=f"{db_data['raw_news']:,}")
    with col3:
        st.metric(label="وضعیت موتور پردازش", value="Active 🟢")
else:
    st.error(f"خطا در اتصال به دیتابیس: {db_data}")
    st.stop() # متوقف کردن اجرای بقیه کدها در صورت قطعی دیتابیس

st.divider()

# ------------------------------------------
# منطق سوئیچ بین نماها (Live / Replay)
# ------------------------------------------
if display_mode == "Live (زنده)":
    st.header("📡 مانیتورینگ زنده (Live Feed)")
    st.info("در این بخش داده‌های دیتابیس را به صورت درلحظه واکشی کرده و نمایش می‌دهیم.")
    
    # جایگاه برای کدهای گام بعدی (نمایش جدول ترندهای اخیر، نمودار TPS زنده و ...)
    st.markdown("*اینجا محل قرارگیری لیست ترندهای داغ و چارت‌های Real-time خواهد بود.*")

elif display_mode == "Replay (بازپخش)":
    st.header("⏪ بازپخش وقایع (Scenario Replay)")
    st.warning("در این حالت می‌توانید یک شناسه ترند (Trend ID) را انتخاب کرده و نحوه رشد امتیاز TPS و تکامل آن را در طول زمان شبیه‌سازی کنید.")
    
    # جایگاه برای کدهای گام بعدی (اسلایدر زمان، انتخابگر سناریو)
    st.markdown("*اینجا محل قرارگیری تایم‌لاین (Time-slider) و ابزارهای کنترل زمان برای پرزنت خواهد بود.*")