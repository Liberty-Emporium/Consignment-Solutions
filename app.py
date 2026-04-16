"""
Consignment Solutions — Multi-tenant SaaS
$49/month per store. Each store manages vendors, shelves, inventory,
sales, and settlements. Rent is auto-deducted from vendor sales.
"""

import os
import json
import sqlite3
import hashlib
import secrets
import requests
from datetime import datetime, date, timedelta
from flask import (Flask, render_template, request, redirect,
                   url_for, session, flash, jsonify, g)
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'consignment-solutions-secret-2024')

import secrets as _secrets_module

def _get_csrf_token():
    """Generate or retrieve CSRF token from session."""
    if 'csrf_token' not in session:
        session['csrf_token'] = _secrets_module.token_hex(32)
    return session['csrf_token']

def _validate_csrf():
    """Validate CSRF token on POST requests. Returns True if valid."""
    if request.method != 'POST':
        return True
    # Skip API routes
    if request.path.startswith('/api/'):
        return True
    token = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
    return token and token == session.get('csrf_token')

app.jinja_env.globals['csrf_token'] = _get_csrf_token


import time as _rl_time
from collections import defaultdict as _defaultdict

import bcrypt as _bcrypt_lib

def _sha256_hash(pw):
    import hashlib
    return hashlib.sha256(pw.encode()).hexdigest()

def _is_sha256_hash(h):
    return isinstance(h, str) and len(h) == 64 and all(c in '0123456789abcdef' for c in h.lower())

def _bcrypt_hash(pw):
    return _bcrypt_lib.hashpw(pw.encode('utf-8'), _bcrypt_lib.gensalt()).decode('utf-8')

def _bcrypt_verify(pw, stored):
    if _is_sha256_hash(stored):
        return _sha256_hash(pw) == stored, True  # valid, needs_upgrade
    try:
        return _bcrypt_lib.checkpw(pw.encode('utf-8'), stored.encode('utf-8')), False
    except Exception:
        return False, False

_rate_store = _defaultdict(list)

@app.after_request
def _add_security_headers(response):
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    if 'Content-Security-Policy' not in response.headers:
        response.headers['Content-Security-Policy'] = "default-src 'self' 'unsafe-inline' 'unsafe-eval' https: data: blob:;"
    return response

# ── OpenRouter AI (single provider) ──────────────────────────────────────────
def get_openrouter_key(user_id=None):
    """Get OpenRouter API key from config or env."""
    import os
    return get_config('openrouter_key', os.environ.get('OPENROUTER_API_KEY', ''))

def get_openrouter_model(user_id=None):
    """Get selected OpenRouter model from config."""
    return get_config('openrouter_model', 'google/gemini-flash-1.5')

def call_openrouter(messages, user_id=None, max_tokens=1000):
    """Call OpenRouter API with any model. Returns text string."""
    import urllib.request as _ur, json as _json
    key = get_openrouter_key(user_id)
    if not key:
        return "AI unavailable — add your OpenRouter API key in Settings ⚙️"
    model = get_openrouter_model(user_id)
    try:
        payload = _json.dumps({
            'model': model,
            'messages': messages,
            'max_tokens': max_tokens
        }).encode()
        req = _ur.Request(
            'https://openrouter.ai/api/v1/chat/completions',
            data=payload,
            headers={
                'Authorization': f'Bearer {key}',
                'Content-Type': 'application/json',
                'HTTP-Referer': 'https://libertyemporium.com',
                'X-Title': 'Liberty App'
            }
        )
        with _ur.urlopen(req, timeout=30) as resp:
            return _json.loads(resp.read())['choices'][0]['message']['content']
    except Exception as e:
        return f"AI error: {e}"
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/health', methods=['GET', 'HEAD'])
def _health_check():
    import json as _json
    try:
        db = get_db()
        db.execute('SELECT 1').fetchone()
        db_ok = 'ok'
    except Exception:
        db_ok = 'error'
    status = 'ok' if db_ok == 'ok' else 'degraded'
    return _json.dumps({'status': status, 'db': db_ok}), (200 if status == 'ok' else 503), {'Content-Type': 'application/json'}

@app.route('/ping')
def _ping():
    return 'ok', 200

DATA_DIR = os.environ.get('DATA_DIR', '/data')
os.makedirs(DATA_DIR, exist_ok=True)

DB_FILE = os.path.join(DATA_DIR, 'consignment_solutions.db')

PLAN_PRICE   = 49.00
TRIAL_DAYS   = 14
PLATFORM_NAME = 'Consignment Solutions'

