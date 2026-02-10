# استفاده از نسخه 3.11 برای پایداری کامل و جلوگیری از Segmentation Fault
FROM python:3.11-slim

# تنظیمات بهینه‌سازی پایتون
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# رفع خطای Segmentation Fault در کتابخانه‌های هوش مصنوعی و ChromaDB
# محدود کردن تعداد تردها برای جلوگیری از تداخل حافظه در داکر
ENV OMP_NUM_THREADS=1
ENV MKL_NUM_THREADS=1
ENV TOKENIZERS_PARALLELISM=false

# نصب پیش‌نیازهای سیستمی ضروری
RUN apt-get update && apt-get install -y \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# تنظیم پوشه کاری
WORKDIR /app

# نصب پکیج‌های پایتون
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# کپی کدها
COPY . .

# تنظیمات دسترسی و اجرای اسکریپت اصلی
RUN chmod +x scripts/entrypoint.sh

# نقطه شروع
ENTRYPOINT ["/bin/bash", "/app/scripts/entrypoint.sh"]
