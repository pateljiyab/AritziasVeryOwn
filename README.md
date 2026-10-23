# Aritzia's Very Own: AI Outfit Stylist

A conversational outfit stylist built on Aritzia's product catalog. You describe a mood, an occasion, or the weather, and it puts together a complete outfit from real, named Aritzia pieces. You keep steering it (swap a piece, lock one in, make it dressier) until it's something you'd actually wear.

Under the hood: LLM-tagged catalog in SQLite, CLIP embeddings for retrieval and relevance feedback, rule-based outfit compatibility scoring, and an LLM parser that turns free text into structured constraints. Served as a FastAPI REST API with a React + TypeScript client, tested with pytest, built in GitHub Actions, and run with Docker Compose.

## Docs

- [Product Requirements (PRD)](docs/PRD.md): problem, users, requirements, success metrics
- [Technical Specification](docs/TECH_SPEC.md): tech stack, architecture, key design decisions

## Setup

Requires Python 3.11+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then add your CHANNEL3_API_KEY
```

Collect the catalog into `data/seed_catalog.csv`:

```bash
python backend/src/stylist/jobs/collect_channel3.py
```

Load the CSV into SQLite (`data/catalog.db`). Safe to re-run:

```bash
python backend/src/stylist/jobs/ingest.py
```

Run the tests:

```bash
python -m pytest backend/tests
```

## Status

Requirements phase (v2, scoped to a 3.5-day build). See the [milestones](docs/PRD.md#10-milestones-35-day-plan).

---

*Independent educational project. Not affiliated with or endorsed by Aritzia.*
