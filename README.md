# E-Commerce Website (Flask + Bootstrap 5 + PostgreSQL (Render) / SQLite (local development))

## Structure
```
app.py                  Flask backend (routes, DB, auth)
templates/               Jinja2 HTML templates (one file per page)
  dashboard.html
  shop.html
  product_detail.html
  login.html
  register.html
  cart.html
  orders.html
  edit_product.html
  admin.html
static/
  uploads/               Product images (uploaded at runtime)
  hero/                  Hero banner image
  brand/                 Site logo
```

## Run
```
pip install -r requirements.txt
python app.py
```
Visit http://localhost:5000

## Admin login
- Production admin credentials are supplied through Render environment variables: `ADMIN_USERNAME` and `ADMIN_INITIAL_PASSWORD`.
- `ADMIN_INITIAL_PASSWORD` must be at least 12 characters.

## Notes on fixes already applied
- `db` and `cart` are now passed into template context (dashboard, shop, product page)
  so pages no longer crash for logged-in users or once products exist.
- `admin.html` panel also receives `db` for per-product image lookups.
- Deleting a product now also removes its `product_images` rows (previously orphaned).
- Deleting a missing image no longer throws an error.


## Production database
Render production uses PostgreSQL via `DATABASE_URL`. The bundled `ecommerce.db` is only a one-time migration source and should be removed from GitHub after the first successful migration. See `POSTGRES_RENDER.md`.
