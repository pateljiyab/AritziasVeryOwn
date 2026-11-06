"""Turn a chat message into a ParsedRequest or a FeedbackIntent with an LLM (FR-REQ-2).

ClaudeParser (Claude Haiku 5.5 with structured outputs) is what the app uses when
ANTHROPIC_API_KEY is set. OpenAICompatParser talks to any OpenAI-compatible server, such as
Groq's free tier or a local Ollama model, for the parser benchmark and offline development.

Both ask for an LLMParse (below) and then build the core models from it, so the core
validators still run (a swap needs a slot, a nudge needs a direction). Invalid output is retried
once with the error added to the message, then the message goes to the keyword rules. Any API
error, refusal, or timeout also goes to the keyword rules, so a chat turn never fails because of
the LLM.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from stylist.core import keyword_parser
from stylist.core.models import Color, FeedbackIntent, ParsedRequest, Season, Slot, Vibe, Weather
from stylist.core.session import Parse

log = logging.getLogger("stylist.llm")

CLAUDE_MODEL = "claude-haiku-5-5"


# ------------------------------------------------------------ what the LLM fills in

class SlotColor(BaseModel):
    slot: Slot
    color: Color


class LLMRequest(BaseModel):
    vibes: list[Vibe] = Field(default_factory=list)  # most important first; the first 3 are kept
    formality: Literal[1, 2, 3, 4, 5] | None = None
    season: Season | None = None
    weather: list[Weather] = Field(default_factory=list)
    named_items: list[str] = Field(default_factory=list)
    max_total: float | None = None
    max_item: float | None = None
    colors: list[SlotColor] = Field(default_factory=list)  # a list, not a dict: simpler for structured outputs


class LLMFeedback(BaseModel):
    action: Literal["lock", "unlock", "reject", "swap", "nudge", "accept", "budget", "cheaper", "remove"]
    slot: Slot | None = None
    formality: Literal[-1, 1] | None = None
    vibe: Vibe | None = None
    max_total: float | None = None
    max_item: float | None = None
    color: Color | Literal["any"] | None = None


class LLMParse(BaseModel):
    """The LLM's answer. No text or item_id: the message text is added in code, and chat names
    pieces by slot."""

    kind: Literal["request", "feedback"]
    request: LLMRequest | None = None
    feedback: LLMFeedback | None = None


def to_core(answer: LLMParse, text: str, live: bool) -> ParsedRequest | FeedbackIntent:
    """Build the core model, running its validators. Raises ValueError (ValidationError is one)."""
    if answer.kind == "feedback":
        if not live:
            raise ValueError("there is no outfit on screen, so the message must be a request")
        if answer.feedback is None:
            raise ValueError('kind is "feedback" but the feedback field is empty')
        return FeedbackIntent(**answer.feedback.model_dump())
    request = answer.request or LLMRequest()  # a vague request ("something nice") may come back empty
    fields = request.model_dump()
    fields["vibes"] = list(dict.fromkeys(fields["vibes"]))[:3]
    fields["colors"] = {pair.slot: pair.color for pair in reversed(request.colors)}  # the first one for a slot wins
    return ParsedRequest(text=text.strip(), **fields)


SYSTEM_PROMPT = """\
You turn one chat message sent to an outfit stylist into JSON. The stylist builds outfits from \
Aritzia's catalog.

The message is either a new request or feedback on the outfit on screen.
- If no outfit is on screen, it is always a request.
- If an outfit is on screen, it is feedback when it changes that outfit ("different shoes", \
"keep the coat", "dressier", "perfect"), and a request when it asks for something new \
("what about a wedding instead?").

