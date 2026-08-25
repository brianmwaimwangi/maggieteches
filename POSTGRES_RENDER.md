# Render PostgreSQL deployment

1. Create a Render PostgreSQL database in the same region as the Web Service.
2. In the Web Service, add `DATABASE_URL` using the PostgreSQL **Internal Database URL**.
3. Keep `MIGRATE_SQLITE=true` for the first deployment only. It imports the bundled `ecommerce.db` into PostgreSQL and applies the secure admin credentials.
4. Deploy and verify products, users, orders, settings, reviews and admin login.
5. After verification, set `MIGRATE_SQLITE=false` (or remove the variable) and redeploy.
6. Remove `ecommerce.db` from Git/GitHub after the migration has been verified. It is only a one-time migration source.
7. Do not add a Render persistent disk for the database; PostgreSQL is the persistent database.
