#!/usr/bin/env python3
"""
Cluster Audit & Fix Script
- Reads all active clusters with ≥3 news
- Uses Gemini to group news items by actual event
- Splits bad clusters into proper sub-clusters
- No confirmation needed — fully autonomous
"""
import sys, os, json, uuid, time, textwrap
sys.path.insert(0, '/app')

from app.database.models import SessionLocal, Trend, RawNews
from app.core.ai_engine import ai_engine
from sqlalchemy import text
from datetime import datetime

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# ── Gemini client ──────────────────────────────────────────────────────────────
def get_gemini_client():
    from google import genai
    return genai.Client(api_key=GOOGLE_API_KEY)

def group_news_with_gemini(cluster_title, news_items):
    """
    Ask Gemini to group news items by whether they describe the same event.
    Returns: list of groups, each group is a list of news IDs.
    """
    client = get_gemini_client()
    from google.genai import types

    snippets = "\n".join(
        f"[ID:{n['id']}] {n['source']} | {n['preview']}"
        for n in news_items
    )

    prompt = f"""You are a Turkish news deduplication engine.

Current cluster title: "{cluster_title}"

Below are {len(news_items)} news items from this cluster.
Group them by the SPECIFIC REAL-WORLD EVENT they describe.

Rules:
- Same broad topic (crime, politics) is NOT enough — must be the SAME specific incident.
- "İBB ihale operasyonu" and "pırlanta kaçakçılığı operasyonu" are DIFFERENT events.
- "Galatasaray şampiyonluk kutlaması" and "Galatasaray son maçı" are the SAME event.
- News items that don't fit any group → put in group "OTHER".

News items:
{snippets}

Return ONLY valid JSON in this exact format:
{{
  "groups": [
    {{
      "event_summary": "short description of the event in Turkish (max 80 chars)",
      "ids": [list of integer IDs]
    }}
  ]
}}

Do not include any explanation outside the JSON."""

    try:
        resp = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.0,
                max_output_tokens=2000,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            )
        )
        raw = resp.text.strip().strip("`").strip()
        if raw.startswith("json"):
            raw = raw[4:].strip()
        return json.loads(raw)
    except Exception as e:
        print(f"  ⚠️  Gemini error: {e}")
        return None


def _sync_chromadb(external_ids: list, new_cluster_uuid: str):
    """
    Reassign ChromaDB vectors from their current cluster to new_cluster_uuid.
    Called after PostgreSQL is committed so the split is already durable.
    Silently skips items not found in ChromaDB (e.g. old news beyond the rolling window).
    """
    updated = 0
    for ext_id in external_ids:
        if not ext_id:
            continue
        try:
            result = ai_engine.collection.get(
                where={"external_id": ext_id},
                include=["metadatas"]
            )
            if not result["ids"]:
                continue
            for doc_id, meta in zip(result["ids"], result["metadatas"]):
                meta["cluster_id"] = new_cluster_uuid
                meta["is_reference"] = False
                ai_engine.collection.update(ids=[doc_id], metadatas=[meta])
                updated += 1
        except Exception as e:
            print(f"  ⚠️  ChromaDB update failed for {ext_id[:60]}: {e}")
    return updated


def fix_cluster(cluster_id, groups, db):
    """
    Given a cluster and Gemini's grouping:
    - Keep the largest group in the original cluster (update title/count)
    - Create new clusters for all other groups
    - Orphan anything in 'OTHER' that has ≤1 item
    - Sync ChromaDB vectors so future ingest routes correctly
    """
    trend = db.query(Trend).filter(Trend.id == cluster_id).first()
    if not trend:
        return

    # Sort groups: largest first stays in original cluster
    sorted_groups = sorted(groups, key=lambda g: len(g["ids"]), reverse=True)
    main_group = sorted_groups[0]
    other_groups = sorted_groups[1:]

    all_news_ids = {n.id: n for n in db.query(RawNews).filter(RawNews.trend_id == cluster_id).all()}

    changes = []
    # Collect (external_ids, new_cluster_uuid) pairs for ChromaDB sync after commit
    chromadb_tasks = []

    # ── Main group: update original cluster ────────────────────────────────────
    main_ids = set(main_group["ids"])
    main_news = [all_news_ids[i] for i in main_ids if i in all_news_ids]
    if main_news:
        trend.message_count = len(main_news)
        trend.needs_scoring = True
        trend.is_published = False
        trend.summary = None
        trend.ai_processed_at = None
        changes.append(f"  ✅ Main cluster {cluster_id}: kept {len(main_news)} news → '{main_group['event_summary'][:60]}'")

    # ── Other groups: create new clusters ─────────────────────────────────────
    for grp in other_groups:
        grp_ids = set(grp["ids"])
        grp_news = [all_news_ids[i] for i in grp_ids if i in all_news_ids]

        if not grp_news:
            continue

        if len(grp_news) == 1 and grp["event_summary"] == "OTHER":
            # Single-item noise → just orphan
            grp_news[0].trend_id = None
            changes.append(f"  🗑️  Orphaned 1 noise item: {grp_news[0].id}")
            continue

        # Create new trend
        first_pub = min(n.published_at for n in grp_news if n.published_at)
        new_slug = grp["event_summary"][:50].lower()
        new_slug = ''.join(c if c.isalnum() or c == ' ' else '' for c in new_slug).replace(' ', '-')
        # Use uuid fragment to guarantee uniqueness even if event summaries are similar
        new_slug = f"{new_slug}-{str(uuid.uuid4())[:8]}"
        new_cluster_uuid = str(uuid.uuid4())

        new_trend = Trend(
            cluster_id=new_cluster_uuid,
            slug=new_slug[:200],
            title=grp["event_summary"][:255],
            category=trend.category,
            message_count=len(grp_news),
            score=0, tps_signal=0, tps_confidence=0, final_tps=0,
            trajectory="stable",
            first_seen=first_pub,
            last_updated=datetime.now(),
            is_active=True,
            needs_scoring=True,
            is_published=False,
            tags=[], entities=[]
        )
        db.add(new_trend)
        db.flush()  # get new ID

        for n in grp_news:
            n.trend_id = new_trend.id

        # Queue ChromaDB sync for this group
        ext_ids = [n.external_id for n in grp_news]
        chromadb_tasks.append((ext_ids, new_cluster_uuid))

        changes.append(f"  🆕 New cluster {new_trend.id}: {len(grp_news)} news → '{grp['event_summary'][:60]}'")

    db.commit()

    # ── Sync ChromaDB after successful DB commit ───────────────────────────────
    total_synced = 0
    for ext_ids, new_uuid in chromadb_tasks:
        total_synced += _sync_chromadb(ext_ids, new_uuid)
    if total_synced:
        changes.append(f"  🔗 ChromaDB: {total_synced} vectors reassigned")

    return changes


