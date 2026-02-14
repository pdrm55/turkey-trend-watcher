from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Boolean, Index, Float, ForeignKey, inspect, text
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from datetime import datetime, timezone
from app.config import Config

# تعریف پایه مدل‌ها
Base = declarative_base()

# --- تنظیمات استراتژیک: اضافه شدن قابلیت pool_pre_ping ---
# این تنظیم باعث می‌شود که اگر دیتابیس ریستارت شود (مثلاً در حین hard_reset)،
# موتور SQLAlchemy قطعی را تشخیص داده و به طور خودکار اتصال را مجدداً برقرار کند.
engine = create_engine(
    Config.SQLALCHEMY_DATABASE_URI,
    pool_pre_ping=True,
    pool_recycle=3600
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def utc_now():
    """تولید زمان فعلی به فرمت UTC برای هماهنگی تمام بخش‌ها"""
    return datetime.now(timezone.utc).replace(tzinfo=None)

class RawNews(Base):
    """ذخیره اخبار خام دریافتی از منابع مختلف (تلگرام و RSS)"""
    __tablename__ = "raw_news"
    id = Column(Integer, primary_key=True, index=True)
    source_type = Column(String(50)) # rss یا telegram
    source_name = Column(String(100))
    source_tier = Column(Integer, default=3) # لایه اعتبار منبع (1: رسمی، 2: معتبر، 3: ناشناس)
    external_id = Column(String(255), unique=True)
    content = Column(Text)
    published_at = Column(DateTime)
    created_at = Column(DateTime, default=utc_now)
    trend_id = Column(Integer, ForeignKey('trends.id'), nullable=True)
    
    # رابطه با جدول ورود سیگنال‌ها (برای محاسبات Velocity)
    arrivals = relationship("TrendArrivals", backref="news_item", cascade="all, delete-orphan")

    __table_args__ = (Index('idx_source_time', 'source_type', 'published_at'),)

class Trend(Base):
    """خوشه‌های خبری پردازش شده و ترندهای شناسایی شده"""
    __tablename__ = "trends"
    id = Column(Integer, primary_key=True, index=True)
    cluster_id = Column(String(100), unique=True, index=True)
    
    # فیلد سئو (SEO Slug)
    slug = Column(String(255), unique=True, index=True, nullable=True)
    
    # محتوای تولید شده توسط هوش مصنوعی (فقط زبان ترکی)
    title = Column(String(255), nullable=True)
    summary = Column(Text, nullable=True)
    category = Column(String(50), default="Gündem")
    message_count = Column(Integer, default=1)
    
    # فیلدهای مربوط به سیستم امتیازدهی TPS 2.1
    score = Column(Float, default=0.0) # امتیاز کلی (سازگاری با نسخه‌های قدیمی)
    tps_signal = Column(Float, default=0.0) # امتیاز مبتنی بر سیگنال (V, E, S, N)
    tps_confidence = Column(Float, default=0.0) # ضریب اطمینان منابع
    final_tps = Column(Float, default=0.0) # امتیاز نهایی محاسبه شده
    
    # فیلدهای ردیابی روند (Trajectory) - اضافه شده در فاز ۱ بهینه سازی
    previous_tps = Column(Float, default=0.0) # امتیاز در چرخه قبلی برای محاسبه شتاب
    trajectory = Column(String(20), default="steady") # وضعیت: up (صعودی)، down (نزولی)، steady (ثابت)
    
    # --- فاز ۶.۲: فلگ پردازش آسنکرون ---
    # اگر True باشد، یعنی خبر جدیدی آمده و باید امتیاز دوباره محاسبه شود
    needs_scoring = Column(Boolean, default=True, index=True)

    first_seen = Column(DateTime, default=utc_now)
    last_updated = Column(DateTime, default=utc_now)
    is_active = Column(Boolean, default=True)
    
    # روابط دیتابیسی
    news_items = relationship("RawNews", backref="trend")
    arrival_history = relationship("TrendArrivals", backref="trend", cascade="all, delete-orphan")

class TrendArrivals(Base):
    """
    ثبت دقیق لحظه ورود هر خبر به یک ترند.
    این جدول برای محاسبه دقیق پارامتر Velocity (سرعت انتشار) در موتور Scoring حیاتی است.
    """
    __tablename__ = "trend_arrivals"
    id = Column(Integer, primary_key=True, index=True)
    trend_id = Column(Integer, ForeignKey('trends.id'), nullable=False)
    raw_news_id = Column(Integer, ForeignKey('raw_news.id'), nullable=True)
    timestamp = Column(DateTime, default=utc_now)

def init_db():
    """
    آماده‌سازی، هماهنگ‌سازی و مهاجرت خودکار دیتابیس.
    این تابع جداول را ساخته و ستون‌های جدید را بدون حذف داده‌ها به جداول موجود اضافه می‌کند.
    """
    print("⏳ Synchronizing Database (Strategic Mode - TPS 2.1)...")
    try:
        # ۱. ساخت جداول پایه در صورت عدم وجود
        Base.metadata.create_all(bind=engine)
        
        inspector = inspect(engine)
        
        # ۲. بررسی و بروزرسانی جدول trends (مدیریت ستون‌ها و سئو)
        trend_columns = [c['name'] for c in inspector.get_columns('trends')]
        with engine.connect() as conn:
            # الف) حذف فیلدهای منسوخ شده (فارسی) جهت بهینه‌سازی حجم
            if 'title_fa' in trend_columns:
                print("🗑️ Removing legacy 'title_fa' column...")
                conn.execute(text("ALTER TABLE trends DROP COLUMN title_fa"))
            if 'summary_fa' in trend_columns:
                print("🗑️ Removing legacy 'summary_fa' column...")
                conn.execute(text("ALTER TABLE trends DROP COLUMN summary_fa"))

            # ب) مدیریت ستون Slug برای سئو
            if 'slug' not in trend_columns:
                print("⚠️ Adding 'slug' column to 'trends'...")
                conn.execute(text("ALTER TABLE trends ADD COLUMN slug VARCHAR(255)"))
                conn.execute(text("CREATE INDEX idx_trends_slug ON trends (slug)"))
            
            # ج) اضافه کردن فیلدهای پایه TPS
            if 'tps_signal' not in trend_columns:
                print("🚀 Adding TPS scoring columns to 'trends'...")
                conn.execute(text("ALTER TABLE trends ADD COLUMN tps_signal FLOAT DEFAULT 0.0"))
                conn.execute(text("ALTER TABLE trends ADD COLUMN tps_confidence FLOAT DEFAULT 0.0"))
                conn.execute(text("ALTER TABLE trends ADD COLUMN final_tps FLOAT DEFAULT 0.0"))

            # د) اضافه کردن فیلدهای ردیابی شتاب و روند حرکت
            if 'previous_tps' not in trend_columns:
                print("📈 Adding 'previous_tps' for velocity tracking...")
                conn.execute(text("ALTER TABLE trends ADD COLUMN previous_tps FLOAT DEFAULT 0.0"))
            
            if 'trajectory' not in trend_columns:
                print("🏹 Adding 'trajectory' status column...")
                conn.execute(text("ALTER TABLE trends ADD COLUMN trajectory VARCHAR(20) DEFAULT 'steady'"))
            
            # ه) اضافه کردن فیلد پردازش آسنکرون (فاز ۶.۲)
            if 'needs_scoring' not in trend_columns:
                print("⚡ Adding 'needs_scoring' for Async Processing...")
                conn.execute(text("ALTER TABLE trends ADD COLUMN needs_scoring BOOLEAN DEFAULT TRUE"))
                conn.execute(text("CREATE INDEX idx_needs_scoring ON trends (needs_scoring)"))
            
            conn.commit()

        # ۳. بررسی و بروزرسانی جدول raw_news
        news_columns = [c['name'] for c in inspector.get_columns('raw_news')]
        if 'source_tier' not in news_columns:
            print("🛡️ Adding 'source_tier' to 'raw_news' table...")
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE raw_news ADD COLUMN source_tier INTEGER DEFAULT 3"))
                conn.commit()
            
        print("✅ Database synchronization successful. All strategic fields are ready.")
    except Exception as e:
        print(f"❌ Database initialization failed: {e}")

def get_db():
    """تولید یک Session جدید برای استفاده در API یا Workerها"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()