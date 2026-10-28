# AI Outfit Stylist: Technical Specification

| | |
|---|---|
| **Date** | October 20, 2026 |
| **Last updated** | October 27, 2026 (v4: matches what Days 1–2 built) |
| **Status** | v4 |
| **Companion doc** | [Product Requirements](PRD.md) |

This document defines the tech stack and how the pieces fit together. The *what* and *why* of the product live in the [PRD](PRD.md), and implementation details live in the code. Items marked *(Day 3)* are planned but not built yet.

---

## 1. Architecture at a Glance

```mermaid
flowchart LR
    subgraph Offline["Offline jobs (run once)"]
        C3[Channel3 API] --> COL[collect_channel3.py] --> CSV[seed_catalog.csv]
        CSV --> ING[ingest.py] --> B[(SQLite<br/>items, item_tags)]
        B --> T[tag.py<br/>CLIP zero-shot + color rules]
        T --> B
        T --> E[(embeddings.npy)]
    end

    subgraph Online["Online app"]
        CLI[cli.py] --> TURN
        W["React + TypeScript client (Day 3)"] -->|REST / JSON| API["FastAPI service<br/>/api/v1"]
        API --> TURN[core/session.py<br/>one chat turn]
        TURN --> P[Parser:<br/>keyword rules now,<br/>LLM on Day 3]
        TURN --> G[Outfit engine<br/>score + generate + feedback]
        E --> G
        B --> G
        API --> S[(sessions.db<br/>sessions, turns)]
    end
```

- **Offline:** collect the catalog from Channel3, load it into SQLite, then tag every item with CLIP and save its photo embedding. These steps are slow but only run once.
- **Online:** a message goes to `core/session.py`. It parses the message into a request or a feedback action, updates the session, builds the best-scoring outfit, and returns it with a score breakdown. The CLI calls it directly; the Day 3 API will call the same functions.

---

## 2. Tech Stack

### Backend

| Layer | Choice | Why |
|---|---|---|
| Language | **Python 3.11+** (CI runs 3.11 and 3.13) | The ML libraries are Python-first. |
| Web framework | **FastAPI** + Uvicorn | Typed REST endpoints, automatic OpenAPI docs at `/docs`, and a built-in test client. |
| Data validation | **Pydantic v2** | One set of models for API payloads, LLM outputs, and internal types (`ParsedRequest`, `FeedbackIntent`, `SessionState`). |
| Database | **SQLite** via **SQLAlchemy 2.1** | Real SQL with no server to run. Moving to Postgres only means changing the connection URL. |
| Config | **python-dotenv** + `.env` | API keys come from environment variables and are never committed. |
| Catalog source | **Channel3** product API (`channel3-sdk`) | Structured product data with photos, without scraping the site. |

### ML / AI

| Layer | Choice | Why |
|---|---|---|
| Tagging | **open_clip** ViT-B/32 zero-shot (season, vibe, formality) + rules over alt text and hex (color) | No API key or per-item cost. Each label is scored against several prompt phrasings. |
| Image + text embeddings | **open_clip** (ViT-B/32, CPU) | One model embeds photos and request text in a shared space, which powers query relevance (and "more like this," P1). |
| Vector search | **NumPy** | With about 400 items, plain cosine similarity is instant, so a vector database isn't needed. |
| LLM | **Claude** via the Anthropic Python SDK, for chat parsing *(Day 3)* | Reliable structured output through tool use. The model is configurable. |
| Fuzzy name matching | **rapidfuzz** | Word-by-word matching tolerates typos, plurals, and missing words. |

### Frontend *(Day 3)*

| Layer | Choice | Why |
|---|---|---|
| Framework | **React 18 + TypeScript** | The most common frontend stack in internship postings. |
| Build tool | **Vite** | Fast dev server and simple setup. |
| Styling | **CSS Modules** | No UI library to learn in a 3.5-day build. |

### Testing & Quality

| Layer | Choice | Why |
|---|---|---|
| Tests | **pytest** | Unit tests for the engine and jobs; API integration tests through FastAPI's test client. |
| Test doubles | Fake CLIP model, fake embedder, small hand-built catalogs (`tests/factories.py`) | Tests run offline, with no API key, no PyTorch, and no model download. |
| Lint / types | None in CI, by choice | Tooling is kept minimal; `tsc` will type-check the client. |

### DevOps & Workflow

