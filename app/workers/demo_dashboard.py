import streamlit as st
import sys
import os
import pandas as pd
import time
import altair as alt

# اضافه کردن مسیر ریشه پروژه به sys.path
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, "../../"))
if project_root not in sys.path:
    sys.path.append(project_root)

# ایمپورت‌های دیتابیس (TrendScoreHistory اضافه شد)
from app.database.models import SessionLocal, Trend, RawNews, TrendArrivals, XDraft, TrendScoreHistory
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
    .block-container { direction: rtl; text-align: right; max-width: 90% !important; margin: auto; }
    html, body, p, h1, h2, h3, h4, h5, h6, label, li, span, div { font-family: 'Vazirmatn', sans-serif; }
    .material-symbols-rounded, .material-icons, [data-testid="stIconMaterial"], [data-testid="stExpanderToggleIcon"] {
        font-family: 'Material Symbols Rounded', 'Material Icons', sans-serif !important;
        direction: ltr !important;
    }
    .stTabs [data-baseweb="tab-list"] { justify-content: flex-start; flex-direction: row-reverse; }
    [data-testid="stExpander"] details summary { flex-direction: row-reverse; text-align: right; }
    [data-testid="stMetricValue"], [data-testid="stMetricLabel"] { text-align: right !important; direction: rtl; }
    div[data-baseweb="select"], div[data-baseweb="popover"] { direction: ltr !important; text-align: left !important; }
    label[data-testid="stWidgetLabel"] { direction: rtl !important; text-align: right !important; width: 100%; }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# توابع اتصال به دیتابیس
# ==========================================
@st.cache_resource(ttl=60)
def check_db_connection():
    db = SessionLocal()
    try:
        return True, {"trends": db.query(Trend).count(), "raw_news": db.query(RawNews).count()}
    except Exception as e: return False, str(e)
    finally: db.close()

def get_db_session(): return SessionLocal()

# ==========================================
# سایدبار (Sidebar)
# ==========================================
st.sidebar.markdown("""
    <div style="display: flex; align-items: center; justify-content: center; gap: 15px; margin-bottom: 25px; direction: ltr;">
        <div style="background-color: #ef4444; border-radius: 14px; width: 56px; height: 56px; display: flex; align-items: center; justify-content: center; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
            <svg viewBox="0 0 100 100" width="38" height="38" xmlns="http://www.w3.org/2000/svg">
                <text x="50%" y="54%" dominant-baseline="middle" text-anchor="middle" font-family="Arial, sans-serif" font-weight="800" font-size="62" fill="white">TT</text>
            </svg>
        </div>
        <div style="font-size: 26px; font-weight: 800;"><span style="color: #0f172a;">Trendia</span><span style="color: #ef4444;">TR</span></div>
    </div>
""", unsafe_allow_html=True)

display_mode = st.sidebar.radio("انتخاب حالت نمایش:", ["Live (زنده)", "Replay (بازپخش)"])

# ==========================================
# بدنه اصلی داشبورد
# ==========================================
st.title("🔥 TrendiaTR - مانیتورینگ هوشمند")
is_connected, db_data = check_db_connection()

if is_connected:
    c1, c2, c3 = st.columns(3)
    c1.metric("کل ترندها", f"{db_data['trends']:,}")
    c2.metric("کل اخبار خام", f"{db_data['raw_news']:,}")
    c3.metric("وضعیت موتور", "Active 🟢")
else:
    st.error(f"خطا در دیتابیس: {db_data}"); st.stop()

st.divider()