A request ("kind": "request") fills in "request":
- vibes: up to 3, most important first, only the ones the message implies. cozy: comfy, \
lounging. polished: office, work, interview. going-out: party, night out, date, dinner, drinks, \
birthday, rooftop. cute: playful, fun. off-duty: casual weekend, errands, brunch. chic: elegant, \
elevated. athleisure: gym, yoga, hiking, sporty. minimal: simple, clean. romantic: floral, \
flowy, feminine. edgy: leather, grunge, moody.
- formality from 1 to 5, or null if the message gives no hint: 1 lounge or gym, 2 casual \
(errands, brunch, weekend), 3 smart casual (dinner, date, party), 4 business (office, \
interview), 5 formal (wedding, gala, black tie). A style word ("dressy", "casual") wins over the \
occasion.
- season: spring, summer, fall or winter, only if the message names or clearly implies one. \
Otherwise null.
- weather: each of cold, cool, mild, warm, hot, rainy that the message mentions. "Might get \
chilly" is cool. Snow is cold.
- named_items: the pieces the user wants the outfit built around, as typed but without words \
like "the", "my", "those" or "one of those". That includes product names ("build around the \
Effortless Pant" gives "Effortless Pant") and described pieces the user has or wants to wear \
("i have this cashmere sweater" gives "cashmere sweater"). A bare category with no description \
("a top", "some shoes") is not one.
- max_total: the most the whole outfit may cost, in dollars, when the message sets a budget \
("under $200", "i have $150 to spend"). max_item: the most any one piece may cost ("nothing over \
$80", "$50 each"). Otherwise null. A price that isn't a limit ("a $50 gift card") sets neither.
- colors: a color the user wants for a particular piece ("black shoes" gives {"slot": "shoes", \
"color": "black"}). Use the nearest of: black, white, cream, beige, camel, brown, grey, navy, denim, \
olive, red, burgundy, pink, orange, yellow, green, teal, blue, purple, metallic, multi. Tan is camel, \
gray is grey, ivory is cream, gold or silver is metallic, and floral, striped or printed is multi. \
A color for the whole look ("all black") is not one.

Feedback ("kind": "feedback") fills in "feedback" with an action:
- lock: keep a piece ("keep the coat", "love the shoes"). Needs a slot.
- unlock: stop keeping a piece. Needs a slot.
- reject: never show that piece again ("not that top", "hate the bag"). Needs a slot.
- swap: show a different one in a slot ("different shoes"). Needs a slot. When the user asks for \
a piece in a color ("black shoes", "can i get the coat in navy", "swap the shoes for black ones"), \
it is a swap with that slot and color, using the color names above; "any color shoes" has color \
"any". Otherwise color is null. A piece the outfit doesn't have yet ("a black dress instead", \
"add a bag") is a swap too.
- nudge: change the whole outfit. Set formality to 1 for dressier or -1 for more relaxed, or \
set vibe ("edgier" is edgy).
- accept: the user is happy with the outfit ("perfect", "I'll take it").
- budget: set the budget of the outfit on screen, with max_total and max_item as in a request \
("keep the price under $200" is max_total 200; "nothing over $80" is max_item 80). Both null \
removes the budget ("price doesn't matter"). A message that only sets a budget is this feedback, \
not a new request; one that also asks for something new ("a wedding instead, under $300") is a \
request with max_total.
- cheaper: make it cost less. With a slot, a cheaper piece in that slot ("cheaper shoes", "the \
coat is too expensive", "swap the coat for something cheaper"); with no slot, a cheaper outfit \
overall ("make it cheaper", "this is too expensive").
- remove: leave a piece out of the outfit altogether, not replace it ("no bag", "i don't need a \
coat", "skip the accessories"). Only outerwear and accessory can be left out; for any other slot \
it is a reject.
Slots: top, bottom, dress (also jumpsuits), outerwear (coats, jackets, blazers), shoes, \
accessory (bags, scarves, belts, hats).

