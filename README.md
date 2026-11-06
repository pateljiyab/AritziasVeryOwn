# Aritzia's Very Own: AI Outfit Stylist

A conversational outfit stylist built on Aritzia's product catalog. You describe a mood, an occasion, or the weather, and it puts together a complete outfit from real, named Aritzia pieces. You keep steering it (swap a piece, lock one in, make it dressier, keep it under $200, make it cheaper) until it's something you'd actually wear.

Under the hood: a catalog from the Channel3 product API in SQLite, tagged with CLIP zero-shot classification; CLIP embeddings for query relevance; rule-based outfit scoring (vibe, formality, season, color harmony) with an exhaustive search over combinations; and a parser that turns free text into structured constraints with Claude Haiku 5.5 (structured outputs), falling back to keyword rules. Tested with pytest in GitHub Actions. A FastAPI REST API serves a React + TypeScript client, and Docker Compose runs both.

## Docs

- [Product Requirements (PRD)](docs/PRD.md): problem, users, requirements, success metrics
- [Technical Specification](docs/TECH_SPEC.md): tech stack, architecture, key design decisions
- [Day 3 plan](docs/plans/llm-parser.md): the LLM parser, the parser benchmark, LLM vibe tagging, and Docker

## Run it with Docker

Needs only Docker. Copy `.env.example` to `.env` and add `ANTHROPIC_API_KEY` if you want the LLM parser (without it, chat uses keyword rules). Then:

```bash
docker compose up --build
```

Open http://localhost:8080 for the app and http://localhost:8080/docs for the API docs. nginx serves the built client and forwards `/api` to the FastAPI service, so the browser talks to one origin.

- On first start, the API builds `data/catalog.db` from the committed files: the seed catalog, the CLIP tag snapshot (`data/clip_tags.json`), and the LLM vibe cache (`data/llm_vibes.json`). An existing database is used as is. `./data` is mounted into the container, so the database and sessions persist on your machine.
- The image has no PyTorch, so outfits are scored without the CLIP relevance term (like `cli.py --no-clip`). Run the app outside Docker with `requirements-ml.txt` for the full scoring.

## Deploy on Render

`render.yaml` is a Render Blueprint for two services:
- **`outfit-stylist-api`:** the API's Docker image, on the Starter plan ($7 a month, always on).
- **`outfit-stylist`:** the client, as a free static site.

1. In the Render dashboard, choose **New > Blueprint** and pick this repo.
2. Enter `ANTHROPIC_API_KEY` when asked. Set a monthly spend limit in the Claude Console too.
3. After the first deploy, check the two URLs. If Render added a suffix to a name, update `VITE_API_BASE` and `STYLIST_CORS_ORIGINS` in `render.yaml` to match.

Both services redeploy when a merge to `main` passes CI. The API rebuilds its catalog from the committed files on every start, so it needs no disk. Chat sessions reset when it redeploys.

## Setup

Requires Python 3.11+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then add your CHANNEL3_API_KEY, and ANTHROPIC_API_KEY for the LLM parser
```

Without `ANTHROPIC_API_KEY`, chat is parsed with keyword rules, so everything runs (and the tests pass) with no key. `.env.example` also shows how to point the parser at Groq's free tier or a local Ollama model instead.

Collect the catalog into `data/seed_catalog.csv`:

```bash
python backend/src/stylist/jobs/collect_channel3.py
```

Load the CSV into SQLite (`data/catalog.db`). Safe to re-run. Without PyTorch, `restore_tags.py` loads the committed CLIP tags instead of running the tagger below:

```bash
python backend/src/stylist/jobs/ingest.py
python backend/src/stylist/jobs/restore_tags.py
```

Tag the catalog with CLIP (needs `pip install -r requirements-ml.txt`; the first run downloads the photos and model weights), add the LLM vibe tags, then score the tags against the gold set in `eval/tagging_report.md`. The LLM vibes are cached in the committed `data/llm_vibes.json`, so `llm_tag.py` needs an `ANTHROPIC_API_KEY` only for new or changed items (about $0.05 for the whole catalog):

```bash
python backend/src/stylist/jobs/tag.py
python backend/src/stylist/jobs/llm_tag.py
python backend/src/stylist/jobs/tagging_report.py
```

Chat with the stylist in the terminal. `--demo` plays Flows A–D from the PRD, `--no-clip` runs without PyTorch (outfits are then scored without query relevance), and `--keyword` uses keyword rules even when an LLM is set up:

```bash
python backend/src/stylist/cli.py
python backend/src/stylist/cli.py --demo
python backend/src/stylist/cli.py "rooftop birthday dinner, it might get chilly" "different shoes" "perfect"
```

Run the REST API, then open http://127.0.0.1:8000/docs for the interactive OpenAPI docs. Sessions and every turn are saved to `data/sessions.db`. Set `STYLIST_NO_CLIP=1` to run without PyTorch. Each client IP can send 20 chat messages a minute (`STYLIST_MESSAGES_PER_MINUTE`), so a script can't spend the LLM budget; buttons aren't limited, since they make no LLM call:

```bash
uvicorn --factory --app-dir backend/src stylist.api.app:create_app --env-file .env --reload
```

With the API running, start the web client (needs Node 20.19+) and open http://localhost:5173. It calls the API at `http://localhost:8000`; set `VITE_API_BASE` in `web/.env` to change that:

