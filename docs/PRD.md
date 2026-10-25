# AI Outfit Stylist: Product Requirements Document

| | |
|---|---|
| **Date** | October 20, 2026 |
| **Status** | Draft v2 |
| **Companion doc** | [Technical Specification](TECH_SPEC.md) |

> **Disclaimer.** This is an independent educational project. It is not affiliated with, endorsed by, or sponsored by Aritzia. Product data is collected within the site's terms of use, and product images are not redistributed.

---

## 1. Problem & Motivation

Aritzia sells clothes, but it doesn't help anyone decide what to wear. A shopper can filter by size, color, or category, or browse an editorial lookbook. Neither answers the question people actually ask on a Tuesday morning: *"I have a closet full of options. What do I put together today?"*

Lookbooks are static: they show one stylist's answer to a question you didn't ask. Filtered search is atomic: it returns individual items and leaves the hard part, composing them into something coherent, to the user. Neither responds to taste, and neither improves when you say "not quite."

This project treats styling as a conversation. The user describes a mood, an occasion, or the weather in plain language. The system keeps regenerating from that feedback until the outfit is something the user would actually wear.

### Why this project

The project has two aims:

1. **Applied ML on a real, visual problem.** CLIP embeddings drive retrieval and item similarity. An LLM turns free text into structured constraints. Outfit coherence is a scoring problem over relationships between items, not over any single item.
2. **Production-style software engineering.** It is built as a small full-stack service, with a typed REST API, a SQL-backed data layer, a React + TypeScript client, automated tests, CI, and Docker.

### Scoping principle 

Every feature has to answer one question: *does it show up in the demo or the README?* Anything that would be research rather than engineering is left out, including a two-source tag merge, approximate-nearest-neighbour indexes, beam search, and a multi-rater study. Each of these is still listed in §8 as a stretch goal, with a design sketch in the tech spec.

---

## 2. Goals & Non-Goals

### Goals

1. **G1.** Turn a free-text style request into a complete, coherent outfit made of real Aritzia catalog items.
2. **G2.** Let the user refine that outfit through chat or buttons (lock, reject, swap, nudge) until they accept it.
3. **G3.** Auto-tag the catalog (season, vibe, formality) and measure the tagging accuracy against a small hand-labeled set.
4. **G4.** Reliably resolve Aritzia product names that the user types.
5. **G5.** Ship a tested, containerized, demoable web app: a REST API plus a web client, running with one command.

### Non-Goals

- **Purchasing or checkout.** The system recommends. It does not sell, though links to product pages are fine.
- **Sizing and fit.** No size recommendations and no body-shape modeling.
- **Inventory and stock.** The catalog is a snapshot, and live availability is out of scope.
- **User accounts and authentication.** Sessions are anonymous and live on the server.
- **Production deployment at scale.** Deploying to one free-tier host is a stretch goal (S-5). There is no autoscaling and no multi-region setup.
- **Official Aritzia integration.** No partner APIs and no brand affiliation.

---

## 3. Target Users & Personas

The core user owns Aritzia clothes or shops there. They have a rough sense of the feeling they're going for and want help turning that feeling into a specific combination of pieces.

| Persona | Situation | What they say |
|---|---|---|
| **Maya, the weather-driven commuter** | Gets dressed in ten minutes and cares mostly about being warm, office-appropriate, and not boring. | *"It's 8°C and rainy, I have meetings all day. Something polished but comfortable."* |
| **Jordan, the event planner** | Has a specific occasion coming up and wants to explore options before committing. | *"Rooftop birthday dinner Saturday, going-out but not trying too hard."* |
| **Sam, the anchor-piece builder** | Owns (or has their eye on) one specific Aritzia piece and wants to build around it. | *"I just got the Effortless Pant in black. What do I wear with it for a weekend brunch?"* |

---

## 4. Key User Flows

### Flow A: Mood request
1. Maya types *"cozy fall Sunday, running errands."*
2. The system returns an outfit: a top, a bottom, shoes, and outerwear or an accessory when relevant. Each piece shows its image, name, brand line, and price.
3. Maya accepts, and she's done in one turn.

