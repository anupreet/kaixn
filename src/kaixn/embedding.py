"""Embedding backends behind one protocol.

Open decision #2 (cloud-1536 vs local-768) stays swappable by config instead of
blocking: pick an `Embedder` here, the rest of the system never sees the choice.
"""

from __future__ import annotations

import hashlib
import os
import struct
from typing import Protocol, runtime_checkable


@runtime_checkable
class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class FakeEmbedder:
    """Deterministic bag-of-words hashing embedder — no network, no keys.

    Shared tokens land in shared dimensions, so near-identical statements get
    high cosine similarity. Good enough to exercise dedup logic in tests and to
    run the POC offline.
    """

    def __init__(self, dim: int = 64) -> None:
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self.dim
            for tok in text.lower().split():
                h = hashlib.sha1(tok.encode()).digest()
                idx = struct.unpack_from(">I", h)[0] % self.dim
                sign = 1.0 if h[4] & 1 else -1.0
                vec[idx] += sign
            out.append(vec)
        return out


class OllamaEmbedder:
    """Local embeddings via Ollama (e.g. nomic-embed-text, dim 768)."""

    def __init__(self, model: str = "nomic-embed-text",
                 host: str = "http://localhost:11434", dim: int = 768) -> None:
        self.model = model
        self.host = host.rstrip("/")
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        import httpx

        out: list[list[float]] = []
        with httpx.Client(timeout=60) as client:
            for text in texts:
                r = client.post(f"{self.host}/api/embeddings",
                                json={"model": self.model, "prompt": text})
                r.raise_for_status()
                out.append(r.json()["embedding"])
        return out


class OpenAIEmbedder:
    """Cloud embeddings via OpenAI (text-embedding-3-small, dim 1536)."""

    def __init__(self, model: str = "text-embedding-3-small", dim: int = 1536) -> None:
        self.model = model
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        from openai import OpenAI

        client = OpenAI()
        resp = client.embeddings.create(model=self.model, input=texts)
        return [d.embedding for d in resp.data]


def get_embedder(name: str | None = None) -> Embedder:
    """Resolve an embedder from KAIXN_EMBEDDER (fake | ollama | openai)."""
    name = (name or os.getenv("KAIXN_EMBEDDER", "fake")).lower()
    if name == "openai":
        return OpenAIEmbedder()
    if name == "ollama":
        return OllamaEmbedder()
    return FakeEmbedder()
