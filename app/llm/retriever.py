"""
app/llm/retriever.py
─────────────────────
Retrieval module for the AI Teammate.

This module provides:
  • local vector storage using Chroma
  • native Gemini embeddings
  • text chunking with overlap for long documents
  • query-time retrieval of relevant chunks
"""

import logging
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings
from google import genai

log = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = 500
DEFAULT_CHUNK_OVERLAP = 100
DEFAULT_COLLECTION_NAME = "ai_teammate"


class Retriever:
    """A simple retrieval module backed by a local Chroma vector store."""

    def __init__(
        self,
        api_key: str | None = None,
        persist_directory: str | Path | None = None,
        collection_name: str = DEFAULT_COLLECTION_NAME,
        embedding_model_name: str = "text-embedding-3-small",
    ) -> None:
        self.persist_directory = (
            Path(str(persist_directory)).resolve()
            if persist_directory
            else Path(__file__).resolve().parent.parent.parent / "vector_db"
        )
        self.persist_directory.mkdir(parents=True, exist_ok=True)

        self._api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self._embed_client: genai.Client | None = None
        self._collection_client = chromadb.Client(
            Settings(
                persist_directory=str(self.persist_directory),
                allow_reset=False,
            )
        )
        self._collection = self._collection_client.get_or_create_collection(name=collection_name)
        self._embedding_model_name = embedding_model_name

        log.info(
            "Initialized Retriever with collection=%s and persist_directory=%s",
            collection_name,
            self.persist_directory,
        )

    def index_text(
        self,
        document_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Index a single text document by chunking it and storing chunks."""
        chunks = self._chunk_text(text)
        if not chunks:
            return 0

        ids = [f"{document_id}__chunk__{idx}" for idx in range(len(chunks))]
        metadatas = [
            {**(metadata or {}), "chunk_index": idx}
            for idx in range(len(chunks))
        ]

        embeddings = self._embed_contents(chunks)

        self._collection.add(
            ids=ids,
            documents=chunks,
            metadatas=metadatas,
            embeddings=embeddings,
        )

        log.info(
            "Indexed document %s as %d chunks.",
            document_id,
            len(chunks),
        )
        return len(chunks)

    def retrieve(self, query: str, k: int = 5) -> list[dict[str, Any]]:
        """Retrieve the top-k most relevant document chunks for the query."""
        query_embedding = self._embed_contents([query])

        results = self._collection.query(
            query_embeddings=query_embedding,
            n_results=k,
            include=["documents", "metadatas", "distances"],
        )

        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        return [
            {
                "text": documents[idx],
                "metadata": metadatas[idx] if idx < len(metadatas) else {},
                "distance": distances[idx] if idx < len(distances) else None,
            }
            for idx in range(len(documents))
        ]

    def get_context(
        self,
        query: str,
        k: int = 5,
    ) -> list[str]:
        """Return a list of human-readable context strings for a query."""
        hits = self.retrieve(query, k=k)
        return [
            self._format_hit(hit, index=index)
            for index, hit in enumerate(hits, start=1)
        ]

    @staticmethod
    def _format_hit(hit: dict[str, Any], index: int) -> str:
        metadata = hit.get("metadata", {}) or {}
        source = metadata.get("source", "document")
        chunk_index = metadata.get("chunk_index")
        if chunk_index is not None:
            return f"[{index}] source={source} chunk={chunk_index}\n{hit['text']}"
        return f"[{index}] source={source}\n{hit['text']}"

    def count(self) -> int:
        """Return the number of vectors stored in the collection."""
        return int(self._collection.count())

    def is_populated(self) -> bool:
        """Return True if the vector store already contains items."""
        return self.count() > 0

    @staticmethod
    def _chunk_text(text: str) -> list[str]:
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return []

        tokens = text.split(" ")
        chunks: list[str] = []
        start = 0
        while start < len(tokens):
            end = min(start + DEFAULT_CHUNK_SIZE, len(tokens))
            chunks.append(" ".join(tokens[start:end]))
            if end >= len(tokens):
                break
            start = max(end - DEFAULT_CHUNK_OVERLAP, start + 1)
        return chunks

    def _get_embed_client(self) -> genai.Client:
        if self._embed_client is None:
            if not self._api_key:
                raise RuntimeError(
                    "GEMINI_API_KEY is required to compute embeddings. "
                    "Set GEMINI_API_KEY in the environment."
                )
            self._embed_client = genai.Client(api_key=self._api_key)
        return self._embed_client

    def _embed_contents(self, contents: list[str]) -> list[list[float]]:
        if not contents:
            return []

        client = self._get_embed_client()
        response = client.models.embed_content(
            model=self._embedding_model_name,
            contents=contents,
        )
        if not response.embeddings:
            raise RuntimeError("Embedding API returned no embeddings.")

        return [
            embedding.values or []
            for embedding in response.embeddings
        ]


@lru_cache(maxsize=1)
def get_retriever(
    api_key: str | None = None,
    persist_directory: str | Path | None = None,
    collection_name: str = DEFAULT_COLLECTION_NAME,
    embedding_model_name: str = "text-embedding-3-small",
) -> Retriever:
    """Return a cached Retriever instance for the current process."""
    return Retriever(
        api_key=api_key,
        persist_directory=persist_directory,
        collection_name=collection_name,
        embedding_model_name=embedding_model_name,
    )