### Flow B: Occasion + weather request
1. Jordan types *"rooftop birthday dinner, it might get chilly."*
2. The system parses the request into an occasion (dinner), a vibe (going-out), weather (cool), and a target formality (3 out of 5). It returns an outfit that includes outerwear.

### Flow C: Named-item request
1. Sam types *"build something around the Effortless Pant."*
2. The system resolves "Effortless Pant" to a catalog item, locks it into the bottom slot, and fills the other slots around it.
3. If the name matches several items, the system asks Sam to choose (P1).

### Flow D: Refinement loop
1. After any generated outfit, the user can:
   - **Lock** a piece (*"keep the coat"* or the 🔒 button), which keeps it through all later regenerations.
   - **Reject** a piece (*"not that top"* or the ✕ button), which excludes it for the rest of the session.
   - **Swap** a piece (*"different shoes"*), which replaces that slot only.
   - **Nudge** the outfit (*"dressier"*, *"more relaxed"*), which shifts the target formality or vibe.
   - **More like this** (*"more like the blazer"*, P1), which pushes later picks toward that item's style.
2. The system regenerates and shows the new outfit.
3. The loop repeats until the user **accepts**. The system then shows a summary of the final outfit.

---

## 5. Functional Requirements

Priority key: **P0** = must ship in 3.5 days · **P1** = ship if on schedule · **P2** = stretch.

### 5.1 Catalog (`FR-CAT`)

| ID | Requirement | Priority | Acceptance criteria |
|---|---|---|---|
| FR-CAT-1 | Store a product catalog in SQL with: name, brand line, slot, color name, price, description, image URL, and product URL. | P0 | Every row passes schema validation on load. Rows that fail are logged and skipped, not silently dropped. |
| FR-CAT-2 | Assign every item to exactly one slot: `top`, `bottom`, `dress`, `outerwear`, `shoes`, or `accessory`. | P0 | 100% of items have a valid slot. |
| FR-CAT-3 | Cover enough items to make generation meaningful. | P0 | At least 90 items in total and at least 10 per slot. |
| FR-CAT-4 | Collect data within the site's terms: respect robots.txt, rate-limit requests, and send a descriptive User-Agent. Otherwise, fall back to a hand-curated CSV. | P0 | The collector documents its rate limit. Scraper work is timeboxed to 3 hours, after which the CSV fallback is used. |
| FR-CAT-5 | Re-running ingestion updates existing records without creating duplicates. | P0 | Ingesting twice produces the same row count, which a test checks. |

### 5.2 Tagging (`FR-TAG`)

