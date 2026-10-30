#!/bin/sh
# Build data/catalog.db on first start from the committed files (the seed CSV and the CLIP and LLM
# tag snapshots), then run the API. An existing database, like the one ./data mounts in, is left alone.
set -e

if [ ! -f data/catalog.db ]; then
    echo "No data/catalog.db yet: building it from data/seed_catalog.csv."
    python backend/src/stylist/jobs/ingest.py
    python backend/src/stylist/jobs/restore_tags.py
    python backend/src/stylist/jobs/llm_tag.py
fi

exec "$@"
