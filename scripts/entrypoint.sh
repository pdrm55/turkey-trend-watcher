#!/bin/bash

# خروج در صورت بروز خطای جدی
set -e

echo "🚀 Starting Turkey Trend Watcher System (Protected Mode)..."

# --- رفع خطای Segmentation Fault (تنظیمات محیطی) ---
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export TOKENIZERS_PARALLELISM=false

# پاکسازی فایل‌های قفل دیتابیس اگر از اجرای قبلی مانده باشند
if [ -d "/app/chroma_db_data" ]; then
    echo "🔍 Cleaning old database locks..."
    find /app/chroma_db_data -name "*.lock" -delete 2>/dev/null || true
fi

# ۱. انتظار برای آماده‌سازی سرویس هوش مصنوعی (Ollama)
echo "⏳ Waiting for Ollama service..."
until curl -s http://ttw_ollama:11434/api/tags > /dev/null; do
    sleep 5
done

# ۲. بررسی و دانلود مدل
MODEL_NAME="qwen2.5:1.5b"
if ! curl -s http://ttw_ollama:11434/api/tags | grep -q "$MODEL_NAME"; then
    echo "⬇️ Model $MODEL_NAME not found. Pulling now..."
    curl -X POST http://ttw_ollama:11434/api/pull -d "{\"name\": \"$MODEL_NAME\"}"
fi

echo "🔥 Starting Multi-Process Environment..."

# ۳. وب‌سرور اصلی (پورت ۵۰۰۰)
python3 web_server.py > web_server.log 2>&1 &
sleep 5

# ۴. خلاصه‌ساز و سئو
python3 app/workers/summarizer.py &
sleep 5

# ۵. مترجم فارسی
if [ -f "app/workers/translator_worker.py" ]; then
    python3 app/workers/translator_worker.py &
    sleep 2
fi

# ۶. کالکتورها (بسیار مهم: فاصله زمانی زیاد برای جلوگیری از تداخل ChromaDB)
echo "📡 Starting Collectors..."
# ابتدا RSS اجرا می‌شود
python3 app/collectors/rss_fetcher.py &
sleep 30 # وقفه طولانی ۳۰ ثانیه‌ای تا RSS کارش با دیتابیس تمام شود یا آن را پایدار کند
# سپس تلگرام اجرا می‌شود
python3 app/collectors/telegram_bot.py &

# ۷. داشبورد
streamlit run app/workers/dashboard.py --server.port 8501 --server.address 0.0.0.0 &

wait
