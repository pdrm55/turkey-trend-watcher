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
# تزریق CSS برای راست‌چین (RTL) و فونت فارسی
# ==========================================
st.markdown("""
    <style>
    @import url('https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/Vazirmatn-font-face.css');
    
    /* تنظیمات کانتینر اصلی برای رفع مشکل چسبیدن به لبه‌ها */
    .block-container {
        direction: rtl;
        text-align: right;
        padding-top: 2rem !important;
        padding-bottom: 2rem !important;
        max-width: 90% !important; /* ایجاد حاشیه مناسب از کناره‌ها */
        margin: auto;
    }
    
    /* اعمال فونت روی متون، با اولویت پایین‌تر تا آیکون‌ها خراب نشوند */
    html, body, [class*="css"], p, h1, h2, h3, h4, h5, h6, label, li, span, div {
        font-family: 'Vazirmatn', sans-serif;
    }
    
    /* محافظت قطعی از آیکون‌های متریال استریم‌لیت */
    .material-symbols-rounded, 
    .material-icons, 
    [data-testid="stIconMaterial"], 
    [data-testid="stExpanderToggleIcon"],
    i {
        font-family: 'Material Symbols Rounded', 'Material Icons', sans-serif !important;
        direction: ltr !important;
    }
    
    /* اصلاح تب‌ها برای نمایش صحیح راست‌به‌چپ */
    .stTabs [data-baseweb="tab-list"] {
        justify-content: flex-start;
        flex-direction: row-reverse;
    }
    
    /* اصلاح دکمه‌های آکاردئون (Expander) */
    [data-testid="stExpander"] details summary {
        flex-direction: row-reverse;
        text-align: right;
    }
    
    [data-testid="stExpander"] details summary svg {
        margin-left: 0.5rem;
    }
    
    /* راست‌چین کردن متون داخل ویجت‌ها، متریک‌ها و دیتافریم‌ها */
    [data-testid="stMetricValue"], [data-testid="stMetricLabel"], [data-testid="stMetricDelta"] {
        text-align: right !important;
        direction: rtl;
    }
    .stDataFrame {
        direction: rtl;
    }
    
    /* 🌟 فیکس جدید: چپ‌چین کردن باکس انتخاب خبر و پاپ‌آپ آن (چون تیترها ترکی و لاتین هستند) 🌟 */
    div[data-baseweb="select"], div[data-baseweb="popover"] {
        direction: ltr !important;
        text-align: left !important;
    }
    
    /* لیبل بالای سلکت‌باکس ("رویداد مورد نظر را انتخاب کنید") همچنان راست‌چین بماند */
    label[data-testid="stWidgetLabel"] {
        direction: rtl !important;
        text-align: right !important;
        width: 100%;
    }
    </style>
""", unsafe_allow_html=True)

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
                    "حجم اخبار": t.message_count,
                    "زمان کشف (First Seen)": t.first_seen.strftime('%Y-%m-%d %H:%M') if t.first_seen else '-',
                    "آخرین آپدیت": t.last_updated.strftime('%H:%M:%S') if t.last_updated else '-'
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
        # 🌟 فیکس 🌟: اضافه شدن desc(Trend.id) برای جلوگیری از مرتب‌سازی تصادفیِ اخباری که TPS برابر دارند
        top_trends = db.query(Trend).filter(Trend.title.isnot(None)).order_by(desc(Trend.final_tps), desc(Trend.id)).limit(20).all()
        
        if top_trends:
            # 🌟 فیکس 🌟: جداسازی کامل دیتای نمایشی از حافظه Streamlit
            trend_mapping = {t.id: f"{t.title[:90]}... (TPS: {round(t.final_tps, 1)}) [ID: {t.id}]" for t in top_trends}
            
            # در اینجا استریم‌لیت فقط با ID ثابت کار می‌کند و گمراه نمی‌شود
            selected_trend_id = st.selectbox(
                "🎯 رویداد مورد نظر را انتخاب کنید:",
                options=[t.id for t in top_trends],
                format_func=lambda x: trend_mapping[x]
            )
            
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
                
                # دریافت تمام اخبار این ترند و مرتب‌سازی از قدیمی به جدید برای محاسبه زمان
                raw_news_items = db.query(RawNews).filter(RawNews.trend_id == selected_trend_id).order_by(RawNews.published_at).all()
                
                if raw_news_items:
                    earliest_news_time = raw_news_items[0].published_at
                    discovery_time = selected_trend.first_seen
                    publish_time = selected_trend.last_updated
                    
                    # محاسبه اختلاف زمان (به دقیقه)
                    reaction_diff = (discovery_time - earliest_news_time).total_seconds() / 60
                    publish_diff = (publish_time - discovery_time).total_seconds() / 60
                    
                    # نمایش متریک‌های زمانی برای مقایسه
                    st.info("⏱️ **مقایسه سرعت عملکرد پلتفرم با خبرگزاری‌ها:**")
                    time_col1, time_col2, time_col3 = st.columns(3)
                    
                    with time_col1:
                        st.metric("۱. انتشار اولین خبر (خبرگزاری‌ها)", earliest_news_time.strftime('%H:%M:%S'))
                    with time_col2:
                        st.metric("۲. کشف و ساخت کلاستر (AI)", discovery_time.strftime('%H:%M:%S'), delta=f"{int(reaction_diff)} دقیقه فاصله با اولین خبر", delta_color="inverse")
                    with time_col3:
                        st.metric("۳. انتشار نهایی در پلتفرم", publish_time.strftime('%H:%M:%S'), delta=f"{int(publish_diff)} دقیقه پردازش و خلاصه", delta_color="off")
                        
                    st.divider()
                    st.markdown(f"**لیست اخبار خام (تعداد {len(raw_news_items)} خبر در این کلاستر):**")
                    
                    # نمایش لیست اخبار (از جدید به قدیم)
                    for idx, rn in enumerate(reversed(raw_news_items)):
                        # گرفتن 60 کاراکتر اول خبر برای نمایش در تیتر کشو
                        content_snippet = (rn.content[:60] + "...") if rn.content else "متن خبر خالی است"
                        
                        with st.expander(f"📌 منبع: {rn.source_name} | {content_snippet}"):
                            st.write(f"**زمان انتشار مرجع:** {rn.published_at.strftime('%Y-%m-%d %H:%M:%S')}")
                            st.markdown("---")
                            st.write(rn.content)
                            if rn.external_id:
                                st.caption(f"لینک/آیدی مرجع: {rn.external_id}")
                else:
                    st.warning("هیچ خبر خامی برای این کلاستر ثبت نشده است.")

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