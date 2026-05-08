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
from app.database.models import SessionLocal, Trend, RawNews, TrendArrivals, TrendScoreHistory
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
    
    /* استایل اختصاصی برای باکس‌های Chaos و Magic */
    .chaos-box {
        background-color: #f8fafc;
        border-right: 4px solid #cbd5e1;
        padding: 15px;
        border-radius: 0 12px 12px 0;
        margin-bottom: 10px;
    }
    .magic-title {
        color: #0f172a;
        font-weight: 900;
        line-height: 1.4;
        border-bottom: 2px solid #ef4444;
        padding-bottom: 10px;
        margin-bottom: 20px;
    }
    /* فاصله مناسب بین دو نمودار عمودی */
    .stAltairChart { margin-bottom: 10px; }
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
            df_cats = pd.DataFrame(cats, columns=["دسته", "تعداد"])
            bar_chart = alt.Chart(df_cats).mark_bar(color="#0f172a", cornerRadiusTopLeft=8, cornerRadiusTopRight=8).encode(
                x=alt.X('دسته:N', title='دسته‌بندی موضوعی', axis=alt.Axis(labelAngle=0, labelPadding=10)),
                y=alt.Y('تعداد:Q', title='تعداد رویدادها'),
                tooltip=['دسته', 'تعداد']
            ).properties(height=350)
            st.altair_chart(bar_chart, use_container_width=True)
    finally: db.close()

