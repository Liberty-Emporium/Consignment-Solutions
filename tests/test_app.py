"""
Tests for Consignment Solutions

Fixed 2026-04-17: @login_required was used at line ~1396 but never defined.
Fix applied: `login_required = super_admin_required` alias added in app.py.
conftest.py still in place for safety.
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ.setdefault('SECRET_KEY', 'test-secret-key')
os.environ.setdefault('DATABASE_URL', '')

import app as cs


@pytest.fixture
def client(tmp_path):
    cs.app.config['TESTING'] = True
    cs.app.config['SECRET_KEY'] = 'test-secret-key'
    cs.DATA_DIR = str(tmp_path)
    cs.DB_PATH = str(tmp_path / 'test.db')
    with cs.app.test_client() as c:
        with cs.app.app_context():
            cs.init_db()
        yield c


# ── Fix verification ──────────────────────────────────────────────────────────

def test_login_required_is_now_defined():
    """
    Verifies fix for: @login_required was undefined (NameError on import).
    Fixed 2026-04-17: aliased to super_admin_required in app.py.
    """
    assert hasattr(cs, 'login_required'), "login_required must be defined"
    assert callable(cs.login_required), "login_required must be callable"


# ── Health ────────────────────────────────────────────────────────────────────

def test_health_returns_json_ok(client):
    res = client.get('/health')
    assert res.status_code == 200
    data = res.get_json()
    assert data['status'] == 'ok'

def test_healthz_returns_ok(client):
    assert client.get('/healthz').status_code == 200

def test_ping_returns_pong(client):
    assert client.get('/ping').status_code == 200


# ── Public pages ──────────────────────────────────────────────────────────────

def test_index_returns_200(client):
    assert client.get('/').status_code == 200

def test_pricing_returns_200(client):
    assert client.get('/pricing').status_code == 200

def test_signup_page_returns_200(client):
    assert client.get('/signup').status_code == 200

def test_store_login_page_returns_200(client):
    assert client.get('/store/login').status_code == 200

def test_vendor_login_page_returns_200(client):
    assert client.get('/vendor/login').status_code == 200


# ── Auth — protected routes ───────────────────────────────────────────────────

def test_dashboard_requires_store_login(client):
    res = client.get('/dashboard', follow_redirects=False)
    assert res.status_code in (302, 401)

def test_vendors_requires_login(client):
    res = client.get('/vendors', follow_redirects=False)
    assert res.status_code in (302, 401)

def test_items_requires_login(client):
    res = client.get('/items', follow_redirects=False)
    assert res.status_code in (302, 401)

def test_sales_requires_login(client):
    res = client.get('/sales', follow_redirects=False)
    assert res.status_code in (302, 401)

def test_settlements_requires_login(client):
    res = client.get('/settlements', follow_redirects=False)
    assert res.status_code in (302, 401)

def test_vendor_dashboard_requires_login(client):
    res = client.get('/vendor/dashboard', follow_redirects=False)
    assert res.status_code in (302, 401)


# ── Store login flow ──────────────────────────────────────────────────────────

def test_store_login_wrong_credentials(client):
    res = client.post('/store/login', data={
        'email': 'nobody@test.com',
        'password': 'wrongpass'
    }, follow_redirects=True)
    assert res.status_code == 200
    assert (b'invalid' in res.data.lower() or
            b'incorrect' in res.data.lower() or
            b'wrong' in res.data.lower())


# ── Utility functions ─────────────────────────────────────────────────────────

def test_validate_slug_valid():
    result = cs._validate_slug('my-store')
    assert isinstance(result, str) and result

def test_validate_slug_rejects_empty():
    with pytest.raises((ValueError, Exception)):
        cs._validate_slug('')

def test_current_month_returns_yyyy_mm_format():
    result = cs.current_month()
    assert isinstance(result, str)
    assert len(result) == 7
    assert result[4] == '-'


# ── Password hashing ──────────────────────────────────────────────────────────

def test_bcrypt_hash_and_verify():
    # _bcrypt_verify returns (is_valid: bool, needs_upgrade: bool)
    h = cs._bcrypt_hash('mypassword')
    valid, needs_upgrade = cs._bcrypt_verify('mypassword', h)
    assert valid is True
    assert needs_upgrade is False

def test_bcrypt_verify_wrong_password():
    h = cs._bcrypt_hash('mypassword')
    valid, _ = cs._bcrypt_verify('wrongpassword', h)
    assert valid is False

def test_sha256_hash_produces_64_char_hex():
    h = cs._sha256_hash('test')
    assert len(h) == 64
    assert all(c in '0123456789abcdef' for c in h)


# ── Security headers ──────────────────────────────────────────────────────────

def test_security_headers_on_public_route(client):
    res = client.get('/')
    assert 'X-Content-Type-Options' in res.headers
    assert 'X-Frame-Options' in res.headers
