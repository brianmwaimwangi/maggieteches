"""One-time migration of the existing ecommerce.db into Render PostgreSQL.
Set DATABASE_URL to the Render Internal Database URL and run this once.
"""
import os, sqlite3
from pathlib import Path
import psycopg2
from psycopg2.extras import execute_values

SQLITE_PATH = Path(os.environ.get('SQLITE_SOURCE', Path(__file__).resolve().parent / 'ecommerce.db'))
DATABASE_URL = os.environ.get('DATABASE_URL', '').strip()
if not DATABASE_URL:
    raise RuntimeError('DATABASE_URL is required')
if not SQLITE_PATH.exists():
    raise RuntimeError(f'SQLite source not found: {SQLITE_PATH}')

TABLES = {
    'users': ['id','username','password','is_admin'],
    'products': ['id','name','original_price','price','description','category','created_at'],
    'product_images': ['id','product_id','filename','is_primary','sort_order','cloud_id'],
    'product_variants': ['id','product_id','size','color','sku','stock','price_override'],
    'orders': ['id','user_id','product_ids','total','status','order_date','guest_name','guest_email','shipping_address','guest_token','mpesa_code'],
    'order_items': ['id','order_id','product_id','variant_id','product_name','variant_label','qty','price'],
    'hero_images': ['id','filename','sort_order','cloud_id','created_at'],
    'categories': ['id','name'],
    'product_categories': ['product_id','category_id'],
    'site_reviews': ['id','user_id','reviewer_name','rating','comment','created_at'],
    'settings': ['key','value'],
    'admin_messages': ['id','order_id','message','is_read','created_at'],
}
ORDER=['users','products','categories','product_images','product_variants','orders','order_items','product_categories','site_reviews','settings','admin_messages','hero_images']

def migrate():
    src=sqlite3.connect(str(SQLITE_PATH)); src.row_factory=sqlite3.Row
    pg=psycopg2.connect(DATABASE_URL)
    cur=pg.cursor()
    # Schema creation is imported from app without bootstrapping an admin.
    import sys
    sys.path.insert(0, str(SQLITE_PATH.parent))
    from app import app, init_postgres_db
    with app.app_context():
        init_postgres_db(bootstrap_admin=False)
    for table in ORDER:
        cols=TABLES[table]
        rows=src.execute(f'SELECT {", ".join(cols)} FROM {table}').fetchall()
        if not rows:
            continue
        values=[tuple(r[c] for c in cols) for r in rows]
        col_sql=', '.join(cols)
        placeholders='(' + ','.join(['%s']*len(cols)) + ')'
        conflict='ON CONFLICT DO NOTHING'
        sql=f'INSERT INTO {table} ({col_sql}) VALUES {placeholders} {conflict}'
        cur.executemany(sql, values)
        print(f'{table}: {len(rows)} rows')
    pg.commit()
    # Reset identity sequences to the imported maximum IDs.
    for table,cols in TABLES.items():
        if 'id' not in cols or table=='product_categories': continue
        cur.execute("SELECT setval(pg_get_serial_sequence(%s, 'id'), COALESCE(MAX(id), 1), COUNT(*) > 0) FROM " + table, (table,))
    pg.commit()
    cur.close(); pg.close(); src.close()
    print('Migration complete.')

if __name__ == '__main__': migrate()