Examples:
Outfit on screen: no. Message: cozy fall Sunday, running errands
{"kind": "request", "request": {"vibes": ["cozy", "off-duty"], "formality": 2, "season": "fall", \
"weather": [], "named_items": [], "max_total": null, "max_item": null, "colors": []}, "feedback": null}
Outfit on screen: no. Message: rooftop birthday dinner, it might get chilly
{"kind": "request", "request": {"vibes": ["going-out"], "formality": 3, "season": null, \
"weather": ["cool"], "named_items": [], "max_total": null, "max_item": null, "colors": []}, "feedback": null}
Outfit on screen: no. Message: build something around the Effortless Pant
{"kind": "request", "request": {"vibes": [], "formality": null, "season": null, "weather": [], \
"named_items": ["Effortless Pant"], "max_total": null, "max_item": null, "colors": []}, "feedback": null}
Outfit on screen: no. Message: office outfit, nothing over $100
{"kind": "request", "request": {"vibes": ["polished"], "formality": 4, "season": null, "weather": [], \
"named_items": [], "max_total": null, "max_item": 100, "colors": []}, "feedback": null}
Outfit on screen: no. Message: date night, i want black shoes
{"kind": "request", "request": {"vibes": ["going-out"], "formality": 3, "season": null, "weather": [], \
"named_items": [], "max_total": null, "max_item": null, "colors": [{"slot": "shoes", "color": "black"}]}, \
"feedback": null}
Outfit on screen: yes. Message: different shoes
{"kind": "feedback", "request": null, "feedback": {"action": "swap", "slot": "shoes", \
"formality": null, "vibe": null, "max_total": null, "max_item": null, "color": null}}
Outfit on screen: yes. Message: can i get black shoes
{"kind": "feedback", "request": null, "feedback": {"action": "swap", "slot": "shoes", \
"formality": null, "vibe": null, "max_total": null, "max_item": null, "color": "black"}}
Outfit on screen: yes. Message: make it edgier
{"kind": "feedback", "request": null, "feedback": {"action": "nudge", "slot": null, \
"formality": null, "vibe": "edgy", "max_total": null, "max_item": null, "color": null}}
Outfit on screen: yes. Message: keep the price under $200
{"kind": "feedback", "request": null, "feedback": {"action": "budget", "slot": null, \
"formality": null, "vibe": null, "max_total": 200, "max_item": null, "color": null}}
Outfit on screen: yes. Message: cheaper coat please
{"kind": "feedback", "request": null, "feedback": {"action": "cheaper", "slot": "outerwear", \
"formality": null, "vibe": null, "max_total": null, "max_item": null, "color": null}}
Outfit on screen: yes. Message: i don't need a bag
{"kind": "feedback", "request": null, "feedback": {"action": "remove", "slot": "accessory", \
"formality": null, "vibe": null, "max_total": null, "max_item": null, "color": null}}
Outfit on screen: yes. Message: looks good
{"kind": "feedback", "request": null, "feedback": {"action": "accept", "slot": null, \
"formality": null, "vibe": null, "max_total": null, "max_item": null, "color": null}}

