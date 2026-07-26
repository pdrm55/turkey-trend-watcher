import os
import secrets
from functools import wraps
from datetime import datetime, timedelta, timezone
from flask import request, jsonify, g
from sqlalchemy import text
from app.database.models import SessionLocal, APIClient

# Roll the monthly window over, but only if it is genuinely due. The predicate is
# in the statement so two concurrent requests cannot both reset the counter.
_SQL_RESET_WINDOW = text("""
    UPDATE api_clients
       SET calls_used = 0, calls_reset_at = :now
     WHERE id = :id
       AND calls_reset_at IS NOT NULL
       AND calls_reset_at <= :cutoff
""")

# Check-and-increment in a single statement. Reading calls_used into Python and
# writing it back lost increments under concurrency — two requests both read N
# and both wrote N+1 — and the limit check ran against the stale value, so the
# monthly cap could be walked straight past with parallel requests. Zero rows
# updated means the quota is spent.
_SQL_CONSUME_CALL = text("""
    UPDATE api_clients
       SET calls_used = calls_used + 1, last_seen_at = :now
     WHERE id = :id
       AND (monthly_limit IS NULL OR monthly_limit <= 0 OR calls_used < monthly_limit)
 RETURNING calls_used
""")

BASE_SITE_URL = os.getenv("BASE_SITE_URL", "https://trendiatr.com")


def generate_api_key() -> str:
    return f"ttr_{secrets.token_urlsafe(32)}"


def require_api_key(f):
    """
    Validates API key from header or query param.
    Sets g.api_client for use in route handlers.

    Accepts:
        Header:  Authorization: Bearer ttr_xxx
        Header:  X-API-Key: ttr_xxx
        Query:   ?api_key=ttr_xxx
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        api_key = None
        auth_header = request.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            api_key = auth_header[7:].strip()
        if not api_key:
            api_key = request.headers.get('X-API-Key', '').strip()
        if not api_key:
            api_key = request.args.get('api_key', '').strip()

        if not api_key:
            return jsonify({
                "error": "unauthorized",
                "message": "API key required. Pass via Authorization: Bearer <key> or X-API-Key header."
            }), 401

        db = SessionLocal()
        try:
            client = db.query(APIClient).filter_by(
                api_key=api_key, is_active=True
            ).first()

            if not client:
                return jsonify({
                    "error": "unauthorized",
                    "message": "Invalid or inactive API key."
                }), 401

            now = datetime.now(timezone.utc).replace(tzinfo=None)

            db.execute(_SQL_RESET_WINDOW, {
                "id": client.id, "now": now, "cutoff": now - timedelta(days=30),
            })

            consumed = db.execute(_SQL_CONSUME_CALL, {"id": client.id, "now": now}).first()
            db.commit()

            if consumed is None:
                # The guard in the UPDATE rejected it: the quota is already spent.
                db.refresh(client)
                return jsonify({
                    "error": "rate_limit_exceeded",
                    "message": f"Monthly limit of {client.monthly_limit} calls reached.",
                    "limit": client.monthly_limit,
                    "used": client.calls_used,
                    "resets_at": client.calls_reset_at.isoformat() + "Z" if client.calls_reset_at else None
                }), 429

            g.api_client = {
                "id": client.id,
                "name": client.name,
                "plan": client.plan,
                "tps_threshold": client.tps_threshold,
                "calls_used": consumed[0],
                "monthly_limit": client.monthly_limit
            }

        finally:
            db.close()

        return f(*args, **kwargs)
    return decorated