# ─── Database ─────────────────────────────────────────────────────────────────

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_FILE)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA journal_mode=WAL')
        g.db.execute('PRAGMA foreign_keys=ON')
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db():
    db = sqlite3.connect(DB_FILE)
    db.executescript('''
        -- ── Platform level ──────────────────────────────────────────────

        -- Super admins (platform owners — Jay)
        CREATE TABLE IF NOT EXISTS super_admins (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            email       TEXT UNIQUE NOT NULL,
            password    TEXT NOT NULL,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Stores (each paying customer)
        CREATE TABLE IF NOT EXISTS stores (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT NOT NULL,
            email           TEXT UNIQUE NOT NULL,
            password        TEXT NOT NULL,
            phone           TEXT DEFAULT '',
            plan            TEXT DEFAULT 'trial',
            trial_ends      DATE,
            subscription_id TEXT DEFAULT '',
            stripe_customer TEXT DEFAULT '',
            status          TEXT DEFAULT 'trial',
            stripe_key      TEXT DEFAULT '',
            stripe_pub_key  TEXT DEFAULT '',
            square_token    TEXT DEFAULT '',
            square_location TEXT DEFAULT '',
            square_env      TEXT DEFAULT 'sandbox',
            square_webhook_sig TEXT DEFAULT '',
            groq_key        TEXT DEFAULT '',
            qwen_key        TEXT DEFAULT '',
            ai_provider     TEXT DEFAULT 'qwen',
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- ── Store level (all scoped by store_id) ─────────────────────────

        -- Vendor accounts inside each store
        CREATE TABLE IF NOT EXISTS vendors (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id    INTEGER NOT NULL,
            name        TEXT NOT NULL,
            email       TEXT NOT NULL,
            phone       TEXT DEFAULT '',
            password    TEXT NOT NULL,
            notes       TEXT DEFAULT '',
            status      TEXT DEFAULT 'active',
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (store_id) REFERENCES stores(id),
            UNIQUE(store_id, email)
        );

        -- Shelf spaces
        CREATE TABLE IF NOT EXISTS shelves (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id        INTEGER NOT NULL,
            shelf_number    TEXT NOT NULL,
            description     TEXT DEFAULT '',
            size            TEXT DEFAULT 'standard',
            monthly_rent    REAL NOT NULL DEFAULT 50.00,
            status          TEXT DEFAULT 'available',
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (store_id) REFERENCES stores(id),
            UNIQUE(store_id, shelf_number)
        );

        -- Shelf assignments
        CREATE TABLE IF NOT EXISTS vendor_shelves (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id    INTEGER NOT NULL,
            vendor_id   INTEGER NOT NULL,
            shelf_id    INTEGER NOT NULL,
            start_date  DATE NOT NULL,
            end_date    DATE,
            status      TEXT DEFAULT 'active',
            FOREIGN KEY (store_id)  REFERENCES stores(id),
            FOREIGN KEY (vendor_id) REFERENCES vendors(id),
            FOREIGN KEY (shelf_id)  REFERENCES shelves(id)
        );

        -- Vendor inventory items
        CREATE TABLE IF NOT EXISTS items (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id            INTEGER NOT NULL,
            vendor_id           INTEGER NOT NULL,
            shelf_id            INTEGER,
            name                TEXT NOT NULL,
            description         TEXT DEFAULT '',
            price               REAL NOT NULL,
            quantity            INTEGER DEFAULT 1,
            quantity_sold       INTEGER DEFAULT 0,
            category            TEXT DEFAULT '',
            sku                 TEXT DEFAULT '',
            square_item_id      TEXT DEFAULT '',
            square_variation_id TEXT DEFAULT '',
            status              TEXT DEFAULT 'active',
            created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (store_id)  REFERENCES stores(id),
            FOREIGN KEY (vendor_id) REFERENCES vendors(id)
        );

        -- Sales (from Square webhook or manual)
        CREATE TABLE IF NOT EXISTS sales (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id          INTEGER NOT NULL,
            vendor_id         INTEGER NOT NULL,
            item_id           INTEGER,
            item_name         TEXT NOT NULL,
            quantity          INTEGER DEFAULT 1,
            unit_price        REAL NOT NULL,
            total_amount      REAL NOT NULL,
            sale_date         DATE NOT NULL,
            period_month      TEXT NOT NULL,
            square_order_id   TEXT DEFAULT '',
            source            TEXT DEFAULT 'manual',
            created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (store_id)  REFERENCES stores(id),
            FOREIGN KEY (vendor_id) REFERENCES vendors(id)
        );

        -- Direct rent payments
        CREATE TABLE IF NOT EXISTS rent_payments (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id        INTEGER NOT NULL,
            vendor_id       INTEGER NOT NULL,
            shelf_id        INTEGER,
            amount          REAL NOT NULL,
            payment_date    DATE NOT NULL,
            period_month    TEXT NOT NULL,
            method          TEXT DEFAULT 'cash',
            notes           TEXT DEFAULT '',
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (store_id)  REFERENCES stores(id),
            FOREIGN KEY (vendor_id) REFERENCES vendors(id)
        );
    ''')

    # Default super admin
    sa = db.execute("SELECT id FROM super_admins LIMIT 1").fetchone()
    if not sa:
        pwd = hashlib.sha256('admin1'.encode()).hexdigest()
        db.execute("INSERT INTO super_admins (name,email,password) VALUES (?,?,?)",
                   ('Admin', 'admin', pwd))

    # Default test store
    ts = db.execute("SELECT id FROM stores WHERE email='admin' LIMIT 1").fetchone()
    if not ts:
        pwd = hashlib.sha256('admin1'.encode()).hexdigest()
        trial_end = (date.today() + timedelta(days=TRIAL_DAYS)).isoformat()
        store_id = db.execute(
            '''INSERT INTO stores (name,email,password,plan,status,trial_ends)
               VALUES (?,?,?,?,?,?)''',
            ('Demo Store', 'admin', pwd, 'trial', 'active', trial_end)
        ).lastrowid
        for i in range(1, 11):
            db.execute('INSERT INTO shelves (store_id,shelf_number,monthly_rent) VALUES (?,?,?)',
                       (store_id, f'S-{i:02d}', 50.00))

    db.commit()
    db.close()
    print("[STARTUP] Consignment Solutions DB initialized", flush=True)

init_db()

# ─── Helpers ──────────────────────────────────────────────────────────────────

def hash_pw(pw): return _bcrypt_hash(pw)

def current_month():
    return date.today().strftime('%Y-%m')

def store_active(store):
    """Check if a store's subscription is active."""
    if not store:
        return False
    status = store['status'] if isinstance(store, dict) else store['status']
    if status == 'active':
        return True
    if status == 'trial':
        trial_end = store['trial_ends']
        if trial_end and date.today().isoformat() <= trial_end:
            return True
    return False

def calc_settlement(store_id, vendor_id, period_month):
    db = get_db()
    total_sales = db.execute(
        "SELECT COALESCE(SUM(total_amount),0) FROM sales WHERE store_id=? AND vendor_id=? AND period_month=?",
        (store_id, vendor_id, period_month)).fetchone()[0]
    total_rent = db.execute("""
        SELECT COALESCE(SUM(sh.monthly_rent),0) FROM vendor_shelves vs
        JOIN shelves sh ON vs.shelf_id=sh.id
        WHERE vs.store_id=? AND vs.vendor_id=? AND vs.status='active'""",
        (store_id, vendor_id)).fetchone()[0]
    direct_paid = db.execute(
        "SELECT COALESCE(SUM(amount),0) FROM rent_payments WHERE store_id=? AND vendor_id=? AND period_month=?",
        (store_id, vendor_id, period_month)).fetchone()[0]
    balance = total_sales - total_rent + direct_paid
    return {
        'vendor_id': vendor_id, 'period_month': period_month,
        'total_sales': round(total_sales, 2),
        'total_rent': round(total_rent, 2),
        'direct_paid': round(direct_paid, 2),
        'balance': round(balance, 2)
    }