Reply with the JSON object only."""


def user_content(text: str, live: bool, problem: str | None = None) -> str:
    content = f"Outfit on screen: {'yes' if live else 'no'}. Message: {text}"
    if problem:
        content += f"\n\nYour last answer was invalid: {problem}. Answer again with a corrected JSON object."
    return content


# ------------------------------------------------------------ the retry and fallback loop

@dataclass
class Details:
    """How one message was parsed, for the turn log and the parser benchmark."""

    source: Literal["llm", "keyword"] = "llm"
    attempts: int = 0  # LLM calls made (2 means the first answer was invalid and retried)
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    problems: list[str] = field(default_factory=list)  # why each failed attempt failed


class APIFailure(Exception):
    """The provider call failed (network, rate limit, refusal, ...); go straight to keyword rules."""


class LLMParser:
    """A Parse callable. Subclasses implement ask(): one call, returning the parsed answer."""

    name = "llm"

    def ask(self, content: str, details: Details) -> LLMParse:
        raise NotImplementedError

    def __call__(self, text: str, live: bool) -> ParsedRequest | FeedbackIntent:
        return self.parse_with_details(text, live)[0]

    def parse_with_details(self, text: str, live: bool) -> tuple[ParsedRequest | FeedbackIntent, Details]:
        details = Details()
        started = time.perf_counter()
        problem = None
        try:
            for _ in range(2):  # the first answer, then one retry if it's invalid (FR-REQ-2)
                details.attempts += 1
                try:
                    result = to_core(self.ask(user_content(text, live, problem), details), text, live)
                except ValueError as exc:  # ValidationError from the schema or a core validator
                    problem = short_error(exc)
                    details.problems.append(problem)
                    continue
                details.latency_ms = round((time.perf_counter() - started) * 1000, 1)
                return result, details
        except APIFailure as exc:
            details.problems.append(str(exc))
        log.warning("%s fell back to keyword rules for %r: %s", self.name, text, "; ".join(details.problems))
        details.source = "keyword"
        details.latency_ms = round((time.perf_counter() - started) * 1000, 1)
        return keyword_parser.parse(text, live), details


def short_error(exc: ValueError) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(f"{'.'.join(map(str, e['loc'])) or 'answer'}: {e['msg']}" for e in exc.errors()[:3])
    return str(exc)


# ------------------------------------------------------------ providers

class ClaudeParser(LLMParser):
    """Claude through structured outputs: the reply is constrained to the LLMParse schema."""

    def __init__(self, api_key: str | None = None, model: str = CLAUDE_MODEL, client=None, timeout: float = 6.0):
        import anthropic
        import httpx2

        self.anthropic = anthropic
        # Keep the connection to the API open between chat turns: the HTTP client closes idle
        # connections after 5 s by default, and opening a new one from Render sometimes took long
        # enough that a 3.5 s timeout sent the message to keyword rules. One SDK retry covers a
        # dropped or stale connection; a reply that never comes still falls back after the timeout.
        http = anthropic.DefaultHttpxClient(limits=httpx2.Limits(max_connections=20, max_keepalive_connections=5,
                                                                 keepalive_expiry=300.0))
        self.client = client or anthropic.Anthropic(api_key=api_key, timeout=anthropic.Timeout(timeout, connect=3.0),
                                                    max_retries=1, http_client=http)
        self.model = model
        self.name = model

    def ask(self, content: str, details: Details) -> LLMParse:
        try:
            response = self.client.messages.parse(
                model=self.model, max_tokens=1024, system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": content}],
                output_format=LLMParse, output_config={"effort": "low"},
            )
        except self.anthropic.AnthropicError as exc:
            raise APIFailure(f"{type(exc).__name__}: {exc}") from exc
        details.input_tokens += response.usage.input_tokens
        details.output_tokens += response.usage.output_tokens
        if response.stop_reason == "refusal":
            raise APIFailure("the model declined the message")
        if response.parsed_output is None:
            raise ValueError(f"no JSON answer (stop reason {response.stop_reason})")
        return response.parsed_output


class OpenAICompatParser(LLMParser):
    """Any OpenAI-compatible chat API: Groq (https://api.groq.com/openai/v1) or a local Ollama
    server (http://localhost:11434/v1, any non-empty key). The JSON schema is sent when the
    server accepts it; otherwise plain JSON mode, with the shape given by the prompt's examples."""

    def __init__(self, base_url: str, api_key: str, model: str, client=None, timeout: float = 15.0):
        import openai

        self.openai = openai
        self.client = client or openai.OpenAI(base_url=base_url, api_key=api_key, timeout=timeout, max_retries=0)
        self.model = model
        self.name = model
        self.use_schema = True

    def response_format(self) -> dict:
        if self.use_schema:
            return {"type": "json_schema", "json_schema": {"name": "LLMParse", "schema": LLMParse.model_json_schema()}}
        return {"type": "json_object"}

    def ask(self, content: str, details: Details) -> LLMParse:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": content}]
        try:
            try:
                response = self.client.chat.completions.create(
                    model=self.model, messages=messages, response_format=self.response_format())
            except self.openai.BadRequestError:
                if not self.use_schema:
                    raise
                self.use_schema = False  # this server or model doesn't take a JSON schema
                response = self.client.chat.completions.create(
                    model=self.model, messages=messages, response_format=self.response_format())
        except self.openai.OpenAIError as exc:
            raise APIFailure(f"{type(exc).__name__}: {exc}") from exc
        if response.usage:
            details.input_tokens += response.usage.prompt_tokens
            details.output_tokens += response.usage.completion_tokens
        return LLMParse.model_validate_json(response.choices[0].message.content or "")


def make_parser(env: Mapping[str, str] = os.environ) -> Parse:
    """The parser the app and the CLI use: Claude if ANTHROPIC_API_KEY is set, else an
    OpenAI-compatible server if LLM_BASE_URL and LLM_API_KEY are, else keyword rules."""
    if env.get("ANTHROPIC_API_KEY"):
        return ClaudeParser(env["ANTHROPIC_API_KEY"], model=env.get("CLAUDE_MODEL") or CLAUDE_MODEL)
    if env.get("LLM_BASE_URL") and env.get("LLM_API_KEY") and env.get("LLM_MODEL"):
        return OpenAICompatParser(env["LLM_BASE_URL"], env["LLM_API_KEY"], env["LLM_MODEL"])
    return keyword_parser.parse
