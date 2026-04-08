# Azure: one Web App with Docker (Next.js + FastAPI)

This path runs **frontend and API in one container**: **nginx** on port **8080** routes `/` to Next.js and `/api/` to FastAPI. It matches the repo root **`Dockerfile`** and files under **`deploy/`**.

---

## 1. Azure resources

1. **Azure Container Registry (ACR)**  
   Create a registry and enable **Admin user** (simplest for GitHub Actions) or use a service principal with **AcrPush**.

2. **Web App (Linux, container)**  
   - Create **Web App** → **Linux** → **Container** (not “Python” or “Node” code stack).  
   - Pick your ACR image (any initial tag; CI will update it).  
   - Configure registry credentials in the Web App if ACR is private.

---

## 2. Application settings (Web App → Configuration)

| Name | Value | Notes |
|------|--------|--------|
| **`WEBSITES_PORT`** | **`8080`** | Required: nginx listens on 8080 in this image. |
| **`DATABASE_URL`** | your PostgreSQL URL | Same as local `backend/.env`. |
| **`CORS_ORIGINS`** | `https://<your-app>.azurewebsites.net` | Single origin for this host. |
| Other | from `backend/.env` | e.g. `REDIS_URL`, Kite keys, etc. |

Do **not** set a **Startup Command** in the portal for this image: **supervisord** starts nginx, Node, and uvicorn (`Dockerfile` **CMD**).

**Optional:** For Linux, before downloading **Get publish profile**, set **`WEBSITE_WEBDEPLOY_USE_SCM`** = **`true`** on the Web App (Microsoft’s note for publish-profile auth).

---

## 3. GitHub Actions secrets

Create these under **Settings → Secrets and variables → Actions**:

| Secret | Value |
|--------|--------|
| **`ACR_LOGIN_SERVER`** | e.g. `myregistry.azurecr.io` |
| **`ACR_USERNAME`** | ACR admin username |
| **`ACR_PASSWORD`** | ACR admin password |
| **`AZURE_WEBAPP_NAME`** | Web App **Name** from **Overview** |
| **`AZURE_WEBAPP_PUBLISH_PROFILE`** | Entire **Get publish profile** XML |

Workflow file: **`.github/workflows/main_a004.yml`** — builds from repo root, pushes **`s004-app`** to ACR, deploys the **`${{ github.sha }}`** tag to the Web App.

---

## 4. First-time / manual test

From the **repository root**:

```bash
docker build -t s004-app:local .
docker run --rm -p 8080:8080 --env-file backend/.env s004-app:local
```

Open `http://localhost:8080`. API: `http://localhost:8080/api/health`.

---

## 5. Compared to “two code Web Apps”

| Topic | Two code apps | This Docker app |
|--------|----------------|-----------------|
| Web Apps | 2 (Python + Node) | 1 (container) |
| Stack in portal | Python + Node | Docker / custom image |
| Startup command | `bash startup.sh` + `node …` | *(none — use image CMD)* |
| `NEXT_PUBLIC_API_URL` for split UI | Set to API URL | Not required for browser; nginx serves `/api` on same host |

See also **`docs/azure-deployment.md`** for database and general Azure notes.
