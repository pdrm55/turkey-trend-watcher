import streamlit as st
import pandas as pd
import os
import sys
import redis as redis_lib
from datetime import datetime, timezone, timedelta

sys.path.insert(0, '/app')

# --- Page Configuration ---
st.set_page_config(
    page_title="TrendiaTR Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- Authentication ---
def check_password():
    def password_entered():
        if (
            st.session_state["username"] == "admin"
            and st.session_state["password"] == "trendia2026"
        ):
            st.session_state["password_correct"] = True
            del st.session_state["password"]
            del st.session_state["username"]
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.title("🔒 TrendiaTR Login")
        st.text_input("Username", key="username")
        st.text_input("Password", type="password", on_change=password_entered, key="password")
        return False
    elif not st.session_state["password_correct"]:
        st.title("🔒 TrendiaTR Login")
        st.text_input("Username", key="username")
        st.text_input("Password", type="password", on_change=password_entered, key="password")
        st.error("😕 User not known or password incorrect")
        return False
    return True

if not check_password():
    st.stop()

# --- Pricing table (mirrors summarizer.py) ---
_MODEL_PRICING = {
    "gemini-2.5-flash-lite": (0.10 / 1_000_000, 0.40 / 1_000_000),
    "gemini-2.5-flash":      (0.15 / 1_000_000, 0.60 / 1_000_000),
    "gemini-2.0-flash-lite": (0.075 / 1_000_000, 0.30 / 1_000_000),
    "gemini-1.5-flash":      (0.075 / 1_000_000, 0.30 / 1_000_000),
}
_DEFAULT_PRICING = (0.10 / 1_000_000, 0.40 / 1_000_000)

def _calc_cost(model: str, in_tok, out_tok) -> float:
    name_lower = (model or "").lower()
    for key, (ip, op) in _MODEL_PRICING.items():
        if key in name_lower:
            return in_tok * ip + out_tok * op
    return in_tok * _DEFAULT_PRICING[0] + out_tok * _DEFAULT_PRICING[1]

# --- Redis helper for visitors tab ---
def _get_redis():
    try:
        redis_host = os.getenv("REDIS_HOST", "ttw_redis")
        r = redis_lib.from_url(f"redis://{redis_host}:6379/0", socket_connect_timeout=2, decode_responses=True)
        r.ping()
        return r
    except Exception:
        return None

# --- Main Application ---
st.sidebar.title("TrendiaTR Admin")
st.sidebar.markdown("---")
tab_selection = st.sidebar.radio(
    "Navigation",
    ["🤖 AI Token Monitor", "👥 Visitor Analytics", "🛠️ Workers & System Live Monitor"]
)

# ══════════════════════════════════════════════════════════════
# TAB 1 — AI Token Monitor
# ══════════════════════════════════════════════════════════════
if tab_selection == "🤖 AI Token Monitor":
    st.title("🤖 AI Token Usage & Cost Monitor")

    LOG_FILE = "ai_monitor_data.csv"

    if not os.path.exists(LOG_FILE):
        st.warning(f"Log file not found at {os.path.abspath(LOG_FILE)}. Waiting for AI activity...")
        st.stop()

    # --- Date range filter ---
    st.sidebar.markdown("---")
    st.sidebar.subheader("Filters")
    days_back = st.sidebar.slider("Days to show", min_value=1, max_value=120, value=30)
    cutoff_dt = datetime.now() - timedelta(days=days_back)

    try:
        # Read with date filter — skip old rows for performance
        df_full = pd.read_csv(LOG_FILE, parse_dates=["timestamp"])
        df = df_full[df_full["timestamp"] >= cutoff_dt].copy()

        if df.empty:
            st.info(f"No AI activity in the last {days_back} days.")
            st.stop()

        # Recalculate cost using correct per-model pricing
        df["cost_correct"] = df.apply(
            lambda r: _calc_cost(r["model"], r["input_tokens"], r["output_tokens"]), axis=1
        )

        # Check if stored cost diverges from recalculated (pricing bug detection)
        stored_total  = pd.to_numeric(df["cost_usd"], errors="coerce").sum()
        correct_total = df["cost_correct"].sum()
        pricing_drift = abs(correct_total - stored_total) / max(correct_total, 1e-9)

        # ── KPI row ──────────────────────────────────────────
        total_input  = int(df["input_tokens"].sum())
        total_output = int(df["output_tokens"].sum())
        avg_duration = pd.to_numeric(df["duration_sec"], errors="coerce").mean()
        success_mask = df["status"].str.lower().str.contains("success|override", na=False)
        success_rate = success_mask.mean() * 100

        col1, col2, col3, col4, col5 = st.columns(5)
        col1.metric("Total Cost (corrected)", f"${correct_total:.4f}")
        col2.metric("Input Tokens",  f"{total_input:,}")
        col3.metric("Output Tokens", f"{total_output:,}")
        col4.metric("Avg Duration",  f"{avg_duration:.2f}s")
        col5.metric("Success Rate",  f"{success_rate:.1f}%")

        if pricing_drift > 0.05:
            st.warning(
                f"⚠️ Stored `cost_usd` in CSV differs from recalculated cost by **{pricing_drift*100:.1f}%** "
                f"(stored ${stored_total:.4f} vs corrected ${correct_total:.4f}). "
                "This happens when model pricing was updated. Dashboard now shows corrected values."
            )

        st.divider()

        # ── Charts row 1 ──────────────────────────────────────
        col_c1, col_c2 = st.columns(2)

        with col_c1:
            st.subheader("Daily Cost ($) — corrected")
            df_daily_cost = (
                df.set_index("timestamp")
                  .resample("D")["cost_correct"]
                  .sum()
                  .rename("Cost ($)")
            )
            st.line_chart(df_daily_cost)

        with col_c2:
            st.subheader("Daily Token Volume")
            df_daily_tok = (
                df.set_index("timestamp")
                  .resample("D")[["input_tokens", "output_tokens"]]
                  .sum()
            )
            st.bar_chart(df_daily_tok)

        # ── Charts row 2 ──────────────────────────────────────
        col_c3, col_c4 = st.columns(2)

        with col_c3:
            st.subheader("Cost by Model")
            df_model = (
                df.groupby("model")["cost_correct"]
                  .sum()
                  .sort_values(ascending=False)
                  .rename("Cost ($)")
                  .to_frame()
            )
            st.bar_chart(df_model)

        with col_c4:
            st.subheader("Requests by Status")
            status_counts = df["status"].value_counts().rename("Count").to_frame()
            st.bar_chart(status_counts)

        # ── Cumulative cost trend ──────────────────────────────
        st.subheader("Cumulative Cost Over Time")
        df_cum = (
            df.set_index("timestamp")
              .sort_index()["cost_correct"]
              .cumsum()
              .rename("Cumulative Cost ($)")
        )
        st.line_chart(df_cum)

        # ── Monthly projection ─────────────────────────────────
        days_spanned = max((df["timestamp"].max() - df["timestamp"].min()).days, 1)
        daily_avg    = correct_total / days_spanned
        st.info(
            f"📈 **Projected monthly cost** (based on last {days_back}d avg): "
            f"**${daily_avg * 30:.4f}**  |  "
            f"Daily avg: **${daily_avg:.4f}**  |  "
            f"Total records shown: **{len(df):,}** of **{len(df_full):,}**"
        )

        # ── Recent logs table ──────────────────────────────────
        st.divider()
        st.subheader("Recent AI Logs")
        display_df = (
            df[["timestamp", "trend_id", "model", "input_tokens", "output_tokens",
                "duration_sec", "category", "status", "cost_correct"]]
            .rename(columns={"cost_correct": "cost_usd (corrected)"})
            .sort_values("timestamp", ascending=False)
            .head(200)
        )
        st.dataframe(display_df, use_container_width=True)

    except Exception as e:
        st.error(f"Error reading log file: {e}")


# ══════════════════════════════════════════════════════════════
# TAB 2 — Visitor Analytics
# ══════════════════════════════════════════════════════════════
elif tab_selection == "👥 Visitor Analytics":
    st.title("👥 Visitor Analytics")
    st.caption("Page views tracked via Redis on HTML routes (/, /trend/*, /category/*). Data accumulates from when tracking was deployed.")

    r = _get_redis()
    if not r:
        st.error("❌ Cannot connect to Redis. Make sure ttw_redis is running.")
        st.stop()

    # ── Date range selector ────────────────────────────────────
    st.sidebar.markdown("---")
    st.sidebar.subheader("Filters")
    days_back_v = st.sidebar.slider("Days to show (daily chart)", 7, 30, 14)
    hours_back  = st.sidebar.slider("Hours to show (hourly chart)", 24, 48, 48)

    # ── Fetch data ─────────────────────────────────────────────
    _PREFIX = "ttw:pv"
    now_utc  = datetime.now(timezone.utc)

    # Today stats
    today_str = now_utc.strftime("%Y-%m-%d")
    pipe = r.pipeline(transaction=False)
    pipe.get(f"{_PREFIX}:daily:{today_str}")
    pipe.pfcount(f"{_PREFIX}:uniq:daily:{today_str}")
    t_views, t_unique = pipe.execute()
    today_views  = int(t_views  or 0)
    today_unique = int(t_unique or 0)

    # Yesterday stats for delta
    yesterday_str = (now_utc - timedelta(days=1)).strftime("%Y-%m-%d")
    pipe2 = r.pipeline(transaction=False)
    pipe2.get(f"{_PREFIX}:daily:{yesterday_str}")
    pipe2.pfcount(f"{_PREFIX}:uniq:daily:{yesterday_str}")
    y_views, y_unique = pipe2.execute()
    yest_views  = int(y_views  or 0)
    yest_unique = int(y_unique or 0)

    # Daily stats for chart
    daily_dates  = [(now_utc - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days_back_v - 1, -1, -1)]
    pipe3 = r.pipeline(transaction=False)
    for d in daily_dates:
        pipe3.get(f"{_PREFIX}:daily:{d}")
        pipe3.pfcount(f"{_PREFIX}:uniq:daily:{d}")
    daily_values = pipe3.execute()

    df_daily = pd.DataFrame([
        {
            "Date":            daily_dates[i],
            "Page Views":      int(daily_values[i * 2]     or 0),
            "Unique Visitors": int(daily_values[i * 2 + 1] or 0),
        }
        for i in range(len(daily_dates))
    ]).set_index("Date")

    # Hourly stats
    hour_labels, hour_keys = [], []
    for i in range(hours_back - 1, -1, -1):
        t = now_utc - timedelta(hours=i)
        hour_labels.append(t.strftime("%m/%d %H:00"))
        hour_keys.append(t.strftime("%Y-%m-%d:%H"))

    pipe4 = r.pipeline(transaction=False)
    for k in hour_keys:
        pipe4.get(f"{_PREFIX}:hourly:{k}")
    hourly_values = pipe4.execute()

    df_hourly = pd.DataFrame([
        {"Hour": hour_labels[i], "Page Views": int(hourly_values[i] or 0)}
        for i in range(len(hour_labels))
    ]).set_index("Hour")

    # ── KPI row ────────────────────────────────────────────────
    total_views_period  = int(df_daily["Page Views"].sum())
    total_unique_period = int(df_daily["Unique Visitors"].sum())

    col1, col2, col3, col4 = st.columns(4)
    col1.metric(
        "Today — Page Views",
        f"{today_views:,}",
        delta=f"{today_views - yest_views:+,} vs yesterday" if yest_views else None
    )
    col2.metric(
        "Today — Unique Visitors",
        f"{today_unique:,}",
        delta=f"{today_unique - yest_unique:+,} vs yesterday" if yest_unique else None
    )
    col3.metric(f"Total Views ({days_back_v}d)",   f"{total_views_period:,}")
    col4.metric(f"Unique Visitors ({days_back_v}d)", f"{total_unique_period:,}")

    st.divider()

    # ── Charts ─────────────────────────────────────────────────
    col_chart1, col_chart2 = st.columns(2)

    with col_chart1:
        st.subheader(f"Daily Traffic — last {days_back_v} days")
        if df_daily["Page Views"].sum() > 0:
            st.line_chart(df_daily)
        else:
            st.info("No page view data yet. Tracking started — data will appear as visitors arrive.")

    with col_chart2:
        st.subheader(f"Hourly Traffic — last {hours_back} hours")
        if df_hourly["Page Views"].sum() > 0:
            st.bar_chart(df_hourly)
        else:
            st.info("No hourly data yet.")

    # ── Daily breakdown table ──────────────────────────────────
    st.divider()
    st.subheader("Daily Breakdown")
    st.dataframe(
        df_daily.sort_index(ascending=False),
        use_container_width=True
    )

    st.caption(
        "Unique Visitors are estimated using HyperLogLog (±2% error). "
        "IPs are SHA-256 hashed before storage — no raw IPs are saved."
    )


# ══════════════════════════════════════════════════════════════
# TAB 3 — Workers & System Live Monitor
# ══════════════════════════════════════════════════════════════
elif tab_selection == "🛠️ Workers & System Live Monitor":
    import docker
    import psutil

    st.title("🛠️ Workers & System Live Monitor")
    st.markdown("Real-time monitoring of Docker containers and worker logs.")

    # --- Host Resource Monitor ---
    st.subheader("🖥️ Host Server Live Resources (htop style)")

    @st.fragment(run_every="2s")
    def live_system_stats():
        col1, col2 = st.columns(2)
        with col1:
            cpu_percent = psutil.cpu_percent(interval=None)
            st.metric("Host CPU Usage", f"{cpu_percent}%")
            st.progress(cpu_percent / 100)
        with col2:
            ram = psutil.virtual_memory()
            ram_total_gb = ram.total / (1024**3)
            ram_used_gb  = ram.used  / (1024**3)
            st.metric(f"Host RAM ({ram_used_gb:.1f}GB / {ram_total_gb:.1f}GB)", f"{ram.percent}%")
            st.progress(ram.percent / 100)

    live_system_stats()
    st.divider()

    try:
        client     = docker.from_env()
        containers = client.containers.list(all=True)
        ttw_containers = [c for c in containers if "ttw_" in c.name]

        if not ttw_containers:
            st.warning("No containers found with 'ttw_' prefix.")

        container_stats = []
        for c in ttw_containers:
            try:
                if c.status == "running":
                    stats = c.stats(stream=False)

                    cpu_stats    = stats.get("cpu_stats", {})
                    precpu_stats = stats.get("precpu_stats", {})
                    cpu_delta    = cpu_stats.get("cpu_usage", {}).get("total_usage", 0) - \
                                   precpu_stats.get("cpu_usage", {}).get("total_usage", 0)
                    sys_delta    = cpu_stats.get("system_cpu_usage", 0) - \
                                   precpu_stats.get("system_cpu_usage", 0)
                    online_cpus  = max(cpu_stats.get("online_cpus", 1), 1)
                    cpu_pct      = (cpu_delta / sys_delta) * online_cpus * 100.0 if sys_delta > 0 and cpu_delta > 0 else 0.0

                    memory_mb   = stats.get("memory_stats", {}).get("usage", 0) / (1024 * 1024)
                    status_icon = "🟢"
                else:
                    cpu_pct     = 0.0
                    memory_mb   = 0.0
                    status_icon = "🔴"

                container_stats.append({
                    "Name":     c.name,
                    "Status":   f"{status_icon} {c.status}",
                    "CPU (%)":  f"{cpu_pct:.2f}%",
                    "RAM (MB)": f"{memory_mb:.0f} MB",
                    "Image":    c.image.tags[0] if c.image.tags else "Unknown",
                })
            except Exception as e:
                container_stats.append({
                    "Name": c.name, "Status": "⚠️ Error",
                    "CPU (%)": "-", "RAM (MB)": "-", "Image": str(e),
                })

        st.dataframe(pd.DataFrame(container_stats), use_container_width=True)
        st.divider()

        st.subheader("📜 Live Container Logs")
        col_sel, col_btn = st.columns([3, 1])
        with col_sel:
            selected_container_name = st.selectbox(
                "Select Container to View Logs",
                [c.name for c in ttw_containers]
            )
        with col_btn:
            st.write("")
            st.write("")
            st.button("🔄 Refresh Logs", use_container_width=True)

        if selected_container_name:
            try:
                selected_c = client.containers.get(selected_container_name)
                logs = selected_c.logs(tail=100).decode("utf-8")
                st.code(logs, language="bash")
            except Exception as e:
                st.error(f"Could not fetch logs for {selected_container_name}: {e}")

    except Exception as e:
        st.error(f"Docker Connection Error: {e}")
        st.info("Ensure the Docker socket is mounted: `- /var/run/docker.sock:/var/run/docker.sock`")
