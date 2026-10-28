# Aritzia's Very Own: AI Outfit Stylist

A conversational outfit stylist built on Aritzia's product catalog. You describe a mood, an occasion, or the weather, and it puts together a complete outfit from real, named Aritzia pieces. You keep steering it (swap a piece, lock one in, make it dressier) until it's something you'd actually wear.

Under the hood: a catalog from the Channel3 product API in SQLite, tagged with CLIP zero-shot classification; CLIP embeddings for query relevance; rule-based outfit scoring (vibe, formality, season, color harmony) with an exhaustive search over combinations; and a parser that turns free text into structured constraints (keyword rules now, an LLM next). Tested with pytest in GitHub Actions. A FastAPI REST API, a React + TypeScript client, and Docker Compose are next (Day 3).

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

Tag the catalog with CLIP (needs `pip install -r requirements-ml.txt`; the first run downloads the photos and model weights), then score the tags against the gold set in `eval/tagging_report.md`:

```bash
python backend/src/stylist/jobs/tag.py
python backend/src/stylist/jobs/tagging_report.py
```

Chat with the stylist in the terminal. `--demo` plays Flows A–D from the PRD, and `--no-clip` runs without PyTorch (outfits are then scored without query relevance):

```bash
python backend/src/stylist/cli.py
python backend/src/stylist/cli.py --demo
python backend/src/stylist/cli.py "rooftop birthday dinner, it might get chilly" "different shoes" "perfect"
```

Score the name resolver on the hand-written queries in `eval/names_test.csv`:

```bash
python backend/src/stylist/jobs/names_report.py
```

Run the tests:

```bash
python -m pytest backend/tests
```

## Status

Days 1–2 of the [3.5-day plan](docs/PRD.md#10-milestones-35-day-plan) are done: the catalog, CLIP tagging, and the outfit engine, with Flows A–D working in the terminal (`cli.py --demo`). Day 3 (API, LLM parser, web client, Docker) is next.

Results so far, against the [PRD targets](docs/PRD.md#7-success-metrics):

| Goal | Result | Target | Met |
|---|---|---|---|
| G3 Tagging: season top-1 | 26/30 (87%) | ≥ 75% | Yes |
| G3 Tagging: vibe top-1 | 13/30 (43%) | ≥ 75% | No |
| G3 Tagging: formality within ±1 | 26/30 (87%) | ≥ 85% | Yes |
| G4 Names: top-1 | 24/30 (80%) | ≥ 90% | No |

- Tagging details are in [`eval/tagging_report.md`](eval/tagging_report.md). CLIP rarely predicts `polished` or `going-out`, and an LLM vibe tagger is the planned fix (FR-TAG-5).
- G4 is not a held-out score: the resolver was revised once using these queries (17/30 before). All 6 misses are queries that fit several products ("sweater", "chinos"), and 3 of them have the right product among the choices offered.

---

*Independent educational project. Not affiliated with or endorsed by Aritzia.*
