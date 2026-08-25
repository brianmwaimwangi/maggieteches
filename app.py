"""
Full Responsive E-Commerce Website with Admin Panel
- Multiple images per product (up to 10)
- Promotional pricing (original price crossed out)
- Customizable site logo and name
Single Python File (Flask + Bootstrap 5 + SQLite)
Run this file and visit http://localhost:5000
Admin credentials are bootstrapped from ADMIN_USERNAME / ADMIN_INITIAL_PASSWORD environment variables.
Never ship or rely on default admin credentials in production.
"""
import os
import sqlite3
import re

try:
    import psycopg2
    from psycopg2.extras import DictCursor
except ImportError:
    psycopg2 = None
    DictCursor = None
import uuid
from flask import Flask, render_template, request, redirect, url_for, session, flash, g, send_from_directory, abort
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask_wtf import CSRFProtect
from PIL import Image, ImageOps
import cloudinary
import cloudinary.uploader
import cloudinary.utils

# ---------- APP CONFIG ----------
app = Flask(__name__)

def _load_secret_key():
    # Prefer an environment variable, then fall back to a Render Secret File.
    value = os.environ.get('SECRET_KEY', '').strip()
    if value:
        return value
    secret_file = os.environ.get('SECRET_KEY_FILE', '/etc/secrets/SECRET_KEY')
    try:
        return Path(secret_file).read_text(encoding='utf-8').strip()
    except (OSError, UnicodeError):
        return ''

app.secret_key = _load_secret_key()
if not app.secret_key:
    if os.environ.get('RENDER'):
        raise RuntimeError('SECRET_KEY must be provided as a Render environment variable or Secret File at /etc/secrets/SECRET_KEY')
    app.secret_key = 'local-development-only-change-me'

# Harden Flask session cookies for production.
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=bool(os.environ.get('RENDER')),
)

ADMIN_USERNAME_ENV = os.environ.get('ADMIN_USERNAME', '').strip()
ADMIN_INITIAL_PASSWORD_ENV = os.environ.get('ADMIN_INITIAL_PASSWORD', '')

