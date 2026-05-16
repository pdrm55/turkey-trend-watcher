from flask import Blueprint, jsonify, request
from app.database.models import SessionLocal, APIClient, utc_now
from app.core.api_auth import generate_api_key

bp = Blueprint('api_admin_b2b', __name__, url_prefix='/api/admin/b2b')

PLAN_DEFAULTS = {
    "starter":    {"monthly_limit": 1000,   "tps_threshold": 70.0},
    "pro":        {"monthly_limit": 10000,  "tps_threshold": 50.0},
    "enterprise": {"monthly_limit": 999999, "tps_threshold": 30.0},
}


@bp.route('/clients', methods=['GET'])
def list_clients():
    db = SessionLocal()
    try:
        clients = db.query(APIClient).order_by(APIClient.created_at.desc()).all()
        return jsonify([{
            "id": c.id,
            "name": c.name,
            "email": c.email,
            "plan": c.plan,
            "tps_threshold": c.tps_threshold,
            "monthly_limit": c.monthly_limit,
            "calls_used": c.calls_used,
            "is_active": c.is_active,
            "created_at": c.created_at.isoformat() + "Z" if c.created_at else None,
            "last_seen_at": c.last_seen_at.isoformat() + "Z" if c.last_seen_at else None,
            "api_key_preview": c.api_key[:12] + "..." if c.api_key else None
        } for c in clients])
    finally:
        db.close()


@bp.route('/clients', methods=['POST'])
def create_client():
    data = request.get_json() or {}
    name = data.get('name', '').strip()
    email = data.get('email', '').strip()
    plan = data.get('plan', 'starter')

    if not name or not email:
        return jsonify({"error": "name and email are required"}), 400
    if plan not in PLAN_DEFAULTS:
        return jsonify({"error": f"plan must be one of: {list(PLAN_DEFAULTS.keys())}"}), 400

    defaults = PLAN_DEFAULTS[plan]
    tps_threshold = float(data.get('tps_threshold', defaults['tps_threshold']))
    monthly_limit = int(data.get('monthly_limit', defaults['monthly_limit']))

    db = SessionLocal()
    try:
        key = generate_api_key()
        client = APIClient(
            name=name,
            email=email,
            api_key=key,
            plan=plan,
            tps_threshold=tps_threshold,
            monthly_limit=monthly_limit,
            calls_reset_at=utc_now()
        )
        db.add(client)
        db.commit()
        return jsonify({
            "status": "created",
            "id": client.id,
            "api_key": key,
            "plan": plan,
            "tps_threshold": tps_threshold,
            "monthly_limit": monthly_limit
        }), 201
    finally:
        db.close()


@bp.route('/clients/<int:client_id>', methods=['PATCH'])
def update_client(client_id):
    data = request.get_json() or {}
    db = SessionLocal()
    try:
        client = db.get(APIClient, client_id)
        if not client:
            return jsonify({"error": "not_found"}), 404

        if 'is_active' in data:
            client.is_active = bool(data['is_active'])
        if 'tps_threshold' in data:
            client.tps_threshold = float(data['tps_threshold'])
        if 'monthly_limit' in data:
            client.monthly_limit = int(data['monthly_limit'])
        if 'plan' in data and data['plan'] in PLAN_DEFAULTS:
            client.plan = data['plan']

        db.commit()
        return jsonify({"status": "updated", "id": client_id})
    finally:
        db.close()


@bp.route('/clients/<int:client_id>/reset-key', methods=['POST'])
def reset_key(client_id):
    db = SessionLocal()
    try:
        client = db.get(APIClient, client_id)
        if not client:
            return jsonify({"error": "not_found"}), 404
        new_key = generate_api_key()
        client.api_key = new_key
        db.commit()
        return jsonify({"status": "key_reset", "new_api_key": new_key})
    finally:
        db.close()
