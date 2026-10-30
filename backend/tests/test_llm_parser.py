"""The LLM parser with fake clients (NFR-4): no network, no key. Covers the retry and the
keyword fallback (FR-REQ-2) for both providers, and which parser make_parser picks."""

import json
from types import SimpleNamespace

import anthropic
import httpx2
import openai
import pytest

from stylist.adapters.llm_parser import (
    ClaudeParser, LLMFeedback, LLMParse, LLMRequest, OpenAICompatParser, make_parser,
)
from stylist.core import keyword_parser
from stylist.core.models import FeedbackIntent, ParsedRequest


def request(**fields) -> LLMParse:
    return LLMParse(kind="request", request=LLMRequest(**fields))


def feedback(**fields) -> LLMParse:
    return LLMParse(kind="feedback", feedback=LLMFeedback(**fields))


class FakeClaude:
    """Stands in for anthropic.Anthropic: each call returns (or raises) the next canned answer."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []
        self.messages = self

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        stop = "refusal" if answer == "refusal" else "end_turn"
        return SimpleNamespace(usage=SimpleNamespace(input_tokens=1000, output_tokens=50), stop_reason=stop,
                               parsed_output=None if answer == "refusal" else answer)


def claude(*answers) -> tuple[ClaudeParser, FakeClaude]:
    fake = FakeClaude(*answers)
    return ClaudeParser(client=fake), fake


def test_request_keeps_the_original_text_for_clip():
    parser, fake = claude(request(vibes=["going-out"], formality=3, weather=["cool"]))

    parsed = parser("rooftop birthday dinner, it might get chilly ", False)

    assert parsed == ParsedRequest(text="rooftop birthday dinner, it might get chilly", vibes=["going-out"],
                                   formality=3, weather=["cool"])
    assert fake.calls[0]["model"] == "claude-haiku-5-5"
    assert fake.calls[0]["output_format"] is LLMParse
    assert "Outfit on screen: no" in fake.calls[0]["messages"][0]["content"]


def test_feedback_when_an_outfit_is_on_screen():
    parser, fake = claude(feedback(action="swap", slot="shoes"))

    assert parser("different shoes", True) == FeedbackIntent(action="swap", slot="shoes")
    assert "Outfit on screen: yes" in fake.calls[0]["messages"][0]["content"]


def test_vibes_are_deduplicated_and_capped_at_three():
    parser, _ = claude(request(vibes=["cozy", "cozy", "minimal", "chic", "edgy"]))

    assert parser("comfy but chic", False).vibes == ["cozy", "minimal", "chic"]


def test_invalid_answer_is_retried_once_with_the_reason():
    parser, fake = claude(feedback(action="swap"), feedback(action="swap", slot="top"))  # first has no slot

    parsed, details = parser.parse_with_details("swap it", True)

    assert parsed == FeedbackIntent(action="swap", slot="top")
    assert details.source == "llm" and details.attempts == 2
    assert "needs a slot" in fake.calls[1]["messages"][0]["content"]
    assert details.input_tokens == 2000


def test_two_invalid_answers_fall_back_to_keyword_rules():
    parser, _ = claude(feedback(action="nudge"), feedback(action="nudge"))

    parsed, details = parser.parse_with_details("dressier", True)

    assert parsed == keyword_parser.parse("dressier", True) and details.source == "keyword"
    assert len(details.problems) == 2


def test_feedback_with_no_outfit_on_screen_is_invalid():
    parser, _ = claude(feedback(action="accept"), request(vibes=["cozy"]))

    assert parser("perfect cozy look", False) == ParsedRequest(text="perfect cozy look", vibes=["cozy"])


@pytest.mark.parametrize("failure", [anthropic.AnthropicError("no credit"), "refusal"])
def test_api_errors_and_refusals_fall_back_without_a_retry(failure):
    parser, fake = claude(failure)

    parsed, details = parser.parse_with_details("cozy fall Sunday, running errands", False)

    assert parsed == keyword_parser.parse("cozy fall Sunday, running errands", False)
    assert details.source == "keyword" and len(fake.calls) == 1


def test_vague_request_with_an_empty_answer_still_works():  # FR-REQ-3
    parser, _ = claude(LLMParse(kind="request"))

    assert parser("something nice", False) == ParsedRequest(text="something nice")


# ------------------------------------------------------------ OpenAI-compatible (Groq, Ollama)

class FakeOpenAI:
    def __init__(self, *answers, schema_ok=True):
        self.answers = list(answers)
        self.schema_ok = schema_ok
        self.formats = []
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        self.formats.append(kwargs["response_format"]["type"])
        if kwargs["response_format"]["type"] == "json_schema" and not self.schema_ok:
            request = httpx2.Request("POST", "http://test/v1/chat/completions")
            raise openai.BadRequestError("response_format not supported", response=httpx2.Response(400, request=request),
                                         body=None)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        content = answer if isinstance(answer, str) else answer.model_dump_json()
        return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=900, completion_tokens=40),
                               choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def compat(*answers, **kwargs) -> tuple[OpenAICompatParser, FakeOpenAI]:
    fake = FakeOpenAI(*answers, **kwargs)
    return OpenAICompatParser("http://test/v1", "key", "llama3.1:8b", client=fake), fake


def test_openai_compatible_parses_json():
    parser, fake = compat(feedback(action="lock", slot="outerwear"))

    assert parser("keep the coat", True) == FeedbackIntent(action="lock", slot="outerwear")
    assert fake.formats == ["json_schema"]


def test_server_without_json_schema_switches_to_json_mode():
    parser, fake = compat(request(vibes=["cozy"]), request(vibes=["chic"]), schema_ok=False)

    parser("cozy", False)
    parser("chic", False)

    assert fake.formats == ["json_schema", "json_object", "json_object"]  # only tries the schema once


def test_malformed_json_is_retried_then_falls_back():
    parser, _ = compat("not json", json.dumps({"kind": "request", "request": {"vibes": ["boho"]}}))

    parsed, details = parser.parse_with_details("boho festival look", False)

    assert details.source == "keyword" and details.attempts == 2
    assert parsed == keyword_parser.parse("boho festival look", False)


def test_openai_api_error_falls_back():
    parser, _ = compat(openai.APIConnectionError(request=httpx2.Request("POST", "http://test")))

    assert parser("dinner", False) == keyword_parser.parse("dinner", False)


# ------------------------------------------------------------ make_parser

def test_make_parser_prefers_claude_then_compat_then_keywords():
    assert isinstance(make_parser({"ANTHROPIC_API_KEY": "sk-test", "LLM_BASE_URL": "x", "LLM_API_KEY": "y",
                                   "LLM_MODEL": "z"}), ClaudeParser)
    compat_parser = make_parser({"LLM_BASE_URL": "http://localhost:11434/v1", "LLM_API_KEY": "ollama",
                                 "LLM_MODEL": "llama3.1:8b"})
    assert isinstance(compat_parser, OpenAICompatParser) and compat_parser.name == "llama3.1:8b"
    assert make_parser({}) is keyword_parser.parse
    assert make_parser({"LLM_BASE_URL": "http://localhost:11434/v1"}) is keyword_parser.parse  # incomplete


def test_claude_model_can_be_overridden():
    assert make_parser({"ANTHROPIC_API_KEY": "sk-test", "CLAUDE_MODEL": "claude-sonnet-5-5"}).model == \
        "claude-sonnet-5-5"


# ------------------------------------------------------------ budgets

def test_budget_in_a_request_passes_through():
    parser, _ = claude(request(vibes=["polished"], formality=4, max_item=100))

    assert parser("office outfit, nothing over $100", False).max_item == 100


def test_budget_and_cheaper_feedback():
    parser, fake = claude(feedback(action="budget", max_total=200), feedback(action="cheaper", slot="outerwear"))

    assert parser("keep the price under $200", True) == FeedbackIntent(action="budget", max_total=200)
    assert parser("cheaper coat please", True) == FeedbackIntent(action="cheaper", slot="outerwear")
    assert "keep the price under $200" in fake.calls[0]["system"]  # the prompt shows this case


def test_an_invalid_budget_is_retried():
    parser, fake = claude(feedback(action="budget", max_total=-5), feedback(action="budget", max_total=150))

    assert parser("keep it under $150", True) == FeedbackIntent(action="budget", max_total=150)
    assert len(fake.calls) == 2


# ------------------------------------------------------------ colors

def test_piece_colors_pass_through():
    from stylist.adapters.llm_parser import SlotColor

    parser, _ = claude(request(vibes=["going-out"], colors=[SlotColor(slot="shoes", color="black")]),
                       feedback(action="swap", slot="shoes", color="black"))

    assert parser("date night, i want black shoes", False).colors == {"shoes": "black"}
    assert parser("can i get black shoes", True) == FeedbackIntent(action="swap", slot="shoes", color="black")


def test_a_color_on_anything_but_a_swap_is_retried():
    parser, fake = claude(feedback(action="lock", slot="shoes", color="black"), feedback(action="lock", slot="shoes"))

    assert parser("keep the black shoes", True) == FeedbackIntent(action="lock", slot="shoes")
    assert len(fake.calls) == 2
