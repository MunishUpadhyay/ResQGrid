import pytest
import json
from unittest.mock import MagicMock
from django.conf import settings

@pytest.fixture(autouse=True)
def configure_settings(settings):
    # Force Celery to execute tasks synchronously
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True
    
    # Ensure WebSocket channel layers use in-memory backend (no Redis required)
    settings.CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels.layers.InMemoryChannelLayer",
        },
    }
    
    # Use SQLite memory database for testing
    settings.DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": ":memory:",
        }
    }
    
    # Exclude django.contrib.gis to prevent test database initialization errors on SQLite
    if "django.contrib.gis" in settings.INSTALLED_APPS:
        settings.INSTALLED_APPS.remove("django.contrib.gis")
        
    # Use LocMemCache for unit tests to prevent connecting to external Redis cache
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        }
    }


@pytest.fixture(autouse=True)
def mock_groq(monkeypatch):
    def mock_completion_create(*args, **kwargs):
        model = kwargs.get("model")
        messages = kwargs.get("messages", [])
        system_prompt = ""
        user_message = ""
        for msg in messages:
            if msg.get("role") == "system":
                system_prompt = msg.get("content", "")
            elif msg.get("role") == "user":
                user_message = msg.get("content", "")
                
        response_content = "{}"
        
        # Identify calling agent by analyzing the system prompt or user message
        if "sentinel" in system_prompt or "Classify this" in user_message:
            if "medical" in user_message:
                response_content = json.dumps({
                    "severity_score": 0.5,
                    "severity_label": "medium",
                    "domain": "medical",
                    "escalate": False,
                    "reasoning": "Test medical domain"
                })
            else:
                response_content = json.dumps({
                    "severity_score": 0.8,
                    "severity_label": "high",
                    "domain": "cross_domain",
                    "escalate": True,
                    "reasoning": "Test sentinel reasoning"
                })
        elif "coordination" in system_prompt:
            response_content = json.dumps({
                "situation_title": "Emergency Situation Title",
                "overall_severity": "critical",
                "overall_severity_score": 0.9,
                "what_is_happening": "Critical medical denial issue.",
                "immediate_actions": [
                    {
                        "priority": 1,
                        "action": "Notify authorities",
                        "responsible_party": "Coordinator",
                        "time_window": "10 minutes"
                    }
                ],
                "resources_needed": ["Ambulance"],
                "authorities_to_notify": ["DLSA"],
                "situation_brief": "A summarized brief of the incident.",
                "escalation_required": True,
                "estimated_resolution_time": "1 hour",
                "conflict_resolution": {
                    "primary_priority": "medical",
                    "reasoning": "Life threat is priority",
                    "sequence": "Medical first, then legal"
                },
                "escalation_path": [
                    {
                        "level": 1,
                        "authority": "CMO",
                        "trigger": "No response",
                        "contact": "CMO Office"
                    }
                ],
                "evidence_to_collect": [
                    {
                        "item": "Video recording",
                        "why_important": "Proof of denial",
                        "how_to_collect": "Use phone",
                        "time_sensitive": True
                    }
                ]
            })
        elif "rights" in system_prompt:
            response_content = json.dumps({
                "rights_violated": ["Right to Life"],
                "severity": "high",
                "legal_provisions": [
                    {
                        "provision": "Article 21",
                        "description": "Protection of life and personal liberty",
                        "relevance": "Directly violated"
                    }
                ],
                "immediate_actions": ["File a complaint"],
                "authority_to_contact": "DLSA",
                "case_strength": 0.8,
                "nearest_authority_type": "DLSA",
                "legal_timeline": [
                    {
                        "step": 1,
                        "action": "File FIR",
                        "timeframe": "Within 24 hours",
                        "why_urgent": "To preserve evidence"
                    }
                ]
            })
        elif "language" in system_prompt or "Translate all" in system_prompt or "Translate the following" in system_prompt:
            # Handle translation request by echoing back structural keys with dummy text
            if "Input JSON:" in user_message:
                parts = user_message.split("Input JSON:")
                if len(parts) > 1:
                    json_str = parts[1].strip()
                    # Strip any trailing instruct text
                    json_str = json_str.split("\n\n")[0].strip()
                    if json_str.endswith("Return only the valid JSON object."):
                        json_str = json_str[:-34].strip()
                    try:
                        response_content = json.dumps(json.loads(json_str))
                    except Exception:
                        response_content = json_str
            else:
                response_content = json.dumps({
                    "situation_title": "translated title",
                    "situation_brief": "translated brief"
                })
        else:
            response_content = json.dumps({"status": "success", "domain": "legal", "severity_score": 0.5})

        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_message = MagicMock()
        mock_message.content = response_content
        mock_choice.message = mock_message
        mock_response.choices = [mock_choice]
        return mock_response

    mock_client = MagicMock()
    mock_completions = MagicMock()
    mock_completions.create = MagicMock(side_effect=mock_completion_create)
    mock_client.chat.completions = mock_completions
    
    monkeypatch.setattr("apps.agents.base.Groq", lambda *args, **kwargs: mock_client)
    monkeypatch.setattr("groq.Groq", lambda *args, **kwargs: mock_client)
    return mock_completions.create

@pytest.fixture(autouse=True)
def mock_chromadb(monkeypatch):
    mock_client = MagicMock()
    mock_collection = MagicMock()
    
    # Default query results
    mock_collection.query.return_value = {
        "documents": [["Mock provisions/protocols document"]],
        "metadatas": [[{"category": "test", "act": "test_act", "section": "1"}]],
        "distances": [[0.15]]
    }
    
    mock_client.get_collection.return_value = mock_collection
    mock_client.get_or_create_collection.return_value = mock_collection
    
    # Patch PersistentClient to return our mock client
    monkeypatch.setattr("chromadb.PersistentClient", lambda *args, **kwargs: mock_client)
    
    # Patch SentenceTransformer to bypass weight downloads during tests
    mock_emb_fn = MagicMock()
    monkeypatch.setattr(
        "chromadb.utils.embedding_functions.SentenceTransformerEmbeddingFunction",
        lambda *args, **kwargs: mock_emb_fn
    )
    return mock_client
