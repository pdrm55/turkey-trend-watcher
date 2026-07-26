import sys, os
sys.path.append('/app')
from app.database.models import SessionLocal
from sqlalchemy import text as sa_text

db = SessionLocal()
try:
    rows = db.execute(sa_text("""
        SELECT rn.id, rn.media_path, rn.trend_id
        FROM raw_news rn
        WHERE rn.media_url = 'placeholder'
          AND rn.media_status = 2
    """)).fetchall()

    print(f"Found {len(rows)} placeholder items")

    deleted_files = 0
    all_paths = []
    for row in rows:
        if row.media_path:
            full_path = os.path.join("/app/app/static", row.media_path)
            all_paths.append(row.media_path)
            if os.path.exists(full_path):
                os.remove(full_path)
                deleted_files += 1

    print(f"Deleted {deleted_files} physical WebP files")

    result = db.execute(sa_text("""
        UPDATE raw_news
        SET media_status = 0,
            media_path    = NULL,
            media_url     = NULL
        WHERE media_url = 'placeholder'
          AND media_status = 2
    """))
    print(f"Reset {result.rowcount} news rows -> media_status=0")

    cleared_trends = 0
    for path in all_paths:
        r = db.execute(sa_text(
            "UPDATE trends SET cover_image = NULL WHERE cover_image = :p"
        ), {"p": path})
        cleared_trends += r.rowcount

    print(f"Cleared cover_image on {cleared_trends} trends")
    db.commit()
    print("DONE")

except Exception as e:
    db.rollback()
    print(f"ERROR: {e}")
    import traceback; traceback.print_exc()
finally:
    db.close()