def get_ai_response(store_id, prompt):
    db = get_db()
    store = db.execute("SELECT * FROM stores WHERE id=?", (store_id,)).fetchone()
    if not store:
        return "Store not found."
    provider = store['ai_provider'] or 'qwen'
    providers = [provider] + [p for p in ['qwen','groq'] if p != provider]
    system = f"""You are the AI assistant for {store['name']} on Consignment Solutions.
You help store owners track vendor sales, shelf rent, inventory, and monthly settlements.
Be concise, data-focused, and helpful."""
    messages = [{"role":"system","content":system},{"role":"user","content":prompt}]
    for p in providers:
        key = store[f'{p}_key'] or ''
        if not key:
            continue
        try:
            if p == 'qwen':
                url = 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions'
                model = 'qwen-plus'
            else:
                url = 'https://api.groq.com/openai/v1/chat/completions'
                model = 'llama-3.3-70b-versatile'
            r = requests.post(url,
                headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"},
                json={"model":model,"messages":messages,"max_tokens":800}, timeout=30)
            if r.status_code == 200:
                return r.json()['choices'][0]['message']['content']
        except Exception as e:
            print(f"AI ({p}): {e}")
    return "AI unavailable. Add your API key in Settings → AI."

# ─── Auth decorators ──────────────────────────────────────────────────────────

def store_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'store_id' not in session:
            return redirect(url_for('store_login'))
        db = get_db()
        store = db.execute("SELECT * FROM stores WHERE id=?", (session['store_id'],)).fetchone()
        if not store_active(store):
            session.clear()
            flash('Your trial has ended. Please subscribe to continue.', 'warning')
            return redirect(url_for('pricing'))
        return f(*args, **kwargs)
    return decorated

def vendor_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'vendor_id' not in session:
            return redirect(url_for('vendor_login'))
        return f(*args, **kwargs)
    return decorated

def super_admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'super_admin_id' not in session:
            return redirect(url_for('super_admin_login'))
        return f(*args, **kwargs)
    return decorated

# ─── Public Routes ──────────────────────────────────────────────────────

@app.route('/')
def index():
    if 'store_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('landing.html')

@app.route('/pricing')
def pricing():
    return render_template('pricing.html')

@app.route('/signup', methods=['GET','POST'])
def signup():
    if request.method == 'POST':
        store_name = request.form.get('store_name','').strip()
        email      = request.form.get('email','').strip().lower()
        pw         = request.form.get('password','')
        phone      = request.form.get('phone','').strip()
        db         = get_db()
        if db.execute('SELECT id FROM stores WHERE email=?', (email,)).fetchone():
            flash('That email is already registered.', 'error')
            return redirect(url_for('signup'))
        trial_end = (date.today() + timedelta(days=TRIAL_DAYS)).isoformat()
        store_id = db.execute(
            '''INSERT INTO stores (name,email,password,phone,plan,status,trial_ends)
               VALUES (?,?,?,?,?,?,?)''',
            (store_name, email, hash_pw(pw), phone, 'trial', 'trial', trial_end)
        ).lastrowid
        # Seed 10 default shelves for the new store
        for i in range(1, 11):
            db.execute('INSERT INTO shelves (store_id,shelf_number,monthly_rent) VALUES (?,?,?)',
                       (store_id, f'S-{i:02d}', 50.00))
        db.commit()
        session['store_id']   = store_id
        session['store_name'] = store_name
        session['role']       = 'store_admin'
        flash(f'Welcome to Consignment Solutions! Your {TRIAL_DAYS}-day free trial has started.', 'success')
        return redirect(url_for('dashboard'))
    return render_template('signup.html')

@app.route('/store/login', methods=['GET','POST'])
def store_login():
    if request.method == 'POST':
        email = request.form.get('email','').strip().lower()
        pw    = request.form.get('password','')
        db    = get_db()
        store = db.execute('SELECT * FROM stores WHERE email=?', (email,)).fetchone()
        if store and store['password'] == hash_pw(pw):
            if not store_active(store):
                flash('Your trial has ended. Please subscribe to continue.', 'warning')
                return redirect(url_for('pricing'))
            session['store_id']   = store['id']
            session['store_name'] = store['name']
            session['role']       = 'store_admin'
            return redirect(url_for('dashboard'))
        flash('Invalid email or password.', 'error')
    return render_template('store_login.html')

@app.route('/store/logout')
def store_logout():
    session.clear()
    return redirect(url_for('index'))

# ─── Vendor Auth ─────────────────────────────────────────────────────────

@app.route('/vendor/login', methods=['GET','POST'])
def vendor_login():
    if request.method == 'POST':
        email = request.form.get('email','').strip().lower()
        pw    = request.form.get('password','')
        db    = get_db()
        vendor = db.execute('SELECT * FROM vendors WHERE email=?', (email,)).fetchone()
        if vendor and vendor['password'] == hash_pw(pw):
            store = db.execute('SELECT * FROM stores WHERE id=?', (vendor['store_id'],)).fetchone()
            if not store_active(store):
                flash('This store\'s subscription is inactive.', 'error')
                return redirect(url_for('vendor_login'))
            session['vendor_id']   = vendor['id']
            session['vendor_name'] = vendor['name']
            session['store_id']    = vendor['store_id']
            session['store_name']  = store['name']
            session['role']        = 'vendor'
            return redirect(url_for('vendor_dashboard'))
        flash('Invalid email or password.', 'error')
    return render_template('vendor_login.html')

@app.route('/vendor/logout')
def vendor_logout():
    session.clear()
    return redirect(url_for('vendor_login'))

# ─── Store Dashboard ─────────────────────────────────────────────────────