if display_mode == "Live (زنده)":
    db = get_db_session()
    try:
        st.subheader("🔥 ۱۰ ترند داغ اخیر")
        recent = db.query(Trend).filter(Trend.is_active == True).order_by(desc(Trend.final_tps)).limit(10).all()
        if recent:
            df = pd.DataFrame([{"شناسه": t.id, "عنوان": t.title, "دسته": t.category, "TPS": round(t.final_tps, 1), "اخبار": t.message_count, "کشف": t.first_seen.strftime('%H:%M')} for t in recent])
            st.dataframe(df, use_container_width=True, hide_index=True)
        
        st.subheader("📊 توزیع موضوعی")
        cats = db.query(Trend.category, func.count(Trend.id)).group_by(Trend.category).all()
        if cats:
            df_cats = pd.DataFrame(cats, columns=["دسته", "تعداد"]).set_index("دسته")
            st.bar_chart(df_cats, use_container_width=True, height=300)
    finally: db.close()

elif display_mode == "Replay (بازپخش)":
    st.header("⏪ کالبدشکافی هوش مصنوعی (AI Autopsy)")
    db = get_db_session()
    try:
        top_trends = db.query(Trend).filter(Trend.title.isnot(None)).order_by(desc(Trend.final_tps), desc(Trend.id)).limit(20).all()
        if top_trends:
            trend_mapping = {t.id: f"{t.title[:80]}... (TPS: {round(t.final_tps, 1)}) [ID: {t.id}]" for t in top_trends}
            selected_trend_id = st.selectbox("🎯 رویداد مورد نظر را انتخاب کنید:", options=[t.id for t in top_trends], format_func=lambda x: trend_mapping[x])
            trend_obj = db.query(Trend).get(selected_trend_id)

            tab1, tab2, tab3, tab4 = st.tabs(["📈 گراف شتاب و نوسان (Live TPS)", "🧠 تحلیل محتوایی AI", "🔍 اخبار خام (Cluster)", "🐦 خروجی توییتر"])
            
            with tab1:
                # دریافت همزمان آرایوال‌ها و تاریخچه نمرات (فیلد جدید)
                arrivals = db.query(TrendArrivals, RawNews.source_name).outerjoin(RawNews).filter(TrendArrivals.trend_id == selected_trend_id).order_by(TrendArrivals.timestamp).all()
                score_history = db.query(TrendScoreHistory).filter(TrendScoreHistory.trend_id == selected_trend_id).order_by(TrendScoreHistory.timestamp).all()
                
                if arrivals:
                    start_replay = st.button("▶️ پخش سناریوی کشف و نوسان TPS", type="primary")
                    
                    if start_replay:
                        st.info("💡 در حال بازسازی تایم‌لاین ورود اخبار و تغییرات نمره داغ بودن...")
                        
                        # مکان‌نماها
                        metric_placeholder = st.empty()
                        chart_placeholder = st.empty()
                        tps_chart_placeholder = st.empty()
                        log_placeholder = st.empty()

                        history_df = pd.DataFrame()
                        tps_df = pd.DataFrame()
                        sources = []
                        
                        # ترکیب دو منبع داده بر اساس زمان برای انیمیشن یکپارچه
                        timeline_events = []
                        for a, src in arrivals: timeline_events.append({'time': a.timestamp, 'type': 'arrival', 'data': src})
                        for s in score_history: timeline_events.append({'time': s.timestamp, 'type': 'score', 'data': s.tps_score})
                        timeline_events.sort(key=lambda x: x['time'])

                        arrival_count = 0
                        current_tps = 0.0
                        peak_tps = 0.0

                        for event in timeline_events:
                            if event['type'] == 'arrival':
                                arrival_count += 1
                                sources.insert(0, {"زمان": event['time'].strftime('%H:%M:%S'), "منبع": event['data'] or "سیستم"})
                                new_row = pd.DataFrame({"زمان": [event['time']], "حجم اخبار": [arrival_count]})
                                history_df = pd.concat([history_df, new_row])
                            else:
                                current_tps = event['data']
                                if current_tps > peak_tps: peak_tps = current_tps
                                new_score = pd.DataFrame({"زمان": [event['time']], "نمره TPS": [current_tps]})
                                tps_df = pd.concat([tps_df, new_score])

                            # ۱. آپدیت متریک‌ها
                            with metric_placeholder.container():
                                m1, m2, m3 = st.columns(3)
                                m1.metric("زمان رویداد", event['time'].strftime('%H:%M:%S'))
                                m2.metric("نمره TPS فعلی", f"{round(current_tps, 1)}", delta=f"{arrival_count} خبر")
                                m3.metric("قله نمره (Peak)", f"{round(peak_tps, 1)}")

                            # ۲. آپدیت گراف حجم
                            if not history_df.empty:
                                c1 = alt.Chart(history_df).mark_line(point=True, color="#3b82f6").encode(x='زمان:T', y='حجم اخبار:Q').properties(height=200, title="رشد کلاستر خبری")
                                chart_placeholder.altair_chart(c1, use_container_width=True)

                            # ۳. آپدیت گراف TPS (نوسانی)
                            if not tps_df.empty:
                                c2 = alt.Chart(tps_df).mark_area(line={'color':'#ef4444'}, color=alt.Gradient(gradient='linear', stops=[alt.GradientStop(color='#ef4444', offset=0), alt.GradientStop(color='white', offset=1)], x1=1, x2=1, y1=1, y2=0)).encode(x='زمان:T', y='نمره TPS:Q').properties(height=250, title="نوسان حرارت خبر (TPS Real-time)")
                                tps_chart_placeholder.altair_chart(c2, use_container_width=True)

                            log_placeholder.dataframe(pd.DataFrame(sources[:5]), use_container_width=True, hide_index=True)
                            time.sleep(0.6) # سرعت کمی بیشتر برای پرزنت جذاب‌تر

                        st.success(f"✅ بازپخش تمام شد. بالاترین نمره ثبت شده برای این خبر: {round(peak_tps, 1)}")
                    else:
                        st.info("برای مشاهده سیر تکامل خبر و نوسان نمره هوش مصنوعی، روی دکمه Replay کلیک کنید.")
                        # نمایش استاتیک در صورت عدم کلیک
                        if score_history:
                            df_static = pd.DataFrame([{"زمان": s.timestamp, "TPS": s.tps_score} for s in score_history])
                            st.line_chart(df_static.set_index("زمان"), use_container_width=True, height=300)

            with tab2:
                colA, colB = st.columns([2, 1])
                colA.markdown("##### 📝 خلاصه خبری جمینای")
                colA.info(trend_obj.summary or "در حال تحلیل...")
                colB.markdown("##### 🏷️ موجودیت‌ها")
                colB.json(trend_obj.entities or {})

            with tab3:
                raw_items = db.query(RawNews).filter(RawNews.trend_id == selected_trend_id).order_by(desc(RawNews.published_at)).all()
                if raw_items:
                    # نمایش بخش زمان‌سنجی اصلاح شده
                    reaction = (trend_obj.first_seen - raw_items[-1].published_at).total_seconds() / 60
                    lifespan = (trend_obj.last_updated - trend_obj.first_seen).total_seconds() / 60
                    t1, t2, t3 = st.columns(3)
                    t1.metric("اولین خبر مرجع", raw_items[-1].published_at.strftime('%H:%M'))
                    t2.metric("کشف توسط ترندیا", trend_obj.first_seen.strftime('%H:%M'), delta=f"{int(reaction)} دقیقه تاخیر")
                    t3.metric("آخرین آپدیت کلاستر", trend_obj.last_updated.strftime('%H:%M'), delta=f"عمر ترند: {int(lifespan)} دقیقه", delta_color="off")
                    st.divider()
                    for rn in raw_items:
                        with st.expander(f"📌 {rn.source_name} | {rn.content[:60]}..."):
                            st.write(f"**زمان:** {rn.published_at}"); st.write(rn.content)
            
            with tab4:
                drafts = db.query(XDraft).filter(XDraft.trend_id == selected_trend_id).all()
                if drafts:
                    for d in drafts:
                        st.markdown(f"**وضعیت:** {d.status} | **نمره در لحظه تولید:** {d.tps_score}")
                        st.code(d.long_caption)
                else: st.warning("پیش‌نویسی یافت نشد.")

    finally: db.close()