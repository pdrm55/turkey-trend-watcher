#!/bin/bash

echo "🧹 Starting Deep Clean of Vector Database..."

# ۱. متوقف کردن کانتینرها
sudo docker-compose down

# ۲. پاک کردن پوشه ChromaDB (جایی که ایندکس‌های خراب احتمالا ذخیره شده‌اند)
# توجه: این کار باعث می‌شود هوش مصنوعی دوباره خبرها را یاد بگیرد، اما از کرش جلوگیری می‌کند.
if [ -d "chroma_db_data" ]; then
    echo "🗑️ Removing corrupted chroma_db_data..."
    sudo rm -rf chroma_db_data
fi

# ۳. پاک کردن قفل‌های پایتون و فایل‌های موقت
find . -type d -name "__pycache__" -exec rm -rf {} +
find . -type f -name "*.pyc" -delete

# ۴. بازسازی و اجرای مجدد با ظرفیت حافظه تازه
echo "🚀 Rebuilding and starting..."
sudo docker-compose up -d --build app

echo "✨ System reset complete. Check logs with: sudo docker logs -f ttw_app"
