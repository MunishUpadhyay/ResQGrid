import pytest
from unittest.mock import MagicMock
from django.db import OperationalError
from apps.signals.models import Signal, SourceType
from apps.incidents.models import Incident
from apps.tenants.models import Tenant
from pipeline.tasks import (
    ingest_signal,
    classify_domain,
    route_to_agents,
    coordination_agent,
    push_to_websocket,
    is_retryable_exception
)
from celery.exceptions import Retry

@pytest.mark.django_db
def test_is_retryable_exception():
    # OperationalError is retryable
    assert is_retryable_exception(OperationalError("Database down")) is True
    
    # Value errors are not retryable
    assert is_retryable_exception(ValueError("Invalid format")) is False
    
    # Groq bad request is not retryable
    assert is_retryable_exception(Exception("BadRequestError status 400")) is False
    
    # Key authentication errors are not retryable
    assert is_retryable_exception(Exception("AuthenticationError status 401")) is False

@pytest.mark.django_db
def test_celery_task_invocation_success(mock_groq):
    tenant = Tenant.objects.create(name="Test Tenant", is_active=True)
    signal = Signal.objects.create(tenant=tenant, raw_text="Emergency test case", source_type=SourceType.TEXT)
    
    # Synchronous eager run
    result = ingest_signal.delay(str(signal.id))
    assert result is not None
    
    signal.refresh_from_db()
    assert signal.status == "processed"

@pytest.mark.django_db
def test_route_to_agents_retryable_error(monkeypatch, mock_groq):
    tenant = Tenant.objects.create(name="Test Tenant", is_active=True)
    signal = Signal.objects.create(tenant=tenant, raw_text="Emergency test case", source_type=SourceType.TEXT)
    
    # Get actual task object from Proxy wrapper
    actual_task = route_to_agents._get_current_object()
    
    # Mock self.retry to raise a Retry exception
    retry_mock = MagicMock(side_effect=Retry("Retry scheduled"))
    
    # Force triage agent to raise an OperationalError (retryable)
    class FailingAgent:
        def run(self, *args, **kwargs):
            raise OperationalError("Lost DB connection")
            
    monkeypatch.setattr("apps.agents.agents.TriageAgent", FailingAgent)
    monkeypatch.setattr(actual_task, "retry", retry_mock)
    
    mock_request = MagicMock()
    mock_request.retries = 0
    monkeypatch.setattr(type(actual_task), "request", property(lambda self: mock_request))
    
    with pytest.raises(Retry):
        route_to_agents.run(str(signal.id), {"domain": "health"})
        
    assert retry_mock.called
    # Assert countdown was calculated with backoff (5 * 2^0 = 5)
    assert retry_mock.call_args[1]["countdown"] == 5
    
    # Status should NOT be set to failed yet since it's a retry
    signal.refresh_from_db()
    assert signal.status != "failed"

@pytest.mark.django_db
def test_route_to_agents_non_retryable_error(monkeypatch, mock_groq):
    tenant = Tenant.objects.create(name="Test Tenant", is_active=True)
    signal = Signal.objects.create(tenant=tenant, raw_text="Emergency test case", source_type=SourceType.TEXT)
    
    actual_task = route_to_agents._get_current_object()
    
    # Force triage agent to raise a ValueError (non-retryable)
    class FailingAgent:
        def run(self, *args, **kwargs):
            raise ValueError("Programming bug")
            
    monkeypatch.setattr("apps.agents.agents.TriageAgent", FailingAgent)
    
    mock_retry = MagicMock()
    monkeypatch.setattr(actual_task, "retry", mock_retry)
    
    mock_request = MagicMock()
    mock_request.retries = 0
    monkeypatch.setattr(type(actual_task), "request", property(lambda self: mock_request))
    
    with pytest.raises(ValueError):
        route_to_agents.run(str(signal.id), {"domain": "health"})
        
    # Retry should NOT have been called
    assert not mock_retry.called
    
    # Status should be set to failed and error saved in metadata
    signal.refresh_from_db()
    assert signal.status == "failed"
    assert "Programming bug" in signal.metadata["error"]

