# Single image: nginx (8080) → Next.js standalone (3000) + FastAPI (8000)
# Build from repo root:  docker build -t s004-app .
# Run locally:            docker run --env-file backend/.env -p 8080:8080 s004-app

# -----------------------------------------------------------------------------
FROM node:20-bookworm-slim AS frontend-build
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# -----------------------------------------------------------------------------
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    nginx \
    supervisor \
    ca-certificates \
 && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements-prod.txt /app/backend/requirements-prod.txt
RUN pip install --no-cache-dir -r /app/backend/requirements-prod.txt

COPY backend /app/backend

COPY --from=frontend-build /src/frontend/.next/standalone /app/frontend/.next/standalone
COPY --from=frontend-build /src/frontend/.next/static /app/frontend/.next/standalone/.next/static
COPY --from=frontend-build /src/frontend/public /app/frontend/.next/standalone/public

RUN rm -f /etc/nginx/sites-enabled/default \
 && rm -f /etc/nginx/sites-available/default
COPY deploy/nginx.default.conf /etc/nginx/sites-available/s004.conf
RUN ln -s /etc/nginx/sites-available/s004.conf /etc/nginx/sites-enabled/s004.conf

COPY deploy/supervisord.conf /etc/supervisor/conf.d/s004.conf

EXPOSE 8080

# -n: stay in foreground so the container keeps running (PID 1).
CMD ["/usr/bin/supervisord", "-n", "-c", "/etc/supervisor/supervisord.conf"]
