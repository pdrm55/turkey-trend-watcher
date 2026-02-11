#!/bin/bash

# خروج در صورت بروز خطای جدی
set -e

echo "🛠️  TrendiaTR System | Preparing Environment for: ${SERVICE_NAME:-System Task}"

# --- ۱. تنظیمات محیطی برای پایداری پردازش متن ---
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export TOKENIZERS_PARALLELISM=false

# --- ۲. پاکسازی قفل‌های دیتابیس (جلوگیری از Database Locked) ---
# این بخش حیاتی است چون ChromaDB به قفل‌های باقی‌مانده بسیار حساس است
if [ -d "/app/chroma_db_data" ]; then
    echo "🔍 Checking for stale database locks..."
    find /app/chroma_db_data -name "*.lock" -delete 2>/dev/null || true
fi

# --- ۳. انتظار برای سرویس‌های زیرساختی ---
# اطمینان از اینکه شبکه دیتابیس برقرار است
echo "⏳ Waiting for core infrastructure..."
sleep 2

# --- ۴. منطق اجرای هوشمند (The Exec Pattern) ---
# اگر آرگومانی پاس داده شده باشد (از سمت Docker-Compose Command)
if [ $# -gt 0 ]; then
    echo "⚙️  Executing Assigned Task: $@"
    exec "$@"
else
    # این بخش فقط زمانی اجرا می‌شود که هیچ دستوری به کانتینر داده نشده باشد
    echo "❌ Error: No specific task assigned to this container."
    echo "Please specify a command in your docker-compose file."
    exit 1
fi