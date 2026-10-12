# 🚀 EcoCampus Deployment Guide

This project is fully structured and prepared for production deployment using WSGI servers (Gunicorn, Waitress) and containerization (Docker).

---

## 📁 Deployment Files Included

| File | Purpose |
|------|---------|
| [Procfile](file:///d:/kaushal/ecocampus_mvp/Procfile) | Defines web worker command for Render, Railway, Heroku |
| [Dockerfile](file:///d:/kaushal/ecocampus_mvp/Dockerfile) | Production container build with Gunicorn and healthcheck |
| [.dockerignore](file:///d:/kaushal/ecocampus_mvp/.dockerignore) | Excludes caches and local files from container image |
| [render.yaml](file:///d:/kaushal/ecocampus_mvp/render.yaml) | Blueprint for 1-click automatic deployment on Render |
| [wsgi.py](file:///d:/kaushal/ecocampus_mvp/wsgi.py) | WSGI server application entry point |
| [.env.example](file:///d:/kaushal/ecocampus_mvp/.env.example) | Template for environment variable configurations |
| [requirements.txt](file:///d:/kaushal/ecocampus_mvp/requirements.txt) | Python dependencies including Gunicorn & Waitress |

---

## Option 1: Deploy to Render (Recommended — Free & Easy)

1. Push your code to a GitHub or GitLab repository.
2. Sign in to [Render.com](https://render.com).
3. Click **New +** → **Web Service**.
4. Connect your repository.
5. Set the settings:
   - **Environment**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn wsgi:app --bind 0.0.0.0:$PORT --workers 2 --threads 4`
6. Click **Create Web Service**.
7. Render will automatically build, deploy, and assign a public `https://...onrender.com` URL.

---

## Option 2: Deploy with Docker

To build and run the Docker container locally or on any cloud server:

```bash
# 1. Build the docker image
docker build -t ecocampus:latest .

# 2. Run the container (mapping port 5501)
docker run -d -p 5501:5501 --name ecocampus-app ecocampus:latest
```

Open `http://localhost:5501` in your browser.

To persist the database across container restarts:
```bash
docker run -d -p 5501:5501 -v ecocampus_data:/app ecocampus:latest
```

---

## Option 3: Deploy to Railway

1. Sign in to [Railway.app](https://railway.app).
2. Click **New Project** → **Deploy from GitHub repo**.
3. Railway automatically detects the [Procfile](file:///d:/kaushal/ecocampus_mvp/Procfile) and [requirements.txt](file:///d:/kaushal/ecocampus_mvp/requirements.txt).
4. In the settings, click **Generate Domain** to get your public live URL.

---

## Option 4: Production Run on Windows (using Waitress)

On a Windows host or server, run with the production multi-threaded WSGI server `waitress`:

```powershell
pip install -r requirements.txt
python -m waitress --listen=0.0.0.0:5501 wsgi:app
```

---

## ⚙️ Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `5501` | Port the web service binds to (PaaS platforms inject this automatically) |
| `HOST` | `0.0.0.0` | Host IP binding |
| `FLASK_DEBUG` | `0` | Set `0` for production, `1` for development |
| `DATABASE_PATH` | `ecocampus.db` | Custom location for the SQLite database |
| `CORS_ORIGINS` | `*` | Allowed CORS origins for external API access |
| `BOOTSTRAP_ADMIN_USERNAME` | unset | Username for the first administrator account |
| `BOOTSTRAP_ADMIN_PASSWORD` | unset | Bootstrap administrator password; 1-8 characters |
| `SESSION_SECRET_KEY` | DB-generated | Stable session-signing secret; set explicitly for multiple application instances |
| `SESSION_COOKIE_SECURE` | `0` | Set to `1` when serving over HTTPS |

Before starting the service, set `BOOTSTRAP_ADMIN_USERNAME` and `BOOTSTRAP_ADMIN_PASSWORD` in the deployment environment to create the first administrator. New passwords must be 1-8 characters; this short password limit is less secure, so use it only on a trusted/private deployment. Existing accounts with longer passwords can still sign in. Store credentials outside source control; there is no default administrator password. Open `/login` to register standard user accounts. Administrators can assign department managers in **Users & Roles**. If bootstrap credentials are omitted, registration still works but no account has administrator privileges. The IoT simulator requires an administrator session; no API keys are used. For multi-instance deployments, persist the shared database and configure the same `SESSION_SECRET_KEY` on every instance.
