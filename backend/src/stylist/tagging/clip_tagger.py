"""Zero-shot season / vibe / formality tags from product photos with CLIP.

CLIP embeds images and text in one space, so a photo can be compared with sentences like
"a product photo of a cozy soft knit sweater". For each axis (season, vibe, formality) the
photo is scored against every label, and a softmax turns the scores into probabilities.

Each label has several phrasings whose text embeddings are averaged ("prompt ensembling"),
which is steadier than a single prompt for abstract ideas like "chic". Prompts name the
item's slot ("...knit top", "...pair of shoes") so they describe the kind of thing in
the photo.

Everything except `ClipModel` is plain NumPy, so it is unit-tested without PyTorch.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from PIL.Image import Image

MODEL_NAME = "ViT-B-32"
PRETRAINED = "laion2b_s34b_b79k"
MODEL_VERSION = f"open_clip/{MODEL_NAME}/{PRETRAINED}/prompts-v1"

# CLIP's own logit scale: cosine similarities (around 0.2-0.35) times 100 before the softmax.
LOGIT_SCALE = 100.0

# A label is kept if its probability reaches this share of the top label's probability.
# Fixed up front rather than tuned, because the only labeled data is the gold set, which
# is reserved for measuring accuracy.
RELATIVE_THRESHOLD = 0.5
MAX_SEASONS = 4
MAX_VIBES = 3


SLOT_NOUN = {
    "top": "top",
    "bottom": "pair of pants or skirt",
    "dress": "dress",
    "outerwear": "jacket or coat",
    "shoes": "pair of shoes",
    "accessory": "accessory",
}

TEMPLATES = [
    "a product photo of a {phrase} {noun}",
    "a fashion photo of a {phrase} {noun}",
]

SEASON_PHRASES: dict[str, list[str]] = {
    "spring": ["light spring", "fresh pastel spring", "light layering spring"],
    "summer": ["lightweight summer", "breezy hot-weather summer", "sleeveless summer"],
    "fall": ["autumn", "warm layered fall", "cozy autumn"],
    "winter": ["heavy warm winter", "thick insulated winter", "wool winter"],
}

VIBE_PHRASES: dict[str, list[str]] = {
    "cozy": ["cozy soft knit", "warm relaxed comfortable", "snug fleece"],
    "polished": ["polished tailored office", "clean-lined professional", "crisp structured workwear"],
    "going-out": ["going-out evening party", "dressy night-out statement", "glamorous party"],
    "cute": ["cute playful", "pretty flowy feminine", "sweet girly"],
    "off-duty": ["casual off-duty weekend", "effortless everyday casual", "laid-back relaxed"],
    "chic": ["chic elegant luxe", "classy sophisticated designer", "elevated expensive-looking"],
    "athleisure": ["sporty athleisure", "athletic activewear", "gym and yoga"],
    "minimal": ["minimalist plain neutral", "simple clean basic", "unembellished solid-color"],
    "romantic": ["romantic lace floral", "delicate ruffled", "soft feminine lace"],
    "edgy": ["edgy leather", "dark bold edgy", "rock-and-roll"],
}

FORMALITY_PHRASES: dict[int, list[str]] = {
    1: ["loungewear or athletic", "sweatpants-and-hoodie lounge", "gym workout"],
    2: ["casual everyday", "relaxed weekend casual", "jeans-and-tee casual"],
    3: ["smart casual", "neat smart-casual", "dressed-up casual"],
    4: ["business professional", "dressy office", "tailored business"],
    5: ["formal evening event", "black-tie formal", "elegant gala"],
}

AXES: dict[str, dict] = {"season": SEASON_PHRASES, "vibe": VIBE_PHRASES, "formality": FORMALITY_PHRASES}


@dataclass
class ClipTags:
    seasons: list[str]
    vibes: list[str]
    formality: int
    probabilities: dict[str, dict[str, float]] = field(default_factory=dict)  # axis -> label -> p

# Generate prompt strings for a given slot and list of phrases.
def prompts_for(slot: str, phrases: list[str]) -> list[str]:
    noun = SLOT_NOUN[slot]
    return [t.format(phrase=p, noun=noun) for p in phrases for t in TEMPLATES]


def label_embeddings(encode_texts, slot: str, phrases_by_label: dict) -> np.ndarray:
    """One unit vector per label: the normalized mean of its prompt embeddings."""
    rows = []
    for phrases in phrases_by_label.values():
        mean = encode_texts(prompts_for(slot, phrases)).mean(axis=0)
        rows.append(mean / np.linalg.norm(mean))
    return np.stack(rows)


def softmax(x: np.ndarray) -> np.ndarray:
    e = np.exp(x - x.max(axis=-1, keepdims=True))
    return e / e.sum(axis=-1, keepdims=True)


def label_probabilities(image_embedding: np.ndarray, labels: np.ndarray) -> np.ndarray:
    """Softmax over labels of the image-text cosine similarities."""
    return softmax(LOGIT_SCALE * labels @ image_embedding)


def pick_labels(names: list[str], probs: np.ndarray, max_labels: int) -> list[str]:
    """Labels close to the best one (at least RELATIVE_THRESHOLD of its probability),
    best first. Always returns at least one."""
    order = np.argsort(-probs)
    cutoff = probs[order[0]] * RELATIVE_THRESHOLD
    return [names[i] for i in order[:max_labels] if probs[i] >= cutoff]


def expected_level(probs: np.ndarray, levels: list[int]) -> int:
    """Probability-weighted average level, rounded half up: steadier than the single top level.
    (Python's round() rounds halves to even, so 2.5 -> 2 but 3.5 -> 4.)"""
    return math.floor(float(np.dot(probs, levels)) + 0.5)


def tags_from_probabilities(season: np.ndarray, vibe: np.ndarray, formality: np.ndarray) -> ClipTags:
    season_names, vibe_names = list(SEASON_PHRASES), list(VIBE_PHRASES)
    levels = list(FORMALITY_PHRASES)
    return ClipTags(
        seasons=pick_labels(season_names, season, MAX_SEASONS),
        vibes=pick_labels(vibe_names, vibe, MAX_VIBES),
        formality=expected_level(formality, levels),
        probabilities={
            "season": dict(zip(season_names, season.round(4).tolist())),
            "vibe": dict(zip(vibe_names, vibe.round(4).tolist())),
            "formality": dict(zip(map(str, levels), formality.round(4).tolist())),
        },
    )


class LabelBank:
    """Label embeddings for every (slot, axis), computed once and reused for every item."""

    def __init__(self, encode_texts, slots: list[str]):
        self.by_slot = {
            slot: {axis: label_embeddings(encode_texts, slot, phrases) for axis, phrases in AXES.items()}
            for slot in slots
        }

    def tag(self, image_embedding: np.ndarray, slot: str) -> ClipTags:
        labels = self.by_slot[slot]
        return tags_from_probabilities(
            label_probabilities(image_embedding, labels["season"]),
            label_probabilities(image_embedding, labels["vibe"]),
            label_probabilities(image_embedding, labels["formality"]),
        )


class ClipModel:
    """open_clip on CPU. Imported lazily so the rest of this module works without PyTorch."""

    def __init__(self) -> None:
        import open_clip
        import torch

        self._torch = torch
        self.model, _, self.preprocess = open_clip.create_model_and_transforms(MODEL_NAME, pretrained=PRETRAINED)
        self.tokenizer = open_clip.get_tokenizer(MODEL_NAME)
        self.model.eval()

    def _normalize(self, features) -> np.ndarray:
        features = features / features.norm(dim=-1, keepdim=True)
        return features.cpu().numpy().astype(np.float32)

    def encode_texts(self, texts: list[str]) -> np.ndarray:
        with self._torch.no_grad():
            return self._normalize(self.model.encode_text(self.tokenizer(texts)))

    def encode_images(self, images: list[Image]) -> np.ndarray:
        batch = self._torch.stack([self.preprocess(img) for img in images])
        with self._torch.no_grad():
            return self._normalize(self.model.encode_image(batch))