@app.route('/dashboard')
@store_login_required
def dashboard():
    db  = get_db()
    sid = session['store_id']
    mon = current_month()
    total_vendors  = db.execute("SELECT COUNT(*) FROM vendors WHERE store_id=? AND status='active'", (sid,)).fetchone()[0]
    total_shelves  = db.execute("SELECT COUNT(*) FROM shelves WHERE store_id=?", (sid,)).fetchone()[0]
    rented_shelves = db.execute("SELECT COUNT(*) FROM shelves WHERE store_id=? AND status='rented'", (sid,)).fetchone()[0]
    month_sales    = db.execute("SELECT COALESCE(SUM(total_amount),0) FROM sales WHERE store_id=? AND period_month=?", (sid, mon)).fetchone()[0]
    monthly_rent   = db.execute("""
        SELECT COALESCE(SUM(sh.monthly_rent),0) FROM vendor_shelves vs
        JOIN shelves sh ON vs.shelf_id=sh.id WHERE vs.store_id=? AND vs.status='active'""", (sid,)).fetchone()[0]
    total_items    = db.execute("SELECT COUNT(*) FROM items WHERE store_id=? AND status='active'", (sid,)).fetchone()[0]
    # Vendors behind on rent
    vendor_rows = db.execute("SELECT id,name FROM vendors WHERE store_id=? AND status='active'", (sid,)).fetchall()
    behind = []
    for v in vendor_rows:
        s = calc_settlement(sid, v['id'], mon)
        if s['balance'] < 0:
            behind.append({'name': v['name'], 'id': v['id'], **s})
    recent_sales = db.execute("""
        SELECT sa.*, v.name as vendor_name FROM sales sa
        JOIN vendors v ON sa.vendor_id=v.id
        WHERE sa.store_id=? ORDER BY sa.created_at DESC LIMIT 8""", (sid,)).fetchall()
    store = db.execute("SELECT * FROM stores WHERE id=?", (sid,)).fetchone()
    return render_template('dashboard.html',
        total_vendors=total_vendors, total_shelves=total_shelves,
        rented_shelves=rented_shelves, available=total_shelves-rented_shelves,
        month_sales=month_sales, monthly_rent=monthly_rent,
        total_items=total_items, behind=behind,
        recent_sales=recent_sales, month=mon, store=store)

# ─── Vendor Management ─────────────────────────────────────────────────

@app.route('/vendors')
@store_login_required
def vendors():
    db  = get_db()
    sid = session['store_id']
    mon = current_month()
    rows = db.execute("""
        SELECT v.*, COUNT(DISTINCT vs.shelf_id) as shelf_count,
               COALESCE(SUM(sh.monthly_rent),0) as monthly_rent
        FROM vendors v
        LEFT JOIN vendor_shelves vs ON v.id=vs.vendor_id AND vs.status='active'
        LEFT JOIN shelves sh ON vs.shelf_id=sh.id
        WHERE v.store_id=? AND v.status='active'
        GROUP BY v.id ORDER BY v.name""", (sid,)).fetchall()
    vendors_list = []
    for v in rows:
        s = calc_settlement(sid, v['id'], mon)
        vendors_list.append({**dict(v), 'settlement': s})
    return render_template('vendors.html', vendors=vendors_list, month=mon)

@app.route('/vendors/add', methods=['GET','POST'])
@store_login_required
def add_vendor():
    db  = get_db()
    sid = session['store_id']
    if request.method == 'POST':
        name  = request.form.get('name','').strip()
        email = request.form.get('email','').strip().lower()
        phone = request.form.get('phone','').strip()
        pw    = request.form.get('password', secrets.token_urlsafe(8))
        notes = request.form.get('notes','')
        if db.execute('SELECT id FROM vendors WHERE store_id=? AND email=?', (sid,email)).fetchone():
            flash('That email is already registered as a vendor.', 'error')
            return redirect(url_for('add_vendor'))
        db.execute('INSERT INTO vendors (store_id,name,email,phone,password,notes) VALUES (?,?,?,?,?,?)',
                   (sid, name, email, phone, hash_pw(pw), notes))
        db.commit()
        flash(f'Vendor {name} added! Login: {email} / {pw}', 'success')
        return redirect(url_for('vendors'))
    return render_template('add_vendor.html')

@app.route('/vendors/<int:vid>')
@store_login_required
def vendor_detail(vid):
    db  = get_db()
    sid = session['store_id']
    vendor = db.execute('SELECT * FROM vendors WHERE id=? AND store_id=?', (vid, sid)).fetchone()
    if not vendor:
        flash('Vendor not found.', 'error')
        return redirect(url_for('vendors'))
    shelves  = db.execute("""
        SELECT vs.*, sh.shelf_number, sh.monthly_rent, sh.size
        FROM vendor_shelves vs JOIN shelves sh ON vs.shelf_id=sh.id
        WHERE vs.vendor_id=? AND vs.store_id=?""", (vid, sid)).fetchall()
    items    = db.execute('SELECT * FROM items WHERE vendor_id=? AND store_id=? ORDER BY created_at DESC', (vid, sid)).fetchall()
    sales    = db.execute('SELECT * FROM sales WHERE vendor_id=? AND store_id=? ORDER BY sale_date DESC LIMIT 20', (vid, sid)).fetchall()
    payments = db.execute('SELECT * FROM rent_payments WHERE vendor_id=? AND store_id=? ORDER BY payment_date DESC', (vid, sid)).fetchall()
    mon = current_month()
    settlement = calc_settlement(sid, vid, mon)
    history = []
    y, m = int(mon[:4]), int(mon[5:])
    for _ in range(6):
        label = f"{y}-{m:02d}"
        history.append(calc_settlement(sid, vid, label))
        m -= 1
        if m == 0: m=12; y-=1
    return render_template('vendor_detail.html',
        vendor=vendor, shelves=shelves, items=items,
        sales=sales, payments=payments,
        settlement=settlement, history=history, month=mon)

# ─── Shelves ──────────────────────────────────────────────────────────────

@app.route('/shelves')
@store_login_required
def shelves():
    db  = get_db()
    sid = session['store_id']
    rows = db.execute("""
        SELECT sh.*, v.name as vendor_name, v.id as vendor_id
        FROM shelves sh
        LEFT JOIN vendor_shelves vs ON sh.id=vs.shelf_id AND vs.status='active'
        LEFT JOIN vendors v ON vs.vendor_id=v.id
        WHERE sh.store_id=? ORDER BY sh.shelf_number""", (sid,)).fetchall()
    return render_template('shelves.html', shelves=rows)

