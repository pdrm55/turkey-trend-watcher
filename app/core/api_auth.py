import os
import secrets
from functools import wraps
from datetime import datetime, timezone
from flask import request, jsonify, g
from app.database.models import SessionLocal, APIClient

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
            ).with_for_update().first()

            if not client:
                return jsonify({
                    "error": "unauthorized",
                    "message": "Invalid or inactive API key."
                }), 401

            now = datetime.now(timezone.utc).replace(tzinfo=None)

            if client.calls_reset_at:
                delta = now - client.calls_reset_at
                if delta.days >= 30:
                    client.calls_used = 0
                    client.calls_reset_at = now
                    db.commit()

            if client.monthly_limit and client.calls_used >= client.monthly_limit:
                return jsonify({
                    "error": "rate_limit_exceeded",
                    "message": f"Monthly limit of {client.monthly_limit} calls reached.",
                    "limit": client.monthly_limit,
                    "used": client.calls_used,
                    "resets_at": client.calls_reset_at.isoformat() + "Z" if client.calls_reset_at else None
                }), 429

            client.calls_used += 1
            client.last_seen_at = now
            db.commit()

            g.api_client = {
                "id": client.id,
                "name": client.name,
                "plan": client.plan,
                "tps_threshold": client.tps_threshold,
                "calls_used": client.calls_used,
                "monthly_limit": client.monthly_limit
            }

        finally:
            db.close()

        return f(*args, **kwargs)
    return decorated
