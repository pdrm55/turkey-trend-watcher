import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database.models import SessionLocal, APIClient, utc_now
from app.core.api_auth import generate_api_key


@pytest.fixture(scope='module')
def app():
    from web_server import create_app
    application = create_app()
    application.config['TESTING'] = True
    return application


@pytest.fixture(scope='module')
def client(app):
    return app.test_client()


@pytest.fixture(scope='module')
def test_api_client():
    db = SessionLocal()
    key = generate_api_key()
    api_client = APIClient(
        name="Test Client",
        email="test@test.com",
        api_key=key,
        plan="pro",
        tps_threshold=0.0,
        monthly_limit=10000,
        calls_reset_at=utc_now(),
        is_active=True
    )
    db.add(api_client)
    db.commit()
    client_id = api_client.id
    db.close()

    yield {"api_key": key, "id": client_id}

    db = SessionLocal()
    db.query(APIClient).filter_by(id=client_id).delete()
    db.commit()
    db.close()


class TestHealth:
    def test_health_no_auth(self, client):
        r = client.get('/api/v1/health')
        assert r.status_code == 200
        data = r.get_json()
        assert data['status'] == 'ok'
        assert 'version' in data


class TestAuthentication:
    def test_no_key_returns_401(self, client):
        r = client.get('/api/v1/trends')
        assert r.status_code == 401
        assert r.get_json()['error'] == 'unauthorized'

    def test_invalid_key_returns_401(self, client):
        r = client.get('/api/v1/trends', headers={'X-API-Key': 'ttr_invalid'})
        assert r.status_code == 401

    def test_bearer_token_accepted(self, client, test_api_client):
        r = client.get(
            '/api/v1/trends',
            headers={'Authorization': f"Bearer {test_api_client['api_key']}"}
        )
        assert r.status_code == 200

    def test_x_api_key_header_accepted(self, client, test_api_client):
        r = client.get(
            '/api/v1/trends',
            headers={'X-API-Key': test_api_client['api_key']}
        )
        assert r.status_code == 200

    def test_query_param_accepted(self, client, test_api_client):
        r = client.get(f"/api/v1/trends?api_key={test_api_client['api_key']}")
        assert r.status_code == 200


class TestTrendsList:
    def test_response_structure(self, client, test_api_client):
        r = client.get(
            '/api/v1/trends',
            headers={'X-API-Key': test_api_client['api_key']}
        )
        assert r.status_code == 200
        data = r.get_json()
        assert 'data' in data
        assert 'meta' in data
        assert isinstance(data['data'], list)
        assert 'total' in data['meta']
        assert 'min_tps_applied' in data['meta']

    def test_each_trend_has_required_fields(self, client, test_api_client):
        r = client.get(
            '/api/v1/trends',
            headers={'X-API-Key': test_api_client['api_key']}
        )
        data = r.get_json()
        required = ['id', 'title', 'category', 'tps_score', 'trajectory', 'article_count', 'first_seen', 'url']
        for trend in data['data']:
            for field in required:
                assert field in trend, f"Missing field '{field}' in trend {trend.get('id')}"

    def test_limit_param(self, client, test_api_client):
        r = client.get(
            '/api/v1/trends?limit=5',
            headers={'X-API-Key': test_api_client['api_key']}
        )
        data = r.get_json()
        assert len(data['data']) <= 5

    def test_limit_capped_at_100(self, client, test_api_client):
        r = client.get(
            '/api/v1/trends?limit=999',
            headers={'X-API-Key': test_api_client['api_key']}
        )
        assert r.status_code == 200
        data = r.get_json()
        assert len(data['data']) <= 100

    def test_invalid_limit_returns_400(self, client, test_api_client):
        r = client.get(
            '/api/v1/trends?limit=abc',
            headers={'X-API-Key': test_api_client['api_key']}
        )
        assert r.status_code == 400

    def test_min_tps_filter(self, client, test_api_client):
        r = client.get(
            '/api/v1/trends?min_tps=99',
            headers={'X-API-Key': test_api_client['api_key']}
        )
        data = r.get_json()
        for trend in data['data']:
            assert trend['tps_score'] >= 99

    def test_trajectory_filter(self, client, test_api_client):
        for val in ['up', 'down', 'steady']:
            r = client.get(
                f'/api/v1/trends?trajectory={val}',
                headers={'X-API-Key': test_api_client['api_key']}
            )
            assert r.status_code == 200
            for trend in r.get_json()['data']:
                assert trend['trajectory'] == val

    def test_tps_threshold_enforced(self, client):
        db = SessionLocal()
        key = generate_api_key()
        restricted = APIClient(
            name="Restricted", email="r@r.com", api_key=key,
            plan="starter", tps_threshold=80.0, monthly_limit=1000,
            calls_reset_at=utc_now(), is_active=True
        )
        db.add(restricted)
        db.commit()
        client_id = restricted.id
        db.close()

        try:
            r = client.get(
                '/api/v1/trends?min_tps=10',
                headers={'X-API-Key': key}
            )
            data = r.get_json()
            assert data['meta']['min_tps_applied'] == 80.0
            for trend in data['data']:
                assert trend['tps_score'] >= 80.0
        finally:
            db = SessionLocal()
            db.query(APIClient).filter_by(id=client_id).delete()
            db.commit()
            db.close()


