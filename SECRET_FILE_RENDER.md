# Render Secret File setup

This project can load `SECRET_KEY` from a Render Secret File, so you do not need to commit the secret to GitHub.

## In Render

1. Open your **Web Service**.
2. Open **Environment**.
3. Find **Secret Files**.
4. Add a secret file named exactly:

```text
SECRET_KEY
```

5. Put a long random secret value in the file. The file should contain only the secret value — **do not** put `SECRET_KEY=` in it.
6. Save the secret file.
7. Redeploy the latest commit.

The application automatically reads `/etc/secrets/SECRET_KEY`. If you choose a different mounted path, set the `SECRET_KEY_FILE` environment variable to that path.

## Important

- Never commit the real secret file to GitHub.
- Do not upload a real `.env` file containing production secrets.
- `DATABASE_URL`, Cloudinary credentials, and admin bootstrap credentials remain Render environment variables.
- After the first successful PostgreSQL migration and admin login, remove `ADMIN_INITIAL_PASSWORD` from Render if it is no longer needed.
