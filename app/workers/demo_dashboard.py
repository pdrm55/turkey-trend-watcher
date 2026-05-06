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
from app.database.models import SessionLocal, Trend, RawNews, TrendArrivals
from sqlalchemy import desc, func
import pandas as pd

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
    
    db = get_db_session()
    try:
        col1, col2 = st.columns([2, 1])
        
        with col1:
            st.subheader("🔥 ۱۰ ترند داغ اخیر")
            # واکشی ۱۰ ترند برتر فعال بر اساس امتیاز TPS
            recent_trends = db.query(Trend).filter(Trend.is_active == True).order_by(desc(Trend.final_tps)).limit(10).all()
            
            if recent_trends:
                df_trends = pd.DataFrame([{
                    "شناسه": t.id,
                    "عنوان": t.title if t.title else "در حال تحلیل AI...",
                    "دسته‌بندی": t.category,
                    "امتیاز TPS": round(t.final_tps, 1),
                    "حجم اخبار": t.message_count
                } for t in recent_trends])
                
                # نمایش جدول تعاملی
                st.dataframe(df_trends, use_container_width=True, hide_index=True)
            else:
                st.warning("هیچ ترندی یافت نشد.")

        with col2:
            st.subheader("📊 توزیع موضوعی")
            # محاسبه تعداد ترندها در هر دسته‌بندی
            category_counts = db.query(Trend.category, func.count(Trend.id)).group_by(Trend.category).all()
            if category_counts:
                df_cats = pd.DataFrame(category_counts, columns=["Category", "Count"]).set_index("Category")
                st.bar_chart(df_cats, use_container_width=True)
                
    finally:
        db.close()

elif display_mode == "Replay (بازپخش)":
    st.header("⏪ بازپخش وقایع (Scenario Replay)")
    st.warning("در این حالت می‌توانید یک رویداد خاص را انتخاب کرده و نحوه رشد و تکامل آن را در طول زمان (براساس ورود اخبار به کلاستر) بررسی کنید.")
    
    db = get_db_session()
    try:
        # واکشی ۵ ترند برتر که دارای عنوان هستند برای لیست کشویی
        top_trends = db.query(Trend).filter(Trend.title.isnot(None)).order_by(desc(Trend.final_tps)).limit(5).all()
        
        if top_trends:
            trend_options = {f"[{t.id}] {t.title[:60]}... (TPS: {round(t.final_tps, 1)})": t.id for t in top_trends}
            
            selected_trend_label = st.selectbox("یک رویداد را برای آنالیز زمانی انتخاب کنید:", list(trend_options.keys()))
            selected_trend_id = trend_options[selected_trend_label]
            
            # واکشی تاریخچه ورود اخبار (سیگنال‌ها) به این ترند
            trend_arrivals = db.query(TrendArrivals).filter(TrendArrivals.trend_id == selected_trend_id).order_by(TrendArrivals.timestamp).all()
            
            if trend_arrivals:
                st.markdown("### 📈 خط زمانی رشد خبر (Timeline Velocity)")
                
                # تبدیل داده‌ها به فرمت مناسب تایم‌لاین تجمعی
                df_timeline = pd.DataFrame([{
                    "زمان": arrival.timestamp,
                    "حجم اخبار ادغام شده": i + 1
                } for i, arrival in enumerate(trend_arrivals)])
                
                df_timeline = df_timeline.set_index("زمان")
                
                # رسم نمودار خطی
                st.line_chart(df_timeline, use_container_width=True)
                
                st.success(f"🎯 هوش مصنوعی تا این لحظه **{len(trend_arrivals)}** خبر پراکنده را تشخیص داده و به این رویداد متصل (Merge) کرده است.")
            else:
                st.info("تاریخچه زمانی (Arrivals) برای این ترند ثبت نشده است.")
        else:
            st.info("ترند مناسبی برای بازپخش یافت نشد.")
            
    finally:
        db.close()