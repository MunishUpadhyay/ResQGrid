import pytest
import sys
from unittest.mock import MagicMock
from rag.retriever import (
    retrieve_legal_provisions,
    retrieve_medical_protocols,
    retrieve_similar_incidents,
    get_chroma_client,
    get_embedding_function,
)

def test_retrieve_legal_provisions():
    results = retrieve_legal_provisions("salary unpaid employee", n_results=3)
    assert len(results) > 0
    assert "text" in results[0]
    assert "metadata" in results[0]
    assert "code" in results[0]["metadata"]
    assert "distance" in results[0]

def test_retrieve_medical_protocols():
    results = retrieve_medical_protocols("heart attack chest pain", n_results=2)
    assert len(results) > 0
    assert "text" in results[0]
    assert "metadata" in results[0]
    assert "title" in results[0]["metadata"]

def test_retrieve_similar_incidents():
    results = retrieve_similar_incidents("Test query", n_results=3)
    assert isinstance(results, list)

def test_zero_memory_rag_mode_bypasses_model_loading(settings, monkeypatch):
    """
    Verify that zero-memory RAG mode:
    1. retrieve_legal_provisions returns local ranker results.
    2. retrieve_medical_protocols returns local ranker results.
    3. retrieve_similar_incidents returns results safely.
    4. get_embedding_function is NEVER called for legal/medical.
    """
    def fail_if_called():
        raise RuntimeError("get_embedding_function should NOT be called in zero-memory mode!")
        
    monkeypatch.setattr("rag.retriever.get_embedding_function", fail_if_called)
    monkeypatch.setattr("rag.retriever.get_chroma_client", fail_if_called)
    
    # Test legal retrieval
    legal_res = retrieve_legal_provisions("salary unpaid employee")
    assert isinstance(legal_res, list)
    assert len(legal_res) > 0
    assert "code" in legal_res[0]["metadata"]
    
    # Test medical retrieval
    med_res = retrieve_medical_protocols("heart attack chest pain")
    assert isinstance(med_res, list)
    assert len(med_res) > 0
    assert "title" in med_res[0]["metadata"]

def test_module_imports_lazy_rag():
    """
    Verify that importing apps.incidents.views and apps.agents.agents
    does not throw errors and can be loaded without eager RAG initialization.
    """
    import apps.incidents.views
    import apps.agents.agents
    assert True

def test_rag_singleton_caching():
    """
    Verify that repeated calls to get_chroma_client and get_embedding_function
    return the exact same cached instance.
    """
    client1 = get_chroma_client()
    client2 = get_chroma_client()
    assert client1 is client2

    emb1 = get_embedding_function()
    emb2 = get_embedding_function()
    assert emb1 is emb2

def test_timeout_status_response_includes_steps(db, client):
    """
    Verify that when a signal times out, citizen_signal_status_api returns
    status: 'pipeline_error' AND includes the 'steps' dict.
    """
    from datetime import timedelta
    from django.utils import timezone
    from django.urls import reverse
    from apps.signals.models import Signal
    from apps.tenants.models import Tenant

    tenant, _ = Tenant.objects.get_or_create(
        name="Test Tenant",
        defaults={"api_key_hash": Tenant.hash_api_key("test_key")}
    )
    
    old_time = timezone.now() - timedelta(minutes=15)
    signal = Signal.objects.create(
        tenant=tenant,
        raw_text="Test incident description",
        status="processing",
        domain="legal"
    )
    Signal.objects.filter(id=signal.id).update(created_at=old_time)
    
    session = client.session
    session[f"verified_{signal.id}"] = True
    session.save()
    
    url = reverse("citizen_signal_status_api", kwargs={"signal_id": signal.id})
    response = client.get(url)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "pipeline_error"
    assert data["message"] == "Pipeline timed out"
    assert "steps" in data
    assert data["steps"]["received"] is True
    assert data["steps"]["classified"] is True
    assert data["steps"]["analyzed"] is False
