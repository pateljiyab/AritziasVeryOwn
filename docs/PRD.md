# AI Outfit Stylist: Product Requirements Document

| | |
|---|---|
| **Date** | October 19, 2026 |
| **Status** | Draft |
| **Companion doc** | [Technical Specification](TECH_SPEC.md) |

> **Disclaimer.** This is an independent educational project. It is not affiliated with, endorsed by, or sponsored by Aritzia. Product data is collected within the site's terms of use, and product images are not redistributed.

---

## 1. Problem & Motivation

Aritzia sells clothes; it doesn't help anyone decide what to wear. A shopper can filter by size, color, or category, and browse an editorial lookbook, but neither answers the question people actually ask on a Tuesday morning: *"I have a closet full of options. What do I put together today?"*

Lookbooks are static: they show one stylist's answer to a question you didn't ask. Filtered search is atomic: it returns individual items and leaves the hard part, composing them into something coherent, to the user. Neither responds to taste, and neither improves when you say "not quite."

This project treats styling as a conversation. The user describes a mood, an occasion, or the weather in plain language. The system regenerates around that feedback until the outfit is something the user would actually wear.

### Why this project

Beyond the product gap, this is a deliberate assistant for applying computer vision and embedding-based retrieval to a real, visually driven problem, not a toy dataset. Fashion is a demanding domain for these techniques:

- Labels like "cozy" or "going-out" are subjective and visual, which makes them a good test of zero-shot CLIP classification.
- Outfit coherence depends on relationships between items (color harmony, formality match), not on any single item, which is a retrieval-plus-scoring problem.
- The feedback loop needs a preference signal that changes within a session, which turns static retrieval into an interactive system.

---

## 2. Goals & Non-Goals

### Goals

1. **G1.** Turn a free-text style request into a complete, coherent outfit made of real Aritzia catalog items.
2. **G2.** Let the user refine that outfit conversationally, with each regeneration moving measurably closer to what they want.
3. **G3.** Build an automated tagging pipeline (season, vibe, formality) whose accuracy is validated against human labels.
4. **G4.** Resolve Aritzia product names typed by the user reliably.
5. **G5.** Ship a working, demoable web app that runs in real time.

### Non-Goals

- **Purchasing or checkout.** The system recommends; it does not sell. Links to product pages are fine.
- **Sizing and fit.** No size recommendations or body-shape modeling.
- **Inventory and stock.** The catalog is a snapshot. Live availability is out of scope.
- **Trend forecasting or editorial content.** The system styles from the catalog it has; it does not predict what's fashionable.
- **Official Aritzia integration.** No partner APIs, no brand affiliation.

---

## 3. Target Users & Personas

The core user owns or shops Aritzia, has a rough sense of the feeling they're going for, and wants help turning that feeling into a specific combination of pieces.

| Persona | Situation | What they say |
|---|---|---|
| **Maya, the weather-driven commuter** | Gets dressed in ten minutes and cares mostly that she's warm, appropriate for the office, and not boring. | *"It's 8°C and rainy, I have meetings all day. Something polished but comfortable."* |
| **Jordan, the event planner** | Has a specific occasion coming up and wants to explore options before committing. | *"Rooftop birthday dinner Saturday, going-out but not trying too hard."* |
| **Sam, the anchor-piece builder** | Owns (or has their eye on) one specific Aritzia piece and wants to build around it. | *"I just got the Effortless Pant in black. What do I wear with it for a weekend brunch?"* |

---

## 4. Key User Flows

### Flow A: Mood request
1. Maya types *"cozy fall Sunday, running errands."*
2. The system returns an outfit: a knit top, a relaxed bottom, a jacket, sneakers, and optionally an accessory. Each piece shows its image, name, brand line, and price.
3. Maya accepts. Done in one turn.

