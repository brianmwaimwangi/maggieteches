# Render admin security

Set these Render environment variables before the first public deployment:

- `SECRET_KEY`: a long random secret.
- `ADMIN_USERNAME`: your real production admin username.
- `ADMIN_INITIAL_PASSWORD`: a unique password of at least 12 characters.
- `DB_PATH`: `/data/ecommerce.db` (already configured by `render.yaml`).

The application does **not** seed `admin@special! / admin123` anymore.

If the bundled database has no admin account, the supplied admin credentials are used to create one. If the bundled database still has the old default admin, the application migrates that legacy account to the supplied username/password once. If the old default credentials are detected without replacement credentials, the Render process refuses to start instead of exposing the default account publicly.

After the first successful deployment and migration, remove `ADMIN_INITIAL_PASSWORD` from Render. The existing admin password remains stored as a password hash in SQLite; removing the variable prevents accidental credential changes later.

The Admin Panel also allows the logged-in admin to change their username/password. Password changes require the current password. Admin passwords are hashed with Werkzeug's password hashing functions.

Flask sessions are configured with HttpOnly and SameSite=Lax cookies, and Secure cookies when running on Render.