| ID | Requirement | Priority | Acceptance criteria |
|---|---|---|---|
| FR-TAG-1 | Tag each item with 1 or more **season** tags, 1 to 3 **vibe** tags, 1 to 3 **color** tags, and a **formality** score from 1 to 5 (see [Appendix A](#appendix-a-tag-taxonomy)). | P0 | 100% of items are tagged, and the tags validate against the schema. |
| FR-TAG-2 | Produce the tags with a single batch LLM pass over each item's name, brand line, color, and description. Use structured output. | P0 | The tagging job is idempotent and cached, so re-running it costs nothing. |
| FR-TAG-3 | Maintain a hand-labeled gold set, labeled **before** looking at model output. | P0 | 30 items, stratified across slots (5 per slot). |
| FR-TAG-4 | Report tagging accuracy against the gold set. | P0 | `eval/tagging_report.md` reports accuracy for each axis. |
| FR-TAG-5 | Use CLIP zero-shot image tagging as a second signal, and compare it with the LLM on the gold set. | P1 | The report includes a CLIP-vs-LLM accuracy table. |

### 5.3 Request Parsing (`FR-REQ`)

| ID | Requirement | Priority | Acceptance criteria |
|---|---|---|---|
| FR-REQ-1 | Accept free-text requests that mix mood, occasion, weather, and named items. | P0 | All four flows in §4 parse without error. |
| FR-REQ-2 | Convert each message into either a `ParsedRequest` or a `FeedbackIntent`, validated by schema. | P0 | Invalid LLM output is retried once and then falls back to keyword rules. It never crashes the API. |
| FR-REQ-3 | When a request is too vague (e.g. *"something nice"*), fall back to sensible defaults. | P0 | Vague requests still produce an outfit. |
| FR-REQ-4 | Cold or rainy weather requires outerwear. | P0 | A unit test covers this. |

### 5.4 Outfit Generation (`FR-GEN`)

| ID | Requirement | Priority | Acceptance criteria |
|---|---|---|---|
| FR-GEN-1 | Generate a valid outfit: either top + bottom or a dress, plus shoes, plus optional outerwear and an optional accessory. | P0 | No outfit has a dress together with a top or bottom, and no outfit is missing a required slot. Tests check this. |
| FR-GEN-2 | Use only real catalog items. | P0 | Every `item_id` in an outfit exists in the database. |
| FR-GEN-3 | Score candidate outfits on query relevance, vibe match, formality match, and color harmony, and return the best one. | P0 | The API response includes the score breakdown. |
| FR-GEN-4 | Respect hard constraints: locked items always appear, rejected items never do, and the season matches. | P0 | Zero violations in tests and in logged sessions. |
| FR-GEN-5 | Avoid showing the same outfit twice in a row. | P1 | A test checks this. |
| FR-GEN-6 | Give a 1–2 sentence explanation of why the outfit works. | P1 | Built from a template using the score breakdown, so it needs no extra LLM call. |

### 5.5 Named-Item Resolution (`FR-NAME`)

| ID | Requirement | Priority | Acceptance criteria |
|---|---|---|---|
| FR-NAME-1 | Resolve typed product names, tolerating typos, missing words, and differences in casing. | P0 | At least 90% top-1 accuracy on a 30-query test set. |
| FR-NAME-2 | When nothing matches, say so and suggest up to 3 close alternatives. | P0 | Covered by a test case. |
| FR-NAME-3 | When several items match closely, ask the user to choose. | P1 | Ambiguous queries return a `choices` response. |

### 5.6 Feedback Loop (`FR-FB`)

| ID | Requirement | Priority | Acceptance criteria |
|---|---|---|---|
| FR-FB-1 | **Lock / unlock**: a locked item stays in its slot through every regeneration. | P0 | Unit test. |
| FR-FB-2 | **Reject**: a rejected item never reappears in the session. | P0 | Unit test. |
| FR-FB-3 | **Swap**: replace one slot and keep the others unchanged. | P0 | Unit test. |
| FR-FB-4 | **Nudge**: relative formality or vibe changes move the outfit in the requested direction. | P0 | A test checks that the mean formality moves in the requested direction after "dressier." |
| FR-FB-5 | **Accept**: end the session and show a summary with links and the total price. | P0 | API and UI. |
| FR-FB-6 | **More like this**: shift the session preference vector toward an item. | P1 | A test checks that similarity to that item increases in the next outfit. |
| FR-FB-7 | Buttons and chat produce the same state change through the same code path. Buttons skip the LLM. | P0 | Both inputs call `apply_feedback`. |

### 5.7 API & Interface (`FR-API`, `FR-UI`)

| ID | Requirement | Priority | Acceptance criteria |
|---|---|---|---|
| FR-API-1 | A versioned REST API (`/api/v1`) for sessions, messages, actions, and catalog lookups. | P0 | OpenAPI docs are served at `/docs`, and every endpoint has an integration test. |
| FR-API-2 | Consistent error responses (`4xx` for client errors, never a raw `500` for bad input). | P0 | Tests cover an unknown session, an invalid slot, and an unknown item. |
| FR-UI-1 | A web client showing the current outfit as item cards (image, name, brand line, price). | P0 | Renders after each turn. |
| FR-UI-2 | A chat panel with visible conversation history. | P0 | Earlier turns stay visible. |
| FR-UI-3 | Lock and reject buttons on each card. | P0 | Each click calls the actions endpoint and re-renders the outfit. |
| FR-UI-4 | Highlight the slots that changed since the last outfit. | P2 | Changed cards get a visible border. |

---

## 6. Non-Functional Requirements

| ID | Category | Requirement |
|---|---|---|
| NFR-1 | **Latency** | A button action returns in 500 ms or less (p90), since it makes no LLM call. A chat turn returns in 4 s or less (p90), with one LLM call. |
| NFR-2 | **Cost** | At most one LLM call per chat turn and none per button action. Tagging is a one-time cached batch job. |
| NFR-3 | **Quality gates** | CI runs lint, type checks, and tests on every PR. `main` is always green. |
| NFR-4 | **Testability** | The LLM and the embedding model sit behind interfaces, so tests run offline with fakes, with no API key and no model download. |
| NFR-5 | **Reproducibility** | Pinned dependencies and a lockfile, `docker compose up` runs the whole app, and a single command reproduces the evaluation numbers. |
| NFR-6 | **Observability** | Structured logs for each turn (parsed intent, scores, chosen outfit, latency). Every session and turn is persisted to SQL. |
| NFR-7 | **Security & ethics** | API keys come from environment variables and are never committed. Collection is rate-limited. Images are referenced or cached locally and never re-hosted. No personal data is stored. |

---

## 7. Success Metrics

All targets are sized for a 3.5-day build. Each one will be reported in the README, whether or not it is met.

| Goal | Metric | Target | How it's measured |
|---|---|---|---|
| G1 Outfit quality | Share of outfits I rate coherent against a written rubric | At least 70% of 20 outfits | `eval/coherence.csv` (self-rated, rubric committed). A multi-rater study is stretch goal S-3. |
| G2 Loop works | Constraint violations (lock, reject, slot rules) | 0 | Tests plus a scan of the session table |
| G3 Tagging | Agreement with the gold set | Season and vibe top-1 at least 75%; formality within ±1 for at least 85% of items | `eval/tagging_report.md` |
| G4 Names | Top-1 resolution accuracy | At least 90% on 30 queries | `eval/names_test.csv` + script |
| G5 Engineering | Test coverage of the `core/` and `api/` packages | At least 80% line coverage | `pytest --cov` in CI |
| G5 Engineering | Demo | Flows A–D work end to end in the browser | 2–3 minute screen recording linked in the README |
| NFR-1 | Chat and button latency | p90 at or under the NFR-1 targets | `eval/latency.md` |

---

## 8. Scope

### In scope 
- A catalog of at least 90 items in SQLite, collected by a polite scraper or a hand-curated CSV.
- LLM batch tagging, a 30-item gold set, and an accuracy report.
- CLIP image and text embeddings with brute-force cosine retrieval in NumPy.
- A rule-based scorer and an enumeration-based generator.
- A deterministic feedback state machine (lock, unlock, reject, swap, nudge, accept).
- A fuzzy named-item resolver.
- A FastAPI backend, a React + TypeScript client, Docker Compose, and GitHub Actions CI.
- Evaluation scripts for every metric in §7.

### Stretch goals (only after the P0 and P1 items are done)
| ID | Stretch goal | Why it matters |
|---|---|---|
| S-1 | CLIP + LLM tag **merge**, with a review queue for conflicts | Two signals, combined and audited |
| S-2 | **Learned compatibility model** on frozen CLIP features (Polyvore), compared with the rule scorer | Turns hand-tuned heuristics into a learned model |
| S-3 | **Multi-rater study** (3 raters, Fleiss' κ) plus a session **ablation** without the preference vector | A stronger claim about quality |
| S-4 | **Second brand** through the same ingestion interface | Tests whether the pipeline generalizes |
| S-5 | **Cloud deploy** (e.g. Render, Fly.io, or Azure App Service) from CI | A live demo link |
| S-6 | **End-to-end browser test** (Playwright) of Flow D | Covers the UI and API contract together |

---

## 9. Engineering Practices

| Skill postings ask for | Where it shows up in this project |
|---|---|
| Python, object-oriented and typed code | Typed `core/` package, Pydantic models, `mypy` in CI |
| REST API design | FastAPI, versioned routes, OpenAPI docs, consistent error model |
| SQL and databases | SQLite through SQLAlchemy: catalog, tags, sessions, and turns tables; idempotent upserts; tagging accuracy queried in SQL |
| JavaScript / TypeScript, React | Vite + React + TypeScript client with a typed API client |
| Unit and integration testing | pytest unit tests for the scorer, state machine, and resolver; API tests with `TestClient`; fakes for the LLM and CLIP |
| CI/CD | GitHub Actions: ruff, mypy, pytest with coverage, and a frontend type check and build on every PR |
| Docker / containers | A Dockerfile for the API and client, and `docker compose up` for the whole stack |
| Git and code review | Feature branches, small PRs that reference FR IDs, conventional commits, and a protected `main` |
| Agile and working in iterations | A GitHub Projects board with an issue for each FR, day-sized milestones, and a short retro in the README |
| Applied ML / AI | CLIP embeddings, zero-shot tagging, relevance feedback, LLM structured output, and evaluation against a gold set |
| Communication and documentation | This PRD, the tech spec, a README with architecture and results, and a demo video |

---

## 10. Milestones (3.5-day plan)

Each day ends with a merged PR and a green CI run.

| Day | Milestone | Exit criteria |
|---|---|---|
| **Day 1** | **Foundation + data** | Repo scaffold, CI green, and the catalog in SQLite (FR-CAT). The gold set is hand-labeled first, then tagging and embedding run. |
| **Day 2** | **Core engine** | Retrieval, scorer, generator, feedback state machine, and name resolver, all unit-tested. A CLI produces an outfit for Flows A–D. |
| **Day 3** | **API + client** | LLM parser, FastAPI endpoints with integration tests, React client, and Docker Compose. Flows A–D work in the browser. |
| **Day 3.5** | **Evaluate + ship** | Evaluation scripts run, results are in the README, the demo is recorded, and `v1.0.0` is tagged. |

**Cut line.** If the plan slips by more than half a day, drop the P1 items in this order: FR-TAG-5 (CLIP tagging comparison), FR-FB-6 (more like this), FR-NAME-3 (ambiguity prompt), FR-GEN-6 (rationale). If the React client is not rendering outfits by midday on Day 3, switch to a Streamlit client that calls the same API, so that the API and the tests are still finished.

---

## 11. Risks & Open Questions

| Risk | Impact | Mitigation |
|---|---|---|
| The site blocks scraping or its structure changes. | Loses the morning of Day 1. | Strict 3-hour timebox, then fall back to a hand-curated CSV. Ingestion depends only on the CSV/JSON schema. |
| The LLM returns malformed structured output. | A broken turn. | Pydantic validation, one retry, then keyword-rule fallback. Tests cover the fallback path. |
| The small catalog limits variety. | Repetitive outfits. | At least 10 items per slot, an optional accessory, and a repetition penalty. |
| PyTorch makes the API image large and slow to build. | Slow CI and Docker builds. | CPU-only torch wheel. CI tests use a fake embedder and never install torch. |
| Frontend work overruns. | No browser demo. | The cut line in §10: fall back to a Streamlit client on the same API. |
| Self-rated coherence is subjective. | A weak quality claim. | Commit the rubric, state the limitation, and list the multi-rater study as S-3. |

**Resolved questions (from v1)**
1. *Is the accessory slot optional?* Yes. It is included only if it raises the score.
2. *Mixed-season requests ("summer dress for a fall wedding")?* The request's season filters outerwear only. Other slots use the season as a soft score term rather than a hard filter.
3. *Catalog depth or breadth?* Breadth: at least 10 items per slot beats depth in a few slots.

---

## Appendix A: Tag Taxonomy

**Season** (multi-label): `spring`, `summer`, `fall`, `winter`

**Vibe** (multi-label, 1–3 per item):

| Tag | Description |
|---|---|
| `cozy` | Soft, warm, relaxed textures: knits, fleece, oversized cuts |
| `polished` | Tailored, clean lines, office-ready |
| `going-out` | Evening, statement, a little dressy or bold |
| `cute` | Pretty, flowy, a little lighter and playful |
| `off-duty` | Casual, effortless, weekend |
| `chic` | Classy, boss-material vibes |
| `athleisure` | Sporty, technical, activewear-adjacent |
| `minimal` | Neutral palette, simple silhouettes, no embellishment |
| `romantic` | Soft details: florals, ruffles, delicate fabrics |
| `edgy` | Leather, darker palette, sharper silhouettes |


**Formality** (single integer, 1–5):

| Score | Meaning | Example |
|---|---|---|
| 1 | Lounge / athletic | Sweatpants, fleece hoodie |
| 2 | Casual | Jeans, t-shirt, sneakers |
| 3 | Smart casual | Knit top, trousers, loafers |
| 4 | Business / dressy | Blazer, tailored pants, heels |
| 5 | Formal / event | Evening dress, statement heels |

**Color** (multi-label, 1–3 per item; the first tag is the item's main color):

Retailer color names such as "Heather Mocha" or "Dreamy Lilac" are mapped onto this fixed set. The **Neutral** column and the **Hue** column feed the color-harmony score: neutrals pair with every color, and non-neutral colors are compared by hue angle.

| Tag | Neutral? | Hue (°) | Covers (example retailer names) |
|---|---|---|---|
| `black` | Yes | n/a | Black, Jet, Onyx |
| `white` | Yes | n/a | White, Optic White, Bright White |
| `cream` | Yes | n/a | Cream, Ivory, Off-White, Oat, Bone |
| `beige` | Yes | n/a | Beige, Sand, Stone, Taupe, Khaki |
| `camel` | Yes | n/a | Camel, Tan, Cognac, Caramel |
| `brown` | Yes | n/a | Brown, Chocolate, Mocha, Espresso |
| `grey` | Yes | n/a | Grey, Heather Grey, Charcoal, Slate |
| `navy` | Yes | n/a | Navy, Midnight, Dark Navy |
| `denim` | Yes | n/a | Light / Mid / Dark Wash, Indigo |
| `olive` | Yes | n/a | Olive, Army, Khaki Green, Moss |
| `red` | No | 0 | Red, Cherry, Tomato, Scarlet |
| `burgundy` | No | 345 | Burgundy, Wine, Oxblood, Merlot |
| `pink` | No | 330 | Pink, Blush, Rose, Fuchsia, Magenta |
| `orange` | No | 25 | Orange, Rust, Terracotta, Coral |
| `yellow` | No | 50 | Yellow, Butter, Mustard, Lemon |
| `green` | No | 130 | Green, Sage, Mint, Emerald, Forest |
| `teal` | No | 180 | Teal, Turquoise, Aqua |
| `blue` | No | 215 | Blue, Cobalt, Royal, Sky, Powder Blue |
| `purple` | No | 275 | Purple, Lilac, Lavender, Plum, Violet |
| `metallic` | Yes | n/a | Gold, Silver, Bronze, Pewter |
| `multi` | No | n/a | Prints, florals, stripes, plaids, colour-block |

Notes:
- `olive`, `denim`, `navy` and the earth tones (`camel`, `brown`, `beige`) count as neutrals, because that's how they are usually styled.
- `multi` has no single hue. It scores as 0.6 against non-neutral colors, and at most one `multi` item is allowed per outfit.
- A color name that matches no tag defaults to its closest tag by the LLM's judgment and is logged for review.

## Appendix B: Glossary

| Term | Definition |
|---|---|
| **Slot** | A position in an outfit: top, bottom, dress, outerwear, shoes, or accessory. |
| **Lock** | Pin an item so every later regeneration keeps it. |
| **Reject** | Exclude an item for the rest of the session. |
| **Nudge** | A relative change to the target formality or vibe (e.g. "dressier"). |
| **Preference vector** | A session-level CLIP-space vector that builds up the user's feedback and steers retrieval. |
| **Brand line** | Aritzia's in-house labels (Babaton, Wilfred, TNA, Sunday Best, etc.). |
| **Neutral** | A color (e.g. black, cream, camel, navy, denim) that pairs with any other color in the harmony score. |
| **Gold set** | Items labeled by hand before seeing model output, used to measure tagging accuracy. |
