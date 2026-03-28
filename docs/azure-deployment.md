# Deploy S004 on Azure App Service (code / Web App — no Docker)

This guide uses **two Linux Web Apps**: one for the **FastAPI** backend and one for the **Next.js** frontend. No container registry is required.

How traffic flows:

1. Users open the **frontend** URL (e.g. `https://s004-ui.azurewebsites.net`).
2. The browser calls **`/api/...` on the same host** (see `frontend/src/lib/api_client.ts`).
3. **Next.js rewrites** (`next.config.mjs`) proxy `/api/*` to the **backend** base URL from **`NEXT_PUBLIC_API_URL`** (set at **build** time on the frontend app).

So you must set **`NEXT_PUBLIC_API_URL`** to the **public https URL of the API Web App** (no trailing slash) before building the frontend.

---

## 1. API Web App (Python)

1. Create **Web App** → **Linux** → runtime **Python 3.12** (or supported 3.11+). **Do not use Python 3.13** for this stack: `pydantic==2.7.1` / `pydantic-core` use a Rust extension whose PyO3 version does not support 3.13, and Oryx will fail building wheels (`PyO3's maximum supported version (3.12)`). The repo includes **`runtime.txt`** (`python-3.12`) next to `requirements.txt` so Oryx targets 3.12 when it honors that file; you should still set the app stack to **3.12** in the portal (**Configuration → General settings** / stack) so the runtime matches.
2. Deploy the **`backend/`** folder contents as the app root (ZIP, GitHub Action, or VS Code Azure extension so that `app/`, `requirements.txt`, `runtime.txt`, and `startup.sh` sit at the site root, e.g. `/home/site/wwwroot`).
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

   If you deploy **only** the standalone folder as the site root (see [section 6](#6-opting-out-of-oryx-optional) and `azure-webapps-deploy-no-oryx.yml`), use:

   ```bash
   node server.js
   ```

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

- **GitHub Actions**: deploy `backend/` to the API Web App and `frontend/` to the UI Web App (`Azure/webapps-deploy` with a `.zip` `package` path), or use **Deployment Center** / matrix jobs as you prefer.
- **Deployment Center**: connect the repo twice (different Web Apps) or use monorepo build steps with different `app-path` outputs.
- **Without Oryx**: use the sample workflow [`.github/workflows/azure-webapps-deploy-no-oryx.yml`](../.github/workflows/azure-webapps-deploy-no-oryx.yml) (`workflow_dispatch`) or the patterns in [Azure actions-workflow-samples — App Service](https://github.com/Azure/actions-workflow-samples/tree/master/AppService).

### Secrets for `.github/workflows/main_s004.yml` (API deploy on push to `main`)

| Secret | Value |
|--------|--------|
| `AZURE_WEBAPP_API_NAME` | App Service **Name** from Portal → **Overview** (exact string). |
| `AZURE_WEBAPP_API_PUBLISH_PROFILE` | Entire contents of **Get publish profile** (`.PublishSettings` XML). Same secret name as in `azure-webapps-deploy-no-oryx.yml`. |

### GitHub deploy: `ENOTFOUND` / `getaddrinfo ENOTFOUND` on `*.scm.*.azurewebsites.net`

The deploy action talks to **Kudu/SCM** at **`https://<app-name>.scm.azurewebsites.net`**. If your publish profile or tooling produced a URL like **`<app>.scm.southindia-01.azurewebsites.net`**, that hostname is **not valid** for public DNS and the runner will fail with **`ENOTFOUND`**.

**Fix:** In Azure Portal → your Web App → **Get publish profile** → download the `.PublishSettings` file → copy the **entire** XML into **`AZURE_WEBAPP_API_PUBLISH_PROFILE`**. In that XML, **`publishUrl`** for the MSDeploy profile should end with **`.scm.azurewebsites.net`** (no region segment such as `southindia-01` before `azurewebsites.net`).

Set **`AZURE_WEBAPP_API_NAME`** to the same **name** as on the **Overview** tab. If you see **Failed to get app runtime OS**, the name or publish profile usually does not match the app; fix both as above.

---

## 6. Opting out of Oryx (optional)

If you prefer **not** to run the Oryx build on the server during deployment:

1. **Remove** the **`SCM_DO_BUILD_DURING_DEPLOYMENT`** app setting from both Web Apps (or do not add it). When it is absent or `false`, Azure will not run `pip install` / `npm install` + build on deploy.
2. You must ship **pre-built** artifacts instead (for example from GitHub Actions on **Ubuntu**, which matches App Service Linux for native wheels):
   - **API**: run `pip install -r requirements.txt --target .python_packages` in CI, zip the `backend/` tree including `.python_packages`. This repo’s **`startup.sh`** prepends `.python_packages` to **`PYTHONPATH`** when that folder exists.
   - **Frontend**: run `npm ci` and `npm run build` in CI with **`NEXT_PUBLIC_API_URL`** set, then zip the **Next.js standalone** output (copy `public/` and `.next/static` into `.next/standalone` per Next’s standalone deploy docs) and deploy that zip. Startup: `node server.js` from the zip root (same as a standalone folder layout).

See [Azure actions-workflow-samples — App Service](https://github.com/Azure/actions-workflow-samples/tree/master/AppService) for more deployment strategies (ZIP, slots, etc.).

---

## 7. Optional: single Web App via Docker later

If you later want **one URL** and **one** Web App without split rewrites, you can use the repo **`Dockerfile`** (nginx + Next + API) and switch that Web App to **Container** deployment. The files under `deploy/` and `Dockerfile` are for that path only.

---

## 8. App changes already in the repo

- **`CORS_ORIGINS`** env on the API (`backend/app/main.py`).
- **`NEXT_PUBLIC_API_URL`** for Next rewrites (`frontend/next.config.mjs`).
- **`backend/startup.sh`** + **`gunicorn`** in `requirements.txt` / `requirements-prod.txt` for App Service–style process binding.
