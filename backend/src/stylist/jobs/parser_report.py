"""Benchmark the chat parsers on the hand-written messages in eval/parser_test.csv (FR-REQ-2).

Usage (from the repo root):
    python backend/src/stylist/jobs/parser_report.py                 # every parser that's set up
    python backend/src/stylist/jobs/parser_report.py --keyword-only  # no LLM calls, prints only (CI)

Parsers: keyword rules always; Claude Haiku 5.5 with ANTHROPIC_API_KEY; gpt-oss-120b on Groq's
free tier with GROQ_API_KEY; a local model on Ollama when it's running (OLLAMA_MODEL, default
llama3.1:8b). Keys are read from .env. The full run writes eval/parser_report.md.

Labels: blank means empty or null; ";" separates list items; "|" separates alternatives that all
count ("swap|reject"). Rows with in_scope=0 ask for something the system can't express yet;
they're listed with each parser's answer but not scored.

Scoring, per in-scope row (a wrong kind makes every field wrong):
    request:  vibe (the first predicted vibe is a labeled one), formality (exact, and within 1),
              season, weather (as a set), named items (same pieces; case, plurals, and words
              like "the" or "those" don't matter, and one extra or missing word is allowed)
    feedback: action, slot, and a nudge's direction or vibe
    exact:    every field of the row is right

The messages were written by hand before any LLM parsed them. The prompt's examples come from
the PRD's Flows A-D, not from this file. The prompt was changed once before the first run, to
count described pieces ("this cashmere sweater") as named items, matching how the messages were
labeled. Changes after the first run are listed in PROMPT_CHANGES and printed in every report.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import statistics
import sys
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

# Run as a plain script: make `import stylist...` work from backend/src.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv  # noqa: E402

from stylist.adapters.llm_parser import (  # noqa: E402
    SYSTEM_PROMPT, ClaudeParser, Details, LLMParser, OpenAICompatParser,
)
from stylist.core import keyword_parser  # noqa: E402
from stylist.core.models import FeedbackIntent, ParsedRequest  # noqa: E402
from stylist.jobs.ingest import ROOT  # noqa: E402

TEST_CSV = ROOT / "eval" / "parser_test.csv"
REPORT_MD = ROOT / "eval" / "parser_report.md"
PROMPT_CHANGES: list[str] = []  # one line per prompt change made after the first run

# Claude Haiku 5.5 list prices, $ per million tokens (prompts under 100K tokens).
CLAUDE_PRICE = {"input": 0.10, "output": 0.50}
GROQ_URL, GROQ_MODEL = "https://api.groq.com/openai/v1", "openai/gpt-oss-120b"
# Groq's free tier allows gpt-oss-120b 8,000 tokens a minute, and one message uses about 2,100
# (the prompt plus the model's reasoning): about 4 messages a minute. Calls are spaced to fit, so
# the benchmark measures accuracy rather than the rate limit.
GROQ_PAUSE = 16.0
OLLAMA_URL = "http://localhost:11434"
FILLER = {"the", "a", "an", "my", "those", "these", "this", "that", "one", "of", "some", "pair", "aritzia"}

REQUEST_FIELDS = ["vibe", "formality", "formality ±1", "season", "weather", "named items"]
FEEDBACK_FIELDS = ["action", "slot", "nudge"]
Parsed = ParsedRequest | FeedbackIntent


# ------------------------------------------------------------ labels

def alternatives(cell: str) -> list[str]:
    """'swap|reject' -> ['swap', 'reject']; '' -> ['']."""
    return [a.strip() for a in cell.split("|")]


def as_list(cell: str) -> list[str]:
    return [v.strip() for v in cell.split(";") if v.strip()]


def read_rows(path: Path = TEST_CSV) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ------------------------------------------------------------ scoring

def piece_words(text: str) -> set[str]:
    out = set()
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        if word in FILLER:
            continue
        if re.search(r"(?:ss|sh|ch|x)es$", word):
            word = word[:-2]  # dresses -> dress
        elif len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]  # pants -> pant
        out.add(word)
    return out


def same_piece(gold: str, predicted: str) -> bool:
    """Named items match when one's words contain the other's, give or take one word."""
    a, b = piece_words(gold), piece_words(predicted)
    small, large = sorted((a, b), key=len)
    return bool(small) and small <= large and len(large) - len(small) <= 1


def same_pieces(gold: list[str], predicted: list[str]) -> bool:
    if len(gold) != len(predicted):
        return False
    left = list(predicted)
    for g in gold:
        match = next((p for p in left if same_piece(g, p)), None)
        if match is None:
            return False
        left.remove(match)
    return True


def score_request(row: dict, got: ParsedRequest) -> dict[str, bool]:
    vibes = [as_list(a) for a in alternatives(row["vibes"])]
    allowed = {v for alt in vibes for v in alt}
    vibe_ok = (got.vibes[0] in allowed) if got.vibes else not allowed

    golds = [int(a) if a else None for a in alternatives(row["formality"])]
    exact = got.formality in golds
    near = exact or any(g is not None and got.formality is not None and abs(g - got.formality) <= 1 for g in golds)

    seasons = [a or None for a in alternatives(row["season"])]
    weathers = [set(as_list(a)) for a in alternatives(row["weather"])]
    return {
        "vibe": vibe_ok, "formality": exact, "formality ±1": near,
        "season": got.season in seasons,
        "weather": set(got.weather) in weathers,
        "named items": same_pieces(as_list(row["named_items"]), got.named_items),
    }


def feedback_fields(row: dict) -> list[str]:
    """Slot is scored when the label names one; the nudge when the label is a nudge."""
    return ["action"] + (["slot"] if row["slot"] else []) + (["nudge"] if "nudge" in row["action"] else [])


def score_feedback(row: dict, got: FeedbackIntent) -> dict[str, bool]:
    scores = {"action": got.action in alternatives(row["action"]),
              "slot": got.slot in alternatives(row["slot"]),
              "nudge": ((not row["formality"] or got.formality in [int(a) for a in alternatives(row["formality"])])
                        and (not row["vibes"] or got.vibe in alternatives(row["vibes"])))}
    return {name: scores[name] for name in feedback_fields(row)}


def score_row(row: dict, got: Parsed) -> dict[str, bool]:
    """Field -> right or wrong. A request scored as feedback (or the other way) is wrong throughout."""
    if row["kind"] == "request":
        if not isinstance(got, ParsedRequest):
            return {"kind": False, **{f: False for f in REQUEST_FIELDS}}
        return {"kind": True, **score_request(row, got)}
    if not isinstance(got, FeedbackIntent):
        return {"kind": False, **{f: False for f in feedback_fields(row)}}
    return {"kind": True, **score_feedback(row, got)}


# ------------------------------------------------------------ running

@dataclass
class Result:
    row: dict
    got: Parsed
    details: Details
    fields: dict[str, bool] = field(default_factory=dict)

    @property
    def exact(self) -> bool:
        return all(ok for name, ok in self.fields.items() if name != "formality ±1")


@dataclass
class Run:
    name: str
    results: list[Result]
    cost_per_turn: Callable[[Details], float] = lambda d: 0.0


def parse_keyword(text: str, live: bool) -> tuple[Parsed, Details]:
    started = time.perf_counter()
    got = keyword_parser.parse(text, live)
    return got, Details(source="keyword", latency_ms=round((time.perf_counter() - started) * 1000, 1))


def run(name: str, parse: Callable[[str, bool], tuple[Parsed, Details]], rows: list[dict],
        pause: float = 0.0, cost: Callable[[Details], float] = lambda d: 0.0) -> Run:
    results = []
    for i, row in enumerate(rows):
        if pause and i:
            time.sleep(pause)  # stay under a free tier's requests-per-minute limit
        got, details = parse(row["text"], row["live"] == "1")
        result = Result(row, got, details)
        if row["in_scope"] == "1":
            result.fields = score_row(row, got)
        results.append(result)
        print(f"  {name}: {i + 1}/{len(rows)}", end="\r", file=sys.stderr)
    print(file=sys.stderr)
    return Run(name, results, cost)


def warm_up(parser: LLMParser) -> None:
    """One untimed call first, so loading a local model or opening a connection isn't measured."""
    parser.parse_with_details("cozy fall Sunday, running errands", False)  # a prompt example, not a test row