elif display_mode == "Replay (بازپخش)":
    st.header("⏪ کالبدشکافی هوش مصنوعی (AI Autopsy)")
    db = get_db_session()
    try:
        top_trends = db.query(Trend).filter(Trend.is_active == True, Trend.title.isnot(None), Trend.first_seen >= last_week).order_by(desc(Trend.final_tps), desc(Trend.id)).limit(20).all()
        
        if top_trends:
            trend_mapping = {t.id: f"[{t.first_seen.strftime('%m-%d')}] {t.title[:70]}... (TPS: {round(t.final_tps, 1)})" for t in top_trends}
            selected_trend_id = st.selectbox("🎯 رویداد مورد نظر را انتخاب کنید:", options=[t.id for t in top_trends], format_func=lambda x: trend_mapping[x])
            trend_obj = db.query(Trend).get(selected_trend_id)

            tab1, tab2 = st.tabs(["📈 گراف شتاب و نوسان (Live TPS)", "⚖️ میز جادویی: مقایسه هوشمند (AI Magic)"])
            
            with tab1:
                arrivals = db.query(TrendArrivals, RawNews.source_name).outerjoin(RawNews).filter(TrendArrivals.trend_id == selected_trend_id).order_by(TrendArrivals.timestamp).all()
                score_history = db.query(TrendScoreHistory).filter(TrendScoreHistory.trend_id == selected_trend_id).order_by(TrendScoreHistory.timestamp).all()
                
                if arrivals:
                    start_replay = st.button("▶️ پخش سناریوی کشف و نوسان TPS", type="primary")
                    if start_replay:
                        metric_placeholder = st.empty()
                        chart_placeholder = st.empty()
                        tps_chart_placeholder = st.empty()
                        log_placeholder = st.empty()

                        history_df = pd.DataFrame()
                        tps_df = pd.DataFrame({"زمان": [arrivals[0][0].timestamp], "نمره TPS": [0.0]})
                        sources = []
                        
                        timeline_events = []
                        for a, src in arrivals: timeline_events.append({'time': a.timestamp, 'type': 'arrival', 'data': src})
                        for s in score_history: timeline_events.append({'time': s.timestamp, 'type': 'score', 'data': s.tps_score})
                        timeline_events.sort(key=lambda x: x['time'])

                        min_ts = timeline_events[0]['time']
                        max_ts = timeline_events[-1]['time']
                        if min_ts == max_ts: max_ts = min_ts + pd.Timedelta(minutes=1)
                        total_arrivals_final = len(arrivals)
                        all_scores = [s.tps_score for s in score_history] + [trend_obj.final_tps]
                        max_tps_val = max(all_scores) if all_scores else 100.0
                        
                        arrival_count = 0
                        current_tps = 0.0
                        peak_tps = 0.0
                        
                        # رهگیری زمان آخرین خبر ورودی
                        latest_arrival_time = trend_obj.first_seen

                        for event in timeline_events:
                            if event['type'] == 'arrival':
                                arrival_count += 1
                                latest_arrival_time = event['time']
                                sources.insert(0, {"زمان": event['time'].strftime('%H:%M:%S'), "منبع": event['data'] or "سیستم"})
                                new_row = pd.DataFrame({"زمان": [event['time']], "حجم اخبار": [arrival_count]})
                                history_df = pd.concat([history_df, new_row])
                            else:
                                current_tps = event['data']
                                if current_tps > peak_tps: peak_tps = current_tps
                                new_score = pd.DataFrame({"زمان": [event['time']], "نمره TPS": [current_tps]})
                                tps_df = pd.concat([tps_df, new_score])

                            # محاسبه فاصله زمانی آخرین خبر تا زمان تشکیل کلاستر
                            diff_seconds = (latest_arrival_time - trend_obj.first_seen).total_seconds()
                            diff_minutes = max(0, int(diff_seconds / 60))

                            with metric_placeholder.container():
                                m1, m2, m3, m4 = st.columns(4)
                                m1.metric("زمان ایجاد کلاستر", trend_obj.first_seen.strftime('%H:%M:%S'))
                                m2.metric("فاصله با آخرین خبر", f"{diff_minutes} دقیقه")
                                m3.metric("نمره TPS فعلی", f"{round(current_tps, 1)}", delta=f"{arrival_count} خبر")
                                m4.metric("قله نمره (Peak)", f"{round(peak_tps, 1)}")

                            if not history_df.empty:
                                c1 = alt.Chart(history_df).mark_line(point=True, color="#3b82f6").encode(
                                    x=alt.X('زمان:T', scale=alt.Scale(domain=[min_ts.isoformat(), max_ts.isoformat()]), axis=alt.Axis(labels=False, title=None)),
                                    y=alt.Y('حجم اخبار:Q', scale=alt.Scale(domain=[0, total_arrivals_final + 1]), title='تعداد سیگنال')
                                ).properties(height=180)
                                chart_placeholder.altair_chart(c1, use_container_width=True)

                            if not tps_df.empty:
                                c2 = alt.Chart(tps_df).mark_area(line={'color':'#ef4444'}, color=alt.Gradient(gradient='linear', stops=[alt.GradientStop(color='#ef4444', offset=0), alt.GradientStop(color='white', offset=1)], x1=1, x2=1, y1=1, y2=0)).encode(
                                    x=alt.X('زمان:T', scale=alt.Scale(domain=[min_ts.isoformat(), max_ts.isoformat()]), title='تایم‌لاین رویداد'),
                                    y=alt.Y('نمره TPS:Q', scale=alt.Scale(domain=[0, max_tps_val + 5]), title='امتیاز حرارت AI')
                                ).properties(height=220)
                                tps_chart_placeholder.altair_chart(c2, use_container_width=True)

                            log_placeholder.dataframe(pd.DataFrame(sources), use_container_width=True, hide_index=True)
                            time.sleep(0.8)
                    else:
                        st.info("برای مشاهده سیر تکامل خبر روی دکمه Replay کلیک کنید.")
                else: st.info("داده‌های ورودی یافت نشد.")

            with tab2:
                # 🌟 نمای مقایسه‌ای AI Magic View 🌟
                st.markdown("""
                    <div style="background-color: #f0f9ff; padding: 20px; border-radius: 15px; border-right: 5px solid #3b82f6; margin-bottom: 25px;">
                        <h4 style="margin: 0; color: #1e3a8a;">🪄 جادوی پردازش ترندیا: تبدیل آشفتگی به آگاهی</h4>
                        <p style="margin: 5px 0 0 0; color: #1e40af; font-size: 0.9rem;">سیستم در کسری از ثانیه، این آشفتگی اطلاعات (راست) را به این محصول خبری نهایی (چپ) تبدیل کرده است.</p>
                    </div>
                """, unsafe_allow_html=True)
                
                col_ai, col_raw = st.columns(2)
                
                with col_raw:
                    st.markdown("##### 🌪️ آشفتگی خبرگزاری‌ها (Chaos)")
                    raw_items = db.query(RawNews).filter(RawNews.trend_id == selected_trend_id).order_by(desc(RawNews.published_at)).all()
                    
                    if raw_items:
                        for rn in raw_items:
                            snippet = (rn.content[:150] + "...") if rn.content else "بدون محتوا"
                            st.markdown(f"""
                                <div class="chaos-box">
                                    <div style="font-size: 0.75rem; color: #ef4444; font-weight: bold;">📢 {rn.source_name} | 🕒 {rn.published_at.strftime('%H:%M')}</div>
                                    <div style="font-size: 0.85rem; color: #64748b; line-height: 1.5;">{snippet}</div>
                                </div>
                            """, unsafe_allow_html=True)
                    else:
                        st.warning("داده خام یافت نشد.")

                with col_ai:
                    st.markdown("##### ✨ خروجی هوشمند (AI Magic)")
                    st.markdown(f'<div class="magic-title">📰 {trend_obj.title}</div>', unsafe_allow_html=True)
                    
                    # اضافه کردن لینک مستقیم به سایت لایو
                    if trend_obj.slug:
                        trend_url = f"https://trendiatr.com/trend/{trend_obj.slug}"
                        st.markdown(f'''
                            <a href="{trend_url}" target="_blank" style="text-decoration: none;">
                                <div style="background-color: #ef4444; color: white; padding: 8px 15px; border-radius: 8px; display: inline-flex; align-items: center; gap: 8px; font-weight: bold; font-size: 0.9rem; margin-bottom: 15px; box-shadow: 0 4px 6px -1px rgba(239, 68, 68, 0.4);">
                                    🌐 مشاهده این خبر در سایت TrendiaTR
                                </div>
                            </a>
                        ''', unsafe_allow_html=True)

                    st.success(trend_obj.summary or "تحلیل موجود نیست.")
                    
                    if trend_obj.entities:
                        st.markdown("**🧩 موجودیت‌های کلیدی استخراج شده:**")
                        ents = trend_obj.entities
                        cols = st.columns(3)
                        if 'people' in ents and ents['people']: 
                            cols[0].caption("👤 اشخاص")
                            cols[0].write(", ".join(ents['people']))
                        if 'locations' in ents and ents['locations']: 
                            cols[1].caption("📍 مکان‌ها")
                            cols[1].write(", ".join(ents['locations']))
                        if 'organizations' in ents and ents['organizations']: 
                            cols[2].caption("🏢 سازمان‌ها")
                            cols[2].write(", ".join(ents['organizations']))

                    if trend_obj.tags:
                        st.markdown("---")
                        st.markdown("**🏷️ برچسب‌های سئو:**")
                        tags_html = "".join([f'<span style="background-color: #fee2e2; color: #dc2626; padding: 2px 8px; border-radius: 5px; margin-left: 5px; font-size: 0.8rem;">#{tag}</span>' for tag in trend_obj.tags])
                        st.markdown(tags_html, unsafe_allow_html=True)
                    
                    st.divider()
                    if raw_items:
                        reaction_mins = int((trend_obj.first_seen - raw_items[-1].published_at).total_seconds() / 60)
                        st.metric("⏱️ سرعت واکنش سیستم", f"{reaction_mins} دقیقه", "پس از اولین سیگنال")

                    st.divider()
                    
                    # 🌟 بخش جدید: قلاب فروش B2B (Social & API Push) 🌟
                    st.markdown("##### 📱 اتوماسیون شبکه‌های اجتماعی (Zero-Click)")
                    
                    # تولید محتوای شبیه‌سازی شده برای توییتر
                    tweet_content = trend_obj.summary[:180] + "..." if trend_obj.summary else "خلاصه خبر برای انتشار آماده نیست."
                    hashtags_twitter = ""
                    if trend_obj.tags:
                        # گرفتن نهایتاً 4 تگ اول برای شبیه‌سازی توییتر
                        hashtags_twitter = " ".join([f"#{t}" for t in trend_obj.tags[:4]])
                        
                    # رسم باکس گرافیکی شبیه‌ساز توییتر (X)
                    st.markdown(f"""
                        <div style="background-color: #15202b; color: #ffffff; padding: 18px; border-radius: 16px; border: 1px solid #38444d; margin-top: 15px; margin-bottom: 25px; direction: rtl; text-align: right; font-family: sans-serif;">
                            <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 12px;">
                                <div style="background-color: #ef4444; width: 44px; height: 44px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: bold; color: white; font-size: 1.2rem;">TT</div>
                                <div style="display: flex; flex-direction: column;">
                                    <div style="font-weight: 700; font-size: 1rem; color: white; display: flex; align-items: center; gap: 4px;">Trendia Desk <span style="color: #1d9bf0; font-size: 1.1rem;">☑️</span></div>
                                    <div style="color: #8b98a5; font-size: 0.85rem; text-align: left; direction: ltr;">@TrendiaTR</div>
                                </div>
                            </div>
                            <div style="font-size: 0.95rem; line-height: 1.6; margin-bottom: 12px; color: #e7e9ea; font-family: 'Vazirmatn', sans-serif;">
                                {tweet_content}
                            </div>
                            <div style="color: #1d9bf0; font-size: 0.9rem; margin-bottom: 16px;">
                                {hashtags_twitter}
                            </div>
                            <div style="display: flex; justify-content: space-between; color: #8b98a5; font-size: 0.85rem; padding-top: 12px; border-top: 1px solid #38444d; direction: ltr;">
                                <span style="cursor: pointer;">💬 24</span>
                                <span style="cursor: pointer;">🔁 89</span>
                                <span style="cursor: pointer; color: #f91880;">❤️ 312</span>
                                <span style="cursor: pointer;">📊 4.5K</span>
                            </div>
                        </div>
                    """, unsafe_allow_html=True)
                    
                    # دکمه بزرگ B2B Push به CMS
                    st.markdown("<br>", unsafe_allow_html=True)
                    
                    # در پرزنت واقعی، کلیک روی این دکمه این حس را می‌دهد که دیتا واقعاً منتقل می‌شود
                    if st.button("🚀 ارسال مستقیم به CMS خبرگزاری (API Push)", type="primary", use_container_width=True):
                        with st.spinner("⏳ در حال برقراری ارتباط با Webhook تحریریه و انتقال پکیج خبری..."):
                            time.sleep(2) # شبیه‌سازی تاخیر ارسال شبکه
                        
                        st.toast(f"✅ پکیج خبری «{trend_obj.title}» با موفقیت به سیستم وردپرس ارسال شد!", icon="🚀")
                        st.balloons()

    finally: db.close()