class TestTrendDetail:
    def test_valid_id_returns_full_data(self, client, test_api_client):
        list_r = client.get(
            '/api/v1/trends?limit=1',
            headers={'X-API-Key': test_api_client['api_key']}
        )
        trends = list_r.get_json()['data']
        if not trends:
            pytest.skip("No trends in DB to test")

        trend_id = trends[0]['id']
        r = client.get(
            f'/api/v1/trends/{trend_id}',
            headers={'X-API-Key': test_api_client['api_key']}
        )
        assert r.status_code == 200
        data = r.get_json()['data']
        assert 'cluster' in data
        assert 'articles' in data['cluster']
        assert 'summary' in data

    def test_invalid_id_returns_404(self, client, test_api_client):
        r = client.get(
            '/api/v1/trends/999999999',
            headers={'X-API-Key': test_api_client['api_key']}
        )
        assert r.status_code == 404


class TestMediaEndpoint:
    def test_media_endpoint_structure(self, client, test_api_client):
        list_r = client.get(
            '/api/v1/trends?limit=1',
            headers={'X-API-Key': test_api_client['api_key']}
        )
        trends = list_r.get_json()['data']
        if not trends:
            pytest.skip("No trends in DB to test")

        trend_id = trends[0]['id']
        r = client.get(
            f'/api/v1/trends/{trend_id}/media',
            headers={'X-API-Key': test_api_client['api_key']}
        )
        assert r.status_code == 200
        data = r.get_json()
        assert 'media_count' in data
        assert 'data' in data
        assert isinstance(data['data'], list)
        for m in data['data']:
            assert 'type' in m
            assert 'url' in m


class TestUsage:
    def test_usage_returns_correct_structure(self, client, test_api_client):
        r = client.get(
            '/api/v1/usage',
            headers={'X-API-Key': test_api_client['api_key']}
        )
        assert r.status_code == 200
        data = r.get_json()
        assert 'plan' in data
        assert 'calls_used' in data
        assert 'calls_remaining' in data
        assert 'monthly_limit' in data

    def test_usage_counter_increments(self, client, test_api_client):
        r1 = client.get('/api/v1/usage', headers={'X-API-Key': test_api_client['api_key']})
        used_before = r1.get_json()['calls_used']

        client.get('/api/v1/trends', headers={'X-API-Key': test_api_client['api_key']})

        r2 = client.get('/api/v1/usage', headers={'X-API-Key': test_api_client['api_key']})
        used_after = r2.get_json()['calls_used']

        assert used_after > used_before


class TestAdminRoutes:
    def test_create_client(self, client):
        r = client.post(
            '/api/admin/b2b/clients',
            json={"name": "Test Org", "email": "org@test.com", "plan": "starter"}
        )
        assert r.status_code == 201
        data = r.get_json()
        assert 'api_key' in data
        assert data['api_key'].startswith('ttr_')
        assert data['plan'] == 'starter'

        db = SessionLocal()
        db.query(APIClient).filter_by(id=data['id']).delete()
        db.commit()
        db.close()

    def test_create_client_missing_fields(self, client):
        r = client.post('/api/admin/b2b/clients', json={"name": "No Email"})
        assert r.status_code == 400

    def test_create_client_invalid_plan(self, client):
        r = client.post(
            '/api/admin/b2b/clients',
            json={"name": "X", "email": "x@x.com", "plan": "invalid"}
        )
        assert r.status_code == 400

    def test_list_clients(self, client):
        r = client.get('/api/admin/b2b/clients')
        assert r.status_code == 200
        assert isinstance(r.get_json(), list)

    def test_update_client(self, client):
        create_r = client.post(
            '/api/admin/b2b/clients',
            json={"name": "Update Test", "email": "u@u.com", "plan": "starter"}
        )
        client_id = create_r.get_json()['id']

        r = client.patch(
            f'/api/admin/b2b/clients/{client_id}',
            json={"is_active": False, "tps_threshold": 55.0}
        )
        assert r.status_code == 200

        db = SessionLocal()
        db.query(APIClient).filter_by(id=client_id).delete()
        db.commit()
        db.close()

    def test_reset_key(self, client):
        create_r = client.post(
            '/api/admin/b2b/clients',
            json={"name": "Reset Test", "email": "reset@test.com", "plan": "starter"}
        )
        client_id = create_r.get_json()['id']
        old_key = create_r.get_json()['api_key']

        r = client.post(f'/api/admin/b2b/clients/{client_id}/reset-key')
        assert r.status_code == 200
        data = r.get_json()
        assert 'new_api_key' in data
        assert data['new_api_key'].startswith('ttr_')
        assert data['new_api_key'] != old_key

        db = SessionLocal()
        db.query(APIClient).filter_by(id=client_id).delete()
        db.commit()
        db.close()
