# Render deployment

## Web Service
- Root Directory: leave empty
- Build Command: `pip install -r requirements.txt`
- Start Command: `gunicorn wsgi:app`

## PostgreSQL
Create a Render PostgreSQL database in the same region as the Web Service. In the Web Service environment variables, set `DATABASE_URL` to the database's **Internal Database URL**.

## Required environment variables
- `DATABASE_URL`
- `SECRET_KEY` (generate a strong value or let Render generate it)
- `ADMIN_USERNAME`
- `ADMIN_INITIAL_PASSWORD` (12+ characters)
- `CLOUDINARY_CLOUD_NAME`
- `CLOUDINARY_API_KEY`
- `CLOUDINARY_API_SECRET`
- `MIGRATE_SQLITE=true` for the first deployment only

## First deployment
The first start creates PostgreSQL tables, imports the bundled `ecommerce.db`, and then applies the secure admin credentials.

After you verify the site and admin panel, set `MIGRATE_SQLITE=false` or remove it and redeploy. Then remove `ecommerce.db` from GitHub because PostgreSQL is now the source of truth.

Do not add a persistent Render disk for the database; PostgreSQL handles persistence.
