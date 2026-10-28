"""CLIP text embeddings for query relevance, shared by the CLI and the API."""

from __future__ import annotations

import numpy as np


class TextEmbedder:
    """Loads CLIP on first use and caches one embedding per text. With enabled=False it
    returns None, and the engine skips the relevance term (no PyTorch needed)."""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self._model = None
        self._cache: dict[str, np.ndarray] = {}

    def __call__(self, text: str) -> np.ndarray | None:
        if not self.enabled or not text:
            return None
        if self._model is None:
            from stylist.tagging.clip_tagger import ClipModel  # needs requirements-ml.txt

            print("(loading CLIP...)")
            self._model = ClipModel()
        if text not in self._cache:
            self._cache[text] = self._model.encode_texts([text])[0]
        return self._cache[text]