@pytest.mark.django_db
def test_route_to_agents_max_retries_exhausted(monkeypatch, mock_groq):
    tenant = Tenant.objects.create(name="Test Tenant", is_active=True)
    signal = Signal.objects.create(tenant=tenant, raw_text="Emergency test case", source_type=SourceType.TEXT)
    
    actual_task = route_to_agents._get_current_object()
    
    class FailingAgent:
        def run(self, *args, **kwargs):
            raise OperationalError("Persistent DB error")
            
    monkeypatch.setattr("apps.agents.agents.TriageAgent", FailingAgent)
    
    mock_retry = MagicMock()
    monkeypatch.setattr(actual_task, "retry", mock_retry)
    
    mock_request = MagicMock()
    mock_request.retries = 3  # Exhausted (max_retries = 3)
    monkeypatch.setattr(type(actual_task), "request", property(lambda self: mock_request))
    
    with pytest.raises(OperationalError):
        route_to_agents.run(str(signal.id), {"domain": "health"})
        
    # Retry should NOT have been called because retries are exhausted
    assert not mock_retry.called
    
    # Status should be set to failed
    signal.refresh_from_db()
    assert signal.status == "failed"
    assert "Persistent DB error" in signal.metadata["error"]

@pytest.mark.django_db
def test_pipeline_idempotency_prevents_duplicate_calls(monkeypatch, mock_groq):
    tenant = Tenant.objects.create(name="Test Tenant", is_active=True)
    signal = Signal.objects.create(tenant=tenant, raw_text="Emergency case", source_type=SourceType.TEXT)
    
    actual_task = route_to_agents._get_current_object()
    
    # Create an Incident and populate agent_outputs to simulate a partially completed run
    mock_triage = {"triage_severity": "minor"}
    incident = Incident.objects.create(
        signal=signal,
        severity_score=0.5,
        severity_label="medium",
        domain="health",
        agent_outputs={"triage": mock_triage, "timing": {"triage": {}}}
    )
    
    # Mock TriageAgent run so we can detect if it was called
    triage_run_mock = MagicMock()
    monkeypatch.setattr("apps.agents.agents.TriageAgent.run", triage_run_mock)
    
    mock_request = MagicMock()
    mock_request.retries = 0
    monkeypatch.setattr(type(actual_task), "request", property(lambda self: mock_request))
    
    route_to_agents.run(str(signal.id), {"domain": "health"})
    
    # Assert that TriageAgent.run was NOT called again because output was reused
    assert not triage_run_mock.called
    
    # Load incident outputs and confirm triage data is preserved
    incident.refresh_from_db()
    assert incident.agent_outputs["triage"] == mock_triage


def test_worker_recycling_counter_filtering(settings, monkeypatch):
    import pipeline.tasks as tasks_module

    settings.CELERY_TASK_ALWAYS_EAGER = False
    # Reset global counter
    monkeypatch.setattr(tasks_module, "COMPLETED_REPORT_COUNT", 0)

    # 1. Intermediate pipeline tasks should NOT increment counter
    tasks_module.recycle_worker_on_report_count(
        sender=ingest_signal, state="SUCCESS"
    )
    assert tasks_module.COMPLETED_REPORT_COUNT == 0

    tasks_module.recycle_worker_on_report_count(
        sender=route_to_agents, state="SUCCESS"
    )
    assert tasks_module.COMPLETED_REPORT_COUNT == 0

    # 2. Failed or retried push_to_websocket tasks should NOT increment counter
    tasks_module.recycle_worker_on_report_count(
        sender=push_to_websocket, state="FAILURE"
    )
    assert tasks_module.COMPLETED_REPORT_COUNT == 0

    tasks_module.recycle_worker_on_report_count(
        sender=push_to_websocket, state="RETRY"
    )
    assert tasks_module.COMPLETED_REPORT_COUNT == 0

    # 3. Successful push_to_websocket SHOULD increment counter
    tasks_module.recycle_worker_on_report_count(
        sender=push_to_websocket, state="SUCCESS"
    )
    assert tasks_module.COMPLETED_REPORT_COUNT == 1


def test_worker_recycles_at_exact_5_completed_reports(settings, monkeypatch):
    import pipeline.tasks as tasks_module
    from celery.exceptions import WorkerShutdown

    settings.CELERY_TASK_ALWAYS_EAGER = False
    monkeypatch.setattr(tasks_module, "COMPLETED_REPORT_COUNT", 0)

    # Run 4 successful report completions -> no recycle requested
    for i in range(1, 5):
        tasks_module.recycle_worker_on_report_count(
            sender=push_to_websocket, state="SUCCESS"
        )
        assert tasks_module.COMPLETED_REPORT_COUNT == i

    # 5th successful report completion MUST trigger recycle (WorkerShutdown or SystemExit)
    with pytest.raises((WorkerShutdown, SystemExit)):
        tasks_module.recycle_worker_on_report_count(
            sender=push_to_websocket, state="SUCCESS"
        )