# ---------- CSRF PROTECTION ----------
# Guards every POST/PUT/PATCH/DELETE request against cross-site request
# forgery by requiring a per-session token that must be present in the
# submitted form (as csrf_token()) or an X-CSRFToken header for any AJAX
# calls added later. GET requests are never state-changing in this app -
# every route that deletes/updates data has been made POST-only so it's
# actually covered by this.
csrf = CSRFProtect(app)
# Render's filesystem is ephemeral. If DB_PATH is set to a mounted persistent
# disk (for example /data/ecommerce.db), the database survives redeploys.
# Locally, it continues to use ecommerce.db in the project directory.
DATABASE_URL = os.environ.get('DATABASE_URL', '').strip()
DATABASE = os.environ.get('DB_PATH', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ecommerce.db'))
USE_POSTGRES = bool(DATABASE_URL)
if USE_POSTGRES and psycopg2 is None:
    raise RuntimeError('DATABASE_URL is set, but psycopg2-binary is not installed.')
UPLOAD_FOLDER = 'static/uploads'
HERO_IMAGE_FOLDER = 'static/hero'
BRAND_FOLDER = 'static/brand'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'}

# Create upload directories if they don't exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(HERO_IMAGE_FOLDER, exist_ok=True)
os.makedirs(BRAND_FOLDER, exist_ok=True)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['HERO_IMAGE_FOLDER'] = HERO_IMAGE_FOLDER
app.config['BRAND_FOLDER'] = BRAND_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB
MAX_HERO_IMAGES = 20

# ---------- CLOUDINARY CONFIG (permanent cloud image storage) ----------
# Every upload is still saved to local disk first (fast, no network round
# trip needed to render a page). The problem local-only storage has is that
# many hosts (Render, Railway, Heroku, etc.) use an ephemeral filesystem -
# it gets wiped on every restart/redeploy, silently deleting every product,
# hero and logo image that was ever uploaded.
#
# Configuring Cloudinary below backs each upload up to permanent cloud
# storage at upload time. If a local file ever goes missing, the /uploads,
# /hero and /brand routes automatically fall back to serving the Cloudinary
# copy instead of 404ing - so images survive redeploys even though nothing
# else about how the site works has to change.
#
# Get free credentials at https://cloudinary.com/users/register/free (no
# credit card required). Two ways to provide them:
#   1. Environment variables (recommended - keeps secrets out of the
#      database). These always win if set:
#        export CLOUDINARY_CLOUD_NAME=your_cloud_name
#        export CLOUDINARY_API_KEY=your_api_key
#        export CLOUDINARY_API_SECRET=your_api_secret
#   2. The Settings tab in the admin panel (Image Cloud Backup card), which
#      saves them to the database and applies them immediately - no
#      restart needed. Only used when the environment variables above
#      aren't set.
CLOUDINARY_CLOUD_NAME_ENV = os.environ.get('CLOUDINARY_CLOUD_NAME', '')
CLOUDINARY_API_KEY_ENV = os.environ.get('CLOUDINARY_API_KEY', '')
CLOUDINARY_API_SECRET_ENV = os.environ.get('CLOUDINARY_API_SECRET', '')
CLOUDINARY_ENV_LOCKED = bool(CLOUDINARY_CLOUD_NAME_ENV and CLOUDINARY_API_KEY_ENV and CLOUDINARY_API_SECRET_ENV)

CLOUDINARY_ENABLED = False  # kept in sync by refresh_cloudinary_config() below


def refresh_cloudinary_config(db=None):
    """(Re)configure the Cloudinary SDK from whichever source is active.
    Environment variables always win when present; otherwise falls back to
    the values saved from the admin panel's Settings tab. Safe to call
    repeatedly - e.g. once at startup and again right after the admin
    saves new keys, so changes apply without restarting the server."""
    global CLOUDINARY_ENABLED
    if CLOUDINARY_ENV_LOCKED:
        cloud_name, api_key, api_secret = CLOUDINARY_CLOUD_NAME_ENV, CLOUDINARY_API_KEY_ENV, CLOUDINARY_API_SECRET_ENV
    else:
        settings = get_site_settings(db) if db is not None else {}
        cloud_name = settings.get('cloudinary_cloud_name', '')
        api_key = settings.get('cloudinary_api_key', '')
        api_secret = settings.get('cloudinary_api_secret', '')

    CLOUDINARY_ENABLED = bool(cloud_name and api_key and api_secret)
    if CLOUDINARY_ENABLED:
        cloudinary.config(cloud_name=cloud_name, api_key=api_key, api_secret=api_secret, secure=True)
    return CLOUDINARY_ENABLED


if CLOUDINARY_ENV_LOCKED:
    refresh_cloudinary_config()
else:
    print("[Cloudinary] Not configured via environment variables - checking the database "
          "on each request instead. Uploads stay local-only until keys are set (env vars, "
          "or the Image Cloud Backup card in Admin > Settings).")

# ---------- SITE SETTINGS (editable from the admin panel) ----------
# Single source of truth for every "settings" key + its default value.
# Anything in here is auto-created on first run (init_db) and auto-injected
# into every template as `site_settings` (see inject_site_settings below),
# so new pages never need their own settings query.
SETTINGS_DEFAULTS = {
    'hero_image': 'default_hero.jpg',
    'site_name': 'MaggieCity',
    'site_logo': 'maggiecity_logo.jpg',
    'site_tagline': 'Tools and Electronics — powering your projects with reliable performance.',
    'hero_heading': 'Powering Your',
    'hero_highlight': 'Projects',
    'hero_subtitle': 'Your one-stop shop for tools, generators, solar power and electronics — built for reliable performance on every job.',
    'hero_cta_text': 'Explore Now',
    'features_title': 'Why Shop With Us',
    'feature1_title': 'Secure M-Pesa Payments',
    'feature1_text': 'Pay safely with Lipa na M-Pesa — Buy Goods or Paybill.',
    'feature2_title': 'Fast Delivery',
    'feature2_text': 'Quick, reliable delivery to your doorstep, wherever you are.',
    'feature3_title': 'Quality Guaranteed',
    'feature3_text': 'Every product is checked for quality before it ships.',
    # M-Pesa payment settings
    'payment_method': 'till',  # 'till' (Buy Goods) or 'paybill'
    'mpesa_till_number': '',
    'mpesa_paybill_number': '',
    'mpesa_account_number': '',
    'mpesa_instructions': 'Go to M-Pesa > Lipa na M-Pesa, complete the payment, then enter the '
                           'M-Pesa confirmation code below so we can match it to your order.',
    # Cloudinary backup id for the current logo (see upload_to_cloudinary).
    'site_logo_cloud_id': '',
    # Shop location pin, set from the admin panel (device geolocation or
    # typed in by hand) and shown on the homepage map.
    'store_lat': '',
    'store_lng': '',
    # Cloudinary credentials, only used when the CLOUDINARY_* environment
    # variables aren't set (see refresh_cloudinary_config). Editable from
    # the Image Cloud Backup card in Admin > Settings.
    'cloudinary_cloud_name': '',
    'cloudinary_api_key': '',
    'cloudinary_api_secret': '',
}


def get_site_settings(db):
    """Return every settings key with its current value, falling back to
    SETTINGS_DEFAULTS for anything missing/empty in the database."""
    rows = db.execute('SELECT key, value FROM settings').fetchall()
    settings = dict(SETTINGS_DEFAULTS)
    for r in rows:
        if r['value'] is not None and r['value'] != '':
            settings[r['key']] = r['value']
    return settings


@app.context_processor
def inject_site_settings():
    """Makes `site_settings` (a dict) available in every template without
    every single route having to fetch it manually."""
    return dict(site_settings=get_site_settings(get_db()))


@app.context_processor
def inject_admin_notifications():
    """Unread admin-message badge count, only computed for admins."""
    if session.get('is_admin'):
        db = get_db()
        count = db.execute('SELECT COUNT(*) FROM admin_messages WHERE is_read = 0').fetchone()[0]
        return dict(unread_admin_messages=count)
    return dict(unread_admin_messages=0)

# ---------- RESPONSIVE IMAGE HANDLING ----------
# Every raster upload is re-encoded into a small set of pre-resized files so
# templates can pick the right size instead of shipping the original upload
# (which might be a multi-megabyte camera photo) to every page that shows it.
IMAGE_SIZES = {
    'thumb': 320,   # product cards, list thumbnails
    'medium': 800,  # product detail main image, hero on small screens
    'large': 1600,  # product detail zoom / large hero, hard cap on any upload
}
JPEG_QUALITY = 82
WEBP_QUALITY = 82


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def _is_vector(filename):
    return filename.rsplit('.', 1)[1].lower() == 'svg'


def size_variant_name(filename, size):
    """Given 'p3_ab12cd34_photo.jpg' and 'thumb', return 'p3_ab12cd34_photo_thumb.jpg'.
    SVGs are returned unchanged since they're already resolution independent."""
    if not filename or _is_vector(filename):
        return filename
    base, ext = os.path.splitext(filename)
    return f"{base}_{size}{ext}"


def upload_to_cloudinary(local_path, unique_filename):
    """Back a locally-saved upload up to Cloudinary so it survives even if
    the local disk is later wiped. Returns the Cloudinary public_id to store
    in the DB alongside the local filename, or None if Cloudinary isn't
    configured or the upload failed - in which case the site just keeps
    working off the local copy only, exactly as before."""
    if not CLOUDINARY_ENABLED:
        return None
    try:
        public_id = f"maggiecity/{os.path.splitext(os.path.basename(unique_filename))[0]}"
        result = cloudinary.uploader.upload(local_path, public_id=public_id,
                                             overwrite=True, resource_type='image')
        return result.get('public_id')
    except Exception as e:
        # Cloud backup is best-effort - never let a Cloudinary hiccup block
        # the upload the admin is actually waiting on.
        print(f"[Cloudinary] Upload skipped for {unique_filename}: {e}")
        return None


def cloud_variant_url(public_id, size_key):
    """Build an on-the-fly-resized Cloudinary URL for a stored image. Used
    as the fallback source when the matching local file can't be found."""
    width = IMAGE_SIZES.get(size_key, IMAGE_SIZES['large'])
    url, _ = cloudinary.utils.cloudinary_url(
        public_id, width=width, crop='limit', quality='auto', fetch_format='auto', secure=True)
    return url


def strip_size_suffix(filename):
    """'p3_uuid_name_thumb.jpg' -> ('p3_uuid_name.jpg', 'thumb'). Returns
    (filename, 'large') when no known size suffix is present, matching how
    size_variant_name() names the original/large file (no suffix)."""
    base, ext = os.path.splitext(filename)
    for size_key in ('thumb', 'medium'):
        suffix = f'_{size_key}'
        if base.endswith(suffix):
            return f"{base[:-len(suffix)]}{ext}", size_key
    return filename, 'large'


def cloud_fallback_url(table, filename):
    """Look up the Cloudinary URL to serve a requested image from when the
    locally-requested file can't be found on disk anymore (e.g. wiped by a
    redeploy on an ephemeral filesystem). `table` is always one of the two
    fixed internal literals below - never user input."""
    if not CLOUDINARY_ENABLED:
        return None
    base_filename, size_key = strip_size_suffix(filename)
    row = get_db().execute(f'SELECT cloud_id FROM {table} WHERE filename = ?', (base_filename,)).fetchone()
    if row and row['cloud_id']:
        return cloud_variant_url(row['cloud_id'], size_key)
    return None


def process_and_save_image(file_storage, folder, unique_filename):
    """Save an uploaded image, producing responsive resized variants alongside
    the original so pages never have to ship a full-resolution upload just to
    render a 300px thumbnail. Non-raster files (SVG) are saved as-is.
    Returns the Cloudinary public_id for this upload (see
    upload_to_cloudinary), or None if Cloudinary backup isn't configured."""
    dest_path = os.path.join(folder, unique_filename)

    if _is_vector(unique_filename):
        file_storage.save(dest_path)
        return upload_to_cloudinary(dest_path, unique_filename)

    file_storage.save(dest_path)
    try:
        with Image.open(dest_path) as img:
            # Respect EXIF orientation so phone photos don't end up sideways.
            img = ImageOps.exif_transpose(img)
            if img.mode in ('RGBA', 'LA', 'P'):
                img = img.convert('RGBA') if 'A' in img.mode or img.mode == 'P' else img.convert('RGB')

            base, ext = os.path.splitext(dest_path)
            ext_lower = ext.lower()
            save_kwargs = {}
            fmt = None
            if ext_lower in ('.jpg', '.jpeg'):
                if img.mode == 'RGBA':
                    img = img.convert('RGB')
                fmt = 'JPEG'
                save_kwargs = {'quality': JPEG_QUALITY, 'optimize': True}
            elif ext_lower == '.png':
                fmt = 'PNG'
                save_kwargs = {'optimize': True}
            elif ext_lower == '.webp':
                fmt = 'WEBP'
                save_kwargs = {'quality': WEBP_QUALITY}
            elif ext_lower == '.gif':
                fmt = 'GIF'

            orig_w, orig_h = img.size

            # Cap the "original" itself so a 6000px camera photo never gets
            # served in full just because someone uploaded it that way.
            cap = IMAGE_SIZES['large']
            if orig_w > cap:
                ratio = cap / float(orig_w)
                capped = img.resize((cap, max(1, int(orig_h * ratio))), Image.LANCZOS)
                capped.save(dest_path, format=fmt, **save_kwargs) if fmt else capped.save(dest_path)
                img = capped
                orig_w, orig_h = img.size

            # Generate the smaller responsive variants.
            for size_key, target_w in IMAGE_SIZES.items():
                if size_key == 'large':
                    continue  # 'large' just is the (already capped) original
                variant_path = os.path.join(folder, size_variant_name(unique_filename, size_key))
                if orig_w <= target_w:
                    img.save(variant_path, format=fmt, **save_kwargs) if fmt else img.save(variant_path)
                    continue
                ratio = target_w / float(orig_w)
                resized = img.resize((target_w, max(1, int(orig_h * ratio))), Image.LANCZOS)
                if fmt:
                    resized.save(variant_path, format=fmt, **save_kwargs)
                else:
                    resized.save(variant_path)
    except Exception as e:
        # If Pillow can't process it for any reason, fall back to the raw
        # upload rather than losing the file entirely.
        print(f"Image processing skipped for {unique_filename}: {e}")

    # Back the (possibly capped) original up to Cloudinary. Thumb/medium
    # variants aren't uploaded separately - cloud_variant_url() re-derives
    # them on the fly from this same public_id when a fallback is needed.
    return upload_to_cloudinary(dest_path, unique_filename)


def delete_image_variants(folder, filename):
    if not filename:
        return
    paths = [os.path.join(folder, filename)]
    if not _is_vector(filename):
        paths += [os.path.join(folder, size_variant_name(filename, s)) for s in IMAGE_SIZES if s != 'large']
    for p in paths:
        try:
            os.remove(p)
        except Exception:
            pass


@app.context_processor
def inject_image_helpers():
    return dict(thumb_name=lambda f: size_variant_name(f, 'thumb'),
                medium_name=lambda f: size_variant_name(f, 'medium'))


@app.before_request
def sync_cloudinary_config():
    """Keep the Cloudinary SDK in sync with whatever's saved in Settings.
    No-op (and no DB hit) once environment variables are locked in, since
    those never change without a restart anyway."""
    if not CLOUDINARY_ENV_LOCKED:
        refresh_cloudinary_config(get_db())


@app.context_processor
def inject_cloudinary_status():
    return dict(cloudinary_enabled=CLOUDINARY_ENABLED, cloudinary_env_locked=CLOUDINARY_ENV_LOCKED)


@app.context_processor
def inject_site_rating():
    """Real, aggregated website rating shown in the footer on every page -
    computed from actual submitted reviews rather than a hardcoded number."""
    db = get_db()
    row = db.execute('SELECT AVG(rating) AS avg_rating, COUNT(*) AS cnt FROM site_reviews').fetchone()
    return dict(site_rating_avg=row['avg_rating'], site_rating_count=row['cnt'] or 0)


# ---------- DATABASE HELPERS ----------
def _pg_sql(sql):
    sql = sql.replace('?', '%s')
    sql = sql.replace('INSERT OR IGNORE', 'INSERT')
    if 'INSERT INTO' in sql and 'ON CONFLICT DO NOTHING' not in sql and 'RETURNING' not in sql:
        # Only add ON CONFLICT to the two places that used SQLite's INSERT OR IGNORE.
        pass
    sql = sql.replace('MAX(0, stock - %s)', 'GREATEST(0, stock - %s)')
    # SQLite used double quotes for string literals in a few legacy queries.
    sql = re.sub(r'"([A-Za-z_][A-Za-z0-9_ ]*)"', r"'\1'", sql)
    return sql

class _SQLiteCursorCompat:
    def __init__(self, cursor): self._cursor = cursor
    def execute(self, sql, params=None): return self._cursor.execute(sql, params or ())
    def executemany(self, sql, seq): return self._cursor.executemany(sql, seq)
    def fetchone(self): return self._cursor.fetchone()
    def fetchall(self): return self._cursor.fetchall()
    @property
    def lastrowid(self): return self._cursor.lastrowid

class _SQLiteDBCompat:
    def __init__(self, conn): self._conn=conn
    def execute(self, sql, params=None): return _SQLiteCursorCompat(self._conn.execute(sql, params or ()))
    def cursor(self): return _SQLiteCursorCompat(self._conn.cursor())
    def commit(self): return self._conn.commit()
    def rollback(self): return self._conn.rollback()
    def close(self): return self._conn.close()

class _PostgresCursorCompat:
    ID_TABLES={'users','products','product_images','product_variants','orders','order_items','hero_images','categories','site_reviews','admin_messages'}
    def __init__(self, cursor): self._cursor=cursor; self._lastrowid=None
    def execute(self, sql, params=None):
        q=_pg_sql(sql)
        # Translate the two SQLite upsert statements.
        q=q.replace('INSERT INTO categories (name) VALUES (%s)', 'INSERT INTO categories (name) VALUES (%s) ON CONFLICT (name) DO NOTHING')
        q=q.replace('INSERT INTO product_categories (product_id, category_id) VALUES (%s, %s)', 'INSERT INTO product_categories (product_id, category_id) VALUES (%s, %s) ON CONFLICT DO NOTHING')
        # Preserve existing app.lastrowid usage on PostgreSQL by using RETURNING id internally.
        if q.lstrip().upper().startswith('INSERT INTO ') and ' RETURNING ' not in q.upper():
            m=re.match(r'INSERT INTO\s+([A-Za-z_][A-Za-z0-9_]*)', q, re.I)
            if m and m.group(1).lower() in self.ID_TABLES:
                q += ' RETURNING id'
                self._cursor.execute(q, params or ())
                row=self._cursor.fetchone()
                self._lastrowid=row[0] if row else None
                return self
        self._cursor.execute(q, params or ())
        return self
    def executemany(self, sql, seq): self._cursor.executemany(_pg_sql(sql), seq); return self
    def fetchone(self): return self._cursor.fetchone()
    def fetchall(self): return self._cursor.fetchall()
    @property
    def lastrowid(self): return self._lastrowid

class _PostgresDBCompat:
    def __init__(self, conn): self._conn=conn
    def execute(self, sql, params=None):
        cur=_PostgresCursorCompat(self._conn.cursor(cursor_factory=DictCursor)); cur.execute(sql, params); return cur
    def cursor(self): return _PostgresCursorCompat(self._conn.cursor(cursor_factory=DictCursor))
    def commit(self): return self._conn.commit()
    def rollback(self): return self._conn.rollback()
    def close(self): return self._conn.close()

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        if USE_POSTGRES:
            db = g._database = _PostgresDBCompat(psycopg2.connect(DATABASE_URL))
        else:
            conn = sqlite3.connect(DATABASE)
            conn.row_factory = sqlite3.Row
            db = g._database = _SQLiteDBCompat(conn)
    return db


@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()


def migrate_sqlite_db():
    """Add missing columns to existing tables"""
    with app.app_context():
        db = get_db()
        cursor = db.cursor()

        # Check if created_at column exists in products table
        cursor.execute("PRAGMA table_info(products)")
        columns = [col[1] for col in cursor.fetchall()]

        if 'created_at' not in columns:
            print("Adding created_at column to products table...")
            try:
                cursor.execute('''
                    CREATE TABLE products_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        original_price REAL,
                        price REAL NOT NULL,
                        description TEXT,
                        category TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')

                cursor.execute('''
                    INSERT INTO products_new (id, name, original_price, price, description, category)
                    SELECT id, name, original_price, price, description, category FROM products
                ''')

                cursor.execute('DROP TABLE products')
                cursor.execute('ALTER TABLE products_new RENAME TO products')
                db.commit()
                print("Database migration completed successfully!")
            except Exception as e:
                print(f"Migration error: {e}")
                db.rollback()

        # Orders: add guest-checkout + shipping columns if missing
        cursor.execute("PRAGMA table_info(orders)")
        order_columns = [col[1] for col in cursor.fetchall()]
        order_new_columns = {
            'guest_name': 'TEXT',
            'guest_email': 'TEXT',
            'shipping_address': 'TEXT',
            'guest_token': 'TEXT',
            'mpesa_code': 'TEXT',
        }
        for col_name, col_type in order_new_columns.items():
            if col_name not in order_columns:
                try:
                    cursor.execute(f'ALTER TABLE orders ADD COLUMN {col_name} {col_type}')
                    db.commit()
                except Exception as e:
                    print(f"Migration error adding {col_name}: {e}")
                    db.rollback()

        # user_id must be allowed to stay NULL for guest orders - SQLite
        # doesn't enforce NOT NULL here already (no constraint was set), so
        # no table rebuild is required for that part.

        # One-time migration: fold a pre-existing single "hero_image"
        # setting into the new hero_images table so upgrading sites don't
        # lose their current banner.
        cursor.execute("SELECT COUNT(*) FROM hero_images")
        if cursor.fetchone()[0] == 0:
            old_hero = cursor.execute('SELECT value FROM settings WHERE key = "hero_image"').fetchone()
            if old_hero and old_hero['value'] and old_hero['value'] != 'default_hero.jpg':
                cursor.execute('INSERT INTO hero_images (filename, sort_order) VALUES (?, 0)',
                               (old_hero['value'],))
                db.commit()

        # Seed the managed categories list from whatever free-text category
        # values already exist on products, so upgrading sites keep working
        # and admins immediately have a usable category list to pick from.
        existing_product_categories = cursor.execute(
            'SELECT DISTINCT category FROM products WHERE category IS NOT NULL AND category != ""').fetchall()
        for row in existing_product_categories:
            name = row['category'].strip()
            if not name:
                continue
            cat = cursor.execute('SELECT id FROM categories WHERE name = ?', (name,)).fetchone()
            if not cat:
                cursor.execute('INSERT INTO categories (name) VALUES (?)', (name,))
        # A handful of sensible starter categories if none exist at all yet.
        if cursor.execute('SELECT COUNT(*) FROM categories').fetchone()[0] == 0:
            for name in ('Electronics', 'Fashion', 'Home', 'Beauty', 'Sports', 'Toys'):
                cursor.execute('INSERT OR IGNORE INTO categories (name) VALUES (?)', (name,))
        db.commit()

        # Cloudinary backup id for each image, so it can be recovered from
        # the cloud if the local file is ever lost (server redeploy, disk
        # wipe, etc). Nullable - stays empty when Cloudinary isn't configured.
        for img_table in ('product_images', 'hero_images'):
            cursor.execute(f"PRAGMA table_info({img_table})")
            img_cols = [col[1] for col in cursor.fetchall()]
            if 'cloud_id' not in img_cols:
                try:
                    cursor.execute(f'ALTER TABLE {img_table} ADD COLUMN cloud_id TEXT')
                    db.commit()
                except Exception as e:
                    print(f"Migration error adding cloud_id to {img_table}: {e}")
                    db.rollback()

        # Backfill product_categories for any product that has a category
        # string but no rows yet in the new many-to-many table.
        products_needing_link = cursor.execute('''
            SELECT p.id, p.category FROM products p
            WHERE p.category IS NOT NULL AND p.category != ""
            AND NOT EXISTS (SELECT 1 FROM product_categories pc WHERE pc.product_id = p.id)
        ''').fetchall()
        for p in products_needing_link:
            cat = cursor.execute('SELECT id FROM categories WHERE name = ?', (p['category'],)).fetchone()
            if cat:
                cursor.execute('INSERT OR IGNORE INTO product_categories (product_id, category_id) VALUES (?, ?)',
                               (p['id'], cat['id']))
        db.commit()


def init_sqlite_db():
    with app.app_context():
        db = get_db()
        cursor = db.cursor()

        # Users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                is_admin INTEGER DEFAULT 0
            )
        ''')

        # Products table with all columns
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                original_price REAL,
                price REAL NOT NULL,
                description TEXT,
                category TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Product images table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS product_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                is_primary INTEGER DEFAULT 0,
                sort_order INTEGER DEFAULT 0,
                cloud_id TEXT,
                FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
            )
        ''')

        # Product variants (size / color combinations with their own stock)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS product_variants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                size TEXT,
                color TEXT,
                sku TEXT,
                stock INTEGER DEFAULT 0,
                price_override REAL,
                FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
            )
        ''')

        # Orders table. user_id is nullable so guests can check out without
        # an account; guest_name/guest_email/guest_token identify the order
        # for a guest instead.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                product_ids TEXT,
                total REAL,
                status TEXT DEFAULT 'pending',
                order_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                guest_name TEXT,
                guest_email TEXT,
                shipping_address TEXT,
                guest_token TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        ''')

        # Line items for an order - keeps the variant + quantity + price
        # actually purchased, independent of later product/variant edits.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS order_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                product_id INTEGER,
                variant_id INTEGER,
                product_name TEXT,
                variant_label TEXT,
                qty INTEGER NOT NULL,
                price REAL NOT NULL,
                FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE
            )
        ''')

        # Hero images - a rotating set shown as a portrait carousel on the
        # homepage instead of a single static banner.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS hero_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                sort_order INTEGER DEFAULT 0,
                cloud_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Categories - a managed list (not just free-text) so products can
        # carry more than one category and the shop sidebar stays tidy.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS product_categories (
                product_id INTEGER NOT NULL,
                category_id INTEGER NOT NULL,
                PRIMARY KEY (product_id, category_id),
                FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE,
                FOREIGN KEY(category_id) REFERENCES categories(id) ON DELETE CASCADE
            )
        ''')

        # Real, submitted website ratings - replaces the old hardcoded
        # "4.8 stars" stat with something actually computed from reviews.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS site_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                reviewer_name TEXT,
                rating INTEGER NOT NULL,
                comment TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        ''')

        # Settings table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')

        # Admin messages - a lightweight in-app inbox. Whenever a customer
        # checks out, a message lands here so the admin has a clear signal
        # that a payment/order needs verifying, without needing an external
        # email/SMS provider configured.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS admin_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER,
                message TEXT NOT NULL,
                is_read INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(order_id) REFERENCES orders(id)
            )
        ''')

        # Bootstrap an admin only from environment variables. Never seed a
        # known/default password into a production database.
        admin_count = cursor.execute('SELECT COUNT(*) FROM users WHERE is_admin = 1').fetchone()[0]
        if admin_count == 0:
            if not ADMIN_USERNAME_ENV or not ADMIN_INITIAL_PASSWORD_ENV:
                if os.environ.get('RENDER'):
                    raise RuntimeError(
                        'No admin account exists. Set ADMIN_USERNAME and ADMIN_INITIAL_PASSWORD in Render, then redeploy.'
                    )
            elif len(ADMIN_INITIAL_PASSWORD_ENV) < 12:
                raise RuntimeError('ADMIN_INITIAL_PASSWORD must be at least 12 characters long.')
            else:
                existing = cursor.execute(
                    'SELECT id FROM users WHERE username = ?', (ADMIN_USERNAME_ENV,)
                ).fetchone()
                if existing:
                    cursor.execute(
                        'UPDATE users SET password = ?, is_admin = 1 WHERE id = ?',
                        (generate_password_hash(ADMIN_INITIAL_PASSWORD_ENV), existing['id'])
                    )
                else:
                    cursor.execute(
                        'INSERT INTO users (username, password, is_admin) VALUES (?, ?, 1)',
                        (ADMIN_USERNAME_ENV, generate_password_hash(ADMIN_INITIAL_PASSWORD_ENV))
                    )
        elif os.environ.get('RENDER'):
            # A legacy database may still contain the old known default admin.
            # If the owner supplied replacement credentials, migrate that one
            # legacy account once; otherwise refuse to boot publicly.
            legacy = cursor.execute(
                'SELECT id, password FROM users WHERE username = ? AND is_admin = 1',
                ('admin@special!',)
            ).fetchone()
            if legacy and check_password_hash(legacy['password'], 'admin123'):
                if ADMIN_USERNAME_ENV and ADMIN_INITIAL_PASSWORD_ENV and len(ADMIN_INITIAL_PASSWORD_ENV) >= 12:
                    existing = cursor.execute(
                        'SELECT id FROM users WHERE username = ? AND id != ?',
                        (ADMIN_USERNAME_ENV, legacy['id'])
                    ).fetchone()
                    if existing:
                        raise RuntimeError('ADMIN_USERNAME is already in use by another account.')
                    cursor.execute(
                        'UPDATE users SET username = ?, password = ?, is_admin = 1 WHERE id = ?',
                        (ADMIN_USERNAME_ENV, generate_password_hash(ADMIN_INITIAL_PASSWORD_ENV), legacy['id'])
                    )
                else:
                    raise RuntimeError(
                        'Legacy default admin credentials detected. Set ADMIN_USERNAME and ADMIN_INITIAL_PASSWORD (12+ characters) in Render, then redeploy.'
                    )

        # Set default settings if not already present (covers every key in
        # SETTINGS_DEFAULTS, including new ones added by later app updates).
        for key, value in SETTINGS_DEFAULTS.items():
            setting = cursor.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
            if not setting:
                cursor.execute('INSERT INTO settings (key, value) VALUES (?, ?)', (key, value))

        db.commit()

        # Run migration to add any missing columns
        migrate_db()


def init_postgres_db(bootstrap_admin=True):
    db=get_db(); c=db.cursor()
    statements=[
        "CREATE TABLE IF NOT EXISTS users (id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, is_admin INTEGER DEFAULT 0)",
        "CREATE TABLE IF NOT EXISTS products (id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, name TEXT NOT NULL, original_price DOUBLE PRECISION, price DOUBLE PRECISION NOT NULL, description TEXT, category TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS product_images (id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE, filename TEXT NOT NULL, is_primary INTEGER DEFAULT 0, sort_order INTEGER DEFAULT 0, cloud_id TEXT)",
        "CREATE TABLE IF NOT EXISTS product_variants (id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE, size TEXT, color TEXT, sku TEXT, stock INTEGER DEFAULT 0, price_override DOUBLE PRECISION)",
        "CREATE TABLE IF NOT EXISTS orders (id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, user_id INTEGER REFERENCES users(id), product_ids TEXT, total DOUBLE PRECISION, status TEXT DEFAULT 'pending', order_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP, guest_name TEXT, guest_email TEXT, shipping_address TEXT, guest_token TEXT, mpesa_code TEXT)",
        "CREATE TABLE IF NOT EXISTS order_items (id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE, product_id INTEGER, variant_id INTEGER, product_name TEXT, variant_label TEXT, qty INTEGER NOT NULL, price DOUBLE PRECISION NOT NULL)",
        "CREATE TABLE IF NOT EXISTS hero_images (id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, filename TEXT NOT NULL, sort_order INTEGER DEFAULT 0, cloud_id TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS categories (id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, name TEXT UNIQUE NOT NULL)",
        "CREATE TABLE IF NOT EXISTS product_categories (product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE, category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE, PRIMARY KEY (product_id, category_id))",
        "CREATE TABLE IF NOT EXISTS site_reviews (id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, user_id INTEGER REFERENCES users(id), reviewer_name TEXT, rating INTEGER NOT NULL, comment TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)",
        "CREATE TABLE IF NOT EXISTS admin_messages (id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, order_id INTEGER REFERENCES orders(id), message TEXT NOT NULL, is_read INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
    ]
    for sql in statements: c.execute(sql)
    for sql in [
        'ALTER TABLE orders ADD COLUMN IF NOT EXISTS guest_name TEXT',
        'ALTER TABLE orders ADD COLUMN IF NOT EXISTS guest_email TEXT',
        'ALTER TABLE orders ADD COLUMN IF NOT EXISTS shipping_address TEXT',
        'ALTER TABLE orders ADD COLUMN IF NOT EXISTS guest_token TEXT',
        'ALTER TABLE orders ADD COLUMN IF NOT EXISTS mpesa_code TEXT',
        'ALTER TABLE product_images ADD COLUMN IF NOT EXISTS cloud_id TEXT',
        'ALTER TABLE hero_images ADD COLUMN IF NOT EXISTS cloud_id TEXT',
    ]: c.execute(sql)
    for key,value in SETTINGS_DEFAULTS.items():
        if not c.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone():
            c.execute('INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT DO NOTHING',(key,value))
    db.commit()
    if bootstrap_admin: bootstrap_admin_postgres(db)

def bootstrap_admin_postgres(db=None):
    db=db or get_db(); c=db.cursor()
    admin_count=c.execute('SELECT COUNT(*) FROM users WHERE is_admin = 1').fetchone()[0]
    if admin_count==0:
        if not ADMIN_USERNAME_ENV or not ADMIN_INITIAL_PASSWORD_ENV:
            if os.environ.get('RENDER'): raise RuntimeError('No admin account exists. Set ADMIN_USERNAME and ADMIN_INITIAL_PASSWORD in Render, then redeploy.')
        elif len(ADMIN_INITIAL_PASSWORD_ENV)<12: raise RuntimeError('ADMIN_INITIAL_PASSWORD must be at least 12 characters long.')
        else:
            existing=c.execute('SELECT id FROM users WHERE username = ?', (ADMIN_USERNAME_ENV,)).fetchone()
            if existing: c.execute('UPDATE users SET password = ?, is_admin = 1 WHERE id = ?', (generate_password_hash(ADMIN_INITIAL_PASSWORD_ENV),existing['id']))
            else: c.execute('INSERT INTO users (username,password,is_admin) VALUES (?,?,1)',(ADMIN_USERNAME_ENV,generate_password_hash(ADMIN_INITIAL_PASSWORD_ENV)))
    elif os.environ.get('RENDER'):
        legacy=c.execute('SELECT id,password FROM users WHERE username = ? AND is_admin = 1',('admin@special!',)).fetchone()
        if legacy and check_password_hash(legacy['password'],'admin123'):
            if ADMIN_USERNAME_ENV and ADMIN_INITIAL_PASSWORD_ENV and len(ADMIN_INITIAL_PASSWORD_ENV)>=12:
                existing=c.execute('SELECT id FROM users WHERE username = ? AND id != ?',(ADMIN_USERNAME_ENV,legacy['id'])).fetchone()
                if existing: raise RuntimeError('ADMIN_USERNAME is already in use by another account.')
                c.execute('UPDATE users SET username = ?, password = ?, is_admin = 1 WHERE id = ?',(ADMIN_USERNAME_ENV,generate_password_hash(ADMIN_INITIAL_PASSWORD_ENV),legacy['id']))
            else: raise RuntimeError('Legacy default admin credentials detected. Set ADMIN_USERNAME and ADMIN_INITIAL_PASSWORD (12+ characters) in Render, then redeploy.')
    db.commit()

def init_db():
    if USE_POSTGRES: init_postgres_db()
    else: init_sqlite_db()

def migrate_db():
    if not USE_POSTGRES: migrate_sqlite_db()

# ---------- AUTH DECORATORS ----------
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in first.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)

    return decorated_function


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in first.', 'warning')
            return redirect(url_for('login'))
        user = get_db().execute('SELECT is_admin FROM users WHERE id = ?', (session['user_id'],)).fetchone()
        if not user or not user['is_admin']:
            flash('Admin access required.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)

    return decorated_function


# ---------- CART / VARIANT HELPERS ----------
def variant_label(variant_row):
    """Human readable label like 'Size: M, Color: Red' for a variant row."""
    if variant_row is None:
        return None
    parts = []
    if variant_row['size']:
        parts.append(f"Size: {variant_row['size']}")
    if variant_row['color']:
        parts.append(f"Color: {variant_row['color']}")
    return ', '.join(parts) if parts else None


def cart_key(product_id, variant_id=None):
    return f"{product_id}:{variant_id or 0}"


def parse_cart_key(key):
    pid, _, vid = key.partition(':')
    return int(pid), (int(vid) if vid and vid != '0' else None)


def get_cart_items(db, cart):
    """Resolve the session cart (dict of 'pid:vid' -> qty) into full item
    rows with product/variant details, unit price and subtotal."""
    items = []
    total = 0
    for key, qty in cart.items():
        try:
            pid, vid = parse_cart_key(key)
        except (ValueError, TypeError):
            # Malformed key (e.g. left over from a session created before
            # update_cart's validation was added) - skip it instead of
            # crashing the whole cart/checkout page.
            continue
        product = db.execute('SELECT * FROM products WHERE id = ?', (pid,)).fetchone()
        if not product:
            continue
        variant = None
        if vid:
            variant = db.execute('SELECT * FROM product_variants WHERE id = ? AND product_id = ?',
                                  (vid, pid)).fetchone()
        unit_price = variant['price_override'] if (variant and variant['price_override']) else product['price']
        subtotal = unit_price * qty
        items.append({
            'key': key,
            'product': product,
            'variant': variant,
            'variant_label': variant_label(variant),
            'qty': qty,
            'unit_price': unit_price,
            'subtotal': subtotal,
            'stock': variant['stock'] if variant else None,
        })
        total += subtotal
    return items, total


# ---------- CATEGORY HELPERS ----------
def get_or_create_category(db, name):
    name = (name or '').strip()
    if not name:
        return None
    row = db.execute('SELECT id FROM categories WHERE name = ?', (name,)).fetchone()
    if row:
        return row['id']
    cursor = db.cursor()
    cursor.execute('INSERT INTO categories (name) VALUES (?)', (name,))
    return cursor.lastrowid


def save_product_categories(db, product_id, category_ids, new_category_name=None):
    """Replace a product's category assignments. The first assigned category
    is also mirrored onto products.category so older parts of the templates
    that read that single column (badges, shop cards) keep working, while
    product_categories carries the full multi-category tag set."""
    cursor = db.cursor()
    ids = [int(i) for i in category_ids if str(i).isdigit()]
    if new_category_name:
        new_id = get_or_create_category(db, new_category_name)
        if new_id:
            ids.append(new_id)
    ids = list(dict.fromkeys(ids))  # de-dupe, keep order

    cursor.execute('DELETE FROM product_categories WHERE product_id = ?', (product_id,))
    for cid in ids:
        cursor.execute('INSERT OR IGNORE INTO product_categories (product_id, category_id) VALUES (?, ?)',
                       (product_id, cid))

    primary_name = None
    if ids:
        row = db.execute('SELECT name FROM categories WHERE id = ?', (ids[0],)).fetchone()
        primary_name = row['name'] if row else None
    cursor.execute('UPDATE products SET category = ? WHERE id = ?', (primary_name, product_id))


# ---------- ROUTES ----------
@app.route('/')
def index():
    return redirect(url_for('dashboard'))


@app.route('/dashboard')
def dashboard():
    db = get_db()
    products = db.execute('SELECT * FROM products LIMIT 6').fetchall()
    featured = db.execute('SELECT * FROM products ORDER BY id DESC LIMIT 4').fetchall()

    hero_images = db.execute('SELECT filename FROM hero_images ORDER BY sort_order, id').fetchall()
    hero_images = [r['filename'] for r in hero_images]

    site_name = db.execute('SELECT value FROM settings WHERE key = "site_name"').fetchone()
    site_logo = db.execute('SELECT value FROM settings WHERE key = "site_logo"').fetchone()
    categories = db.execute('SELECT * FROM categories ORDER BY name').fetchall()

    stats = {
        'products': db.execute('SELECT COUNT(*) FROM products').fetchone()[0],
        'categories': db.execute('SELECT COUNT(*) FROM categories').fetchone()[0],
        'orders': db.execute('SELECT COUNT(*) FROM orders').fetchone()[0],
    }
    return render_template('dashboard.html', products=products, featured=featured,
                                  stats=stats, hero_images=hero_images, categories=categories,
                                  site_name=site_name['value'] if site_name else 'TechStore',
                                  site_logo=site_logo['value'] if site_logo else 'default_logo.svg',
                                  db=db, cart=session.get('cart', {}))


@app.route('/shop')
def shop():
    db = get_db()
    category = request.args.get('category')
    if category:
        products = db.execute('''
            SELECT DISTINCT p.* FROM products p
            LEFT JOIN product_categories pc ON pc.product_id = p.id
            LEFT JOIN categories c ON c.id = pc.category_id
            WHERE p.category = ? OR c.name = ?
            ORDER BY p.id DESC
        ''', (category, category)).fetchall()
    else:
        products = db.execute('SELECT * FROM products ORDER BY id DESC').fetchall()
    categories = db.execute('SELECT * FROM categories ORDER BY name').fetchall()

    site_name = db.execute('SELECT value FROM settings WHERE key = "site_name"').fetchone()
    site_logo = db.execute('SELECT value FROM settings WHERE key = "site_logo"').fetchone()

    return render_template('shop.html', products=products, categories=categories, selected=category,
                                  site_name=site_name['value'] if site_name else 'TechStore',
                                  site_logo=site_logo['value'] if site_logo else 'default_logo.svg',
                                  db=db, cart=session.get('cart', {}))


@app.route('/product/<int:product_id>')
def product_detail(product_id):
    db = get_db()
    product = db.execute('SELECT * FROM products WHERE id = ?', (product_id,)).fetchone()
    if not product:
        flash('Product not found.', 'danger')
        return redirect(url_for('shop'))

    images = db.execute('SELECT * FROM product_images WHERE product_id = ? ORDER BY sort_order, id',
                        (product_id,)).fetchall()
    variants = db.execute('SELECT * FROM product_variants WHERE product_id = ? ORDER BY size, color',
                          (product_id,)).fetchall()
    product_categories = db.execute('''
        SELECT c.name FROM categories c
        JOIN product_categories pc ON pc.category_id = c.id
        WHERE pc.product_id = ? ORDER BY c.name
    ''', (product_id,)).fetchall()

    site_name = db.execute('SELECT value FROM settings WHERE key = "site_name"').fetchone()
    site_logo = db.execute('SELECT value FROM settings WHERE key = "site_logo"').fetchone()

    return render_template('product_detail.html', product=product, images=images, variants=variants,
                                  variant_label=variant_label, product_categories=[r['name'] for r in product_categories],
                                  site_name=site_name['value'] if site_name else 'TechStore',
                                  site_logo=site_logo['value'] if site_logo else 'default_logo.svg',
                                  db=db, cart=session.get('cart', {}))


@app.route('/login', methods=['GET', 'POST'])
def login():
    db = get_db()

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        user = db.execute(
            'SELECT * FROM users WHERE username = ?',
            (username,)
        ).fetchone()

        if user and check_password_hash(user['password'], password):
            # Prevent session fixation: discard any pre-login session state
            # before creating the authenticated session.
            session.clear()
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['is_admin'] = bool(user['is_admin'])
            flash('Welcome back!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid credentials.', 'danger')

    site_name = db.execute(
        'SELECT value FROM settings WHERE key = "site_name"'
    ).fetchone()

    return render_template(
        'login.html',
        site_name=site_name['value'] if site_name else 'TechStore'
    )

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully.', 'info')
    return redirect(url_for('dashboard'))


@app.route('/register', methods=['GET', 'POST'])
def register():
    db = get_db()

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        if db.execute(
            'SELECT * FROM users WHERE username = ?',
            (username,)
        ).fetchone():
            flash('Username already exists.', 'danger')
        else:
            hashed = generate_password_hash(password)

            db.execute(
                'INSERT INTO users (username, password, is_admin) VALUES (?, ?, 0)',
                (username, hashed)
            )

            db.commit()

            flash('Account created! Please log in.', 'success')
            return redirect(url_for('login'))

    site_name = db.execute(
        'SELECT value FROM settings WHERE key = "site_name"'
    ).fetchone()

    return render_template(
        'register.html',
        site_name=site_name['value'] if site_name else 'TechStore'
    )
MAX_CART_QTY = 999  # sane upper bound on a single cart line's quantity


@app.route('/add_to_cart/<int:product_id>', methods=['GET', 'POST'])
def add_to_cart(product_id):
    # Guests can add to cart too - the cart just lives in their session,
    # same as a logged-in user's, until they check out.
    db = get_db()
    variant_id = request.values.get('variant_id', type=int)
    qty = request.values.get('qty', default=1, type=int)
    # Reject anything that isn't a sane positive quantity (missing/blank/zero/
    # negative) instead of silently coercing negatives through - a negative
    # qty here would shrink the order total and even *add* stock back at
    # checkout time (see checkout()'s stock update). A too-large qty is
    # capped rather than discarded, so a genuine bulk order still goes
    # through at the max we're willing to accept in one line.
    if qty is None or qty < 1:
        qty = 1
    elif qty > MAX_CART_QTY:
        qty = MAX_CART_QTY

    variants = db.execute('SELECT * FROM product_variants WHERE product_id = ?', (product_id,)).fetchall()
    if variants and not variant_id:
        flash('Please choose a size/color before adding to cart.', 'warning')
        return redirect(request.referrer or url_for('product_detail', product_id=product_id))

    if variant_id:
        variant = db.execute('SELECT * FROM product_variants WHERE id = ? AND product_id = ?',
                             (variant_id, product_id)).fetchone()
        if not variant:
            flash('That option is no longer available.', 'danger')
            return redirect(request.referrer or url_for('shop'))
        if variant['stock'] is not None and variant['stock'] <= 0:
            flash('That size/color is out of stock.', 'danger')
            return redirect(request.referrer or url_for('product_detail', product_id=product_id))

    cart = session.get('cart', {})
    key = cart_key(product_id, variant_id)
    cart[key] = min(cart.get(key, 0) + qty, MAX_CART_QTY)
    session['cart'] = cart
    flash('Product added to cart.', 'success')
    return redirect(request.referrer or url_for('shop'))


@app.route('/cart')
def view_cart():
    db = get_db()
    cart = session.get('cart', {})
    items, total = get_cart_items(db, cart)

    site_name = db.execute('SELECT value FROM settings WHERE key = "site_name"').fetchone()
    site_logo = db.execute('SELECT value FROM settings WHERE key = "site_logo"').fetchone()

    return render_template('cart.html', items=items, total=total,
                                  site_name=site_name['value'] if site_name else 'TechStore',
                                  site_logo=site_logo['value'] if site_logo else 'default_logo.svg')


@app.route('/update_cart', methods=['POST'])
def update_cart():
    # Only accept quantity updates for keys that were already in the
    # session cart - never trust a "qty_<key>" field name as a new cart
    # key straight from the request. Without this, a crafted/malformed
    # field name (e.g. "qty_abc:1") would get stored as-is and blow up
    # later with an unhandled ValueError the next time the cart is read
    # (parse_cart_key() calls int() on the product id half of the key).
    existing_cart = session.get('cart', {})
    cart = {}
    for key, value in request.form.items():
        if key.startswith('qty_'):
            item_key = key[len('qty_'):]
            if item_key not in existing_cart:
                continue
            try:
                qty = int(value)
            except ValueError:
                continue
            if 0 < qty <= MAX_CART_QTY:
                cart[item_key] = qty
    session['cart'] = cart
    flash('Cart updated.', 'info')
    return redirect(url_for('view_cart'))


@app.route('/checkout', methods=['GET', 'POST'])
def checkout():
    db = get_db()
    cart = session.get('cart', {})
    if not cart:
        flash('Cart is empty.', 'warning')
        return redirect(url_for('shop'))

    items, total = get_cart_items(db, cart)
    if not items:
        flash('Cart is empty.', 'warning')
        return redirect(url_for('shop'))

    site_name = db.execute('SELECT value FROM settings WHERE key = "site_name"').fetchone()
    site_logo = db.execute('SELECT value FROM settings WHERE key = "site_logo"').fetchone()
    site_name_val = site_name['value'] if site_name else 'TechStore'
    site_logo_val = site_logo['value'] if site_logo else 'default_logo.svg'

    if request.method == 'POST':
        shipping_address = request.form.get('shipping_address', '').strip()
        if not shipping_address:
            flash('Please enter a shipping address.', 'warning')
            return render_template('checkout.html', items=items, total=total,
                                    site_name=site_name_val, site_logo=site_logo_val)

        guest_name = guest_email = guest_token = None
        user_id = session.get('user_id')

        if not user_id:
            guest_name = request.form.get('guest_name', '').strip()
            guest_email = request.form.get('guest_email', '').strip()
            if not guest_name or not guest_email:
                flash('Please enter your name and email to check out as a guest.', 'warning')
                return render_template('checkout.html', items=items, total=total,
                                        site_name=site_name_val, site_logo=site_logo_val)
            guest_token = uuid.uuid4().hex

        # Validate stock for anything with tracked stock before committing.
        for item in items:
            if item['stock'] is not None and item['qty'] > item['stock']:
                flash(f"Only {item['stock']} left of {item['product']['name']} "
                      f"({item['variant_label']}). Please update your cart.", 'danger')
                return redirect(url_for('view_cart'))

        mpesa_code = request.form.get('mpesa_code', '').strip() or None

        product_ids = ','.join(str(i['product']['id']) for i in items)
        cursor = db.cursor()
        cursor.execute('''INSERT INTO orders
            (user_id, product_ids, total, status, guest_name, guest_email, shipping_address, guest_token, mpesa_code)
            VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?)''',
            (user_id, product_ids, total, guest_name, guest_email, shipping_address, guest_token, mpesa_code))
        order_id = cursor.lastrowid

        for item in items:
            cursor.execute('''INSERT INTO order_items
                (order_id, product_id, variant_id, product_name, variant_label, qty, price)
                VALUES (?, ?, ?, ?, ?, ?, ?)''',
                (order_id, item['product']['id'], item['variant']['id'] if item['variant'] else None,
                 item['product']['name'], item['variant_label'], item['qty'], item['unit_price']))
            if item['variant']:
                cursor.execute('UPDATE product_variants SET stock = MAX(0, stock - ?) WHERE id = ?',
                               (item['qty'], item['variant']['id']))

        # Notify the admin (in-app inbox) that a new order/payment needs
        # checking - shown as a message in the admin panel.
        buyer_label = guest_name or session.get('username') or 'A customer'
        notify_text = f"New order #{order_id} from {buyer_label} — total KSh {total:.2f}."
        if mpesa_code:
            notify_text += f" M-Pesa code: {mpesa_code}."
        else:
            notify_text += " No M-Pesa code was entered yet — verify payment before shipping."
        cursor.execute('INSERT INTO admin_messages (order_id, message) VALUES (?, ?)',
                       (order_id, notify_text))

        db.commit()
        session.pop('cart', None)
        if guest_token:
            session.setdefault('guest_order_tokens', []).append(f"{order_id}:{guest_token}")
            session.modified = True
        flash('Order placed successfully! The admin has been notified of your payment.', 'success')
        return redirect(url_for('order_confirmation', order_id=order_id, token=guest_token or ''))

    return render_template('checkout.html', items=items, total=total,
                            site_name=site_name_val, site_logo=site_logo_val)


@app.route('/order/confirmation/<int:order_id>')
def order_confirmation(order_id):
    db = get_db()
    order = db.execute('SELECT * FROM orders WHERE id = ?', (order_id,)).fetchone()
    if not order:
        flash('Order not found.', 'danger')
        return redirect(url_for('shop'))

    token = request.args.get('token', '')
    owns_order = (session.get('user_id') and order['user_id'] == session['user_id'])
    guest_ok = order['guest_token'] and token and order['guest_token'] == token
    if not (owns_order or guest_ok):
        flash('You do not have access to that order.', 'danger')
        return redirect(url_for('shop'))

    line_items = db.execute('SELECT * FROM order_items WHERE order_id = ?', (order_id,)).fetchall()

    site_name = db.execute('SELECT value FROM settings WHERE key = "site_name"').fetchone()
    site_logo = db.execute('SELECT value FROM settings WHERE key = "site_logo"').fetchone()

    return render_template('order_confirmation.html', order=order, line_items=line_items,
                            site_name=site_name['value'] if site_name else 'TechStore',
                            site_logo=site_logo['value'] if site_logo else 'default_logo.svg')


@app.route('/track-order', methods=['GET', 'POST'])
def track_order():
    order = None
    line_items = []
    searched = False
    db = get_db()
    if request.method == 'POST':
        searched = True
        order_id = request.form.get('order_id', '').strip()
        email = request.form.get('email', '').strip().lower()
        if order_id.isdigit():
            candidate = db.execute('SELECT * FROM orders WHERE id = ?', (int(order_id),)).fetchone()
            if candidate and candidate['guest_email'] and candidate['guest_email'].lower() == email:
                order = candidate
                line_items = db.execute('SELECT * FROM order_items WHERE order_id = ?', (order['id'],)).fetchall()
        if not order:
            flash('No matching guest order found. Double check the order number and email.', 'warning')

    site_name = db.execute('SELECT value FROM settings WHERE key = "site_name"').fetchone()
    site_logo = db.execute('SELECT value FROM settings WHERE key = "site_logo"').fetchone()

    return render_template('track_order.html', order=order, line_items=line_items, searched=searched,
                            site_name=site_name['value'] if site_name else 'TechStore',
                            site_logo=site_logo['value'] if site_logo else 'default_logo.svg')


@app.route('/orders')
@login_required
def orders():
    db = get_db()
    if session.get('is_admin'):
        orders = db.execute('''SELECT o.*, COALESCE(u.username, o.guest_name || ' (guest)') AS username
                               FROM orders o LEFT JOIN users u ON o.user_id = u.id
                               ORDER BY o.order_date DESC''').fetchall()
    else:
        orders = db.execute('SELECT * FROM orders WHERE user_id = ? ORDER BY order_date DESC',
                            (session['user_id'],)).fetchall()

    site_name = db.execute('SELECT value FROM settings WHERE key = "site_name"').fetchone()
    site_logo = db.execute('SELECT value FROM settings WHERE key = "site_logo"').fetchone()

    return render_template('orders.html', orders=orders, is_admin=session.get('is_admin', False),
                                  site_name=site_name['value'] if site_name else 'TechStore',
                                  site_logo=site_logo['value'] if site_logo else 'default_logo.svg')


@app.route('/admin')
@admin_required
def admin_panel():
    db = get_db()
    products = db.execute('SELECT * FROM products ORDER BY id DESC').fetchall()
    users = db.execute('SELECT id, username, is_admin FROM users').fetchall()
    orders = db.execute('''SELECT o.*, COALESCE(u.username, o.guest_name || ' (guest)') AS username
                           FROM orders o LEFT JOIN users u ON o.user_id = u.id
                           ORDER BY o.order_date DESC''').fetchall()

    hero_images = db.execute('SELECT * FROM hero_images ORDER BY sort_order, id').fetchall()
    categories = db.execute('''
        SELECT c.*, (SELECT COUNT(*) FROM product_categories pc WHERE pc.category_id = c.id) AS product_count
        FROM categories c ORDER BY c.name
    ''').fetchall()

    site_name = db.execute('SELECT value FROM settings WHERE key = "site_name"').fetchone()
    site_logo = db.execute('SELECT value FROM settings WHERE key = "site_logo"').fetchone()

    messages = db.execute('''
        SELECT am.*, o.total AS order_total, o.status AS order_status
        FROM admin_messages am LEFT JOIN orders o ON am.order_id = o.id
        ORDER BY am.created_at DESC
    ''').fetchall()
    unread_count = sum(1 for m in messages if not m['is_read'])

    stats = {
        'products': len(products),
        'users': len(users),
        'orders': len(orders),
        'revenue': db.execute('SELECT SUM(total) FROM orders WHERE status="delivered"').fetchone()[0] or 0
    }
    return render_template('admin.html', products=products, users=users, orders=orders,
                                  stats=stats, hero_images=hero_images, categories=categories,
                                  max_hero_images=MAX_HERO_IMAGES, messages=messages, unread_count=unread_count,
                                  site_name=site_name['value'] if site_name else 'TechStore',
                                  site_logo=site_logo['value'] if site_logo else 'default_logo.svg',
                                  db=db)


@app.route('/admin/messages/read/<int:message_id>', methods=['POST'])
@admin_required
def mark_message_read(message_id):
    db = get_db()
    db.execute('UPDATE admin_messages SET is_read = 1 WHERE id = ?', (message_id,))
    db.commit()
    return redirect(url_for('admin_panel') + '#messages')


@app.route('/admin/messages/read_all', methods=['POST'])
@admin_required
def mark_all_messages_read():
    db = get_db()
    db.execute('UPDATE admin_messages SET is_read = 1 WHERE is_read = 0')
    db.commit()
    flash('All messages marked as read.', 'info')
    return redirect(url_for('admin_panel') + '#messages')


@app.route('/admin/messages/delete/<int:message_id>', methods=['POST'])
@admin_required
def delete_message(message_id):
    db = get_db()
    db.execute('DELETE FROM admin_messages WHERE id = ?', (message_id,))
    db.commit()
    flash('Message deleted.', 'info')
    return redirect(url_for('admin_panel') + '#messages')


@app.route('/admin/product/add', methods=['POST'])
@admin_required
def add_product():
    name = request.form['name']
    original_price = request.form.get('original_price')
    price = float(request.form['price'])
    description = request.form['description']

    original_price = float(original_price) if original_price and original_price.strip() else None

    db = get_db()
    cursor = db.cursor()
    cursor.execute('INSERT INTO products (name, original_price, price, description, category) VALUES (?,?,?,?,?)',
                   (name, original_price, price, description, None))
    product_id = cursor.lastrowid

    save_product_categories(db, product_id, request.form.getlist('category_ids'),
                            request.form.get('new_category'))

    # Handle multiple image uploads (max 10)
    uploaded_count = 0
    for i in range(10):
        image_key = f'image_{i}'
        if image_key in request.files:
            file = request.files[image_key]
            if file and file.filename and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                unique_filename = f"p{product_id}_{uuid.uuid4().hex[:8]}_{filename}"
                cloud_id = process_and_save_image(file, app.config['UPLOAD_FOLDER'], unique_filename)

                is_primary = 1 if uploaded_count == 0 else 0
                cursor.execute(
                    'INSERT INTO product_images (product_id, filename, is_primary, sort_order, cloud_id) VALUES (?,?,?,?,?)',
                    (product_id, unique_filename, is_primary, uploaded_count, cloud_id))
                uploaded_count += 1

    _save_variants_from_form(cursor, product_id)

    db.commit()
    flash(f'Product added successfully with {uploaded_count} images.', 'success')
    return redirect(url_for('admin_panel'))


def _save_variants_from_form(cursor, product_id):
    """Reads parallel variant_size_N / variant_color_N / variant_stock_N /
    variant_sku_N fields (N = 0..9) and inserts any rows that have at least
    a size or a color filled in."""
    for i in range(10):
        size = request.form.get(f'variant_size_{i}', '').strip()
        color = request.form.get(f'variant_color_{i}', '').strip()
        if not size and not color:
            continue
        stock_raw = request.form.get(f'variant_stock_{i}', '').strip()
        sku = request.form.get(f'variant_sku_{i}', '').strip() or None
        stock = int(stock_raw) if stock_raw.isdigit() else 0
        cursor.execute(
            'INSERT INTO product_variants (product_id, size, color, sku, stock) VALUES (?,?,?,?,?)',
            (product_id, size or None, color or None, sku, stock))


@app.route('/admin/product/edit/<int:product_id>', methods=['GET', 'POST'])
@admin_required
def edit_product(product_id):
    db = get_db()
    product = db.execute('SELECT * FROM products WHERE id = ?', (product_id,)).fetchone()
    if not product:
        flash('Product not found.', 'danger')
        return redirect(url_for('admin_panel'))

    if request.method == 'POST':
        name = request.form['name']
        original_price = request.form.get('original_price')
        price = float(request.form['price'])
        description = request.form['description']

        original_price = float(original_price) if original_price and original_price.strip() else None

        db.execute('UPDATE products SET name=?, original_price=?, price=?, description=? WHERE id=?',
                   (name, original_price, price, description, product_id))

        save_product_categories(db, product_id, request.form.getlist('category_ids'),
                                request.form.get('new_category'))

        # Handle new image uploads
        existing_images = \
        db.execute('SELECT COUNT(*) FROM product_images WHERE product_id = ?', (product_id,)).fetchone()[0]
        uploaded_count = 0
        for i in range(10):
            image_key = f'image_{i}'
            if image_key in request.files:
                file = request.files[image_key]
                if file and file.filename and allowed_file(file.filename):
                    if existing_images + uploaded_count >= 10:
                        flash('Maximum 10 images per product reached.', 'warning')
                        break
                    filename = secure_filename(file.filename)
                    unique_filename = f"p{product_id}_{uuid.uuid4().hex[:8]}_{filename}"
                    cloud_id = process_and_save_image(file, app.config['UPLOAD_FOLDER'], unique_filename)

                    is_primary = 1 if existing_images + uploaded_count == 0 else 0
                    db.execute(
                        'INSERT INTO product_images (product_id, filename, is_primary, sort_order, cloud_id) VALUES (?,?,?,?,?)',
                        (product_id, unique_filename, is_primary, existing_images + uploaded_count, cloud_id))
                    uploaded_count += 1

        _save_variants_from_form(db.cursor(), product_id)

        db.commit()
        flash('Product updated successfully.', 'success')
        return redirect(url_for('edit_product', product_id=product_id))

    images = db.execute('SELECT * FROM product_images WHERE product_id = ? ORDER BY sort_order',
                        (product_id,)).fetchall()
    variants = db.execute('SELECT * FROM product_variants WHERE product_id = ? ORDER BY size, color',
                          (product_id,)).fetchall()
    all_categories = db.execute('SELECT * FROM categories ORDER BY name').fetchall()
    selected_category_ids = {r['category_id'] for r in
                             db.execute('SELECT category_id FROM product_categories WHERE product_id = ?',
                                       (product_id,)).fetchall()}

    site_name = db.execute('SELECT value FROM settings WHERE key = "site_name"').fetchone()
    site_logo = db.execute('SELECT value FROM settings WHERE key = "site_logo"').fetchone()

    return render_template('edit_product.html', product=product, images=images, variants=variants,
                                  all_categories=all_categories, selected_category_ids=selected_category_ids,
                                  site_name=site_name['value'] if site_name else 'TechStore',
                                  site_logo=site_logo['value'] if site_logo else 'default_logo.svg')


@app.route('/admin/product/variant/delete/<int:variant_id>', methods=['POST'])
@admin_required
def delete_variant(variant_id):
    db = get_db()
    variant = db.execute('SELECT product_id FROM product_variants WHERE id = ?', (variant_id,)).fetchone()
    if not variant:
        flash('Variant not found.', 'danger')
        return redirect(url_for('admin_panel'))
    db.execute('DELETE FROM product_variants WHERE id = ?', (variant_id,))
    db.commit()
    flash('Variant removed.', 'info')
    return redirect(url_for('edit_product', product_id=variant['product_id']))


@app.route('/admin/product/image/delete/<int:image_id>', methods=['POST'])
@admin_required
def delete_product_image(image_id):
    db = get_db()
    image = db.execute('SELECT filename, product_id FROM product_images WHERE id = ?', (image_id,)).fetchone()
    if not image:
        flash('Image not found.', 'danger')
        return redirect(url_for('admin_panel'))
    delete_image_variants(app.config['UPLOAD_FOLDER'], image['filename'])
    db.execute('DELETE FROM product_images WHERE id = ?', (image_id,))
    db.commit()
    flash('Image deleted.', 'info')
    return redirect(url_for('edit_product', product_id=image['product_id']))


@app.route('/admin/product/delete/<int:product_id>', methods=['POST'])
@admin_required
def delete_product(product_id):
    db = get_db()
    # Get all images to delete
    images = db.execute('SELECT filename FROM product_images WHERE product_id = ?', (product_id,)).fetchall()
    for img in images:
        delete_image_variants(app.config['UPLOAD_FOLDER'], img['filename'])

    db.execute('DELETE FROM product_images WHERE product_id = ?', (product_id,))
    db.execute('DELETE FROM product_variants WHERE product_id = ?', (product_id,))
    db.execute('DELETE FROM products WHERE id = ?', (product_id,))
    db.commit()
    flash('Product deleted.', 'info')
    return redirect(url_for('admin_panel'))


@app.route('/admin/hero/add', methods=['POST'])
@admin_required
def add_hero_image():
    db = get_db()
    current_count = db.execute('SELECT COUNT(*) FROM hero_images').fetchone()[0]
    if current_count >= MAX_HERO_IMAGES:
        flash(f'Maximum of {MAX_HERO_IMAGES} hero images reached. Remove one before adding another.', 'warning')
        return redirect(url_for('admin_panel'))

    files = request.files.getlist('hero_images')
    added = 0
    for file in files:
        if current_count + added >= MAX_HERO_IMAGES:
            flash(f'Only added {added} image(s) - the {MAX_HERO_IMAGES}-image limit was reached.', 'warning')
            break
        if file and file.filename and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            unique_filename = f"hero_{uuid.uuid4().hex}_{filename}"
            cloud_id = process_and_save_image(file, app.config['HERO_IMAGE_FOLDER'], unique_filename)
            db.execute('INSERT INTO hero_images (filename, sort_order, cloud_id) VALUES (?, ?, ?)',
                      (unique_filename, current_count + added, cloud_id))
            added += 1

    db.commit()
    if added:
        flash(f'Added {added} hero image(s). They rotate automatically on the homepage.', 'success')
    else:
        flash('No valid images were uploaded.', 'warning')
    return redirect(url_for('admin_panel'))


@app.route('/admin/hero/delete/<int:hero_id>', methods=['POST'])
@admin_required
def delete_hero_image(hero_id):
    db = get_db()
    row = db.execute('SELECT filename FROM hero_images WHERE id = ?', (hero_id,)).fetchone()
    if row:
        delete_image_variants(app.config['HERO_IMAGE_FOLDER'], row['filename'])
        db.execute('DELETE FROM hero_images WHERE id = ?', (hero_id,))
        db.commit()
        flash('Hero image removed.', 'info')
    return redirect(url_for('admin_panel'))


@app.route('/admin/category/add', methods=['POST'])
@admin_required
def add_category():
    name = request.form.get('name', '').strip()
    if not name:
        flash('Category name is required.', 'warning')
    else:
        db = get_db()
        existing = db.execute('SELECT id FROM categories WHERE name = ?', (name,)).fetchone()
        if existing:
            flash(f'"{name}" already exists.', 'warning')
        else:
            db.execute('INSERT INTO categories (name) VALUES (?)', (name,))
            db.commit()
            flash(f'Category "{name}" added.', 'success')
    return redirect(url_for('admin_panel'))


@app.route('/admin/category/delete/<int:category_id>', methods=['POST'])
@admin_required
def delete_category(category_id):
    db = get_db()
    db.execute('DELETE FROM product_categories WHERE category_id = ?', (category_id,))
    db.execute('DELETE FROM categories WHERE id = ?', (category_id,))
    db.commit()
    flash('Category removed.', 'info')
    return redirect(url_for('admin_panel'))


@app.route('/site-review', methods=['POST'])
def submit_site_review():
    rating = request.form.get('rating', type=int)
    comment = request.form.get('comment', '').strip() or None
    if not rating or rating < 1 or rating > 5:
        flash('Please choose a star rating between 1 and 5.', 'warning')
        return redirect(request.referrer or url_for('dashboard'))

    db = get_db()
    reviewer_name = session.get('username')
    db.execute('INSERT INTO site_reviews (user_id, reviewer_name, rating, comment) VALUES (?, ?, ?, ?)',
              (session.get('user_id'), reviewer_name, rating, comment))
    db.commit()
    flash('Thanks for rating the site!', 'success')
    return redirect(request.referrer or url_for('dashboard'))


@app.route('/admin/settings/update', methods=['POST'])
@admin_required
def update_settings():
    site_name = request.form.get('site_name', 'TechStore').strip()
    db = get_db()
    db.execute('UPDATE settings SET value = ? WHERE key = "site_name"', (site_name,))

    # Welcome / hero section text (left side of the homepage banner) and the
    # footer tagline - all editable here instead of being hardcoded.
    text_fields = (
        'site_tagline', 'hero_heading', 'hero_highlight', 'hero_subtitle', 'hero_cta_text',
        'features_title', 'feature1_title', 'feature1_text',
        'feature2_title', 'feature2_text', 'feature3_title', 'feature3_text',
    )
    for key in text_fields:
        if key in request.form:
            db.execute('UPDATE settings SET value = ? WHERE key = ?',
                       (request.form.get(key, '').strip(), key))

    # M-Pesa payment settings (Buy Goods / Till, or Paybill + Account)
    if 'payment_method' in request.form:
        payment_method = request.form.get('payment_method', 'till').strip()
        if payment_method not in ('till', 'paybill'):
            payment_method = 'till'
        db.execute('UPDATE settings SET value = ? WHERE key = "payment_method"', (payment_method,))
        for key in ('mpesa_till_number', 'mpesa_paybill_number', 'mpesa_account_number', 'mpesa_instructions'):
            db.execute('UPDATE settings SET value = ? WHERE key = ?',
                       (request.form.get(key, '').strip(), key))

    # Handle logo upload
    if 'site_logo' in request.files:
        file = request.files['site_logo']
        if file and file.filename and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            unique_filename = f"logo_{uuid.uuid4().hex}_{filename}"
            cloud_id = process_and_save_image(file, app.config['BRAND_FOLDER'], unique_filename)

            old_logo = db.execute('SELECT value FROM settings WHERE key = "site_logo"').fetchone()
            if old_logo and old_logo['value'] != 'default_logo.svg':
                delete_image_variants(app.config['BRAND_FOLDER'], old_logo['value'])

            db.execute('UPDATE settings SET value = ? WHERE key = "site_logo"', (unique_filename,))
            db.execute('UPDATE settings SET value = ? WHERE key = "site_logo_cloud_id"', (cloud_id or '',))

    # Store location pin (lat/lng), set from the admin panel and shown on
    # the homepage map.
    if 'store_lat' in request.form or 'store_lng' in request.form:
        for key in ('store_lat', 'store_lng'):
            raw = request.form.get(key, '').strip()
            try:
                float(raw)
                value = raw
            except ValueError:
                value = ''
            db.execute('UPDATE settings SET value = ? WHERE key = ?', (value, key))

    # Cloudinary keys (Image Cloud Backup card). Ignored if env vars already
    # lock the config, since the form is disabled in that case anyway. The
    # API secret field is left blank on page load for anyone who already
    # has one saved, so a blank submission means "keep the current secret"
    # rather than clearing it.
    if not CLOUDINARY_ENV_LOCKED and 'cloudinary_cloud_name' in request.form:
        db.execute('UPDATE settings SET value = ? WHERE key = "cloudinary_cloud_name"',
                   (request.form.get('cloudinary_cloud_name', '').strip(),))
        db.execute('UPDATE settings SET value = ? WHERE key = "cloudinary_api_key"',
                   (request.form.get('cloudinary_api_key', '').strip(),))
        new_secret = request.form.get('cloudinary_api_secret', '').strip()
        if new_secret:
            db.execute('UPDATE settings SET value = ? WHERE key = "cloudinary_api_secret"', (new_secret,))

    db.commit()
    if not CLOUDINARY_ENV_LOCKED:
        refresh_cloudinary_config(db)
    flash('Site settings updated successfully!', 'success')
    return redirect(url_for('admin_panel'))


@app.route('/admin/account/update', methods=['POST'])
@admin_required
def update_admin_account():
    """Lets the logged-in admin change their own login username and/or
    password from the Settings tab, instead of being stuck with whatever
    was seeded on first run. Always requires the current password, so a
    stolen/left-open session can't be used to silently take over the
    account with a new password."""
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()

    current_password = request.form.get('current_password', '')
    new_username = request.form.get('new_username', '').strip()
    new_password = request.form.get('new_password', '')
    confirm_password = request.form.get('confirm_password', '')

    if not user or not check_password_hash(user['password'], current_password):
        flash('Current password is incorrect. Account details were not changed.', 'danger')
        return redirect(url_for('admin_panel') + '#settings')

    changes_made = False

    # Username change (optional)
    if new_username and new_username != user['username']:
        existing = db.execute('SELECT id FROM users WHERE username = ? AND id != ?',
                               (new_username, user['id'])).fetchone()
        if existing:
            flash('That username is already taken. Try a different one.', 'danger')
            return redirect(url_for('admin_panel') + '#settings')
        db.execute('UPDATE users SET username = ? WHERE id = ?', (new_username, user['id']))
        session['username'] = new_username
        changes_made = True

    # Password change (optional)
    if new_password or confirm_password:
        if len(new_password) < 8:
            flash('New password must be at least 8 characters long.', 'danger')
            return redirect(url_for('admin_panel') + '#settings')
        if new_password != confirm_password:
            flash("New password and confirmation don't match.", 'danger')
            return redirect(url_for('admin_panel') + '#settings')
        db.execute('UPDATE users SET password = ? WHERE id = ?',
                   (generate_password_hash(new_password), user['id']))
        changes_made = True

    if changes_made:
        db.commit()
        flash('Admin login details updated successfully.', 'success')
    else:
        flash('No changes to save - fill in a new username and/or new password.', 'warning')

    return redirect(url_for('admin_panel') + '#settings')


@app.route('/admin/order/update/<int:order_id>', methods=['POST'])
@admin_required
def update_order(order_id):
    status = request.form['status']
    db = get_db()
    db.execute('UPDATE orders SET status = ? WHERE id = ?', (status, order_id))
    db.commit()
    flash('Order updated successfully.', 'success')
    return redirect(url_for('admin_panel'))


@app.route('/uploads/<filename>')
def uploaded_file(filename):
    # Filenames embed a uuid, so they're immutable - safe to cache hard.
    if os.path.exists(os.path.join(app.config['UPLOAD_FOLDER'], filename)):
        return send_from_directory(app.config['UPLOAD_FOLDER'], filename, max_age=31536000)
    # Local file missing (e.g. wiped on redeploy) - recover from Cloudinary.
    cloud_url = cloud_fallback_url('product_images', filename)
    if cloud_url:
        return redirect(cloud_url, code=302)
    abort(404)


@app.route('/hero/<filename>')
def hero_file(filename):
    if os.path.exists(os.path.join(app.config['HERO_IMAGE_FOLDER'], filename)):
        return send_from_directory(app.config['HERO_IMAGE_FOLDER'], filename, max_age=31536000)
    cloud_url = cloud_fallback_url('hero_images', filename)
    if cloud_url:
        return redirect(cloud_url, code=302)
    abort(404)


@app.route('/brand/<filename>')
def brand_file(filename):
    if os.path.exists(os.path.join(app.config['BRAND_FOLDER'], filename)):
        return send_from_directory(app.config['BRAND_FOLDER'], filename, max_age=31536000)
    if CLOUDINARY_ENABLED:
        row = get_db().execute('SELECT value FROM settings WHERE key = "site_logo_cloud_id"').fetchone()
        if row and row['value']:
            base_filename, size_key = strip_size_suffix(filename)
            return redirect(cloud_variant_url(row['value'], size_key), code=302)
    abort(404)











# ---------- RUN ----------
if __name__ == '__main__':
    init_db()
    app.run(debug=False, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