@app.route('/shelves/add', methods=['GET','POST'])
@store_login_required
def add_shelf():
    db  = get_db()
    sid = session['store_id']
    if request.method == 'POST':
        db.execute('INSERT INTO shelves (store_id,shelf_number,description,size,monthly_rent) VALUES (?,?,?,?,?)',
                   (sid, request.form['shelf_number'], request.form.get('description',''),
                    request.form.get('size','standard'), float(request.form['monthly_rent'])))
        db.commit()
        flash(f"Shelf {request.form['shelf_number']} added!", 'success')
        return redirect(url_for('shelves'))
    return render_template('add_shelf.html')

@app.route('/shelves/assign', methods=['GET','POST'])
@store_login_required
def assign_shelf():
    db  = get_db()
    sid = session['store_id']
    if request.method == 'POST':
        shelf_id  = int(request.form['shelf_id'])
        vendor_id = int(request.form['vendor_id'])
        start     = request.form.get('start_date', date.today().isoformat())
        db.execute('UPDATE shelves SET status=? WHERE id=? AND store_id=?', ('rented', shelf_id, sid))
        db.execute('INSERT INTO vendor_shelves (store_id,vendor_id,shelf_id,start_date) VALUES (?,?,?,?)',
                   (sid, vendor_id, shelf_id, start))
        db.commit()
        flash('Shelf assigned!', 'success')
        return redirect(url_for('shelves'))
    available = db.execute("SELECT * FROM shelves WHERE store_id=? AND status='available' ORDER BY shelf_number", (sid,)).fetchall()
    vendors   = db.execute("SELECT * FROM vendors WHERE store_id=? AND status='active' ORDER BY name", (sid,)).fetchall()
    return render_template('assign_shelf.html', available=available, vendors=vendors,
                           today=date.today().isoformat())

@app.route('/shelves/release/<int:vs_id>', methods=['POST'])
@store_login_required
def release_shelf(vs_id):
    db  = get_db()
    sid = session['store_id']
    vs  = db.execute('SELECT * FROM vendor_shelves WHERE id=? AND store_id=?', (vs_id, sid)).fetchone()
    if vs:
        db.execute('UPDATE vendor_shelves SET status=?,end_date=? WHERE id=?',
                   ('ended', date.today().isoformat(), vs_id))
        db.execute('UPDATE shelves SET status=? WHERE id=? AND store_id=?', ('available', vs['shelf_id'], sid))
        db.commit()
        flash('Shelf released.', 'success')
    return redirect(url_for('shelves'))

# ─── Items ──────────────────────────────────────────────────────────────

@app.route('/items')
@store_login_required
def items():
    db  = get_db()
    sid = session['store_id']
    rows = db.execute("""
        SELECT i.*, v.name as vendor_name, sh.shelf_number
        FROM items i JOIN vendors v ON i.vendor_id=v.id
        LEFT JOIN shelves sh ON i.shelf_id=sh.id
        WHERE i.store_id=? AND i.status='active'
        ORDER BY i.updated_at DESC""", (sid,)).fetchall()
    return render_template('items.html', items=rows)

@app.route('/items/add', methods=['GET','POST'])
@store_login_required
def add_item():
    db  = get_db()
    sid = session['store_id']
    if request.method == 'POST':
        vendor_id = int(request.form['vendor_id'])
        shelf_id  = request.form.get('shelf_id') or None
        db.execute("""
            INSERT INTO items (store_id,vendor_id,shelf_id,name,description,price,quantity,category,sku)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (sid, vendor_id, shelf_id, request.form['name'],
             request.form.get('description',''), float(request.form['price']),
             int(request.form.get('quantity',1)),
             request.form.get('category',''), request.form.get('sku','')))
        db.commit()
        flash('Item added!', 'success')
        return redirect(url_for('items'))
    vendors = db.execute("SELECT * FROM vendors WHERE store_id=? AND status='active' ORDER BY name", (sid,)).fetchall()
    shelves = db.execute("SELECT * FROM shelves WHERE store_id=? ORDER BY shelf_number", (sid,)).fetchall()
    return render_template('add_item.html', vendors=vendors, shelves=shelves)

@app.route('/items/edit/<int:iid>', methods=['GET','POST'])
@store_login_required
def edit_item(iid):
    db   = get_db()
    sid  = session['store_id']
    item = db.execute('SELECT * FROM items WHERE id=? AND store_id=?', (iid, sid)).fetchone()
    if not item:
        flash('Item not found.', 'error')
        return redirect(url_for('items'))
    if request.method == 'POST':
        db.execute("""
            UPDATE items SET name=?,description=?,price=?,quantity=?,
            category=?,sku=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (request.form['name'], request.form.get('description',''),
             float(request.form['price']), int(request.form.get('quantity',1)),
             request.form.get('category',''), request.form.get('sku',''), iid))
        db.commit()
        flash('Item updated!', 'success')
        return redirect(url_for('items'))
    return render_template('edit_item.html', item=item)

@app.route('/items/delete/<int:iid>', methods=['POST'])
@store_login_required
def delete_item(iid):
    db  = get_db()
    sid = session['store_id']
    db.execute("UPDATE items SET status='deleted' WHERE id=? AND store_id=?", (iid, sid))
    db.commit()
    flash('Item removed.', 'success')
    return redirect(url_for('items'))

# ─── Sales ──────────────────────────────────────────────────────────────

@app.route('/sales')
@store_login_required
def sales():
    db  = get_db()
    sid = session['store_id']
    mon = request.args.get('month', current_month())
    rows = db.execute("""
        SELECT sa.*, v.name as vendor_name FROM sales sa
        JOIN vendors v ON sa.vendor_id=v.id
        WHERE sa.store_id=? AND sa.period_month=?
        ORDER BY sa.sale_date DESC""", (sid, mon)).fetchall()
    total = sum(r['total_amount'] for r in rows)
    return render_template('sales.html', sales=rows, total=total, month=mon)