def ollama_model_ready(model: str, url: str = OLLAMA_URL) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=3) as response:
            names = {m["name"] for m in json.load(response)["models"]}
    except OSError:
        return False
    return model in names or f"{model}:latest" in names


def claude_cost(d: Details) -> float:
    return (d.input_tokens * CLAUDE_PRICE["input"] + d.output_tokens * CLAUDE_PRICE["output"]) / 1e6


def configured_parsers(env=os.environ) -> list[tuple[str, LLMParser, float, Callable[[Details], float]]]:
    """(label, parser, pause between calls, cost per turn) for each LLM that's set up."""
    parsers = []
    if env.get("ANTHROPIC_API_KEY"):
        parsers.append(("Claude Haiku 5.5", ClaudeParser(env["ANTHROPIC_API_KEY"]), 0.0, claude_cost))
    if env.get("GROQ_API_KEY"):
        groq = OpenAICompatParser(GROQ_URL, env["GROQ_API_KEY"], env.get("GROQ_MODEL") or GROQ_MODEL, timeout=10.0)
        parsers.append((f"{groq.model} (Groq, free tier)", groq, GROQ_PAUSE, lambda d: 0.0))
    model = env.get("OLLAMA_MODEL") or "llama3.1:8b"
    if ollama_model_ready(model):
        local = OpenAICompatParser(f"{OLLAMA_URL}/v1", "ollama", model, timeout=60.0)
        parsers.append((f"{model} (Ollama, local)", local, 0.0, lambda d: 0.0))
    return parsers


