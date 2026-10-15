from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

TOKEN_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]*", re.IGNORECASE)


@dataclass(frozen=True)
class KnowledgeDocument:
    uri: str
    document_type: str
    title: str
    section: str
    content: str
    services: tuple[str, ...]
    tags: tuple[str, ...]
    updated_at: str

    @property
    def searchable_text(self) -> str:
        return " ".join(
            [
                self.title,
                self.section,
                self.content,
                *self.services,
                *self.tags,
            ]
        )


class KnowledgeIndex:
    """Small deterministic BM25 index for the checked-in fault-lab corpus."""

    def __init__(self, documents: list[KnowledgeDocument]) -> None:
        if not documents:
            raise ValueError("Knowledge corpus must contain at least one document.")
        self.documents = documents
        self._tokens = [self._tokenize(item.searchable_text) for item in documents]
        self._lengths = [len(item) for item in self._tokens]
        self._average_length = sum(self._lengths) / len(self._lengths)
        self._document_frequency: dict[str, int] = {}
        for tokens in self._tokens:
            for token in set(tokens):
                self._document_frequency[token] = (
                    self._document_frequency.get(token, 0) + 1
                )

    @classmethod
    def load_default(cls) -> KnowledgeIndex:
        resource = files("incident_mcp").joinpath("knowledge_corpus.json")
        raw = json.loads(resource.read_text(encoding="utf-8"))
        documents = [
            KnowledgeDocument(
                uri=item["uri"],
                document_type=item["document_type"],
                title=item["title"],
                section=item["section"],
                content=item["content"],
                services=tuple(item["services"]),
                tags=tuple(item["tags"]),
                updated_at=item["updated_at"],
            )
            for item in raw
        ]
        return cls(documents)

    def search(
        self,
        *,
        service: str,
        query: str,
        content_types: set[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        query_tokens = self._tokenize(f"{service} {query}")
        scored: list[tuple[float, KnowledgeDocument, list[str]]] = []
        for document, tokens, length in zip(
            self.documents,
            self._tokens,
            self._lengths,
            strict=True,
        ):
            if document.document_type not in content_types:
                continue
            matched = sorted(set(query_tokens) & set(tokens))
            lexical_score = sum(
                self._bm25(token, tokens, length) for token in query_tokens
            )
            service_boost = 2.5 if service in document.services else 0.0
            score = lexical_score + service_boost
            if score <= 0:
                continue
            scored.append((score, document, matched))
        scored.sort(key=lambda item: (-item[0], item[1].uri))
        maximum = scored[0][0] if scored else 1.0
        return [
            {
                "title": document.title,
                "uri": document.uri,
                "document_type": document.document_type,
                "section": document.section,
                "score": round(score / maximum, 4),
                "excerpt": self._excerpt(document.content, matched),
                "services": list(document.services),
                "tags": list(document.tags),
                "updated_at": document.updated_at,
                "matched_terms": matched,
                "content_hash": hashlib.sha256(
                    document.content.encode("utf-8")
                ).hexdigest(),
            }
            for score, document, matched in scored[:limit]
        ]

    def _bm25(self, token: str, document_tokens: list[str], length: int) -> float:
        frequency = document_tokens.count(token)
        if frequency == 0:
            return 0.0
        document_count = len(self.documents)
        containing = self._document_frequency[token]
        inverse_frequency = math.log(
            1 + (document_count - containing + 0.5) / (containing + 0.5)
        )
        k1 = 1.5
        b = 0.75
        denominator = frequency + k1 * (
            1 - b + b * length / self._average_length
        )
        return inverse_frequency * frequency * (k1 + 1) / denominator

    @staticmethod
    def _tokenize(value: str) -> list[str]:
        tokens: list[str] = []
        for item in TOKEN_PATTERN.findall(value):
            normalized = item.lower()
            tokens.append(normalized)
            parts = [part for part in re.split(r"[._-]+", normalized) if part]
            if len(parts) > 1:
                tokens.extend(parts)
        return tokens

    @staticmethod
    def _excerpt(content: str, matched_terms: list[str]) -> str:
        sentences = re.split(r"(?<=[.!?])\s+", content.strip())
        for sentence in sentences:
            lowered = sentence.lower()
            if any(term in lowered for term in matched_terms):
                return sentence[:320]
        return content[:320]