@app.route('/sales/add', methods=['GET','POST'])
@store_login_required
def add_sale():
    db  = get_db()
    sid = session['store_id']
    if request.method == 'POST':
        vendor_id  = int(request.form['vendor_id'])
        item_id    = request.form.get('item_id') or None
        item_name  = request.form.get('item_name','')
        qty        = int(request.form.get('quantity',1))
        unit_price = float(request.form['unit_price'])
        sale_date  = request.form.get('sale_date', date.today().isoformat())
        period     = sale_date[:7]
        total      = round(qty * unit_price, 2)
        if item_id:
            irow = db.execute('SELECT * FROM items WHERE id=? AND store_id=?', (item_id, sid)).fetchone()
            if irow:
                item_name = irow['name']
                db.execute("""
                    UPDATE items SET quantity_sold=quantity_sold+?,
                    quantity=MAX(0,quantity-?),updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (qty, qty, item_id))
        db.execute("""
            INSERT INTO sales (store_id,vendor_id,item_id,item_name,quantity,unit_price,
                               total_amount,sale_date,period_month,source)
            VALUES (?,?,?,?,?,?,?,?,?,'manual')""",
            (sid, vendor_id, item_id, item_name, qty, unit_price, total, sale_date, period))
        db.commit()
        flash(f'Sale of ${total:.2f} recorded!', 'success')
        return redirect(url_for('sales'))
    vendors = db.execute("SELECT * FROM vendors WHERE store_id=? AND status='active' ORDER BY name", (sid,)).fetchall()
    items   = db.execute("""
        SELECT i.*,v.name as vendor_name FROM items i
        JOIN vendors v ON i.vendor_id=v.id
        WHERE i.store_id=? AND i.status='active' ORDER BY v.name,i.name""", (sid,)).fetchall()
    return render_template('add_sale.html', vendors=vendors, items=items,
                           today=date.today().isoformat())

# ─── Settlements ──────────────────────────────────────────────────────────

@app.route('/settlements')
@store_login_required
def settlements():
    db  = get_db()
    sid = session['store_id']
    mon = request.args.get('month', current_month())
    vendor_rows = db.execute("SELECT id,name FROM vendors WHERE store_id=? AND status='active' ORDER BY name", (sid,)).fetchall()
    data = []
    for v in vendor_rows:
        s = calc_settlement(sid, v['id'], mon)
        if s['total_rent'] > 0 or s['total_sales'] > 0:
            data.append({'vendor': dict(v), **s})
    return render_template('settlements.html', data=data, month=mon)

@app.route('/settlements/payment', methods=['GET','POST'])
@store_login_required
def record_payment():
    db  = get_db()
    sid = session['store_id']
    if request.method == 'POST':
        db.execute("""
            INSERT INTO rent_payments
            (store_id,vendor_id,shelf_id,amount,payment_date,period_month,method,notes)
            VALUES (?,?,?,?,?,?,?,?)""",
            (sid, int(request.form['vendor_id']),
             request.form.get('shelf_id') or None,
             float(request.form['amount']),
             request.form.get('payment_date', date.today().isoformat()),
             request.form.get('period_month', current_month()),
             request.form.get('method','cash'),
             request.form.get('notes','')))
        db.commit()
        flash('Payment recorded!', 'success')
        return redirect(url_for('settlements'))
    vendors = db.execute("SELECT * FROM vendors WHERE store_id=? AND status='active' ORDER BY name", (sid,)).fetchall()
    shelves = db.execute("SELECT * FROM shelves WHERE store_id=? AND status='rented' ORDER BY shelf_number", (sid,)).fetchall()
    return render_template('record_payment.html', vendors=vendors, shelves=shelves,
                           today=date.today().isoformat(), this_month=current_month())

# ─── Square Webhook ─────────────────────────────────────────────────────

@app.route('/webhook/square/<int:store_id>', methods=['POST'])
def square_webhook(store_id):
    db    = get_db()
    store = db.execute('SELECT * FROM stores WHERE id=?', (store_id,)).fetchone()
    if not store:
        return jsonify({'error':'Store not found'}), 404
    data       = request.get_json(force=True)
    event_type = data.get('type','')
    if event_type in ('payment.completed','order.fulfillment.updated'):
        try:
            order      = data.get('data',{}).get('object',{}).get('order',{})
            line_items = order.get('line_items',[])
            order_id   = order.get('id','')
            sale_date  = date.today().isoformat()
            period     = sale_date[:7]
            for li in line_items:
                var_id     = li.get('catalog_object_id','')
                item_name  = li.get('name','Unknown')
                qty        = int(li.get('quantity',1))
                unit_price = int(li.get('base_price_money',{}).get('amount',0))/100
                total      = round(qty*unit_price,2)
                item_row   = db.execute(
                    'SELECT * FROM items WHERE square_variation_id=? AND store_id=?',
                    (var_id, store_id)).fetchone()
                vendor_id = item_row['vendor_id'] if item_row else None
                item_id   = item_row['id'] if item_row else None
                if item_row:
                    item_name = item_row['name']
                    db.execute("""
                        UPDATE items SET quantity_sold=quantity_sold+?,
                        quantity=MAX(0,quantity-?),updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                        (qty,qty,item_id))
                if vendor_id:
                    db.execute("""
                        INSERT INTO sales
                        (store_id,vendor_id,item_id,item_name,quantity,unit_price,
                         total_amount,sale_date,period_month,square_order_id,source)
                        VALUES (?,?,?,?,?,?,?,?,?,?,'square')""",
                        (store_id,vendor_id,item_id,item_name,qty,unit_price,
                         total,sale_date,period,order_id))
            db.commit()
        except Exception as e:
            print(f'[WEBHOOK] Error: {e}')
    return jsonify({'status':'ok'})

# ─── AI Chat ──────────────────────────────────────────────────────────────

@app.route('/ai')
@store_login_required
def ai_page():
    return render_template('ai.html')

@app.route('/api/ai/chat', methods=['POST'])
@store_login_required
def ai_chat():
    db  = get_db()
    sid = session['store_id']
    mon = current_month()
    msg = request.json.get('message','').strip()
    if not msg:
        return jsonify({'error':'No message'}), 400
    total_vendors  = db.execute("SELECT COUNT(*) FROM vendors WHERE store_id=? AND status='active'", (sid,)).fetchone()[0]
    rented_shelves = db.execute("SELECT COUNT(*) FROM shelves WHERE store_id=? AND status='rented'", (sid,)).fetchone()[0]
    month_sales    = db.execute("SELECT COALESCE(SUM(total_amount),0) FROM sales WHERE store_id=? AND period_month=?", (sid, mon)).fetchone()[0]
    month_rent     = db.execute("""
        SELECT COALESCE(SUM(sh.monthly_rent),0) FROM vendor_shelves vs
        JOIN shelves sh ON vs.shelf_id=sh.id WHERE vs.store_id=? AND vs.status='active'""", (sid,)).fetchone()[0]
    context = f"""Store: {session['store_name']} | Today: {date.today()} | Month: {mon}
- Active vendors: {total_vendors} | Rented shelves: {rented_shelves}
- Sales this month: ${month_sales:.2f} | Rent owed: ${month_rent:.2f}"""
    response = get_ai_response(sid, f"{context}\n\nQuestion: {msg}")
    return jsonify({'response': response})

