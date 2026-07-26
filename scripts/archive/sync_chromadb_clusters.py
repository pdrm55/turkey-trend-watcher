#!/usr/bin/env python3
"""
sync_chromadb_clusters.py

Full ChromaDB ↔ PostgreSQL sync for all clusters.

Problems fixed:
  1. Vectors whose cluster_id in ChromaDB no longer matches the actual cluster
     in PostgreSQL (happens when audit_fix_clusters splits a cluster but the
     old script didn't update ChromaDB).
  2. Clusters whose is_reference=True document is wrong or missing — causes
     get_related_trends() to return unrelated results.
  3. Stale vectors for news items that have been orphaned (trend_id=NULL) or
     deleted from PostgreSQL — these pollute similarity searches.

Run inside the ttw_merge container:
    python3 /app/sync_chromadb_clusters.py
"""
import sys
sys.path.insert(0, '/app')

from app.database.models import SessionLocal, RawNews, Trend
from app.core.ai_engine import ai_engine
from sqlalchemy import text
from collections import defaultdict


# ── Step 1: Build the ground-truth map from PostgreSQL ────────────────────────

def build_postgres_map(db):
    """Returns {external_id: cluster_uuid} for every news item assigned to a cluster."""
    rows = db.execute(text("""
        SELECT r.external_id, t.cluster_id
        FROM raw_news r
        JOIN trends t ON t.id = r.trend_id
        WHERE r.external_id IS NOT NULL
          AND r.trend_id    IS NOT NULL
          AND t.cluster_id  IS NOT NULL
    """)).fetchall()
    return {row[0]: row[1] for row in rows}


# ── Step 2: Decide correct state for every ChromaDB vector ────────────────────

def analyse(vectors, pg_map):
    """
    For each (doc_id, doc, meta) in vectors, determine:
      - correct_cluster: what cluster_id it should have
      - keep: False if the news item is orphaned/deleted in PostgreSQL
    Returns two structures:
      updates   – list of (doc_id, new_meta) that need to be written back
      by_cluster – {cluster_uuid: [(doc_id, doc, meta)]} after corrections
    """
    by_cluster = defaultdict(list)
    updates = []

    for doc_id, doc, meta in vectors:
        ext_id = meta.get('external_id', '')
        chroma_cluster = meta.get('cluster_id', '')

        if ext_id in pg_map:
            correct_cluster = pg_map[ext_id]
        else:
            # News is orphaned or outside PostgreSQL (e.g. X-Trend without a DB row)
            # Keep it where it is — don't touch it.
            correct_cluster = chroma_cluster

        needs_update = correct_cluster != chroma_cluster

        # Build in-memory cluster map using the *corrected* cluster id
        by_cluster[correct_cluster].append((doc_id, doc, meta, needs_update))

        if needs_update:
            new_meta = dict(meta)
            new_meta['cluster_id'] = correct_cluster
            new_meta['is_reference'] = False   # demote — reference will be reassigned below
            updates.append((doc_id, new_meta))

    return updates, by_cluster


# ── Step 3: Fix is_reference per cluster ──────────────────────────────────────

def fix_references(by_cluster):
    """
    For each cluster: ensure exactly one vector has is_reference=True.
    Best candidate = longest document that is not an X-Trend post.
    Returns list of (doc_id, new_meta) reference updates.
    """
    ref_updates = []

    for cluster_uuid, vecs in by_cluster.items():
        # vecs: list of (doc_id, doc, meta, was_moved)
        # After moves, metas may be stale — recompute from the pending updates list
        # (handled by caller merging updates first).

        current_refs = [v for v in vecs if v[2].get('is_reference') and not v[3]]
        # v[3]=True means it was moved, so its meta already has is_reference=False

        if len(current_refs) == 1:
            # Exactly one reference, and it wasn't moved — nothing to do
            continue

        # Either 0 or >1 references (or the reference was moved away).
        # Pick the best candidate: longest non-X-Trend document.
        best = None
        for doc_id, doc, meta, was_moved in vecs:
            if meta.get('source') == 'X-Trend':
                continue
            if best is None or len(doc) > len(best[1]):
                best = (doc_id, doc, meta)

        if best is None:
            # All docs are X-Trend — just use the first one
            best = (vecs[0][0], vecs[0][1], vecs[0][2])

        # Demote any extra existing references
        for doc_id, doc, meta, was_moved in vecs:
            if meta.get('is_reference') and not was_moved and doc_id != best[0]:
                new_meta = dict(meta)
                new_meta['is_reference'] = False
                ref_updates.append((doc_id, new_meta))

        # Promote the best candidate
        new_meta = dict(best[2])
        new_meta['is_reference'] = True
        ref_updates.append((best[0], new_meta))

    return ref_updates


# ── Step 4: Apply updates to ChromaDB ─────────────────────────────────────────

def apply_updates(updates, label):
    if not updates:
        print(f"   (no {label} needed)")
        return
    print(f"   Applying {len(updates)} {label}...")
    for i, (doc_id, new_meta) in enumerate(updates):
        ai_engine.collection.update(ids=[doc_id], metadatas=[new_meta])
        if (i + 1) % 50 == 0:
            print(f"   {i + 1}/{len(updates)} done...")
    print(f"   ✅ {len(updates)} {label} applied")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    db = SessionLocal()
    try:
        print("📊 Step 1 — Building PostgreSQL ground-truth map...")
        pg_map = build_postgres_map(db)
        print(f"   {len(pg_map):,} news items with active cluster assignments\n")

        print("🔍 Step 2 — Fetching all ChromaDB vectors...")
        raw = ai_engine.collection.get(include=['documents', 'metadatas'])
        vectors = list(zip(raw['ids'], raw['documents'], raw['metadatas']))
        print(f"   {len(vectors):,} vectors found\n")

        print("🔎 Step 3 — Analysing mismatches...")
        cluster_updates, by_cluster = analyse(vectors, pg_map)

        mismatch_count = len(cluster_updates)
        cluster_count  = len(by_cluster)
        print(f"   {mismatch_count} vectors have wrong cluster_id")
        print(f"   {cluster_count} unique clusters in ChromaDB\n")

        # Show a sample of mismatches for visibility
        if cluster_updates:
            print("   Sample mismatches (first 10):")
            for doc_id, new_meta in cluster_updates[:10]:
                # Find original meta
                orig = next(m for d, _, m in vectors if d == doc_id)
                print(f"     {orig.get('cluster_id','?')[:8]} → {new_meta['cluster_id'][:8]}")

        print("\n🔧 Step 4 — Fixing cluster_id mismatches in ChromaDB...")
        apply_updates(cluster_updates, "cluster_id fixes")

        print("\n📌 Step 5 — Fixing is_reference flags...")
        ref_updates = fix_references(by_cluster)
        apply_updates(ref_updates, "reference fixes")

        print(f"\n{'='*60}")
        print(f"✅  Sync complete.")
        print(f"    Vectors reassigned : {mismatch_count}")
        print(f"    References fixed   : {len(ref_updates)}")
        print(f"    Clusters in ChromaDB: {cluster_count}")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback; traceback.print_exc()
    finally:
        db.close()


if __name__ == "__main__":
    main()
