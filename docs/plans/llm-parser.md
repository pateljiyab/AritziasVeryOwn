# Plan: Claude Haiku 5.5 parser, a four-way parser benchmark, and an LLM vibe tagger (on top of `feat/web`)

## Context

Jiya's `feat/web` (c09a2c7) finishes the React client. It covers cards, lock/swap/reject, nudges, the choices picker, session restore, and a CI `web` job. Three things are left:
- **Day 3:** the LLM parser (FR-REQ-2, P0) and Docker Compose.
- **The open issue:** vibe tagging is 43% (FR-TAG-5).

**Decision:**
- **Live provider:** Claude Haiku 5.5 (`claude-haiku-5-5`, $0.10 / $0.50 per million tokens) for the parser and the vibe tagger.
  - Expected spend is about $1.50 for the whole recruiting cycle, and about $4 if traffic is heavy. The $15 budget, with a spend cap, covers it.
  - Structured outputs constrain replies to the schema, and paid access won't change partway through the cycle.
- **Benchmark only:** the Groq free tier, as a free comparison point. (Planned as Llama 3.3 70B; Groq no longer serves it, so the benchmark uses OpenAI's open-weight gpt-oss-120b.)
- **Benchmark and offline dev mode:** a local 8B model on Ollama (on the M1 Pro, 16 GB).
  - Not used for the live site, because hosting it would need several GB of RAM or a GPU.
  - Ollama serves an OpenAI-compatible endpoint, so `OpenAICompatParser` covers it with only env vars.
- Resume line: "Chose Claude Haiku 5.5 after benchmarking it against Llama 3.3 70B on Groq, a local 8B model on Ollama, and keyword rules (accuracy, p90 latency, cost)."

## Branch

1. `git switch -c feat/llm-parser feat/web`. Once `feat/web` is merged, rebase onto `main`.
2. Changes stay uncommitted until reviewed.

## Phase 1: Haiku parser (FR-REQ-2, P0)

**New `backend/src/stylist/adapters/llm_parser.py`**
- **Schemas the LLM fills in:** `LLMRequest` (vibes, formality, season, weather, named_items) and `LLMFeedback` (action, slot, formality, vibe), inside a wrapper `LLMParse {kind: "request" | "feedback", request: LLMRequest | None, feedback: LLMFeedback | None}`.
  - The LLM never fills in `text` or `item_id`. They're added in code: `text` is the original message, so CLIP relevance is unchanged.
  - The wrapper is then turned into the real `ParsedRequest` / `FeedbackIntent` from `core/models.py`, which runs their validators, including `has_a_target`.
  - The fields reuse the `Literal` types `Vibe`, `Slot`, `Season`, and `Weather` from `core/models.py`, so the schema can't drift.
- **`ClaudeParser(client, model="claude-haiku-5-5")`** is a callable that matches `core.session.Parse = Callable[[str, bool], ParsedRequest | FeedbackIntent]`.
  - The call is `client.messages.parse(model=..., max_tokens=1024, system=PROMPT, messages=[{"role": "user", "content": ...}], output_format=LLMParse, output_config={"effort": "low"})`, and it reads `response.parsed_output`.
  - The user content includes whether an outfit is on screen (`live`).
  - The system prompt is fixed text and changes only when you edit it. It holds the vocabulary, the rule for `live` (an outfit is on screen, so read the message as feedback first), and a few examples from Flows A–D.
  - Each turn is a single call with no history to replay.
  - Client timeout is about 3 s with `max_retries=1`, which keeps a chat turn within NFR-1's 4 s.
- **Fallback chain (FR-REQ-2):**
  - If converting to the core model raises `ValidationError` (for example, a nudge with no target), retry once with the error in the prompt. If it fails again, use `keyword_parser.parse`.
  - Also fall back to `keyword_parser.parse` on `stop_reason == "refusal"` (Haiku 5.5 has no server-side fallback) and on any `anthropic.APIError`: rate limit, timeout, connection, or credit used up.
  - Each fallback is logged to the `stylist.llm` logger.
- **`OpenAICompatParser(base_url, api_key, model)`:** the same interface for Groq and Ollama.
  - It sends the `LLMParse` JSON schema as `response_format` where the provider supports it, and falls back to JSON mode where it doesn't. It uses the same retry and fallback.
  - Groq: `https://api.groq.com/openai/v1`. Ollama: `http://localhost:11434/v1`, any non-empty key.
  - The local timeout is longer (about 15 s), since that mode is for development.
  - It's used by the benchmark, and as an offline dev or backup provider.
- **`make_parser()`:**
  - `ANTHROPIC_API_KEY` set: returns `ClaudeParser`.
  - Otherwise, `LLM_BASE_URL` and `LLM_API_KEY` set: returns `OpenAICompatParser`.
  - Otherwise: returns `keyword_parser.parse`, so CI and tests need no key.

**Wiring**
- `api/app.py`: the default `parse` comes from `make_parser()`.
- `cli.py`: uses the same `make_parser()`, and its docstring gets updated.
- **Per-IP rate limit on `POST /sessions/{id}/messages`.** A small in-memory token bucket in `api/app.py`, about 20 per minute, returning a 429 with `{"detail": ...}`. It needs no new dependency, and it stops a script from burning credit. Add a test for it.
- `requirements.txt`: add `anthropic==<newest 1.x at least two weeks old>` and `openai==<same rule>`. Check both install on Python 3.11 and 3.13.
- `.env.example`: add `ANTHROPIC_API_KEY=`, plus `LLM_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL` for Groq, commented.
- **Manual step:** set a $5 monthly spend limit in the Claude Console.

**New `backend/tests/test_llm_parser.py`.** It uses fake clients and no network (NFR-4), and covers:
- a valid request
- valid feedback when `live`
- a validator failure, then success (one retry)
- two failures (keyword fallback)
- a refusal (fallback)
- an API error (fallback)
- `make_parser()` choosing the right parser from env vars

## Phase 2: parser benchmark

1. **Write `eval/parser_test.csv` by hand before any LLM output is seen.** It has about 40 messages: Flows A–D, vague requests, typos, feedback phrasings, and messages that name products. The columns are `text, live, kind, action, slot, vibes, formality, season, weather, named_items`.
2. **New `backend/src/stylist/jobs/parser_report.py`**, following `names_report.py` and `tagging_report.py`. It runs keyword rules, Haiku 5.5, Groq Llama 3.3 70B, and a local 8B on Ollama (`ollama pull llama3.1:8b` or `qwen3:8b`). Each LLM runs only if it's configured or reachable. It reports:
   - per-field accuracy and exact match
   - p50 and p90 latency
   - retry and fallback rate
   - cost per turn, from `response.usage` for Haiku

   It writes `eval/parser_report.md`. The prompt is frozen before the first run. Any later change is disclosed with a baseline line, like `names_report.BASELINE`.
3. CI: add `parser_report.py --keyword-only` to the smoke-test step.

## Phase 3: Haiku vibe tagger (FR-TAG-5, P1)

1. **New `backend/src/stylist/jobs/llm_tag.py`.**
   - It runs offline over the 344 products, using `name`, `description`, `fabric`, and `image_alt_text` from `data/seed_catalog.csv`.
   - Each call is `messages.parse` with a `vibes: list[Vibe]` schema.
   - Results are cached to JSON, so re-runs are free.
   - Cost is about $0.05 a run.
2. It writes to `item_tags` with `source="llm"`. `tagging_report.read_predictions(source=...)` already takes a source, so the report gains a CLIP vs. Haiku vs. merged vibe table (FR-TAG-5's acceptance criterion).
3. Honesty: the tagging prompt is written without looking at gold-set items, and the report says so.
4. If the merged tags beat CLIP alone, `catalog_store` uses them for vibe. Season and formality stay on CLIP.

## Phase 4: Docker Compose (Day 3)

`docker-compose.yml` with:
- an `api` service: Python image, `.env` passed through, `STYLIST_NO_CLIP` optional
- a `web` service

`data/` is mounted as a volume. The README gets a `docker compose up` section.

## Docs

Update after each phase:
- **README:** status, plus parser and vibe results.
- **TECH_SPEC:** the LLM row becomes Claude Haiku 5.5 through structured outputs, with Groq in the benchmark. Add design decisions for the fallback chain and the per-IP limit.
- **PRD:** milestone status, and FR-TAG-5 result.

## Verification

- `python -m pytest backend/tests`: everything passes, with no keys set.
- With `ANTHROPIC_API_KEY` in `.env`:
  - `python backend/src/stylist/cli.py --demo --no-clip` plays Flows A–D through Haiku.
  - Without the key, the same run uses keyword rules.
- Run the API and `cd web && npm run dev`: chat in the browser, with a chat turn under 4 s.
- An invalid key still gets an answer, through the fallback, with a log line.
- More than 20 messages a minute from one IP returns a 429.
- With `ollama serve` running, the Ollama env vars give the CLI demo through the local model.
- `parser_report.py` writes the four-way table. Check the Haiku spend in the Console after a run (expect about $0.01).
- `llm_tag.py`, then `tagging_report.py`: the CLIP-vs-Haiku vibe table appears.
- `docker compose up`: the app works at localhost:5173.