### Flow B: Occasion + weather request
1. Jordan types *"rooftop birthday dinner, it might get chilly."*
2. The system parses the request into occasion (dinner/party), vibe (going-out), weather (cool evening), and target formality (3 of 5), then returns an outfit that includes outerwear.

### Flow C: Named-item request
1. Sam types *"build something around the Effortless Pant."*
2. The system resolves "Effortless Pant" to the catalog item, locks it into the bottom slot, and fills the other slots around it.
3. If the name matches several items (e.g. different cuts or lengths), the system asks Sam to choose.

### Flow D: Refinement loop
1. After any generated outfit, the user can:
   - **Swap** a piece (*"different shoes"*), which replaces that slot only.
   - **Lock** a piece (*"keep the coat"*), which keeps it through all later regenerations.
   - **Reject** a piece (*"not that top"*), which excludes it for the rest of the session.
   - **More like this** (*"more like the blazer"*), which pushes later picks toward that item's style.
   - **Nudge the vibe** (*"a bit more relaxed"*, *"dressier"*), which shifts the target vibe or formality.
   - **Name a swap** (*"swap in the Effortless Pant"*), which resolves the item and puts it in its slot.
2. The system regenerates and shows what changed.
3. The loop repeats until the user **accepts**, and the system then shows a final summary of the outfit.

---

## 5. Functional Requirements

Priority key: **P0** = required for MVP · **P1** = expected for a strong demo · **P2** = nice to have.

### 5.1 Catalog (`FR-CAT`)

| ID | Requirement | Priority | Acceptance criteria |
|---|---|---|---|
| FR-CAT-1 | Store a product catalog with: name, brand line (e.g. Babaton, Wilfred, TNA, Sunday Best), slot, primary color, fabric, price, description, image, and product URL. | P0 | Every catalog record has all required fields populated, or an explicit null with a logged reason. |
| FR-CAT-2 | Assign every item to exactly one slot: `top`, `bottom`, `dress`, `outerwear`, `shoes`, `accessory`. | P0 | 100% of items have a valid slot; a sampled audit of 30 items shows 0 misassignments. |
| FR-CAT-3 | The catalog covers enough items per slot to make generation meaningful. | P0 | At least 150 items total *(proposed)*, at least 15 per slot *(proposed)*. |
| FR-CAT-4 | Collect data within the site's terms of use (rate-limited, respecting robots.txt), or fall back to a hand-collected catalog. | P0 | The collection script documents its rate limits; no blocked or disallowed paths are fetched. |
| FR-CAT-5 | Re-running ingestion updates existing records without creating duplicates. | P1 | Running ingestion twice produces the same item count. |

### 5.2 Tagging (`FR-TAG`)

