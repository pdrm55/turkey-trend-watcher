#!/bin/bash

# نام شبکه پروژه
NETWORK_NAME="turkey-trend-watcher_ttw_network"

echo "🚨 WARNING: This is a NUCLEAR RESET. It will WIPE all vector data (ChromaDB)."
echo "Press Ctrl+C to cancel or wait 5 seconds..."
sleep 5

# ۱. توقف سرویس‌ها
echo "🛑 Stopping everything..."
sudo docker-compose down --remove-orphans

# ۲. آزادسازی شبکه
echo "🔍 Force cleaning network: $NETWORK_NAME"
STUCK_CONTAINERS=$(docker ps -a -q --filter network=$NETWORK_NAME)

if [ ! -z "$STUCK_CONTAINERS" ]; then
    docker rm -f $STUCK_CONTAINERS
fi

docker network rm $NETWORK_NAME 2>/dev/null || true

# ۳. پاکسازی فیزیکی دیتابیس برداری (بخش اصلی تفاوت با Safe Restart)
echo "🧹 WIPING DATABASE: Deleting chroma_db_data..."
sudo rm -rf chroma_db_data
find . -type d -name "__pycache__" -exec rm -rf {} +

# ۴. اجرای مجدد
echo "🚀 Building fresh environment..."
sudo docker-compose up -d --build

echo "✅ Hard reset complete. Everything is fresh. Logs: sudo docker logs -f ttw_api"