| Layer | Choice | Why |
|---|---|---|
| Python packaging | `requirements.txt` (exact pins) + `requirements-ml.txt` (PyTorch, CPU wheels) | Plain pip. PyTorch stays out of CI and the core install. |
| JS packaging | **npm** *(Day 3)* | Standard. |
| Containers | **Docker** + **Docker Compose** *(Day 3)* | `docker compose up` runs the whole app. |
| CI | **GitHub Actions** | On every PR and push to `main`, on Python 3.11 and 3.13: pytest, then a smoke test that runs `ingest.py`, `cli.py --demo --no-clip`, and `names_report.py` as scripts. |
| Version control | **Git + GitHub** | Feature branches, small PRs, a protected `main`, and Conventional Commits. |
| Project tracking | **GitHub Projects** | One issue per requirement, organized into day-sized milestones. |

---

## 3. Key Design Decisions

| Decision | Reason |
|---|---|
| Expensive work (tagging, image embeddings) runs offline. | Keeps each chat turn fast on a laptop CPU (about 80 ms to generate an outfit). |
| Tags come from CLIP and rules, not an LLM. | No API key or per-item cost. CLIP meets the season and formality targets but not vibe, so an LLM vibe tagger is the planned second signal (FR-TAG-5). |
| Outfit logic in `core/` is pure Python with no I/O. | It is easy to unit-test and doesn't depend on the LLM, CLIP, or the database. `adapters/` does the loading. |
| Listings are grouped into products by a cleaned-up name (`core.catalog.product_key`). | Channel3 lists colorways and renamed copies separately (403 listings, 344 products). Grouping stops an outfit showing one product twice, and makes reject cover every colorway. |
| The generator shortlists the top 6 products per slot, then scores every valid combination. | About 12,000 combinations is small enough to search exhaustively, so there's no beam search to tune. |
| Score terms the request doesn't mention are left out, and the weights rebalance. | A vague request ("something nice") is scored on what it does say, not penalized for what it doesn't. |
| CLIP relevance is rescaled over the whole slot, not the shortlist. | Rejecting or swapping a piece can't inflate the next outfit's score. |
| Buttons and chat both become a `FeedbackIntent` and go through `apply_feedback`. | Chat and buttons always behave the same way (FR-FB-7). Buttons skip the parser. |
| `core/session.py` takes the parser and the text embedder as arguments. | The same turn logic runs with keyword rules or the LLM, and with real CLIP or a test fake. |
| The API is built by `create_app(catalog, embed, store, parse)`. | Tests pass a small catalog, a fake embedder, and a temporary database; `uvicorn --factory` passes nothing and gets the real ones. |
| A chat message that doesn't fit the outfit ("keep the dress" with no dress) is a 200 with a note; the same thing as a button click is a 409. | Typed messages are conversation, so the reply explains. A button that targets a missing piece is a client bug, so it's an error (FR-API-2). |
| Sessions and turns live in `data/sessions.db`, apart from `catalog.db`. | Re-running ingest can't touch them. Each turn row stores the parsed intent, outfit, locks, rejects, score, and latency, so G2 can be checked by scanning it. |
| If the LLM fails or there is no API key, the app falls back to keyword rules (`core/keyword_parser.py`). | The demo never crashes. The keyword parser is also the CLI's parser until Day 3. |
| Evaluation sets are labeled before results are seen, and any reuse is disclosed. | The gold set was labeled before tagging ran. The G4 queries were used once while improving the resolver, so the report prints the pre-tuning baseline next to every result. |
| NumPy instead of FAISS, and SQLite instead of Postgres. | They are the right size for about 400 items, with an easy upgrade path. |

---

## 4. Repository Layout

```text
AritziasVeryOwn/
├── docs/                      # PRD.md, TECH_SPEC.md
├── backend/
│   ├── src/stylist/
│   │   ├── core/              # outfit engine, pure Python: models, catalog (product grouping),
│   │   │                      #   scoring, generator, feedback, names, keyword_parser, session
│   │   ├── adapters/          # catalog_store (SQLite + embeddings -> Catalog), clip_text (query
│   │   │                      #   embeddings), session_store (sessions.db); Claude wrapper on Day 3
│   │   ├── tagging/           # clip_tagger.py (zero-shot), colors.py (alt-text and hex rules)
│   │   ├── jobs/              # offline jobs: collect_channel3, ingest, tag, tagging_report, names_report
│   │   ├── api/               # app.py (FastAPI routes, error handlers), schemas.py (request/response bodies)
│   │   └── cli.py             # terminal chat; --demo plays Flows A–D
│   └── tests/
├── web/                       # React + TypeScript client (Day 3)
├── data/                      # seed_catalog.csv (committed); catalog.db, sessions.db, images/, embeddings (gitignored)
├── eval/                      # gold_set.csv + gold_labeling.md, tagging_report.md, names_test.csv
├── requirements.txt           # core + tests (what CI installs)
├── requirements-ml.txt        # adds PyTorch + open_clip for tagging and the CLI's query embeddings
├── docker-compose.yml         # (Day 3)
└── .github/workflows/ci.yml
```
