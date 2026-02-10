#!/bin/bash

# تابع برای بستن تمام پروسه‌ها هنگام خروج (Cleanup)
cleanup() {
    echo ""
    echo "🛑 Shutting down all services..."
    kill $(jobs -p) 2>/dev/null
    exit
}

# اجرای تابع cleanup با زدن Ctrl+C
trap cleanup SIGINT SIGTERM

echo "🚀 Starting TrendiaTR System..."

# فعال‌سازی محیط مجازی
if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "⚠️  Virtual environment 'venv' not found! Trying system python..."
fi

# ساخت پوشه لاگ‌ها اگر وجود ندارد
mkdir -p logs

# 1. اجرای ربات تلگرام (در پس‌زمینه)
echo "📡 Starting Telegram Listener... (Logs: logs/telegram.log)"
python3 app/collectors/telegram_bot.py > logs/telegram.log 2>&1 &

# 2. اجرای RSS (در پس‌زمینه)
echo "📰 Starting RSS Fetcher... (Logs: logs/rss.log)"
python3 app/collectors/rss_fetcher.py > logs/rss.log 2>&1 &

# 3. اجرای هوش مصنوعی (در پس‌زمینه)
echo "🧠 Starting AI Summarizer... (Logs: logs/ai.log)"
python3 app/workers/summarizer.py > logs/ai.log 2>&1 &

# 4. اجرای وب سرور (در پیش‌زمینه تا خروجی وب را ببینید)
echo "🌐 Starting Web Server on http://localhost:5000"
echo "👉 Press Ctrl+C to stop all services."
python3 web_server.py

# منتظر ماندن برای بسته شدن وب سرور
wait