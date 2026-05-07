import streamlit as st
import sys
import os
import pandas as pd
import time
import altair as alt
from datetime import datetime, timedelta

# اضافه کردن مسیر ریشه پروژه به sys.path
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, "../../"))
if project_root not in sys.path:
    sys.path.append(project_root)

# ایمپورت‌های دیتابیس
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
    
    /* استایل اختصاصی برای جدا کردن دو نمودار همگام */
    .stAltairChart { margin-bottom: -15px; }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# توابع اتصال به دیتابیس
# ==========================================
@st.cache_resource(ttl=60)
def check_db_connection():
    db = SessionLocal()
    try:
        last_week = datetime.now() - timedelta(days=7)
        trends_count = db.query(Trend).filter(Trend.first_seen >= last_week).count()
        raw_news_count = db.query(RawNews).filter(RawNews.created_at >= last_week).count()
        return True, {"trends": trends_count, "raw_news": raw_news_count}
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
st.sidebar.info("📅 فیلتر زمانی: ۷ روز اخیر")

# ==========================================
# بدنه اصلی داشبورد
# ==========================================
st.title("🔥 TrendiaTR - مانیتورینگ هوشمند")
is_connected, db_data = check_db_connection()

if is_connected:
    c1, c2, c3 = st.columns(3)
    c1.metric("ترندهای هفته اخیر", f"{db_data['trends']:,}")
    c2.metric("اخبار هفته اخیر", f"{db_data['raw_news']:,}")
    c3.metric("وضعیت موتور", "Active 🟢")
else:
    st.error(f"خطا در دیتابیس: {db_data}"); st.stop()

st.divider()

last_week = datetime.now() - timedelta(days=7)

if display_mode == "Live (زنده)":
    db = get_db_session()
    try:
        st.subheader("🔥 ۱۰ ترند داغ (۷ روز گذشته)")
        recent = db.query(Trend).filter(Trend.is_active == True, Trend.first_seen >= last_week).order_by(desc(Trend.final_tps)).limit(10).all()
        if recent:
            df = pd.DataFrame([{"شناسه": t.id, "عنوان": t.title, "دسته": t.category, "TPS": round(t.final_tps, 1), "اخبار": t.message_count, "کشف": t.first_seen.strftime('%Y-%m-%d %H:%M')} for t in recent])
            st.dataframe(df, use_container_width=True, hide_index=True)
        else: st.warning("در هفته اخیر ترندی ثبت نشده است.")
        
        st.subheader("📊 توزیع موضوعی هفته")
        cats = db.query(Trend.category, func.count(Trend.id)).filter(Trend.first_seen >= last_week).group_by(Trend.category).all()
        if cats:
            df_cats = pd.DataFrame(cats, columns=["دسته", "تعداد"]).set_index("دسته")
            st.bar_chart(df_cats, use_container_width=True, height=300)
    finally: db.close()

