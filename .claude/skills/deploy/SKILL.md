---
name: deploy
description: Build production Docker images and push to Digital Ocean container registry. Watchtower on the server auto-pulls new images within 5 minutes.
---

# Deploy to Production

## Overview

Builds production Docker images for backend and frontend, then pushes them to the Digital Ocean container registry. Watchtower running on the production server automatically detects new images and restarts containers.

## Registry

- Registry: `registry.digitalocean.com/wanderindev/`
- Backend image: `pic-backend:latest`
- Frontend image: `pic-frontend:latest`
- Cert-watcher image: `pic-cert-watcher:latest`

## Production Dockerfiles

| Service | Dockerfile | Base | Build context |
|---------|-----------|------|---------------|
| Backend | `docker/backend/Dockerfile.prod` | python:3.11-slim + uvicorn | repo root |
| Frontend | `docker/frontend/Dockerfile.prod` | node:20-alpine (build) → nginx:alpine (serve) | repo root |
| Cert-watcher | `docker/cert-watcher/Dockerfile` | python:3.12-slim | `docker/cert-watcher/` |

## Procedure

### 1. Verify code is committed

Before deploying, ensure all changes are committed. Uncommitted changes will still be included in the Docker build context but this can cause confusion.

### 2. Build images

Build both images in parallel from the repo root (`/home/javier/vc/panama-in-context`):

```bash
# Backend
docker build -f docker/backend/Dockerfile.prod -t registry.digitalocean.com/wanderindev/pic-backend:latest .

# Frontend (bakes in VITE_API_URL and VITE_SENTRY_DSN, both defaulted to the
# prod values in Dockerfile.prod — no extra env setup needed).
docker build -f docker/frontend/Dockerfile.prod -t registry.digitalocean.com/wanderindev/pic-frontend:latest .

# Cert-watcher (uses its own sub-dir as context)
docker build -t registry.digitalocean.com/wanderindev/pic-cert-watcher:latest docker/cert-watcher
```

Run these in parallel using background tasks for speed.

### 3. Push images

Push images to the Digital Ocean registry in parallel:

```bash
docker push registry.digitalocean.com/wanderindev/pic-backend:latest
docker push registry.digitalocean.com/wanderindev/pic-frontend:latest
docker push registry.digitalocean.com/wanderindev/pic-cert-watcher:latest
```

### 4. Wait for Watchtower

Watchtower checks for new images every 300 seconds (5 minutes). No manual action needed on the server. Confirm deployment by checking the site.

## Selective Deployment

The user may request deploying only one service:

- `/deploy backend` — build and push only `pic-backend`
- `/deploy frontend` — build and push only `pic-frontend`
- `/deploy cert-watcher` — build and push only `pic-cert-watcher`
- `/deploy` (no argument) — build and push all three

## Troubleshooting

### Authentication issues
If push fails with auth errors, re-authenticate:
```bash
doctl registry login
```

### Build failures
- Backend: check `backend/requirements.txt` for missing packages
- Frontend: check `npm ci` output and `npm run build` for errors

### Verifying deployment
Check the production health endpoint after Watchtower cycles:
```bash
curl https://api.panamaincontext.com/api/v1/health
```