import streamlit as st
import pandas as pd
import os
from datetime import datetime

# تنظیمات صفحه
st.set_page_config(page_title="TrendiaTR Monitoring", layout="wide")

# مسیر فایل لاگ
LOG_FILE = "ai_monitor_data.csv"

def load_data():
    if os.path.exists(LOG_FILE):
        try:
            df = pd.read_csv(LOG_FILE)
            return df
        except:
            return pd.DataFrame()
    return pd.DataFrame()

st.title("📊 TrendiaTR AI & SEO Monitor")

df = load_data()

if not df.empty:
    # --- آمار کلی ---
    col1, col2, col3, col4 = st.columns(4)
    with col1: st.metric("Total Processed", len(df))
    with col2:
        success_rate = (df['status'] == 'Success').sum() / len(df) * 100
        st.metric("Success Rate", f"{success_rate:.1f}%")
    with col3:
        avg_tok = df['input_tokens'].mean() + df['output_tokens'].mean()
        st.metric("Avg Tokens", f"{int(avg_tok)}")
    with col4:
        cost = pd.to_numeric(df['cost_usd'], errors='coerce').sum()
        st.metric("Total Cost", f"${cost:.4f}")

    # --- جدول لاگ‌ها ---
    st.subheader("📝 Recent Logs (Last 20)")
    
    # رفع قطعی هشدار استریم‌لیت
    st.dataframe(
        df.sort_values(by="timestamp", ascending=False).head(20),
        width=1200 # استفاده از پیکسل یا stretch در نسخه‌های مختلف متفاوت است، این ایمن‌ترین راه است
    )

    st.subheader("📁 Categories")
    st.bar_chart(df['category'].value_counts())

else:
    st.info("Waiting for data...")
    if st.button("Refresh"): st.rerun()

st.caption(f"Last update: {datetime.now().strftime('%H:%M:%S')}")
