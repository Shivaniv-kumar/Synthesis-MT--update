# Deploying on Vercel

This repository is configured as one Vercel Services project:

- The Vite frontend handles `/` and browser routes.
- The FastAPI backend handles `/api/*` and `/health*`.
- The frontend calls the backend through the same-origin `/api` path.

## Deploy from GitHub

1. Replace the files in your GitHub repository with this corrected version and push the changes.
2. In Vercel, open the project and ensure **Root Directory** is the repository root (the directory containing `vercel.json`).
3. Set **Framework Preset** to **Services** if Vercel does not select it automatically.
4. Add the backend variables from `backend/.env.example` under **Settings → Environment Variables**. Do not upload or commit a real `.env` file.
5. Redeploy the latest Git commit without reusing the failed build cache.

At minimum, production requires a valid `DATABASE_URL` and `SECRET_KEY`. Add provider credentials only for the features you use, such as `ANTHROPIC_API_KEY`, Redis, email, or object storage.

## Create the first test administrator

Add these Vercel environment variables before redeploying:

```text
ENVIRONMENT=production
ALLOW_PASSWORD_LOGIN=true
SEED_ADMIN=true
SEED_ADMIN_EMAIL=admin@example.com
SEED_ADMIN_PASSWORD=<a strong temporary password of at least 8 characters>
SEED_ADMIN_DISPLAY_NAME=Test Administrator
FRONTEND_URL=https://YOUR_DOMAIN
```

The backend creates or refreshes this administrator at startup, even if other
pending users already exist. After signing in, use **Settings → Users** to
create permanent users or send invitations. Then set `SEED_ADMIN=false` and
redeploy so the temporary password is no longer reapplied on every cold start.

Invitations require either `SENDGRID_API_KEY` or working SMTP variables. The
direct **Create User** option does not require email delivery because the Admin
sets the initial password in the form.

After deployment, verify:

- `https://YOUR_DOMAIN/health`
- `https://YOUR_DOMAIN/`

If the health endpoint reports a database error, the Vercel build is working and the remaining issue is the database connection or environment-variable configuration.
