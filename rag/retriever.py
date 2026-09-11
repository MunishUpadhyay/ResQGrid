"""
RAG document retrieval module.
Provides zero-cost lightweight pure-Python local document ranking for static legal
and medical reference databases, and ChromaDB retrieval for incident history.
"""

import logging
import re
import math
from collections import Counter
from django.conf import settings
from apps.agents.legal_reference import VERIFIED_LEGAL_DATABASE
from apps.agents.medical_reference import VERIFIED_MEDICAL_DATABASE

logger = logging.getLogger(__name__)

_client_instance = None
_embedding_function_instance = None


class DummyEmbeddingFunction:
    """
    Lightweight dummy embedding function to satisfy ChromaDB interface
    without importing torch or sentence-transformers.
    """
    def __call__(self, input):
        if isinstance(input, str):
            input = [input]
        return [[0.0] * 384 for _ in input]


def get_chroma_client():
    """
    Returns a singleton persistent ChromaDB client.
    """
    global _client_instance
    if _client_instance is None:
        import chromadb
        from chromadb.config import Settings
        _client_instance = chromadb.PersistentClient(
            path="rag/chroma_db",
            settings=Settings(anonymized_telemetry=False)
        )
    return _client_instance


def get_embedding_function():
    """
    Returns a singleton dummy embedding function.
    """
    global _embedding_function_instance
    if _embedding_function_instance is None:
        _embedding_function_instance = DummyEmbeddingFunction()
    return _embedding_function_instance


def _tokenize(text: str) -> list[str]:
    """Tokenize input text into lowercase words."""
    if not text:
        return []
    return re.findall(r'\w+', str(text).lower())


def _score_document(query_tokens: list[str], text: str, metadata: dict) -> float:
    """
    Compute keyword relevance score using term frequency with metadata title/section weighting.
    """
    if not query_tokens:
        return 0.0

    text_tokens = _tokenize(text)
    token_counts = Counter(text_tokens)

    title = str(metadata.get('title', '')).lower()
    section = str(metadata.get('section', '')).lower()
    code = str(metadata.get('code', '')).lower()
    category = str(metadata.get('category', '')).lower()
    act = str(metadata.get('act', '')).lower()

    score = 0.0
    for q in query_tokens:
        if not q or len(q) < 2:
            continue
        
        # Priority boost for matching exact section, code, or title terms
        if q in section or q in code:
            score += 5.0
        if q in title or q in category or q in act:
            score += 3.0
            
        tf = token_counts.get(q, 0)
        if tf > 0:
            # Sublinear term frequency scaling
            score += (tf / (tf + 1.5)) * 2.0

    return score


def retrieve_legal_provisions(query: str, n_results: int = 3) -> list[dict]:
    """
    Retrieve top relevant Indian legal provisions using lightweight pure-Python document ranking.
    Preserves exact return schema: list[dict] with text, metadata, distance, and similarity_score.
    """
    try:
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        threshold = float(getattr(settings, "RAG_LEGAL_DISTANCE_THRESHOLD", 0.95))
        scored_docs = []

        for (code, sec), record in VERIFIED_LEGAL_DATABASE.items():
            doc_text = f"{code} Section {sec} — {record['title']}. Statutory Text: {record['statutory_text']}"
            metadata = {
                "code": code,
                "section": sec,
                "title": record["title"],
                "category": record["type"],
                "legacy_code": record.get("legacy_code") or "None",
                "legacy_section": record.get("legacy_section") or "None",
                "verified": "True"
            }
            score = _score_document(query_tokens, doc_text, metadata)
            distance = round(1.0 / (1.0 + score * 2.0), 4) if score > 0 else 1.0
            if distance <= threshold:
                scored_docs.append((score, distance, doc_text, metadata))

        # Sort by score descending
        scored_docs.sort(key=lambda item: item[0], reverse=True)

        results = []
        for score, distance, doc_text, metadata in scored_docs[:n_results]:
            similarity_score = round(1.0 - distance, 4) if distance <= 1.0 else 0.5
            results.append({
                "text": doc_text,
                "metadata": metadata,
                "distance": distance,
                "similarity_score": similarity_score
            })
        return results
    except Exception as e:
        logger.exception("[retrieve_legal_provisions] Retrieval failed: %s", e)
        return []


def retrieve_medical_protocols(query: str, n_results: int = 3) -> list[dict]:
    """
    Retrieve top relevant emergency medical protocols using lightweight pure-Python document ranking.
    Preserves exact return schema: list[dict] with text, metadata, distance, and similarity_score.
    """
    try:
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        threshold = float(getattr(settings, "RAG_MEDICAL_DISTANCE_THRESHOLD", 0.95))
        scored_docs = []

        for doc_id, record in VERIFIED_MEDICAL_DATABASE.items():
            doc_text = f"{record['title']}. Protocol: {record['statutory_text']}"
            metadata = {
                "title": record["title"],
                "category": record["category"],
                "act": record["act"],
                "section": record["title"]
            }
            score = _score_document(query_tokens, doc_text, metadata)
            distance = round(1.0 / (1.0 + score * 2.0), 4) if score > 0 else 1.0
            if distance <= threshold:
                scored_docs.append((score, distance, doc_text, metadata))

        scored_docs.sort(key=lambda item: item[0], reverse=True)

        results = []
        for score, distance, doc_text, metadata in scored_docs[:n_results]:
            similarity_score = round(1.0 - distance, 4) if distance <= 1.0 else 0.5
            results.append({
                "text": doc_text,
                "metadata": metadata,
                "distance": distance,
                "similarity_score": similarity_score
            })
        return results
    except Exception as e:
        logger.exception("[retrieve_medical_protocols] Retrieval failed: %s", e)
        return []


def retrieve_similar_incidents(query: str, n_results: int = 3, exclude_id: str = None) -> list[dict]:
    """
    Retrieve similar historical incidents from ChromaDB 'incident_history' collection.
    If database error occurs or zero-memory RAG mode is active, returns empty list gracefully.
    """
    try:
        client = get_chroma_client()
        collection = client.get_or_create_collection(
            name="incident_history",
            embedding_function=get_embedding_function()
        )
        
        query_res = collection.query(
            query_texts=[query],
            n_results=n_results + (1 if exclude_id else 0)
        )
        
        results = []
        if query_res and query_res.get("documents") and query_res["documents"][0]:
            for doc, meta, dist in zip(query_res["documents"][0], query_res["metadatas"][0], query_res["distances"][0]):
                inc_id = meta.get("incident_id")
                if exclude_id and str(inc_id) == str(exclude_id):
                    continue
                d_val = dist if dist is not None else 0.0
                results.append({
                    "text": doc,
                    "metadata": meta,
                    "distance": round(d_val, 4),
                    "similarity_score": round(1.0 / (1.0 + d_val), 4)
                })
                if len(results) >= n_results:
                    break
        return results
    except Exception as e:
        logger.warning("[retrieve_similar_incidents] Error or empty incident_history collection: %s", e)
        return []
