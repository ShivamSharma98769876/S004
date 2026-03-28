# Deploy S004 on Azure App Service (code / Web App — no Docker)

This guide uses **two Linux Web Apps**: one for the **FastAPI** backend and one for the **Next.js** frontend. No container registry is required.

How traffic flows:

1. Users open the **frontend** URL (e.g. `https://s004-ui.azurewebsites.net`).
2. The browser calls **`/api/...` on the same host** (see `frontend/src/lib/api_client.ts`).
3. **Next.js rewrites** (`next.config.mjs`) proxy `/api/*` to the **backend** base URL from **`NEXT_PUBLIC_API_URL`** (set at **build** time on the frontend app).

So you must set **`NEXT_PUBLIC_API_URL`** to the **public https URL of the API Web App** (no trailing slash) before building the frontend.

---

## 1. API Web App (Python)

1. Create **Web App** → **Linux** → runtime **Python 3.12** (or supported 3.11+).
2. Deploy the **`backend/`** folder contents as the app root (ZIP, GitHub Action, or VS Code Azure extension so that `app/`, `requirements.txt`, and `startup.sh` sit at the site root, e.g. `/home/site/wwwroot`).
3. **Configuration → General settings → Startup Command**:

   ```bash
   bash startup.sh
   ```

   Or inline:

   ```bash
   gunicorn -w 1 -k uvicorn.workers.UvicornWorker app.main:app --bind 0.0.0.0:8000 --timeout 120
   ```

   If your plan injects **`PORT`**, prefer `startup.sh`, which uses **`${PORT:-8000}`**.

4. **Application settings** (examples):

   | Name | Example |
   |------|---------|
   | `DATABASE_URL` | PostgreSQL connection string |
   | `CORS_ORIGINS` | `https://<your-frontend-app>.azurewebsites.net` |
   | `REDIS_URL` | Optional |
   | `SCM_DO_BUILD_DURING_DEPLOYMENT` | `true` (so Oryx runs `pip install -r requirements.txt`) |
   | Plus | Copy the rest from your local `backend/.env` |

5. **Health**: `https://<api-app>.azurewebsites.net/api/health`

6. Enable **Always On** if you rely on background loops (auto-execute, position monitor).

---

## 2. Frontend Web App (Node)

1. Create **Web App** → **Linux** → runtime **Node 20 LTS** (or 18+).
2. **Application settings** → add **before first build / deploy**:

   | Name | Value |
   |------|--------|
   | `NEXT_PUBLIC_API_URL` | `https://<api-app>.azurewebsites.net` |
   | `SCM_DO_BUILD_DURING_DEPLOYMENT` | `true` |

3. Deploy the **`frontend/`** folder (so `package.json` is at the site root). Oryx will run `npm install` and `npm run build`.

4. **Startup Command** (Next **standalone** output, matches this repo’s `output: "standalone"`):

   ```bash
   node .next/standalone/server.js
   ```

   Set **Working directory** if the portal offers it to the folder that contains `.next/standalone/server.js` (usually repo `frontend` root after build). If the built files are at site root, the command above is correct.

   **Alternative** (simpler, no standalone): remove `output: "standalone"` from `next.config.mjs` and use:

   ```bash
   npm start
   ```

5. **Health**: open the frontend URL; login and API calls should hit the API via rewrites.

---

## 3. CORS

The UI talks to the API **from the Next server** (rewrite), not from the browser’s origin to the API domain, so **browser CORS** is often unnecessary. Still set **`CORS_ORIGINS`** on the API to your **frontend https URL** for direct API calls (tools, mobile, Postman from browser extensions, etc.).

---

## 4. Database and migrations

Provision **Azure Database for PostgreSQL** (Flexible Server). Run your schema/seed (e.g. `python scripts/apply_db_schema.py` or SQL migrations) from a secure machine or pipeline **against** the Azure database before going live.

---

## 5. CI/CD (optional)

- **GitHub Actions**: two jobs or matrix — deploy `backend/` to the API app and `frontend/` to the UI app (`azure/webapps-deploy` with `package` path).
- **Deployment Center**: connect the repo twice (different Web Apps) or use monorepo build steps with different `app-path` outputs.

---

## 6. Optional: single Web App via Docker later

If you later want **one URL** and **one** Web App without split rewrites, you can use the repo **`Dockerfile`** (nginx + Next + API) and switch that Web App to **Container** deployment. The files under `deploy/` and `Dockerfile` are for that path only.

---

## App changes already in the repo

- **`CORS_ORIGINS`** env on the API (`backend/app/main.py`).
- **`NEXT_PUBLIC_API_URL`** for Next rewrites (`frontend/next.config.mjs`).
- **`backend/startup.sh`** + **`gunicorn`** in `requirements.txt` / `requirements-prod.txt` for App Service–style process binding.