# ------------------------------------------------------------ the report

def pct(hits: int, total: int) -> str:
    return f"{hits}/{total} ({hits / total:.0%})" if total else "n/a"


def field_score(run: Run, name: str, kind: str | None = None) -> str:
    scored = [r for r in run.results if r.fields and (kind is None or r.row["kind"] == kind) and name in r.fields]
    return pct(sum(r.fields[name] for r in scored), len(scored))


def describe(got: Parsed) -> str:
    if isinstance(got, FeedbackIntent):
        return "feedback " + json.dumps(got.model_dump(exclude_none=True, exclude={"item_id"}))
    fields = got.model_dump(exclude={"text"})
    return "request " + json.dumps({k: v for k, v in fields.items() if v not in (None, [])})


def fallback_reasons(run: Run) -> str:
    """Why messages went to keyword rules: rate limits, other API errors, or two invalid answers."""
    counts = {"rate limited": 0, "API error": 0, "invalid twice": 0}
    for r in run.results:
        if r.details.source != "keyword" or run.name == "Keyword rules":
            continue
        last = r.details.problems[-1] if r.details.problems else ""
        reason = "rate limited" if last.startswith("RateLimitError") else \
            "API error" if re.match(r"\w+Error: |the model declined", last) else "invalid twice"
        counts[reason] += 1
    return ", ".join(f"{n} {reason}" for reason, n in counts.items() if n) or "none"


def milliseconds(ms: float) -> str:
    return "<1 ms" if ms < 1 else f"{ms:,.0f} ms"


def cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def build_report(runs: list[Run], rows: list[dict]) -> str:
    in_scope = [r for r in rows if r["in_scope"] == "1"]
    examples = set(re.findall(r"Message: (.+)", SYSTEM_PROMPT))
    overlap = [r["text"] for r in rows if r["text"] in examples]
    lines = [
        "# Parser benchmark",
        "",
        f"Generated by `backend/src/stylist/jobs/parser_report.py` from `eval/parser_test.csv`: {len(rows)} messages "
        f"written by hand, {len(in_scope)} of them in scope "
        f"({sum(r['kind'] == 'request' for r in in_scope)} requests, "
        f"{sum(r['kind'] == 'feedback' for r in in_scope)} feedback).",
        "",
        "- The messages were written and labeled before any LLM parsed them. The prompt's examples come from the "
        f"PRD's Flows A–D; {len(overlap)} test messages match one.",
        "- The prompt was changed once before the first run: described pieces (\"this cashmere sweater\") count as "
        "named items, matching the labels.",
        f"- Prompt changes after the first run: {'; '.join(PROMPT_CHANGES) if PROMPT_CHANGES else 'none'}.",
        "- Latency is measured on a MacBook Pro (M1 Pro, 16 GB) over home Wi-Fi, after one untimed warm-up call.",
        f"- Each parser ran once, and LLM answers vary between runs. One message is "
        f"{100 / len(in_scope):.1f} points of the exact score, so a gap of one or two messages is within noise.",
        "",
        "## Results",
        "",
        "| | " + " | ".join(run.name for run in runs) + " |",
        "|---|" + "---|" * len(runs),
    ]

    def row(label: str, values: list[str]) -> None:
        lines.append(f"| {label} | " + " | ".join(values) + " |")

    row("**Exact (every field right)**", [pct(sum(r.exact for r in run.results if r.fields), len(in_scope))
                                          for run in runs])
    row("Request or feedback", [field_score(run, "kind") for run in runs])
    for name in REQUEST_FIELDS:
        row(f"Request: {name}", [field_score(run, name, "request") for run in runs])
    for name in FEEDBACK_FIELDS:
        row(f"Feedback: {name}" + (" (nudges only)" if name == "nudge" else ""),
            [field_score(run, name, "feedback") for run in runs])
    latencies = [[r.details.latency_ms for r in run.results] for run in runs]
    row("Latency p50", [milliseconds(statistics.median(ms)) for ms in latencies])
    row("Latency p90", [milliseconds(statistics.quantiles(ms, n=10)[-1]) for ms in latencies])
    row("Retried (invalid first answer)", [pct(sum(r.details.attempts > 1 for r in run.results), len(rows))
                                           if run.name != "Keyword rules" else "n/a" for run in runs])
    row("Fell back to keyword rules", [pct(sum(r.details.source == "keyword" for r in run.results), len(rows))
                                       if run.name != "Keyword rules" else "n/a" for run in runs])
    row("Why it fell back", [fallback_reasons(run) if run.name != "Keyword rules" else "n/a" for run in runs])
    row("Cost per message", [f"${statistics.mean(run.cost_per_turn(r.details) for r in run.results):.5f}"
                             for run in runs])

    for run in runs:
        wrong = [r for r in run.results if r.fields and not r.exact]
        lines += ["", f"## Misses: {run.name} ({len(wrong)})", ""]
        if not wrong:
            lines.append("None.")
            continue
        lines += ["| Message | Wrong | Parsed as |", "|---|---|---|"]
        for r in wrong:
            bad = ", ".join(name for name, ok in r.fields.items() if not ok and name != "formality ±1")
            fallback = " (keyword fallback)" if r.details.source == "keyword" and run.name != "Keyword rules" else ""
            lines.append(f"| {cell(r.row['text'])} | {bad} | `{cell(describe(r.got))}`{fallback} |")

    lines += ["", "## Out of scope", "",
              "Messages the system can't express yet, with what each parser made of them.", ""]
    for i, row_ in enumerate(rows):
        if row_["in_scope"] == "1":
            continue
        lines.append(f"- **{row_['text']}** ({row_['notes'].split('OUT OF SCOPE: ')[-1]})")
        for run in runs:
            lines.append(f"  - {run.name}: `{describe(run.results[i].got)}`")
    return "\n".join(lines) + "\n"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--keyword-only", action="store_true", help="score keyword rules only and print the result")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")

    rows = read_rows()
    runs = [run("Keyword rules", parse_keyword, rows)]
    if not args.keyword_only:
        for label, llm, pause, cost in configured_parsers():
            warm_up(llm)
            runs.append(run(label, llm.parse_with_details, rows, pause, cost))

    report = build_report(runs, rows)
    if args.keyword_only:
        print(report)
        return
    REPORT_MD.write_text(report, encoding="utf-8")
    print(report)
    print(f"Wrote {REPORT_MD.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
