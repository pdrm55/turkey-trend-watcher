import streamlit as st
import pandas as pd
import os
import time
import docker
from datetime import datetime

# --- Page Configuration ---
st.set_page_config(
    page_title="TrendiaTR Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- Authentication ---
def check_password():
    """Returns `True` if the user had a correct password."""

    def password_entered():
        """Checks whether a password entered by the user is correct."""
        if (
            st.session_state["username"] == "admin"
            and st.session_state["password"] == "trendia2026"
        ):
            st.session_state["password_correct"] = True
            del st.session_state["password"]  # don't store username + password
            del st.session_state["username"]
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        # First run, show inputs for username + password.
        st.title("🔒 TrendiaTR Login")
        st.text_input("Username", key="username")
        st.text_input("Password", type="password", on_change=password_entered, key="password")
        return False
    elif not st.session_state["password_correct"]:
        # Password not correct, show input + error.
        st.title("🔒 TrendiaTR Login")
        st.text_input("Username", key="username")
        st.text_input("Password", type="password", on_change=password_entered, key="password")
        st.error("😕 User not known or password incorrect")
        return False
    else:
        # Password correct.
        return True

if not check_password():
    st.stop()

# --- Main Application (Authenticated) ---

st.sidebar.title("TrendiaTR Admin")
st.sidebar.markdown("---")
tab_selection = st.sidebar.radio("Navigation", ["🤖 AI Token Monitor", "🛠️ Workers & System Live Monitor"])

# --- Tab 1: AI Token Monitor ---
if tab_selection == "🤖 AI Token Monitor":
    st.title("🤖 AI Token Usage & Cost Monitor")
    st.markdown("Monitor Gemini API usage, costs, and performance metrics.")
    
    LOG_FILE = "ai_monitor_data.csv"
    
    if os.path.exists(LOG_FILE):
        try:
            df = pd.read_csv(LOG_FILE)
            
            if not df.empty:
                # Basic Metrics
                total_cost = pd.to_numeric(df['cost_usd'], errors='coerce').sum()
                total_input = df['input_tokens'].sum()
                total_output = df['output_tokens'].sum()
                avg_duration = pd.to_numeric(df['duration_sec'], errors='coerce').mean()
                
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Total Cost", f"${total_cost:.4f}")
                col2.metric("Input Tokens", f"{total_input:,.0f}")
                col3.metric("Output Tokens", f"{total_output:,.0f}")
                col4.metric("Avg Duration", f"{avg_duration:.2f}s")
                
                st.divider()

                # Charts
                col_chart1, col_chart2 = st.columns(2)
                
                with col_chart1:
                    st.subheader("Daily Cost ($)")
                    if 'timestamp' in df.columns:
                        df['timestamp'] = pd.to_datetime(df['timestamp'])
                        df_daily = df.groupby(df['timestamp'].dt.date)['cost_usd'].sum()
                        st.line_chart(df_daily)
                
                with col_chart2:
                    st.subheader("Token Usage by Model")
                    if 'model' in df.columns:
                        df_model = df.groupby('model')[['input_tokens', 'output_tokens']].sum()
                        st.bar_chart(df_model)
                    
                st.subheader("Recent AI Logs")
                st.dataframe(
                    df.sort_values(by="timestamp", ascending=False).head(100),
                    use_container_width=True
                )
            else:
                st.info("Log file is empty.")
            
        except Exception as e:
            st.error(f"Error reading log file: {e}")
    else:
        st.warning(f"Log file not found at {os.path.abspath(LOG_FILE)}. Waiting for AI activity...")

# --- Tab 2: Workers & System Live Monitor ---
elif tab_selection == "🛠️ Workers & System Live Monitor":
    st.title("🛠️ Workers & System Live Monitor")
    st.markdown("Real-time monitoring of Docker containers and worker logs.")
    
    try:
        client = docker.from_env()
        containers = client.containers.list(all=True)
        
        # Filter for TrendiaTR containers
        ttw_containers = [c for c in containers if "ttw_" in c.name]
        
        if not ttw_containers:
            st.warning("No containers found with 'ttw_' prefix. Are you running this inside the correct Docker network?")
        
        container_stats = []
        
        for c in ttw_containers:
            try:
                if c.status == 'running':
                    stats = c.stats(stream=False)
                    
                    # --- CPU Calculation ---
                    cpu_stats = stats.get('cpu_stats', {})
                    precpu_stats = stats.get('precpu_stats', {})
                    
                    cpu_usage = cpu_stats.get('cpu_usage', {}).get('total_usage', 0)
                    precpu_usage = precpu_stats.get('cpu_usage', {}).get('total_usage', 0)
                    
                    system_cpu_usage = cpu_stats.get('system_cpu_usage', 0)
                    system_precpu_usage = precpu_stats.get('system_cpu_usage', 0)
                    
                    online_cpus = cpu_stats.get('online_cpus', 1)
                    if online_cpus == 0: online_cpus = 1
                    
                    cpu_delta = cpu_usage - precpu_usage
                    system_delta = system_cpu_usage - system_precpu_usage
                    
                    if system_delta > 0 and cpu_delta > 0:
                        cpu_percent = (cpu_delta / system_delta) * online_cpus * 100.0
                    else:
                        cpu_percent = 0.0
                        
                    # --- RAM Calculation ---
                    memory_usage = stats.get('memory_stats', {}).get('usage', 0)
                    memory_mb = memory_usage / (1024 * 1024)
                    
                    status_icon = "🟢"
                else:
                    cpu_percent = 0.0
                    memory_mb = 0.0
                    status_icon = "🔴"
                
                container_stats.append({
                    "Name": c.name,
                    "Status": f"{status_icon} {c.status}",
                    "CPU (%)": f"{cpu_percent:.2f}%",
                    "RAM (MB)": f"{memory_mb:.2f} MB",
                    "Image": c.image.tags[0] if c.image.tags else "Unknown"
                })
                
            except Exception as e:
                container_stats.append({
                    "Name": c.name,
                    "Status": "⚠️ Error",
                    "CPU (%)": "-",
                    "RAM (MB)": "-",
                    "Image": str(e)
                })
            
        # Display Metrics Table
        st.dataframe(pd.DataFrame(container_stats), use_container_width=True)
        
        st.divider()
        
        # --- Live Logs Section ---
        st.subheader("📜 Live Container Logs")
        
        col_sel, col_btn = st.columns([3, 1])
        with col_sel:
            selected_container_name = st.selectbox(
                "Select Container to View Logs",
                [c.name for c in ttw_containers]
            )
        
        with col_btn:
            st.write("") # Spacing
            st.write("") # Spacing
            refresh = st.button("🔄 Refresh Logs", use_container_width=True)
        
        if selected_container_name:
            try:
                selected_c = client.containers.get(selected_container_name)
                # Fetch last 100 lines
                logs = selected_c.logs(tail=100).decode('utf-8')
                
                st.code(logs, language='bash')
            except Exception as e:
                st.error(f"Could not fetch logs for {selected_container_name}: {e}")
        
    except Exception as e:
        st.error(f"Docker Connection Error: {e}")
        st.info("Ensure the Docker socket is mounted: `- /var/run/docker.sock:/var/run/docker.sock` in docker-compose.yml")
