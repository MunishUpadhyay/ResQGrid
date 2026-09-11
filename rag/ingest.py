"""
RAG document ingestion module.
Ingests Indian legal provisions into a ChromaDB vector store.
"""

import logging
import chromadb
from chromadb.config import Settings

logger = logging.getLogger(__name__)


def ingest_legal_documents():
    """
    Ingest Indian legal provisions programmatically from the centralized legal reference database.
    """
    from apps.agents.legal_reference import VERIFIED_LEGAL_DATABASE
    
    # 1. Initialize local persistent client
    client = chromadb.PersistentClient(
        path="rag/chroma_db",
        settings=Settings(anonymized_telemetry=False)
    )
    
    
    # 3. Delete old collection if exists to avoid stale records, then create/get
    try:
        client.delete_collection("legal_provisions")
    except Exception:
        pass
    collection = client.get_or_create_collection(
        name="legal_provisions"
    )

    # 4. Map centralized database to Chroma documents format
    documents = []
    for (code, sec), record in VERIFIED_LEGAL_DATABASE.items():
        doc_id = f"{code.lower()}_{sec.lower().replace(' ', '_').replace('(', '').replace(')', '')}"
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
        documents.append({
            "id": doc_id,
            "document": doc_text,
            "metadata": metadata
        })

    # 5. Ingest/upsert documents
    for doc in documents:
        collection.upsert(
            ids=[doc["id"]],
            documents=[doc["document"]],
            metadatas=[doc["metadata"]]
        )
        print(f"Ingested: {doc['id']} - {doc['metadata']['section']}")

    logger.info("Successfully ingested %d legal provisions into ChromaDB.", len(documents))


def ingest_medical_protocols():
    """
    Ingest the 8 core medical protocols into local ChromaDB collection "medical_protocols".
    Prints confirmation for each document ingested.
    """
    # 1. Initialize local persistent client
    client = chromadb.PersistentClient(
        path="rag/chroma_db",
        settings=Settings(anonymized_telemetry=False)
    )
    
    
    # 3. Delete old collection if exists to avoid stale records, then create/get
    try:
        client.delete_collection("medical_protocols")
    except Exception:
        pass
    collection = client.get_or_create_collection(
        name="medical_protocols"
    )

    # 4. Core medical protocols from centralized medical reference database
    from apps.agents.medical_reference import VERIFIED_MEDICAL_DATABASE
    documents = []
    for doc_id, record in VERIFIED_MEDICAL_DATABASE.items():
        doc_text = f"{record['title']}. Protocol: {record['statutory_text']}"
        metadata = {
            "category": record["category"],
            "act": record["act"],
            "section": record["title"]
        }
        documents.append({
            "id": doc_id,
            "document": doc_text,
            "metadata": metadata
        })

    # 5. Ingest/upsert documents
    for doc in documents:
        collection.upsert(
            ids=[doc["id"]],
            documents=[doc["document"]],
            metadatas=[doc["metadata"]]
        )
        print(f"Ingested: {doc['id']} - {doc['metadata']['section']}")

    logger.info("Successfully ingested %d medical protocols into ChromaDB.", len(documents))


def ingest_incident_to_history(incident_id: str, situation_brief: str,
                               domain: str, severity: str, resolved: bool):
    """
    Ingest a processed incident into the 'incident_history' collection.
    Embeds the situation_brief and stores metadata:
    - incident_id
    - domain
    - severity
    - resolved (bool)
    - created_at
    """
    from datetime import datetime, timezone
    import chromadb
    
    logger.info("[ingest_incident_to_history] Ingesting incident_id=%s to incident_history", incident_id)
    try:
        # 1. Initialize persistent client
        client = chromadb.PersistentClient(
            path="rag/chroma_db",
            settings=Settings(anonymized_telemetry=False)
        )
        
        
        # 3. Create or get collection
        collection = client.get_or_create_collection(
            name="incident_history"
        )
        
        # 4. Ingest/upsert the incident
        metadata = {
            "incident_id": str(incident_id),
            "domain": str(domain),
            "severity": str(severity),
            "resolved": bool(resolved),
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        collection.upsert(
            ids=[str(incident_id)],
            documents=[situation_brief],
            metadatas=[metadata]
        )
        logger.info("[ingest_incident_to_history] Successfully ingested incident_id=%s", incident_id)
    except Exception as e:
        logger.exception("Error ingesting incident %s to history: %s", incident_id, e)