```bash
cd web
npm install
npm run dev
```

Score the name resolver on the hand-written queries in `eval/names_test.csv`:

```bash
python backend/src/stylist/jobs/names_report.py
```

Benchmark the chat parsers on the hand-written messages in `eval/parser_test.csv` and write `eval/parser_report.md`. It runs keyword rules plus every LLM that's set up: Claude (`ANTHROPIC_API_KEY`), gpt-oss-120b on Groq's free tier (`GROQ_API_KEY`), and a local model on Ollama if it's running. `--keyword-only` makes no LLM calls:

```bash
python backend/src/stylist/jobs/parser_report.py
```

Run the tests:

```bash
python -m pytest backend/tests
```

## Status

Days 1–3 of the [3.5-day plan](docs/PRD.md#10-milestones-35-day-plan) are done: the catalog and tagging, the outfit engine, and the full app (REST API, React client, LLM parser, and Docker Compose), with Flows A–D working in the browser. Day 3.5 is next: the outfit-quality rating (G1), test coverage (G5), a latency report, the demo recording, and `v1.0.0`.

Results so far, against the [PRD targets](docs/PRD.md#7-success-metrics):

| Goal | Result | Target | Met |
|---|---|---|---|
| G3 Tagging: season top-1 | 26/30 (87%) | ≥ 75% | Yes |
| G3 Tagging: vibe top-1 | 19/30 (63%) with the Haiku vibe tagger; CLIP alone 13/30 (43%) | ≥ 75% | No |
| G3 Tagging: formality within ±1 | 26/30 (87%) | ≥ 85% | Yes |
| G4 Names: top-1 | 24/30 (80%) | ≥ 90% | No |
| Chat parser: exact (every field right) | 24/37 (65%) with Claude Haiku 5.5; keyword rules 11/37 (30%) | none set | |
| NFR-1 Chat latency p90 | 2.3 s with Haiku (parser benchmark, 40 messages) | ≤ 4 s | Yes |

- Tagging details are in [`eval/tagging_report.md`](eval/tagging_report.md). CLIP rarely predicts `polished` or `going-out`, so vibe now comes from Claude Haiku 5.5 reading each item's photo and details (FR-TAG-5). That raised vibe top-1 from 43% to 63%, still short of the target. Most remaining misses are judgment calls (the gold set's first vibe is `polished` for 15 of 30 items), and the prompt is not tuned on the gold set.
- Parser details are in [`eval/parser_report.md`](eval/parser_report.md): 40 hand-written messages, scored for keyword rules, Claude Haiku 5.5, gpt-oss-120b on Groq's free tier, and Llama 3.1 8B on Ollama. Haiku and gpt-oss-120b tie within noise (65% vs 68% exact), but Groq's free tier allows only about 4 messages a minute with this prompt, so the live app uses Haiku (about $0.0004 a message).
- G4 is not a held-out score: the resolver was revised once using these queries (17/30 before). All 6 misses are queries that fit several products ("sweater", "chinos"), and 3 of them have the right product among the choices offered.

---

*Independent educational project. Not affiliated with or endorsed by Aritzia.*