elif display_mode == "Replay (بازپخش)":
    st.header("⏪ کالبدشکافی هوش مصنوعی (AI Autopsy)")
    db = get_db_session()
    try:
        top_trends = db.query(Trend).filter(Trend.title.isnot(None), Trend.first_seen >= last_week).order_by(desc(Trend.final_tps), desc(Trend.id)).limit(20).all()
        
        if top_trends:
            trend_mapping = {t.id: f"[{t.first_seen.strftime('%m-%d')}] {t.title[:70]}... (TPS: {round(t.final_tps, 1)})" for t in top_trends}
            selected_trend_id = st.selectbox("🎯 رویدادهای ۷ روز اخیر را انتخاب کنید:", options=[t.id for t in top_trends], format_func=lambda x: trend_mapping[x])
            trend_obj = db.query(Trend).get(selected_trend_id)

            tab1, tab2, tab3, tab4 = st.tabs(["📈 گراف شتاب و نوسان (Live TPS)", "🧠 تحلیل محتوایی AI", "🔍 اخبار خام (Cluster)", "🐦 خروجی توییتر"])
            
            with tab1:
                arrivals = db.query(TrendArrivals, RawNews.source_name).outerjoin(RawNews).filter(TrendArrivals.trend_id == selected_trend_id).order_by(TrendArrivals.timestamp).all()
                score_history = db.query(TrendScoreHistory).filter(TrendScoreHistory.trend_id == selected_trend_id).order_by(TrendScoreHistory.timestamp).all()
                
                if arrivals:
                    start_replay = st.button("▶️ پخش سناریوی کشف و نوسان TPS", type="primary")
                    if start_replay:
                        st.info("💡 در حال بازسازی تایم‌لاین ورود اخبار و تغییرات نمره داغ بودن...")
                        metric_placeholder = st.empty()
                        chart_placeholder = st.empty()
                        tps_chart_placeholder = st.empty()
                        log_placeholder = st.empty()

                        history_df = pd.DataFrame()
                        tps_df = pd.DataFrame()
                        sources = []
                        
                        timeline_events = []
                        for a, src in arrivals: timeline_events.append({'time': a.timestamp, 'type': 'arrival', 'data': src})
                        for s in score_history: timeline_events.append({'time': s.timestamp, 'type': 'score', 'data': s.tps_score})
                        timeline_events.sort(key=lambda x: x['time'])

                        if timeline_events:
                            min_ts = timeline_events[0]['time']
                            max_ts = timeline_events[-1]['time']
                            if min_ts == max_ts: max_ts = min_ts + pd.Timedelta(minutes=1)
                            
                            total_arrivals_final = len(arrivals)
                            max_tps_val = max([s.tps_score for s in score_history] + [trend_obj.final_tps])
                            
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

                                with metric_placeholder.container():
                                    m1, m2, m3 = st.columns(3)
                                    m1.metric("زمان رویداد", event['time'].strftime('%H:%M:%S'))
                                    m2.metric("نمره TPS فعلی", f"{round(current_tps, 1)}", delta=f"{arrival_count} خبر")
                                    m3.metric("قله نمره (Peak)", f"{round(peak_tps, 1)}")

                                # ۱. گراف حجم کلاستر (بالایی)
                                if not history_df.empty:
                                    c1 = alt.Chart(history_df).mark_line(point=True, color="#3b82f6").encode(
                                        x=alt.X('زمان:T', scale=alt.Scale(domain=[min_ts.isoformat(), max_ts.isoformat()]), axis=None), # حذف محور X برای چسبیدن به گراف پایین
                                        y=alt.Y('حجم اخبار:Q', scale=alt.Scale(domain=[0, total_arrivals_final + 1]), title='تعداد سیگنال')
                                    ).properties(height=180, title="رشد کلاستر خبری (ورود منابع)")
                                    chart_placeholder.altair_chart(c1, use_container_width=True)

                                # ۲. گراف حرارت TPS (پایینی - کاملاً همگام)
                                if not tps_df.empty:
                                    c2 = alt.Chart(tps_df).mark_area(line={'color':'#ef4444'}, color=alt.Gradient(gradient='linear', stops=[alt.GradientStop(color='#ef4444', offset=0), alt.GradientStop(color='white', offset=1)], x1=1, x2=1, y1=1, y2=0)).encode(
                                        x=alt.X('زمان:T', scale=alt.Scale(domain=[min_ts.isoformat(), max_ts.isoformat()]), title='تایم‌لاین مشترک رویداد'),
                                        y=alt.Y('نمره TPS:Q', scale=alt.Scale(domain=[0, max_tps_val + 5]), title='امتیاز حرارت AI')
                                    ).properties(height=220, title="واکنش هوش مصنوعی (نوسان نمره داغ بودن)")
                                    tps_chart_placeholder.altair_chart(c2, use_container_width=True)

                                log_placeholder.dataframe(pd.DataFrame(sources), use_container_width=True, hide_index=True)
                                time.sleep(0.8)
                            st.success(f"✅ بازپخش تمام شد. بالاترین نمره ثبت شده: {round(peak_tps, 1)}")
                    else:
                        st.info("برای مشاهده سیر تکامل خبر روی دکمه Replay کلیک کنید.")
                        if score_history:
                            df_static = pd.DataFrame([{"زمان": s.timestamp, "TPS": s.tps_score} for s in score_history])
                            st.line_chart(df_static.set_index("زمان"), use_container_width=True, height=300)
                else: st.info("داده‌های ورودی یافت نشد.")

            with tab2:
                colA, colB = st.columns([2, 1])
                colA.markdown("##### 📝 خلاصه خبری جمینای")
                colA.info(trend_obj.summary or "در حال تحلیل...")
                colB.markdown("##### 🏷️ موجودیت‌ها")
                colB.json(trend_obj.entities or {})

            with tab3:
                raw_items = db.query(RawNews).filter(RawNews.trend_id == selected_trend_id).order_by(desc(RawNews.published_at)).all()
                if raw_items:
                    reaction = (trend_obj.first_seen - raw_items[-1].published_at).total_seconds() / 60
                    lifespan = (trend_obj.last_updated - trend_obj.first_seen).total_seconds() / 60
                    t1, t2, t3 = st.columns(3)
                    t1.metric("اولین خبر مرجع", raw_items[-1].published_at.strftime('%H:%M'))
                    t2.metric("کشف توسط ترندیا", trend_obj.first_seen.strftime('%H:%M'), delta=f"{int(reaction)} دقیقه تاخیر")
                    t3.metric("آخرین آپدیت کلاستر", trend_obj.last_updated.strftime('%H:%M'), delta=f"عمر ترند: {int(lifespan)} دقیقه", delta_color="off")
                    st.divider()
                    for rn in raw_items:
                        content_snippet = (rn.content[:60] + "...") if rn.content else "متن خبر خالی است"
                        with st.expander(f"📌 منبع: :red[{rn.source_name}] | {content_snippet}"):
                            st.write(f"**زمان انتشار مرجع:** {rn.published_at.strftime('%Y-%m-%d %H:%M:%S')}")
                            st.markdown("---")
                            st.write(rn.content)
                            if rn.external_id: st.caption(f"لینک/آیدی مرجع: {rn.external_id}")
                else: st.warning("هیچ خبر خامی ثبت نشده است.")
            
            with tab4:
                drafts = db.query(XDraft).filter(XDraft.trend_id == selected_trend_id).all()
                if drafts:
                    for d in drafts:
                        st.markdown(f"**وضعیت:** {d.status} | **نمره:** {d.tps_score}")
                        st.code(d.long_caption)
                else: st.warning("پیش‌نویسی یافت نشد.")
        else: st.warning("هیچ رویداد دارای تیتری در ۷ روز گذشته یافت نشد.")
    finally: db.close()