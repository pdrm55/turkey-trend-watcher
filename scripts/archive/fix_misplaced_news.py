#!/usr/bin/env python3
"""
Fix misplaced news items that re-entered the wrong cluster after audit.
Root cause: audit_fix_clusters.py updates PostgreSQL but NOT ChromaDB vectors,
so new similar news keeps routing to the old (wrong) cluster via ChromaDB lookup.

This script:
1. Moves specific raw_news rows from source cluster to target cluster (PostgreSQL)
2. Updates their ChromaDB vectors' cluster_id metadata
3. Reactivates target cluster if needed
4. Updates message counts on both clusters
"""
import sys
sys.path.insert(0, '/app')

from app.database.models import SessionLocal, Trend, RawNews
from app.core.ai_engine import ai_engine

# ── Config ─────────────────────────────────────────────────────────────────────
NEWS_IDS_TO_MOVE = [311921, 313484]   # konut/TOKİ news wrongly in 195606
SOURCE_TREND_ID  = 195606             # Galatasaray şampiyonluk
TARGET_TREND_ID  = 195647             # Konut satışları ve TOKİ projeleri

EXTERNAL_IDS = [
    "https://bigpara.hurriyet.com.tr/haberler/ekonomi-haberleri/konuta-talep-tam-gaz-yilin-zirvesi-goruldu_ID100701579/",
    "https://www.cumhuriyet.com.tr/turkiye/toki-istanbul-konutlari-nereye-yapilacak-toki-istanbul-konutlari-hangi-ilceye-yapilacak-2504065",
]
# ──────────────────────────────────────────────────────────────────────────────

def fix_chromadb(external_ids: list[str], target_cluster_id: str):
    """Update cluster_id in ChromaDB for documents matching given external_ids."""
    print("\n🔍 Searching ChromaDB for misplaced vectors...")
    updated = 0
    for ext_id in external_ids:
        try:
            result = ai_engine.collection.get(
                where={"external_id": ext_id},
                include=["metadatas"]
            )
            if not result["ids"]:
                print(f"  ⚠️  Not found in ChromaDB: {ext_id[:80]}")
                continue

            for doc_id, meta in zip(result["ids"], result["metadatas"]):
                old_cluster = meta.get("cluster_id", "?")
                meta["cluster_id"] = target_cluster_id
                meta["is_reference"] = False
                ai_engine.collection.update(ids=[doc_id], metadatas=[meta])
                print(f"  ✅ ChromaDB: {ext_id[:60]}...")
                print(f"     {old_cluster[:8]} → {target_cluster_id[:8]}")
                updated += 1
        except Exception as e:
            print(f"  ❌ ChromaDB error for {ext_id[:60]}: {e}")
    return updated


def main():
    db = SessionLocal()
    try:
        source = db.query(Trend).filter(Trend.id == SOURCE_TREND_ID).first()
        target = db.query(Trend).filter(Trend.id == TARGET_TREND_ID).first()

        if not source or not target:
            print("❌ Source or target cluster not found")
            return

        print(f"Source [{source.id}]: '{source.title[:60]}' ({source.message_count} news)")
        print(f"Target [{target.id}]: '{target.title[:60]}' ({target.message_count} news, active={target.is_active})")

        # 1. Move news in PostgreSQL
        news_items = db.query(RawNews).filter(RawNews.id.in_(NEWS_IDS_TO_MOVE)).all()
        if not news_items:
            print("❌ No news items found with given IDs")
            return

        print(f"\n📦 Moving {len(news_items)} news items...")
        for n in news_items:
            print(f"  → [{n.id}] {(n.content or '')[:80]}...")
            n.trend_id = TARGET_TREND_ID

        # 2. Update message counts
        source.message_count = max(0, source.message_count - len(news_items))
        source.needs_scoring = True

        target.message_count += len(news_items)
        target.needs_scoring = True
        target.is_active = True        # reactivate if gravity killed it
        target.is_published = False
        target.summary = None
        target.ai_processed_at = None

        db.commit()
        print(f"\n✅ PostgreSQL updated:")
        print(f"   [{source.id}] {source.title[:50]} → {source.message_count} news")
        print(f"   [{target.id}] {target.title[:50]} → {target.message_count} news (reactivated)")

        # 3. Fix ChromaDB vectors
        chroma_updated = fix_chromadb(EXTERNAL_IDS, target.cluster_id)
        print(f"\n✅ ChromaDB: {chroma_updated} vectors reassigned to target cluster")

        print("\n🎯 Done. New konut/TOKİ news will now route correctly to cluster 195647.")

    except Exception as e:
        db.rollback()
        print(f"❌ Error: {e}")
        import traceback; traceback.print_exc()
    finally:
        db.close()


if __name__ == "__main__":
    main()
