"""Production WSGI entrypoint for Gunicorn/Render."""
import os
from app import app, USE_POSTGRES, init_postgres_db, bootstrap_admin_postgres, init_db

with app.app_context():
    if USE_POSTGRES and os.environ.get('MIGRATE_SQLITE','').lower() in ('1','true','yes'):
        # First deployment: create PG schema, import the bundled SQLite data,
        # then apply the production admin credentials.
        init_postgres_db(bootstrap_admin=False)
        from migrate_sqlite_to_postgres import migrate
        migrate()
        bootstrap_admin_postgres()
    else:
        init_db()