# ─── Store Settings ───────────────────────────────────────────────────────

@app.route('/settings', methods=['GET','POST'])
@store_login_required
def settings():
    db  = get_db()
    sid = session['store_id']
    if request.method == 'POST':
        ft = request.form.get('form_type')
        if ft == 'ai':
            db.execute("""
                UPDATE stores SET groq_key=?,qwen_key=?,ai_provider=? WHERE id=?""",
                (request.form.get('groq_key','').strip(),
                 request.form.get('qwen_key','').strip(),
                 request.form.get('ai_provider','qwen'), sid))
            db.commit()
            flash('AI settings saved!', 'success')
        elif ft == 'square':
            db.execute("""
                UPDATE stores SET square_token=?,square_location=?,
                square_env=?,square_webhook_sig=? WHERE id=?""",
                (request.form.get('square_token','').strip(),
                 request.form.get('square_location','').strip(),
                 request.form.get('square_env','sandbox'),
                 request.form.get('square_webhook_sig','').strip(), sid))
            db.commit()
            flash('Square settings saved!', 'success')
        elif ft == 'store':
            db.execute('UPDATE stores SET name=?,phone=? WHERE id=?',
                       (request.form.get('store_name','').strip(),
                        request.form.get('phone','').strip(), sid))
            session['store_name'] = request.form.get('store_name','')
            db.commit()
            flash('Store info updated!', 'success')
        return redirect(url_for('settings'))
    store = db.execute('SELECT * FROM stores WHERE id=?', (sid,)).fetchone()
    # Mask sensitive fields
    masked = dict(store)
    for f in ['groq_key','qwen_key','square_token','square_webhook_sig']:
        if masked.get(f): masked[f] = masked[f][:8]+'...'
    webhook_url = f"{request.host_url}webhook/square/{sid}"
    return render_template('settings.html', store=masked, webhook_url=webhook_url,
        key_set=bool(get_openrouter_key()),
        current_key=get_openrouter_key(),
        current_model=get_openrouter_model())

# ─── Vendor Portal ─────────────────────────────────────────────────────────

@app.route('/vendor/dashboard')
@vendor_login_required
def vendor_dashboard():
    db  = get_db()
    vid = session['vendor_id']
    sid = session['store_id']
    mon = current_month()
    s       = calc_settlement(sid, vid, mon)
    shelves = db.execute("""
        SELECT vs.*, sh.shelf_number, sh.monthly_rent
        FROM vendor_shelves vs JOIN shelves sh ON vs.shelf_id=sh.id
        WHERE vs.vendor_id=? AND vs.store_id=? AND vs.status='active'""", (vid, sid)).fetchall()
    my_items = db.execute(
        "SELECT COUNT(*) FROM items WHERE vendor_id=? AND store_id=? AND status='active'", (vid, sid)).fetchone()[0]
    recent_sales = db.execute(
        'SELECT * FROM sales WHERE vendor_id=? AND store_id=? ORDER BY sale_date DESC LIMIT 8',
        (vid, sid)).fetchall()
    history = []
    y, m = int(mon[:4]), int(mon[5:])
    for _ in range(6):
        label = f"{y}-{m:02d}"
        history.append(calc_settlement(sid, vid, label))
        m -= 1
        if m == 0: m=12; y-=1
    return render_template('vendor_dashboard.html',
        settlement=s, shelves=shelves, my_items=my_items,
        recent_sales=recent_sales, history=history, month=mon)

@app.route('/vendor/items')
@vendor_login_required
def vendor_items():
    db  = get_db()
    vid = session['vendor_id']
    sid = session['store_id']
    rows = db.execute("""
        SELECT i.*, sh.shelf_number FROM items i
        LEFT JOIN shelves sh ON i.shelf_id=sh.id
        WHERE i.vendor_id=? AND i.store_id=? AND i.status='active'
        ORDER BY i.updated_at DESC""", (vid, sid)).fetchall()
    return render_template('vendor_items.html', items=rows)

