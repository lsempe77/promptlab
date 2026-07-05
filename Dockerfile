# Read-only observability API for the DEP prompt lab, packaged for Fly.io.
#
# This image contains ONLY the FastAPI app code (backend/app, backend/scripts
# for maintenance tasks, backend/models.yaml). It does NOT bake in the
# production database or corpus .md files -- those live on a persistent Fly
# volume mounted at /data, so the image can be rebuilt/redeployed without
# re-uploading ~50MB of data every time.
#
# The server is read-only (no OpenRouter calls happen here), so no API key
# secret is required for this deployment.
FROM python:3.12-slim

WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/app backend/app
COPY backend/scripts backend/scripts
COPY backend/models.yaml backend/models.yaml
COPY backend/__init__.py backend/__init__.py

ENV DEP_DB_PATH=/data/promptlab.db
ENV DEP_MD_DIR=/data/corpus

EXPOSE 8080

CMD ["uvicorn", "backend.app.api:app", "--host", "0.0.0.0", "--port", "8080"]
