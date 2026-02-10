#!/bin/bash

echo "🚨 WARNING: This will wipe all vector data and rebuild the system."
echo "Press Ctrl+C to cancel or wait 5 seconds..."
sleep 5

# ۱. توقف کامل و پاکسازی تمام حجم‌های داکر (بجز دیتابیس اصلی پستگرس)
sudo docker-compose down

# ۲. پاکسازی فیزیکی پوشه‌های دیتابیس برداری و قفل‌ها
echo "🧹 Wiping corrupted data and locks..."
sudo rm -rf chroma_db_data
find . -type d -name "__pycache__" -exec rm -rf {} +

# ۳. اجرای مجدد با بیلد تازه
echo "🚀 Building fresh environment..."
sudo docker-compose up -d --build

echo "✅ Hard reset complete. Check logs: sudo docker logs -f ttw_app"
