# The stylist API. Build from the repo root: docker compose build (or
#   docker build -f backend/Dockerfile .)
# No PyTorch: STYLIST_NO_CLIP=1 keeps the image small, and outfits are scored without the CLIP
# relevance term, as with cli.py --no-clip.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STYLIST_NO_CLIP=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/src backend/src
# The committed data, so the image can build its own database. Compose mounts ./data over it.
COPY data/seed_catalog.csv data/clip_tags.json data/llm_vibes.json data/
COPY backend/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://localhost:{os.environ.get(\"PORT\", \"8000\")}/api/v1/health', timeout=2)"
ENTRYPOINT ["docker-entrypoint.sh"]
# PORT is set by hosts like Render; 8000 otherwise. The client IP for the chat limit comes from
# STYLIST_CLIENT_IP_HEADER (see api/app.py), not from uvicorn's proxy handling.
CMD ["sh", "-c", "exec uvicorn --factory --app-dir backend/src stylist.api.app:create_app --host 0.0.0.0 --port ${PORT:-8000}"]