def audit_cluster(cluster_id, title, db):
    news_items = (
        db.query(RawNews)
        .filter(RawNews.trend_id == cluster_id)
        .order_by(RawNews.published_at.asc())
        .all()
    )

    if len(news_items) < 3:
        return None

    # Build preview list for Gemini
    items = []
    for n in news_items:
        content = (n.content or "")[:200].replace("\n", " ").strip()
        items.append({
            "id": n.id,
            "source": n.source_name or n.source_type,
            "preview": content
        })

    print(f"\n{'='*70}")
    print(f"Cluster {cluster_id} | {len(items)} news | TPS: ... | {title[:60]}")

    # Send to Gemini in batches of 30 to avoid token limits
    BATCH = 30
    all_groups = {}  # event_summary → list of IDs

    for start in range(0, len(items), BATCH):
        batch = items[start:start+BATCH]
        print(f"  📤 Sending batch {start//BATCH + 1} ({len(batch)} items) to Gemini...")
        result = group_news_with_gemini(title, batch)
        if not result:
            print("  ⚠️  Gemini returned nothing — skipping batch")
            continue
        for grp in result.get("groups", []):
            key = grp["event_summary"]
            all_groups.setdefault(key, [])
            all_groups[key].extend(grp["ids"])
        time.sleep(1)  # rate limit

    if not all_groups:
        print("  ⚠️  No groups returned — skipping cluster")
        return None

    groups = [{"event_summary": k, "ids": v} for k, v in all_groups.items()]
    print(f"  📊 Gemini found {len(groups)} distinct event group(s):")
    for g in groups:
        print(f"     [{len(g['ids'])} items] {g['event_summary'][:70]}")

    return groups


def main():
    db = SessionLocal()
    try:
        # Get all active clusters with ≥3 news, ordered by TPS DESC
        rows = db.execute(text("""
            SELECT t.id, t.title, t.message_count, t.final_tps
            FROM trends t
            WHERE t.is_active = true
              AND t.message_count >= 3
            ORDER BY t.final_tps DESC
        """)).fetchall()

        print(f"\n🔍 Found {len(rows)} clusters to audit\n")

        total_splits = 0
        total_orphans = 0

        for row in rows:
            cid, title, count, tps = row
            print(f"\n▶ [{tps:.1f} TPS | {count} news] {title[:65]}")

            groups = audit_cluster(cid, title, db)
            if groups is None:
                print("  ✅ Skipped (too few items or Gemini error)")
                continue

            if len(groups) == 1:
                print("  ✅ All news belong to the same event — no fix needed")
                continue

            print(f"  🔧 Fixing: splitting into {len(groups)} clusters...")
            changes = fix_cluster(cid, groups, db)
            if changes:
                for c in changes:
                    print(c)
                total_splits += len(groups) - 1

            time.sleep(2)  # rate limit between clusters

        print(f"\n{'='*70}")
        print(f"✅ Audit complete.")
        print(f"   Clusters split: {total_splits}")
        print(f"   Orphaned items: {total_orphans}")

    except Exception as e:
        db.rollback()
        print(f"❌ Error: {e}")
        import traceback; traceback.print_exc()
    finally:
        db.close()


if __name__ == "__main__":
    main()
