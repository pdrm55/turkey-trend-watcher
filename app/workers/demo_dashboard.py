import streamlit as st
import sys
import os
import pandas as pd

# اضافه کردن مسیر ریشه پروژه به sys.path برای ایمپورت صحیح ماژول‌های app
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, "../../"))
if project_root not in sys.path:
    sys.path.append(project_root)

# ایمپورت‌های دیتابیس (XDraft برای نمایش ربات توییتر اضافه شد)
from app.database.models import SessionLocal, Trend, RawNews, TrendArrivals, XDraft
from sqlalchemy import desc, func

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
@st.cache_resource(ttl=60)
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
    """ایجاد یک نشست جدید دیتابیس"""
    return SessionLocal()

# ==========================================
# سایدبار (Sidebar) - ساختار ناوبری
# ==========================================
st.sidebar.image("https://via.placeholder.com/300x100.png?text=TrendiaTR+Logo", use_container_width=True)
st.sidebar.title("کنترل پنل پرزنت")
st.sidebar.markdown("---")

display_mode = st.sidebar.radio(
    "انتخاب حالت نمایش:",
    ["Live (زنده)", "Replay (بازپخش)"],
    index=0,
    help="حالت زنده داده‌های فعلی را نشان می‌دهد. حالت بازپخش برای نمایش دمو و عملکرد هوش مصنوعی است."
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
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric(label="کل ترندهای پردازش شده", value=f"{db_data['trends']:,}")
    with col2:
        st.metric(label="کل اخبار خام (Raw News)", value=f"{db_data['raw_news']:,}")
    with col3:
        st.metric(label="وضعیت موتور پردازش", value="Active 🟢")
else:
    st.error(f"خطا در اتصال به دیتابیس: {db_data}")
    st.stop()

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
            recent_trends = db.query(Trend).filter(Trend.is_active == True).order_by(desc(Trend.final_tps)).limit(10).all()
            
            if recent_trends:
                df_trends = pd.DataFrame([{
                    "شناسه": t.id,
                    "عنوان": t.title if t.title else "در حال تحلیل AI...",
                    "دسته‌بندی": t.category,
                    "امتیاز TPS": round(t.final_tps, 1),
                    "حجم اخبار": t.message_count
                } for t in recent_trends])
                
                st.dataframe(df_trends, use_container_width=True, hide_index=True)
            else:
                st.warning("هیچ ترندی یافت نشد.")

        with col2:
            st.subheader("📊 توزیع موضوعی")
            category_counts = db.query(Trend.category, func.count(Trend.id)).group_by(Trend.category).all()
            if category_counts:
                df_cats = pd.DataFrame(category_counts, columns=["Category", "Count"]).set_index("Category")
                st.bar_chart(df_cats, use_container_width=True)
                
    finally:
        db.close()

elif display_mode == "Replay (بازپخش)":
    st.header("⏪ کالبدشکافی هوش مصنوعی (AI Autopsy)")
    st.warning("یک رویداد را انتخاب کنید تا ببینیم موتور هوش مصنوعی TrendiaTR چگونه اخبار پراکنده را ادغام، تحلیل و تبدیل به محتوای شبکه‌های اجتماعی کرده است.")
    
    db = get_db_session()
    try:
        # واکشی ۲۰ ترند برتر برای لیست کشویی تا انتخاب‌های بیشتری برای دمو داشته باشید
        top_trends = db.query(Trend).filter(Trend.title.isnot(None)).order_by(desc(Trend.final_tps)).limit(20).all()
        
        if top_trends:
            trend_options = {f"[{t.id}] {t.title[:80]}... (TPS: {round(t.final_tps, 1)})": t.id for t in top_trends}
            
            selected_trend_label = st.selectbox("🎯 رویداد مورد نظر را انتخاب کنید:", list(trend_options.keys()))
            selected_trend_id = trend_options[selected_trend_label]
            selected_trend = db.query(Trend).filter(Trend.id == selected_trend_id).first()
            
            st.markdown(f"### {selected_trend.title}")
            st.caption(f"دسته بندی: {selected_trend.category} | امتیاز داغ بودن: {round(selected_trend.final_tps, 1)} | تاریخ شناسایی: {selected_trend.first_seen.strftime('%Y-%m-%d %H:%M') if selected_trend.first_seen else 'N/A'}")
            
            # ایجاد تب‌های نمایشی برای پرزنت حرفه‌ای
            tab1, tab2, tab3, tab4 = st.tabs(["📈 گراف شتاب (Velocity)", "🧠 تحلیل محتوایی AI", "🔍 اخبار خام (Cluster)", "🐦 خروجی توییتر (X-Studio)"])
            
            with tab1:
                trend_arrivals = db.query(TrendArrivals).filter(TrendArrivals.trend_id == selected_trend_id).order_by(TrendArrivals.timestamp).all()
                if trend_arrivals:
                    df_timeline = pd.DataFrame([{
                        "زمان": arrival.timestamp,
                        "تعداد اخبار ادغام شده": i + 1
                    } for i, arrival in enumerate(trend_arrivals)])
                    df_timeline = df_timeline.set_index("زمان")
                    
                    st.line_chart(df_timeline, use_container_width=True)
                    st.success(f"الگوریتم شباهت‌سنجی (ChromaDB) موفق شد **{len(trend_arrivals)}** خبر تکراری از خبرگزاری‌های مختلف را در این کلاستر واحد ادغام کند.")
                else:
                    st.info("داده‌های زمانی برای این ترند موجود نیست.")

            with tab2:
                colA, colB = st.columns([2, 1])
                with colA:
                    st.markdown("##### 📝 خلاصه خبری جمینای (Gemini Summary)")
                    st.info(selected_trend.summary or "بدون خلاصه.")
                with colB:
                    st.markdown("##### 🏷️ برچسب‌ها و موجودیت‌ها")
                    st.json(selected_trend.entities or {"وضعیت": "موجودیت یافت نشد"})
                    if selected_trend.tags:
                        st.write("برچسب‌ها:", ", ".join(selected_trend.tags))

            with tab3:
                st.markdown("##### 🕵️ اخبار تشکیل‌دهنده این کلاستر")
                st.write("این لیست نشان می‌دهد سیستم چه اخباری را مشابه تشخیص داده است:")
                raw_news_items = db.query(RawNews).filter(RawNews.trend_id == selected_trend_id).order_by(desc(RawNews.published_at)).limit(10).all()
                
                for idx, rn in enumerate(raw_news_items):
                    with st.expander(f"خبر {idx+1} | منبع: {rn.source_name} | زمان: {rn.published_at.strftime('%H:%M')}"):
                        st.write(rn.content)
                        if rn.external_id:
                            st.caption(f"لینک/آیدی مرجع: {rn.external_id}")

            with tab4:
                st.markdown("##### 🤖 پیش‌نویس‌های تولید شده برای شبکه X")
                drafts = db.query(XDraft).filter(XDraft.trend_id == selected_trend_id).all()
                if drafts:
                    for d in drafts:
                        status_color = "🟢 تایید/ارسال شده" if d.status == 'sent' else "🟡 در انتظار تایید (Draft)"
                        st.markdown(f"**وضعیت:** {status_color} | **نوع پست:** {d.draft_type}")
                        st.code(d.long_caption, language="text")
                        if d.image_short_text:
                            st.caption(f"متن پرامپت تصویر: {d.image_short_text}")
                        st.divider()
                else:
                    st.warning("هیچ پیش‌نویسی (Zero-Click یا Thread) برای این خبر در توییتر تولید نشده است.")
                    st.info("دلیل احتمالی: امتیاز TPS این خبر به حدنصاب انتشار خودکار نرسیده است.")

        else:
            st.info("ترندی برای نمایش یافت نشد.")
            
    finally:
        db.close()