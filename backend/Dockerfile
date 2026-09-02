# Image backend production (API + worker Celery + scraping Playwright)
FROM python:3.12-slim

WORKDIR /srv

# dépendances Python (app + prod)
COPY requirements.txt requirements-prod.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-prod.txt \
    && playwright install --with-deps chromium

# code applicatif
COPY app ./app

# frontend compilé : copié dans l'image par le pipeline CI
# (contexte de build élargi) ou monté en volume :
#   docker run -v $(pwd)/frontend/dist:/srv/static_frontend …
RUN mkdir -p ./static_frontend
COPY static_frontend ./static_frontend 2>/dev/null || true

ENV FRONTEND_DIST=/srv/static_frontend \
    PYTHONUNBUFFERED=1 \
    APP_TZ=Indian/Antananarivo

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
