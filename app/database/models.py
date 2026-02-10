from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Boolean, Index, Float, ForeignKey
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
    
    # --- ستون جدید برای سئو (Friendly URL) ---
    slug = Column(String(255), unique=True, index=True, nullable=True)
    
    title = Column(String(255), nullable=True)
    summary = Column(Text, nullable=True)

    # --- ستون‌های جدید برای مانیتورینگ فارسی ---
    title_fa = Column(String(255), nullable=True)
    summary_fa = Column(Text, nullable=True)
    
    category = Column(String(50), default="Gündem")
    
    message_count = Column(Integer, default=1)
    score = Column(Float, default=0.0)
    first_seen = Column(DateTime, default=utc_now)
    last_updated = Column(DateTime, default=utc_now)
    is_active = Column(Boolean, default=True)
    news_items = relationship("RawNews", backref="trend")

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()
