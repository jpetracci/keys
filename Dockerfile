# syntax=docker/dockerfile:1
#
# One image for the whole app: Django (gunicorn) serves the API and the built
# React frontend from the same origin, so no CORS setup is involved.
#
#   docker build -t keys .
#   docker run -p 8000:8000 -e SECRET_KEY=... -v keys-data:/data keys

# ---- Frontend: build the static site ---------------------------------------
FROM node:24-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- Backend: Django + gunicorn ---------------------------------------------
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore
WORKDIR /app

# curl serves the health check below, and Coolify's own HTTP health check
# (if someone enables it) only works when the image has curl or wget.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# requirements.txt names what the app needs; requirements.lock holds the exact
# version of every package, so the same commit always builds the same image.
COPY backend/requirements.txt backend/requirements.lock ./
RUN pip install -r requirements.txt -c requirements.lock

COPY backend/ ./
COPY --from=frontend /frontend/dist ./frontend_dist

# FRONTEND_DIST makes Django serve the built site. The SQLite file lives in
# /data, which must be a mounted volume to survive a redeploy (PostgreSQL via
# DATABASE_URL needs no volume). PORT is the port gunicorn listens on; Coolify
# sets it to the first port in the application's "Ports Exposes".
ENV FRONTEND_DIST=/app/frontend_dist \
    SQLITE_NAME=/data/keys.sqlite3 \
    PORT=8000

# The key below is only for this build step: collectstatic has to load the
# settings, and nothing it writes depends on the key.
# The app runs as an ordinary user (id 1000) that owns only /data and
# /statements; gunicorn keeps a control socket in that user's home directory.
RUN SECRET_KEY=used-only-by-collectstatic python manage.py collectstatic --noinput \
    && python -m whitenoise.compress frontend_dist \
    && useradd --uid 1000 --user-group --create-home app \
    && mkdir /data /statements \
    && chown app:app /data /statements
USER app

EXPOSE 8000
# A deploy only goes live once this passes: it needs the migrations in the
# entrypoint to have succeeded and the database to answer.
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT:-8000}/api/health/" > /dev/null || exit 1

ENTRYPOINT ["sh", "/app/docker-entrypoint.sh"]