| ID | Requirement | Priority | Acceptance criteria |
|---|---|---|---|
| FR-TAG-1 | Label each item with one or more **season** tags from a fixed set (see [Appendix A](#appendix-a-tag-taxonomy)). | P0 | 100% of items have at least 1 season tag. |
| FR-TAG-2 | Label each item with one or more **vibe** tags from a fixed set. | P0 | 100% of items have 1–3 vibe tags. |
| FR-TAG-3 | Label each item with a **formality** score on a 1–5 scale. | P0 | 100% of items have an integer formality score. |
| FR-TAG-4 | Derive tags from the product image (zero-shot vision) and the product description (LLM), then combine the two. | P0 | Each tag records its source (`clip`, `llm`, `merged`) and a confidence value. |
| FR-TAG-5 | Flag low-confidence or conflicting tags for manual review. | P1 | Items where the two sources disagree are listed in a review report. |
| FR-TAG-6 | Maintain a hand-labeled validation set for measuring tagging accuracy. | P0 | At least 50 items, stratified across slots *(proposed)*. |

### 5.3 Request Parsing (`FR-REQ`)

| ID | Requirement | Priority | Acceptance criteria |
|---|---|---|---|
| FR-REQ-1 | Accept free-text requests describing any mix of mood/vibe, occasion, weather, and named items. | P0 | All four request types in §4 parse without error. |
| FR-REQ-2 | Convert a request into structured constraints: target vibes, target formality, season or weather, required slots (e.g. outerwear when it's cold), and named items. | P0 | Parsed output validates against the schema in the tech spec. |
| FR-REQ-3 | When a request is too vague to act on (e.g. *"something nice"*), fall back to sensible defaults instead of failing. | P1 | Vague requests still produce an outfit. |
| FR-REQ-4 | When the request mentions weather, include or exclude outerwear accordingly. | P1 | Cold or rainy requests include outerwear in at least 90% of test cases *(proposed)*. |

### 5.4 Outfit Generation (`FR-GEN`)

| ID | Requirement | Priority | Acceptance criteria |
|---|---|---|---|
| FR-GEN-1 | Generate a complete outfit with one item per slot: either top + bottom or a dress, plus shoes, plus outerwear and an accessory when relevant. | P0 | No generated outfit has a dress together with a top or bottom, and none is missing a required slot. |
| FR-GEN-2 | Use only real catalog items, referenced by their actual product names. | P0 | Every item in an outfit exists in the catalog. |
| FR-GEN-3 | Score candidate outfits on vibe coherence, formality match, and color harmony, and return the highest-scoring one. | P0 | The score breakdown is logged for each generated outfit. |
| FR-GEN-4 | Respect hard constraints: locked items always appear, rejected items never do, and season matches. | P0 | 0 violations across the evaluation sessions. |
| FR-GEN-5 | Give a short natural-language explanation of why the outfit works. | P1 | Each outfit comes with a 1–3 sentence rationale. |
| FR-GEN-6 | Avoid repeating an identical outfit within a session unless the user asks for it. | P1 | No two consecutive outfits in a session are identical. |

### 5.5 Named-Item Resolution (`FR-NAME`)

| ID | Requirement | Priority | Acceptance criteria |
|---|---|---|---|
| FR-NAME-1 | Resolve product names the user types (*"the Effortless Pant"*) to catalog items, tolerating typos, missing words, and casing differences. | P0 | At least 90% top-1 accuracy on the named-item test set *(proposed)*. |
| FR-NAME-2 | When several items match closely, ask the user to choose. | P1 | Ambiguous queries trigger a choice prompt instead of a silent guess. |
| FR-NAME-3 | When nothing matches, say so and suggest the closest alternatives. | P1 | Queries with no match return a clear message and up to 3 suggestions. |

### 5.6 Feedback Loop (`FR-FB`)

| ID | Requirement | Priority | Acceptance criteria |
|---|---|---|---|
| FR-FB-1 | **Lock**: a locked item stays in its slot through every later regeneration until the user unlocks it. | P0 | Locked items persist across 100% of regenerations. |
| FR-FB-2 | **Reject**: a rejected item is excluded for the rest of the session. | P0 | Rejected items never reappear in the session. |
| FR-FB-3 | **Swap**: replace a single slot and leave the other slots unchanged unless they are now incompatible. | P0 | After a swap, slots that weren't swapped are unchanged in at least 80% of cases *(proposed)*. |
| FR-FB-4 | **More like this**: shift later selections toward the referenced item's style. | P1 | The next outfit's embedding similarity to the referenced item increases. |
| FR-FB-5 | **Nudge**: adjust the target vibe or formality from relative language (*"dressier"*, *"more relaxed"*). | P0 | A formality nudge moves the outfit's mean formality in the requested direction. |
| FR-FB-6 | Keep a session preference signal that builds up across turns, not just the most recent instruction. | P0 | The session state records the full feedback history and the current preference vector. |
| FR-FB-7 | Accept feedback in natural language and through UI controls (lock/reject buttons). | P1 | Both input paths produce the same state change. |

### 5.7 Session End (`FR-STOP`)

| ID | Requirement | Priority | Acceptance criteria |
|---|---|---|---|
| FR-STOP-1 | Keep iterating until the user explicitly accepts the outfit; do not stop after one best guess. | P0 | A session ends only on an accept intent or when the user leaves. |
| FR-STOP-2 | On acceptance, show a final summary: each piece with its image, name, brand, price, and link, plus the total price. | P1 | The summary renders for every accepted outfit. |
| FR-STOP-3 | Log turns-to-accept and the outcome of each session for evaluation. | P0 | Each session writes a log record. |

### 5.8 Interface (`FR-UI`)

| ID | Requirement | Priority | Acceptance criteria |
|---|---|---|---|
| FR-UI-1 | A web UI that shows the current outfit as a set of item cards (image, name, brand line, price). | P0 | The outfit renders after each turn. |
| FR-UI-2 | A chat input for requests and follow-up feedback, with visible conversation history. | P0 | Previous turns stay visible within the session. |
| FR-UI-3 | Lock and reject controls on each item card. | P1 | Clicking a control updates the session state and triggers regeneration. |
| FR-UI-4 | Show what changed between consecutive outfits. | P2 | Changed slots are visually highlighted. |

---

## 6. Non-Functional Requirements

| ID | Category | Requirement |
|---|---|---|
| NFR-1 | **Latency** | Regenerating an outfit takes 5 seconds or less end to end, p90 *(proposed)*. The first request may take longer while models load. |
| NFR-2 | **Cost** | At most 2 LLM calls per user turn *(proposed)*. Tagging is a one-time batch job, not done per request. |
| NFR-3 | **Reproducibility** | Pinned dependencies, seeded randomness, and versioned tagging output, so evaluation results can be reproduced. |
| NFR-4 | **Data ethics** | Rate-limited collection that respects the site's terms; images are cached locally for processing only and never redistributed; no personal user data is stored in the MVP. |
| NFR-5 | **Portability** | Runs locally on a laptop CPU, with an optional GPU only for the batch tagging step. |
| NFR-6 | **Observability** | Each turn logs the parsed request, candidate scores, and chosen outfit, for debugging and evaluation. |

---

## 7. Success Metrics

The original outline described success qualitatively. The thresholds below make it measurable; all of them are **proposed** and will be revisited once there is baseline data.

| Goal | Metric | Target *(proposed)* | How it's measured |
|---|---|---|---|
| G3 Tagging accuracy | Agreement with the hand-labeled validation set | At least 80% top-1 for season and vibe; formality within ±1 for at least 85% of items | 50-item stratified validation set (FR-TAG-6) |
| G1 Outfit quality | Share of outfits that human raters judge coherent (color, formality, vibe) | At least 70% rated coherent by a majority of 3 raters | Blind rating of 30–50 generated outfits |
| G2 Loop effectiveness | Median regenerations before acceptance | 4 or fewer | Session logs (FR-STOP-3) |
| G2 Loop effectiveness | Eventual acceptance rate | At least 75% of sessions | Session logs |
| G4 Named-item handling | Top-1 resolution accuracy | At least 90% | 30–50 query test set with typos and partial names |
| G5 Deliverable | Working real-time demo | All four flows in §4 work end to end | Recorded demo walkthrough |

---

## 8. Scope

### MVP (in scope)
- One brand (Aritzia, including its in-house lines), with a catalog of at least 150 items.
- The full tagging pipeline plus the validation set.
- A rule-based compatibility scorer.
- The conversational loop with lock, reject, swap, nudge, and accept.
- Named-item resolution.
- A Streamlit web UI.
- An evaluation harness for all of the metrics in §7.

### Stretch goals
| ID | Stretch goal | Why it matters |
|---|---|---|
| S-1 | Extend the catalog to a **second brand**. | Tests whether the tagging pipeline generalizes beyond one brand's photography and copywriting style. |
| S-2 | Train a **learned outfit-compatibility model** (CLIP embeddings + a Polyvore-style dataset) and compare it with the rule-based scorer. | Turns the scorer from hand-tuned heuristics into a learned model, with a direct A/B comparison. |
| S-3 | **User accounts** so preferences persist across sessions. | Extends the preference signal from a single session to a long-term taste profile. |
| S-4 | **Photo upload** of an item the user already owns, matched into outfits alongside Aritzia pieces. | Bridges the catalog and the user's real closet. |

---

## 9. Milestones

| # | Milestone | Deliverable | Exit criteria |
|---|---|---|---|
| M1 | **Catalog** | Collection script plus a stored catalog | FR-CAT-1 to FR-CAT-4 met |
| M2 | **Tagging** | CLIP and LLM tagging pipeline, plus the validation set | Tagging accuracy measured and reported |
| M3 | **Retrieval & scoring** | Embedding index, per-slot retrieval, and the rule-based scorer | Generates a valid outfit from a structured request |
| M4 | **Conversational layer** | Request parser, intent parser, and named-item resolver | All four flows in §4 work from the command line |
| M5 | **UI & feedback loop** | Streamlit app with session state | Demo flows work end to end in the browser |
| M6 | **Evaluation & write-up** | Metric results, rater study, and demo video | Every metric in §7 reported, whether or not it hits its target |

---

## 10. Risks & Open Questions

| Risk / question | Impact | Mitigation |
|---|---|---|
| Site structure changes or scraping restrictions block collection. | Without a catalog, nothing else works. | Fall back to a hand-collected seed catalog. Keep the pipeline independent of how data is collected. |
| CLIP doesn't reliably understand abstract vibes (e.g. "off-duty"). | Low tagging accuracy. | Engineer prompts with several descriptive phrasings per label; use the LLM pass over descriptions as a second signal; measure against the validation set. |
| A small catalog limits outfit variety. | Repetitive outfits, poor loop convergence. | Set a minimum per slot (FR-CAT-3); allow some slots, like the accessory, to be optional. |
| Coherence is subjective. | Noisy quality metric. | Use multiple raters with a written rubric; report inter-rater agreement. |
| LLM latency or cost per turn. | Slow demo, running costs. | Cap calls per turn (NFR-2); use structured output to avoid retries; cache parses of repeated requests. |
| Product photos are inconsistent (on a model vs. flat lay). | Noisy image embeddings. | Prefer one image type per item where available; note it in the evaluation write-up. |

**Open questions**
1. Should the accessory slot be optional by default, or always filled?
2. How should a request that mixes seasons be handled (e.g. *"summer dress for a fall wedding"*)?
3. What is the right size for the final catalog: depth in a few categories, or breadth across all of them?

---

## Appendix A: Tag Taxonomy

The taxonomy is a draft and will be refined during M2.

**Season** (multi-label): `spring`, `summer`, `fall`, `winter`

**Vibe** (multi-label, 1–3 per item):

| Tag | Description |
|---|---|
| `cozy` | Soft, warm, relaxed textures: knits, fleece, oversized cuts |
| `polished` | Tailored, clean lines, office-ready |
| `going-out` | Evening, statement, a little dressy or bold |
| `off-duty` | Casual, effortless, weekend |
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

## Appendix B: Glossary

| Term | Definition |
|---|---|
| **Slot** | A position in an outfit: top, bottom, dress, outerwear, shoes, or accessory. An outfit has at most one item per slot. |
| **Vibe** | The mood or aesthetic an item or outfit gives off (e.g. cozy, polished). |
| **Lock** | Pin an item so every later regeneration keeps it. |
| **Reject** | Exclude an item for the rest of the session. |
| **Nudge** | A relative adjustment to the target vibe or formality (e.g. "dressier"). |
| **Preference vector** | A session-level embedding that builds up the user's feedback and steers retrieval. |
| **Brand line** | Aritzia's in-house labels (Babaton, Wilfred, TNA, Sunday Best, etc.). |