@app.route('/vendor/items/add', methods=['GET','POST'])
@vendor_login_required
def vendor_add_item():
    db  = get_db()
    vid = session['vendor_id']
    sid = session['store_id']
    if request.method == 'POST':
        shelf_id = request.form.get('shelf_id') or None
        db.execute("""
            INSERT INTO items (store_id,vendor_id,shelf_id,name,description,price,quantity,category,sku)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (sid, vid, shelf_id, request.form['name'],
             request.form.get('description',''), float(request.form['price']),
             int(request.form.get('quantity',1)),
             request.form.get('category',''), request.form.get('sku','')))
        db.commit()
        flash('Item added!', 'success')
        return redirect(url_for('vendor_items'))
    shelves = db.execute("""
        SELECT sh.* FROM shelves sh JOIN vendor_shelves vs ON sh.id=vs.shelf_id
        WHERE vs.vendor_id=? AND vs.store_id=? AND vs.status='active'""", (vid, sid)).fetchall()
    return render_template('vendor_add_item.html', shelves=shelves)

# ─── Super Admin Panel ─────────────────────────────────────────────────────

@app.route('/admin/login', methods=['GET','POST'])
def super_admin_login():
    if request.method == 'POST':
        email = request.form.get('email','').strip().lower()
        pw    = request.form.get('password','')
        db    = get_db()
        sa    = db.execute('SELECT * FROM super_admins WHERE email=?', (email,)).fetchone()
        if sa and sa['password'] == hash_pw(pw):
            session['super_admin_id']   = sa['id']
            session['super_admin_name'] = sa['name']
            return redirect(url_for('super_admin_dashboard'))
        flash('Invalid credentials.', 'error')
    return render_template('admin_login.html')

@app.route('/admin/logout')
def super_admin_logout():
    session.pop('super_admin_id', None)
    session.pop('super_admin_name', None)
    return redirect(url_for('super_admin_login'))

@app.route('/admin')
@super_admin_required
def super_admin_dashboard():
    db = get_db()
    stores      = db.execute('SELECT * FROM stores ORDER BY created_at DESC').fetchall()
    total       = len(stores)
    active      = sum(1 for s in stores if s['status']=='active')
    trial       = sum(1 for s in stores if s['status']=='trial')
    inactive    = sum(1 for s in stores if s['status'] not in ('active','trial'))
    mrr         = active * PLAN_PRICE
    store_data  = []
    for s in stores:
        vendor_count = db.execute('SELECT COUNT(*) FROM vendors WHERE store_id=?', (s['id'],)).fetchone()[0]
        store_data.append({**dict(s), 'vendor_count': vendor_count})
    return render_template('admin_dashboard.html',
        stores=store_data, total=total, active=active,
        trial=trial, inactive=inactive, mrr=mrr)

@app.route('/admin/store/<int:store_id>/activate', methods=['POST'])
@super_admin_required
def admin_activate_store(store_id):
    db = get_db()
    db.execute("UPDATE stores SET status='active',plan='paid' WHERE id=?", (store_id,))
    db.commit()
    flash('Store activated!', 'success')
    return redirect(url_for('super_admin_dashboard'))

@app.route('/admin/store/<int:store_id>/suspend', methods=['POST'])
@super_admin_required
def admin_suspend_store(store_id):
    db = get_db()
    db.execute("UPDATE stores SET status='suspended' WHERE id=?", (store_id,))
    db.commit()
    flash('Store suspended.', 'success')
    return redirect(url_for('super_admin_dashboard'))

@app.route('/healthz')
def healthz(): return 'ok'


# ── Admin-only API token UI routes ───────────────────────────────────────────
@app.route('/api/token/ui', methods=['POST'])
def api_token_ui_generate():
    if not session.get('super_admin_id'):
        return jsonify({'error': 'Admin only'}), 403
    import secrets as _s, hashlib as _h, datetime as _dt
    user_id = session.get('user_id') or session.get('super_admin_id') or 1
    label = 'ui-generated'
    raw_token = _s.token_urlsafe(48)
    token_hash = _h.sha256(raw_token.encode()).hexdigest()
    expires_at = (_dt.datetime.utcnow() + _dt.timedelta(days=365)).isoformat()
    conn = get_db()
    try:
        conn.execute('CREATE TABLE IF NOT EXISTS api_tokens (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, token_hash TEXT UNIQUE, label TEXT, expires_at TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)')
        conn.execute('DELETE FROM api_tokens WHERE user_id=? AND label=?', (user_id, label))
        conn.execute('INSERT INTO api_tokens (user_id,token_hash,label,expires_at) VALUES (?,?,?,?)', (user_id, token_hash, label, expires_at))
        conn.commit()
    finally:
        conn.close()
    return jsonify({'success':True,'api_token':raw_token,'expires_at':expires_at})

@app.route('/api/token/ui', methods=['DELETE'])
def api_token_ui_revoke():
    if not session.get('super_admin_id'):
        return jsonify({'error': 'Admin only'}), 403
    user_id = session.get('user_id') or session.get('super_admin_id') or 1
    conn = get_db()
    try:
        conn.execute('DELETE FROM api_tokens WHERE user_id=? AND label=?', (user_id, 'ui-generated'))
        conn.commit()
    finally:
        conn.close()
    return jsonify({'success':True})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)

# ── Forgot / Reset Password (email-based) ─────────────────────────────────────

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    import secrets as _sec, datetime as _dt, json as _json
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        db = get_db()
        # Check stores table
        store = db.execute('SELECT * FROM stores WHERE email=?', (email,)).fetchone()
        if store:
            token = _sec.token_urlsafe(24)
            resets_path = os.path.join(DATA_DIR, 'password_resets.json')
            resets = []
            try:
                if os.path.exists(resets_path):
                    with open(resets_path) as f: resets = _json.load(f)
            except: pass
            resets = [r for r in resets if r.get('email') != email]
            resets.append({
                'email': email, 'token': token, 'type': 'store',
                'expires': (_dt.datetime.now() + _dt.timedelta(hours=2)).isoformat()
            })
            with open(resets_path, 'w') as f: _json.dump(resets, f, indent=2)
            reset_url = request.host_url.rstrip('/') + f'/reset-password/{token}'
            send_email(
                to=email,
                subject='Reset Your Password',
                body=(
                    "Hi,\n\n"
                    "A password reset was requested for your account.\n\n"
                    "Click this link to set a new password (valid for 2 hours):\n"
                    + reset_url +
                    "\n\nIf you didn't request this, you can safely ignore this email.\n\n"
                    "-- Support"
                )
            )
            flash('If that email is registered, a reset link has been sent.', 'info')
        else:
            flash('If that email is registered, a reset token has been generated.', 'info')
        return redirect(url_for('forgot_password'))
    return render_template('forgot_password.html')

@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    import json as _json, datetime as _dt
    resets_path = os.path.join(DATA_DIR, 'password_resets.json')
    resets = []
    try:
        if os.path.exists(resets_path):
            with open(resets_path) as f: resets = _json.load(f)
    except: pass
    reset = next((r for r in resets if r.get('token') == token), None)
    if not reset:
        flash('Invalid or expired reset link.', 'error')
        return redirect(url_for('store_login'))
    if _dt.datetime.fromisoformat(reset['expires']) < _dt.datetime.now():
        flash('Reset link has expired. Request a new one.', 'error')
        return redirect(url_for('forgot_password'))
    if request.method == 'POST':
        new_pw = request.form.get('password', '').strip()
        if len(new_pw) < 6:
            flash('Password must be at least 6 characters.', 'error')
            return render_template('reset_password.html', token=token, email=reset.get('email',''))
        db = get_db()
        db.execute('UPDATE stores SET password=? WHERE email=?', (hash_pw(new_pw), reset['email']))
        db.commit()
        resets = [r for r in resets if r.get('token') != token]
        with open(resets_path, 'w') as f: _json.dump(resets, f, indent=2)
        flash('Password updated! You can now sign in.', 'success')
        return redirect(url_for('store_login'))
    return render_template('reset_password.html', token=token, email=reset.get('email',''))