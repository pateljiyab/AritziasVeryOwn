"""CLIP tagger logic, tested with fake embeddings so PyTorch isn't needed."""

import zlib

import numpy as np

from stylist.tagging.clip_tagger import (
    AXES,
    SLOT_NOUN,
    LabelBank,
    expected_level,
    label_probabilities,
    pick_labels,
    prompts_for,
    tags_from_probabilities,
)


def fake_encode_texts(texts):
    """Deterministic unit vectors per prompt, like a real text encoder's output shape."""
    vectors = []
    for text in texts:
        rng = np.random.default_rng(zlib.crc32(text.encode()))  # stable across runs, unlike hash()
        v = rng.normal(size=16)
        vectors.append(v / np.linalg.norm(v))
    return np.array(vectors)


def test_prompts_name_the_slot():
    prompts = prompts_for("shoes", ["casual everyday"])
    assert prompts and all("pair of shoes" in p and "casual everyday" in p for p in prompts)


def test_every_slot_has_a_noun_and_every_axis_has_labels():
    assert set(SLOT_NOUN) == {"top", "bottom", "dress", "outerwear", "shoes", "accessory"}
    assert len(AXES["vibe"]) == 10
    assert list(AXES["formality"]) == [1, 2, 3, 4, 5]


def test_image_matching_a_label_gets_that_label():
    bank = LabelBank(fake_encode_texts, ["top"])
    vibe_labels = bank.by_slot["top"]["vibe"]
    names = list(AXES["vibe"])
    image = vibe_labels[names.index("edgy")]  # an "image" identical to the edgy label vector

    tags = bank.tag(image, "top")

    assert tags.vibes[0] == "edgy"
    assert np.isclose(sum(tags.probabilities["vibe"].values()), 1, atol=1e-3)


def test_probabilities_sum_to_one():
    labels = np.eye(4)
    probs = label_probabilities(np.array([1.0, 0, 0, 0]), labels)
    assert np.isclose(probs.sum(), 1) and probs.argmax() == 0


def test_pick_labels_keeps_close_runners_up_and_caps_the_count():
    names = ["a", "b", "c", "d"]
    assert pick_labels(names, np.array([0.5, 0.3, 0.15, 0.05]), max_labels=3) == ["a", "b"]
    assert pick_labels(names, np.array([0.3, 0.28, 0.26, 0.16]), max_labels=2) == ["a", "b"]
    assert pick_labels(names, np.array([0.97, 0.01, 0.01, 0.01]), max_labels=3) == ["a"]


def test_expected_level_is_the_rounded_weighted_average():
    assert expected_level(np.array([0, 0, 1.0, 0, 0]), [1, 2, 3, 4, 5]) == 3
    # Halves round up consistently (Python's round() gives 2.5 -> 2 but 3.5 -> 4).
    assert expected_level(np.array([0, 0.5, 0.5, 0, 0]), [1, 2, 3, 4, 5]) == 3
    assert expected_level(np.array([0, 0, 0.5, 0.5, 0]), [1, 2, 3, 4, 5]) == 4
    assert expected_level(np.array([0.1, 0.1, 0.1, 0.6, 0.1]), [1, 2, 3, 4, 5]) == 4


def test_tags_from_probabilities_respects_limits():
    tags = tags_from_probabilities(
        season=np.full(4, 0.25),       # a year-round item: all four seasons
        vibe=np.full(10, 0.1),         # all tied: capped at 3
        formality=np.array([0, 1.0, 0, 0, 0]),
    )
    assert len(tags.seasons) == 4
    assert len(tags.vibes) == 3
    assert tags.formality == 2
