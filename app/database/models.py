from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Boolean, Index, Float, ForeignKey, inspect, text
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from datetime import datetime, timezone
from app.config import Config

Base = declarative_base()
engine = create_engine(Config.SQLALCHEMY_DATABASE_URI)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

class RawNews(Base):
    __tablename__ = "raw_news"
    id = Column(Integer, primary_key=True, index=True)
    source_type = Column(String(50))
    source_name = Column(String(100))
    external_id = Column(String(255), unique=True)
    content = Column(Text)
    published_at = Column(DateTime)
    created_at = Column(DateTime, default=utc_now)
    trend_id = Column(Integer, ForeignKey('trends.id'), nullable=True)
    __table_args__ = (Index('idx_source_time', 'source_type', 'published_at'),)

class Trend(Base):
    __tablename__ = "trends"
    id = Column(Integer, primary_key=True, index=True)
    cluster_id = Column(String(100), unique=True, index=True)
    
    # ستون سئو
    slug = Column(String(255), unique=True, index=True, nullable=True)
    
    title = Column(String(255), nullable=True)
    summary = Column(Text, nullable=True)
    title_fa = Column(String(255), nullable=True)
    summary_fa = Column(Text, nullable=True)
    category = Column(String(50), default="Gündem")
    message_count = Column(Integer, default=1)
    score = Column(Float, default=0.0)
    first_seen = Column(DateTime, default=utc_now)
    last_updated = Column(DateTime, default=utc_now)
    is_active = Column(Boolean, default=True)
    news_items = relationship("RawNews", backref="trend")

def init_db():
    """
    آماده‌سازی خودکار دیتابیس: ساخت جداول و چک کردن ستون‌های جدید
    """
    print("⏳ Initializing database synchronization...")
    try:
        # ۱. ساخت جداول اگر وجود نداشته باشند
        Base.metadata.create_all(bind=engine)
        
        # ۲. چک کردن دستی برای ستون slug (اگر جدول از قبل وجود داشت اما قدیمی بود)
        inspector = inspect(engine)
        columns = [c['name'] for c in inspector.get_columns('trends')]
        
        if 'slug' not in columns:
            print("⚠️ Column 'slug' missing in 'trends' table. Adding it now...")
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE trends ADD COLUMN slug VARCHAR(255)"))
                conn.execute(text("CREATE INDEX idx_trends_slug ON trends (slug)"))
                conn.commit()
            print("✅ Column 'slug' added successfully.")
            
        print("✅ Database is synchronized and ready.")
    except Exception as e:
        print(f"❌ Database initialization failed: {e}")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()