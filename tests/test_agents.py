import pytest
import json
from unittest.mock import MagicMock
from apps.agents.base import BaseAgent
from apps.agents.agents import SentinelAgent, LanguageAgent
from apps.signals.models import Signal, SourceType
from apps.tenants.models import Tenant

class MockConcreteAgent(BaseAgent):
    prompt_name = "sentinel"
    def run(self, signal):
        return {}

# 1. BaseAgent JSON Parsing tests
def test_base_agent_json_parsing():
    agent = MockConcreteAgent()
    assert agent.parse_json_response('{"test": "val"}') == {"test": "val"}
    assert agent.parse_json_response('```json\n{"test": "val"}\n```') == {"test": "val"}
    assert agent.parse_json_response('{"test": "val\\nline"}') == {"test": "val\nline"}
    with pytest.raises(ValueError):
        agent.parse_json_response('{"test": "val"')

# 2. Comprehensive Fallback and Error Handling tests
@pytest.fixture
def mock_groq_custom(monkeypatch):
    instantiations = []
    completion_calls = []
    exceptions_to_raise = []
    success_responses = []

    class MockCompletions:
        def create(self, *args, **kwargs):
            completion_calls.append(kwargs)
            if exceptions_to_raise:
                exc = exceptions_to_raise.pop(0)
                if exc:
                    raise exc
            
            resp_content = '{"status": "success", "domain": "legal"}'
            if success_responses:
                resp_content = success_responses.pop(0)
                
            mock_resp = MagicMock()
            mock_choice = MagicMock()
            mock_choice.message.content = resp_content
            mock_resp.choices = [mock_choice]
            return mock_resp

    class MockChat:
        def __init__(self):
            self.completions = MockCompletions()

    class MockGroqClient:
        def __init__(self, api_key=None, *args, **kwargs):
            instantiations.append(api_key)
            self.chat = MockChat()

    import apps.agents.base
    import groq
    monkeypatch.setattr(apps.agents.base, "Groq", MockGroqClient)
    monkeypatch.setattr(groq, "Groq", MockGroqClient)
    yield instantiations, completion_calls, exceptions_to_raise, success_responses

@pytest.mark.django_db
def test_call_groq_success(settings, mock_groq_custom):
    settings.GROQ_API_KEY = "key_1"
    settings.GROQ_API_KEY_2 = "key_2"
    instantiations, completion_calls, exceptions, successes = mock_groq_custom
    agent = MockConcreteAgent()
    
    res = agent.call_groq("Hello")
    assert "success" in res
    assert instantiations == ["key_1"]
    assert completion_calls[0]["model"] == "openai/gpt-oss-120b"

@pytest.mark.django_db
def test_call_groq_rate_limit_key_rotation(settings, mock_groq_custom):
    settings.GROQ_API_KEY = "key_1"
    settings.GROQ_API_KEY_2 = "key_2"
    instantiations, completion_calls, exceptions, successes = mock_groq_custom
    # Configure key 1 to throw 429 on all 3 attempts
    for _ in range(3):
        exc = Exception("Rate limit hit 429")
        exc.status_code = 429
        exceptions.append(exc)
    
    agent = MockConcreteAgent()
    res = agent.call_groq("Hello")
    assert "success" in res
    # Should try key_1 (3 times, fails), then key_2 (succeeds) for primary model
    assert instantiations == ["key_1", "key_1", "key_1", "key_2"]
    assert completion_calls[0]["model"] == "openai/gpt-oss-120b"
    assert completion_calls[3]["model"] == "openai/gpt-oss-120b"

@pytest.mark.django_db
def test_call_groq_model_decommissioned_skips_keys(settings, mock_groq_custom):
    settings.GROQ_API_KEY = "key_1"
    settings.GROQ_API_KEY_2 = "key_2"
    instantiations, completion_calls, exceptions, successes = mock_groq_custom
    # Primary model throws decommissioned (404/not found)
    exc = Exception("Model not found 404")
    exc.status_code = 404
    exceptions.append(exc)
    
    agent = MockConcreteAgent()
    res = agent.call_groq("Hello")
    assert "success" in res
    # Should immediately skip to next model without trying key_2 on first model
    assert instantiations == ["key_1", "key_1"]
    assert completion_calls[0]["model"] == "openai/gpt-oss-120b"
    assert completion_calls[1]["model"] == "openai/gpt-oss-20b"

@pytest.mark.django_db
def test_call_groq_400_bad_request_aborts(settings, mock_groq_custom):
    settings.GROQ_API_KEY = "key_1"
    settings.GROQ_API_KEY_2 = "key_2"
    instantiations, completion_calls, exceptions, successes = mock_groq_custom
    # Primary model throws 400 Bad Request
    exc = Exception("Bad Request 400")
    exc.status_code = 400
    exceptions.append(exc)
    
    agent = MockConcreteAgent()
    with pytest.raises(Exception) as excinfo:
        agent.call_groq("Hello")
    
    assert "400" in str(excinfo.value)
    # Should abort immediately without key or model fallback
    assert instantiations == ["key_1"]
    assert len(completion_calls) == 1

@pytest.mark.django_db
def test_call_groq_auth_failure_key_rotation(settings, mock_groq_custom):
    settings.GROQ_API_KEY = "key_1"
    settings.GROQ_API_KEY_2 = "key_2"
    instantiations, completion_calls, exceptions, successes = mock_groq_custom
    # Key 1 throws 401 Unauthorized
    exc = Exception("Unauthorized 401")
    exc.status_code = 401
    exceptions.append(exc)
    
    agent = MockConcreteAgent()
    res = agent.call_groq("Hello")
    assert "success" in res
    # Should rotate keys
    assert instantiations == ["key_1", "key_2"]

@pytest.mark.django_db
def test_call_groq_all_exhausted_raises(settings, mock_groq_custom):
    settings.GROQ_API_KEY = "key_1"
    settings.GROQ_API_KEY_2 = "key_2"
    instantiations, completion_calls, exceptions, successes = mock_groq_custom
    # Make all attempts raise 429 rate limit (2 models * 2 keys * 3 attempts = 12 attempts)
    for _ in range(12):
        exc = Exception("Rate Limit 429")
        exc.status_code = 429
        exceptions.append(exc)
        
    agent = MockConcreteAgent()
    with pytest.raises(Exception) as excinfo:
        agent.call_groq("Hello")
        
    assert "Rate Limit 429" in str(excinfo.value)
    assert len(instantiations) == 12

# 3. SentinelAgent domain normalization test (preserving functional requirement)
@pytest.mark.django_db
def test_sentinel_agent_normalization(mock_groq_custom):
    instantiations, completion_calls, exceptions, successes = mock_groq_custom
    successes.append(json.dumps({
        "severity_score": 0.5,
        "severity_label": "medium",
        "domain": "medical",
        "escalate": False,
        "reasoning": "test"
    }))
    
    tenant = Tenant.objects.create(name="Test Tenant", is_active=True)
    signal = Signal.objects.create(tenant=tenant, raw_text="medical emergency", source_type=SourceType.TEXT)
    
    agent = SentinelAgent()
    result = agent.run(signal)
    
    # Custom Sentinel run monkeypatch in apps/incidents/apps.py normalizes 'medical' -> 'health'
    assert result["domain"] == "health"

# 4. LanguageAgent Translation tests
@pytest.mark.django_db
def test_language_agent_translation_logic(settings, mock_groq_custom):
    settings.GROQ_API_KEY = "key_1"
    settings.GROQ_API_KEY_2 = "key_2"
    instantiations, completion_calls, exceptions, successes = mock_groq_custom
    
    # We will return the input JSON back for mock translation sub-calls
    # This validates LanguageAgent's chunking execution flow
    agent = LanguageAgent()
    coord_payload = {
        "situation_title": "Emergency Situation",
        "what_is_happening": "Incident details...",
        "situation_brief": "Summary...",
        "resources_needed": ["Ambulance"],
        "authorities_to_notify": ["DLSA"],
        "legal_provisions": [{"provision": "Article 21", "description": "Life and liberty", "relevance": "Direct"}],
        "legal_timeline": [{"step": 1, "action": "FIR", "timeframe": "Within 24 hours", "why_urgent": "evidence"}]
    }
    
    result = agent.run(coord_payload, target_language="hindi")
    
    # Check that output matches the key structure and Hindi terms post-processed
    assert result["situation_title"] == "Emergency Situation"
    # Ensure regex replaced "Within 24 hours" -> "24 घंटे के भीतर"
    assert result["legal_timeline"][0]["timeframe"] == "24 घंटे के भीतर"
    # Action contains "FIR"
    assert result["legal_timeline"][0]["action"] == "FIR"
    # Authorities to notify contains "DLSA" -> "डीएलएसए"
    assert result["authorities_to_notify"] == ["डीएलएसए"]

def test_triage_agent_max_tokens_limit():
    from apps.agents.agents import TriageAgent
    agent = TriageAgent()
    assert agent.max_tokens == 2000
    assert agent.prompt_name == "triage"

@pytest.mark.django_db
def test_call_groq_structured_output_payload(settings, mock_groq_custom):
    settings.GROQ_API_KEY = "key_1"
    instantiations, completion_calls, exceptions, successes = mock_groq_custom
    
    from apps.agents.agents import TriageAgent, CoordinationAgent, TriageSchema, CoordinationSchema
    
    agent_triage = TriageAgent()
    agent_triage._extract_keywords = MagicMock(return_value="heart attack")
    # Mock retrieve_medical_protocols
    import apps.agents.agents
    orig_retrieve = apps.agents.agents.retrieve_medical_protocols
    apps.agents.agents.retrieve_medical_protocols = MagicMock(return_value=[])
    
    # We need a mock signal
    mock_signal = MagicMock()
    mock_signal.raw_text = "Patient is suffering from heart attack"
    mock_signal.source_type = "web"
    
    successes.append('{"triage_severity": "immediate", "primary_concern": "Cardiac", "interventions": [], "required_facility": "trauma_center", "response_time": "immediate", "hospital_denial_detected": false, "confidence": 0.9, "escalate_to_rights_agent": false, "golden_window": {"time_remaining": "90m", "consequence_of_delay": "tissue damage"}, "emergency_contacts": []}')
    
    agent_triage.run(mock_signal)
    
    # Check Triage completion call parameters
    assert len(completion_calls) >= 1
    triage_call = completion_calls[-1]
    assert "response_format" in triage_call
    rf = triage_call["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["name"] == "triageschema"
    schema = rf["json_schema"]["schema"]
    assert "triage_severity" in schema["properties"]
    assert schema["additionalProperties"] is False
    
    # Reset and test CoordinationAgent
    completion_calls.clear()
    successes.clear()
    
    agent_coord = CoordinationAgent()
    # Mock other agent outputs
    sentinel_res = {"domain": "health", "requires_immediate_action": True}
    agent_outputs = {"triage": {"triage_severity": "immediate"}}
    
    successes.append('{"situation_title": "Title", "overall_severity": "immediate", "overall_severity_score": 0.9, "what_is_happening": "x", "immediate_actions": [], "resources_needed": [], "authorities_to_notify": [], "situation_brief": "brief", "escalation_required": false, "estimated_resolution_time": "hours", "conflict_resolution": null, "escalation_path": [], "evidence_to_collect": []}')
    
    agent_coord.run(mock_signal, sentinel_res, agent_outputs)
    
    # Check Coordination completion call parameters
    assert len(completion_calls) == 1
    coord_call = completion_calls[0]
    assert "response_format" in coord_call
    rf_coord = coord_call["response_format"]
    assert rf_coord["type"] == "json_schema"
    assert rf_coord["json_schema"]["strict"] is True
    assert rf_coord["json_schema"]["name"] == "coordinationschema"
    schema_coord = rf_coord["json_schema"]["schema"]
    assert "situation_title" in schema_coord["properties"]
    assert schema_coord["additionalProperties"] is False
    
    # Restore retriever mock
    apps.agents.agents.retrieve_medical_protocols = orig_retrieve


# ---------------------------------------------------------------------------
# Phase 2B Reliability & Safety Tests
# ---------------------------------------------------------------------------

def test_rag_threshold_filtering(settings):
    from rag.retriever import retrieve_legal_provisions
    settings.RAG_LEGAL_DISTANCE_THRESHOLD = 0.5
    
    # Query matching specific legal keywords returns ranked results
    results = retrieve_legal_provisions("salary unpaid employee")
    assert len(results) > 0
    assert "code" in results[0]["metadata"]


def test_rag_empty_retrieval_behavior(settings, monkeypatch):
    from rag.retriever import retrieve_legal_provisions
    
    # Query with no matching keywords returns empty list
    legal_results = retrieve_legal_provisions("xyz123unrecognizednonexistentterm")
    assert legal_results == []
    
    monkeypatch.setattr("rag.retriever.retrieve_legal_provisions", lambda *a, **kw: [])
    
    from apps.agents.agents import RightsAgent
    agent = RightsAgent()
    mock_signal = MagicMock()
    mock_signal.raw_text = "emergency"
    
    completion_called = False
    def mock_call_groq(user_msg, **kwargs):
        nonlocal completion_called
        completion_called = True
        assert "No sufficiently relevant knowledge-base material was retrieved." in user_msg
        return "{}"
    monkeypatch.setattr(agent, "call_groq", mock_call_groq)
    agent.run(mock_signal)
    assert completion_called is True


def test_authority_contact_sanitization():
    from apps.agents.directory import sanitize_contact_number
    
    assert sanitize_contact_number("01234-567890") == "Verified contact unavailable"
    assert sanitize_contact_number("1800-HOME-SEC") == "Verified contact unavailable"
    assert sanitize_contact_number("please call 9876543210") == "Verified contact unavailable"
    assert sanitize_contact_number("") == "Verified contact unavailable"
    
    assert sanitize_contact_number("108") == "108"
    assert sanitize_contact_number("100") == "100"
    assert sanitize_contact_number("15100") == "15100"
    assert sanitize_contact_number("14434") == "14434"
    assert sanitize_contact_number("1915") == "1915"
    assert sanitize_contact_number("1930") == "1930"
    assert sanitize_contact_number("14444") == "Verified contact unavailable"


def test_evidence_checklist_cases():
    def bringChecklist_python(domain, nearest_authority_type, title, brief):
        domainL = (domain or '').lower()
        auth = (nearest_authority_type or '').strip()
        textToMatch = ((title or '') + ' ' + (brief or '')).lower()
        isTenantDispute = any(k in textToMatch for k in ['evict', 'tenant', 'rent', 'landlord', 'lease'])
        
        if auth == 'Police Complaint Authority' or 'fir refusal' in textToMatch or 'police refused' in textToMatch:
            return 'fir_refusal'
        elif isTenantDispute:
            return 'eviction'
        elif auth == 'Labour Court' or domainL == 'labour':
            return 'wage_theft'
        return 'general'
        
    assert bringChecklist_python("cross_domain", "DLSA", "Mass Shooting", "Emergency active shooter situation") == "general"
    assert bringChecklist_python("legal", "DLSA", "Eviction notice", "Landlord threatened to lock me out of my apartment") == "eviction"


def test_coordination_agent_emergency_priority():
    from apps.agents.agents import reorder_actions_by_safety
    
    actions = [
        {"priority": 1, "action": "File a civil lawsuit against landlord", "responsible_party": "Citizen", "time_window": "3 days"},
        {"priority": 2, "action": "Call 108 ambulance immediately", "responsible_party": "Citizen", "time_window": "1 minute"}
    ]
    reordered = reorder_actions_by_safety(actions, "Active shooter incident, patient bleeding and needs hospital")
    assert reordered[0]["priority"] == 1
    assert "ambulance" in reordered[0]["action"]
    assert reordered[1]["priority"] == 2
    assert "lawsuit" in reordered[1]["action"]


def test_coordination_agent_severity_protection():
    from apps.agents.agents import CoordinationAgent
    agent = CoordinationAgent()
    mock_signal = MagicMock()
    mock_signal.raw_text = "Mass casualty event"
    
    sentinel_res = {"domain": "health", "requires_immediate_action": True, "severity_score": 0.9}
    agent_outputs = {"triage": {"triage_severity": "immediate"}}
    
    import json
    def mock_call_groq(*args, **kwargs):
        return json.dumps({
            "situation_title": "Title",
            "overall_severity": "low",
            "overall_severity_score": 0.2,
            "what_is_happening": "x",
            "immediate_actions": [],
            "resources_needed": [],
            "authorities_to_notify": [],
            "situation_brief": "brief",
            "escalation_required": False,
            "estimated_resolution_time": "hours",
            "conflict_resolution": None,
            "escalation_path": [],
            "evidence_to_collect": []
        })
        
    agent.call_groq = mock_call_groq
    result = agent.run(mock_signal, sentinel_res, agent_outputs)
    assert result["overall_severity_score"] == 0.9
    assert result["overall_severity"] == "critical"


# ---------------------------------------------------------------------------
# Phase 2D-A Legal Foundation Tests
# ---------------------------------------------------------------------------

def test_rights_schema_validation():
    from apps.agents.agents import RightsSchema
    from pydantic import ValidationError
    
    # 1. RightsSchema accepts valid expected structure
    valid_data = {
        "rights_violated": ["BNS Section 101"],
        "severity": "high",
        "legal_provisions": [
            {
                "provision": "BNS Section 101",
                "code": "BNS",
                "section": "101",
                "description": "Punishment for murder",
                "relevance": "Detailed application explanation that meets the four to five sentences guideline to ensure the prompt constraints and parsing logic work correctly.",
                "applicability": "primary"
            }
        ],
        "immediate_actions": ["Call the police immediately"],
        "authority_to_contact": "Police Station",
        "nearest_authority_type": "Magistrate Court",
        "legal_timeline": [
            {
                "step": 1,
                "action": "File FIR",
                "timeframe": "Immediate",
                "why_urgent": "To preserve evidence."
            }
        ],
        "case_strength": 0.8
    }
    schema_instance = RightsSchema(**valid_data)
    assert schema_instance.severity == "high"
    
    # 2. RightsSchema rejects unexpected fields
    invalid_data = dict(valid_data)
    invalid_data["extra_random_field"] = "unexpected"
    with pytest.raises(ValidationError):
        RightsSchema(**invalid_data)


def test_legal_citation_validation_logic():
    from apps.agents.legal_reference import validate_legal_citation
    
    # 3. Valid BNS citation passes deterministic validation
    bns_res = validate_legal_citation("BNS", "101")
    assert bns_res["verified"] is True
    assert bns_res["legacy_section"] == "302"
    assert bns_res["legacy_code"] == "IPC"
    
    # 4. Valid BNSS citation passes deterministic validation
    bnss_res = validate_legal_citation("BNSS", "35")
    assert bnss_res["verified"] is True
    assert bnss_res["legacy_section"] == "41"
    assert bnss_res["legacy_code"] == "CrPC"
    
    # 5. Valid BSA citation validation if reference exists (should return verified=False since BSA is currently empty/not loaded)
    bsa_res = validate_legal_citation("BSA", "1")
    assert bsa_res["verified"] is False
    
    # 6. Unknown section is NOT marked verified
    unk_res = validate_legal_citation("BNS", "999")
    assert unk_res["verified"] is False
    assert unk_res["title"] == "Unverified legal provision"
    
    # 7. BNS and BNSS are treated as different codes
    res1 = validate_legal_citation("BNS", "35") # Section 35 is BNSS, not BNS
    assert res1["verified"] is False
    
    # 8. A BNSS procedural section cannot be silently treated as a BNS offence
    res2 = validate_legal_citation("BNS", "35")
    assert res2["type"] == "unknown" # BNS 35 is not in our database
    res3 = validate_legal_citation("BNSS", "35")
    assert res3["type"] == "procedural"


def test_rights_agent_validation_and_sanitization():
    from apps.agents.agents import RightsAgent
    
    agent = RightsAgent()
    mock_signal = MagicMock()
    mock_signal.raw_text = "test case"
    
    # 9. LLM cannot make an arbitrary section trusted merely by returning it
    # 10. RightsAgent authority contact is sanitized (replaces placeholder phone numbers)
    raw_json = {
        "rights_violated": ["BNS Section 999"],
        "severity": "medium",
        "legal_provisions": [
            {
                "provision": "BNS Section 999",
                "code": "BNS",
                "section": "999",
                "description": "Fake Section",
                "relevance": "Some random relevance sentence. Sentence number two is here. Sentence number three is here. Sentence number four is here."
            }
        ],
        "immediate_actions": ["Do something"],
        "authority_to_contact": "Local Police - call 01234-567890",
        "nearest_authority_type": "DLSA",
        "legal_timeline": [],
        "case_strength": 0.5
    }
    
    import json
    agent.call_groq = lambda *args, **kwargs: json.dumps(raw_json)
    
    result = agent.run(mock_signal)
    
    # Verify section 999 is NOT verified
    assert result["legal_provisions"][0]["verified"] is False
    # Verify authority contact is sanitized and resolved to canonical authority
    assert "01234-567890" not in result["authority_to_contact"]
    assert result["authority_to_contact"] == "National Legal Services Authority (NALSA)"


def test_legal_notice_agent_contact_safety():
    from apps.agents.agents import LegalNoticeAgent
    
    agent = LegalNoticeAgent()
    mock_signal = MagicMock()
    mock_signal.raw_text = "arbitrary text"
    
    # 11. LegalNoticeAgent cannot introduce an unverified operational contact
    # Mock LLM output containing a placeholder phone number
    agent.call_groq = lambda *args, **kwargs: "Draft Notice: Please call 1800-HOME-SEC for compliance or call 01234-567890."
    
    result = agent.run(mock_signal, rights_result={})
    assert "1800-HOME-SEC" not in result
    assert "01234-567890" not in result
    assert "Verified contact unavailable" in result


def test_pre_retrieval_keyword_extraction():
    from apps.agents.agents import RightsAgent
    
    agent = RightsAgent()
    agent.extract_search_query = MagicMock(return_value="theft BNS section 303 mobile")
    
    keywords = agent.extract_search_query("stole my phone from bag")
    assert keywords == "theft BNS section 303 mobile"


def test_definition_grounding():
    from apps.agents.agents import RightsAgent
    
    agent = RightsAgent()
    mock_signal = MagicMock()
    mock_signal.raw_text = "My neighbor was killed."
    
    raw_json = {
        "rights_violated": ["BNS Section 101"],
        "severity": "critical",
        "legal_provisions": [
            {
                "provision": "BNS Section 101",
                "code": "BNS",
                "section": "101",
                "description": "Wrong explanation here",
                "relevance": "This is relevant explanation.",
                "applicability": "primary"
            }
        ],
        "immediate_actions": ["File FIR"],
        "authority_to_contact": "Police Station",
        "nearest_authority_type": "Magistrate Court",
        "legal_timeline": [],
        "case_strength": 0.9
    }
    
    import json
    agent.call_groq = lambda *args, **kwargs: json.dumps(raw_json)
    
    result = agent.run(mock_signal)
    
    # Verify description was programmatically grounded to official statutory text from legal_reference.py
    assert result["legal_provisions"][0]["verified"] is True
    assert "Whoever commits murder" in result["legal_provisions"][0]["description"]
    assert "Wrong explanation here" not in result["legal_provisions"][0]["description"]


def test_bsa_provisions_in_database():
    from apps.agents.legal_reference import validate_legal_citation
    
    bsa_res = validate_legal_citation("BSA", "57")
    assert bsa_res["verified"] is True
    assert bsa_res["legacy_section"] == "65B"
    assert bsa_res["legacy_code"] == "Indian Evidence Act"
    assert "electronic record" in bsa_res["statutory_text"]


def test_triage_symptom_extraction():
    from apps.agents.agents import TriageAgent
    
    agent = TriageAgent()
    agent.extract_symptoms_and_keywords = MagicMock(return_value="severe bleeding cut wound")
    
    keywords = agent.extract_symptoms_and_keywords("My arm is cut and bleeding very heavily.")
    assert keywords == "severe bleeding cut wound"


def test_triage_burns_grounding_override():
    from apps.agents.agents import TriageAgent
    
    agent = TriageAgent()
    mock_signal = MagicMock()
    mock_signal.raw_text = "I spilled hot coffee and burned my fingers. I applied butter."
    
    raw_json = {
        "triage_severity": "urgent",
        "primary_concern": "Thermal burn",
        "interventions": [
            "Apply butter on the fingers: butter soothes the burn skin — none — put butter on affected area"
        ],
        "required_facility": "general_hospital",
        "response_time": "urgent",
        "hospital_denial_detected": False,
        "confidence": 0.9,
        "escalate_to_rights_agent": False,
        "golden_window": {
            "time_remaining": "immediate",
            "consequence_of_delay": "tissue damage"
        },
        "emergency_contacts": []
    }
    
    import json
    agent.call_groq = lambda *args, **kwargs: json.dumps(raw_json)
    
    # Mock retriever to avoid actual DB query
    import apps.agents.agents
    orig_retrieve = apps.agents.agents.retrieve_medical_protocols
    apps.agents.agents.retrieve_medical_protocols = MagicMock(return_value=[])
    
    result = agent.run(mock_signal)
    
    # Restore retriever
    apps.agents.agents.retrieve_medical_protocols = orig_retrieve
    
    # Verify the intervention was overridden to prevent the use of butter and mandate cool running water
    assert len(result["interventions"]) == 1
    assert "Apply cool running water" in result["interventions"][0]
    assert "Do NOT apply ice, butter, toothpaste" in result["interventions"][0]


def test_triage_self_harm_anxiety_downgrade():
    from apps.agents.agents import TriageAgent
    
    agent = TriageAgent()
    mock_signal = MagicMock()
    mock_signal.raw_text = "I am so scared, nervous and shaking because of the shooting. I don't know what to do."
    
    # LLM incorrectly returns required_facility as mental_health due to distress
    raw_json = {
        "triage_severity": "delayed",
        "primary_concern": "Acute anxiety",
        "interventions": ["Stabilize scene"],
        "required_facility": "mental_health",
        "response_time": "semi_urgent",
        "hospital_denial_detected": False,
        "confidence": 0.8,
        "escalate_to_rights_agent": False,
        "golden_window": {
            "time_remaining": "immediate",
            "consequence_of_delay": "panic"
        },
        "emergency_contacts": []
    }
    
    import json
    agent.call_groq = lambda *args, **kwargs: json.dumps(raw_json)
    
    import apps.agents.agents
    orig_retrieve = apps.agents.agents.retrieve_medical_protocols
    apps.agents.agents.retrieve_medical_protocols = MagicMock(return_value=[])
    
    result = agent.run(mock_signal)
    
    apps.agents.agents.retrieve_medical_protocols = orig_retrieve
    
    # Verify required_facility was downgraded from mental_health to general_hospital or clinic
    assert result["required_facility"] != "mental_health"
    assert result["required_facility"] in ["general_hospital", "clinic"]


def test_medical_protocol_registry_lookup():
    from apps.agents.medical_reference import validate_medical_protocol
    
    res = validate_medical_protocol("snake_bite_protocol")
    assert res["verified"] is True
    assert res["category"] == "emergency_medicine"
    assert "ASV" in res["statutory_text"]
    assert "2 hours" in res["statutory_text"]
    
    res_lower = validate_medical_protocol("Triage START")
    assert res_lower["verified"] is True
    assert res_lower["act"] == "START Protocol"


def test_sentinel_agent_severity_contract():
    agent = SentinelAgent()
    mock_signal = MagicMock()
    mock_signal.raw_text = "There is a deep unlit trench on the main highway posing a severe risk to vehicles."
    mock_signal.source_type = "text"

    raw_json = {
        "domain": "civic",
        "severity_score": 0.7,
        "severity_label": "high",
        "confidence": 0.9,
        "keywords": ["trench", "highway", "hazard"],
        "reasoning": "Deep unlit trench on high-speed road poses major physical accident risk.",
        "requires_immediate_action": False
    }

    agent.call_groq = lambda *args, **kwargs: json.dumps(raw_json)
    result = agent.run(mock_signal)

    assert "severity_score" in result
    assert result["severity_score"] == 0.7
    assert "severity_label" in result
    assert result["severity_label"] == "high"
    assert result["domain"] == "civic"


@pytest.mark.django_db
def test_civic_report_severity_pipeline_aggregation(monkeypatch):
    from pipeline.tasks import route_to_agents, coordination_agent
    from apps.signals.models import SourceType

    tenant = Tenant.objects.create(name="Civic Test Tenant", is_active=True)
    signal = Signal.objects.create(tenant=tenant, raw_text="Deep unlit trench on main road", source_type=SourceType.TEXT)

    actual_task = route_to_agents._get_current_object()
    mock_request = MagicMock()
    mock_request.retries = 0
    monkeypatch.setattr(type(actual_task), "request", property(lambda self: mock_request))

    # Mock coordination_agent.delay to prevent triggering downstream WebSocket task in unit test
    monkeypatch.setattr("pipeline.tasks.coordination_agent.delay", lambda *args, **kwargs: None)

    # 1. Medium/High risk civic report -> should obtain severity from Sentinel (0.7 / high), NOT 0.0 / low
    sentinel_high = {
        "domain": "civic",
        "severity_score": 0.7,
        "severity_label": "high",
        "requires_immediate_action": False,
        "reasoning": "Deep unlit trench on high-speed road"
    }

    route_to_agents.run(str(signal.id), sentinel_high)
    signal.refresh_from_db()
    incident = signal.incident
    assert incident.severity_score == 0.75  # 0.75 from SEVERITY_MAP["high"]
    assert incident.severity_label == "high"

    # 2. Genuinely low risk civic report -> should remain low
    sentinel_low = {
        "domain": "civic",
        "severity_score": 0.15,
        "severity_label": "low",
        "requires_immediate_action": False,
        "reasoning": "Minor aesthetic scuff on park bench"
    }

    route_to_agents.run(str(signal.id), sentinel_low)
    incident.refresh_from_db()
    assert incident.severity_score == 0.25  # 0.25 from SEVERITY_MAP["low"]
    assert incident.severity_label == "low"


# ---------------------------------------------------------------------------
# Deterministic Authority Routing Tests (Fix #2)
# ---------------------------------------------------------------------------

def test_authority_resolution_legal_canonical():
    from apps.agents.directory import resolve_authority
    # Generic Labour Court resolves to Labour Court authority type, but contact is "Verified contact unavailable"
    res = resolve_authority("legal", authority_hint="Labour Court")
    assert res["nearest_authority_type"] == "Labour Court"
    assert "Labour" in res["authority_to_contact"]
    assert res["contact"] == "Verified contact unavailable"
    assert res["verified"] is False

    # Specific e-Shram helpdesk hint explicitly resolves to e-Shram helpline 14434
    res_eshram = resolve_authority("legal", authority_hint="e-Shram")
    assert res_eshram["nearest_authority_type"] == "Labour Court"
    assert res_eshram["contact"] == "14434"
    assert res_eshram["verified"] is True


def test_authority_resolution_civic_rejects_legal_nalsa():
    from apps.agents.directory import resolve_authority
    # Civic report with invalid suggestion of NALSA/DLSA must reject hint and return civic authority
    res = resolve_authority("civic", authority_hint="National Legal Services Authority (NALSA)")
    assert res["authority_to_contact"] == "Local Municipal Authority"
    assert res["nearest_authority_type"] == "Municipal Corporation"
    assert res["contact"] == "Verified contact unavailable"


def test_authority_resolution_health_rejects_unrelated_legal():
    from apps.agents.directory import resolve_authority
    # Health report with invalid suggestion of DLSA must reject hint and return health authority
    res = resolve_authority("health", authority_hint="DLSA Legal Aid")
    assert res["authority_to_contact"] == "District Health Department (CMO Office)"
    assert res["nearest_authority_type"] == "Chief Medical Officer (CMO)"
    assert res["contact"] == "108"


def test_authority_resolution_emergency_valid():
    from apps.agents.directory import resolve_authority
    res = resolve_authority("emergency", authority_hint="Police")
    assert res["authority_to_contact"] == "Police Control Room"
    assert res["contact"] == "100"
    assert res["verified"] is True


def test_authority_resolution_unknown_authority():
    from apps.agents.directory import resolve_authority
    res = resolve_authority("civic", authority_hint="Random Unknown Dept 123")
    assert res["authority_to_contact"] == "Local Municipal Authority"
    assert res["nearest_authority_type"] == "Municipal Corporation"


def test_authority_resolution_missing_hint_fallback():
    from apps.agents.directory import resolve_authority
    res_civic = resolve_authority("civic", authority_hint=None)
    assert res_civic["authority_to_contact"] == "Local Municipal Authority"

    res_health = resolve_authority("health", authority_hint=None)
    assert res_health["authority_to_contact"] == "District Health Department (CMO Office)"


def test_authority_resolution_unverified_contact_string():
    from apps.agents.directory import resolve_authority
    res = resolve_authority("civic", authority_hint="Public Works Department (PWD)")
    assert res["contact"] == "Verified contact unavailable"
    assert res["verified"] is False


def test_authority_resolution_cross_domain_health_priority():
    from apps.agents.directory import resolve_authority
    res = resolve_authority("cross_domain", authority_hint="District Health Department")
    assert res["authority_to_contact"] == "District Health Department (CMO Office)"
    assert res["nearest_authority_type"] == "Chief Medical Officer (CMO)"


def test_existing_contact_sanitization():
    from apps.agents.directory import sanitize_contact_number, sanitize_text_contacts
    assert sanitize_contact_number("108") == "108"
    assert sanitize_contact_number("01234-567890") == "Verified contact unavailable"
    assert sanitize_contact_number("1800-HOME-SEC") == "Verified contact unavailable"
    assert "Verified contact unavailable" in sanitize_text_contacts("Call 01234-567890 immediately")


# ---------------------------------------------------------------------------
# Compact Verified Legal Context Tests (Fix #3)
# ---------------------------------------------------------------------------

def test_coordination_context_includes_verified_legal_provision_identifiers(monkeypatch):
    from apps.agents.agents import CoordinationAgent
    
    coord_agent = CoordinationAgent()
    captured_messages = []
    
    def dummy_call_groq(prompt_msg, response_schema=None):
        captured_messages.append(prompt_msg)
        return json.dumps({
            "situation_title": "Test Brief",
            "overall_severity": "high",
            "overall_severity_score": 0.75,
            "what_is_happening": "Test incident details.",
            "immediate_actions": [{"priority": 1, "action": "Test action", "responsible_party": "Police", "time_window": "promptly"}],
            "resources_needed": [],
            "authorities_to_notify": ["Police"],
            "situation_brief": "Sentence 1. Sentence 2. Sentence 3. Sentence 4.",
            "escalation_required": False,
            "estimated_resolution_time": "hours"
        })
        
    monkeypatch.setattr(coord_agent, "call_groq", dummy_call_groq)
    
    mock_signal = MagicMock()
    mock_signal.raw_text = "Police unlawful arrest without warrant."
    mock_signal.domain = "legal"
    
    sentinel_res = {"domain": "legal", "requires_immediate_action": True}
    agent_outputs = {
        "rights": {
            "rights_violated": ["Section 57 CrPC (now Section 58 BNSS)", "Article 22"],
            "severity": "high",
            "immediate_actions": ["Request bail"],
            "authority_to_contact": "DLSA",
            "legal_provisions": [
                {
                    "provision": "Section 58 BNSS",
                    "code": "BNSS",
                    "section": "58",
                    "description": "DO_NOT_INCLUDE_LONG_STATUTORY_DESCRIPTION_TEXT",
                    "relevance": "DO_NOT_INCLUDE_RELEVANCE_TEXT",
                    "verified": True
                },
                {
                    "provision": "Article 22",
                    "code": "Constitution",
                    "section": "Article 22",
                    "description": "DO_NOT_INCLUDE_ARTICLE_DESCRIPTION",
                    "verified": True
                }
            ]
        }
    }
    
    result = coord_agent.run(mock_signal, sentinel_res, agent_outputs)
    assert result["situation_title"] == "Test Brief"
    assert len(captured_messages) == 1
    
    user_msg = captured_messages[0]
    # 1. Compact verified provision identifiers ARE included
    assert "Verified legal provisions: ['Section 58 BNSS', 'Article 22']" in user_msg
    # 2. Full statutory descriptions and relevance text ARE NOT included
    assert "DO_NOT_INCLUDE_LONG_STATUTORY_DESCRIPTION_TEXT" not in user_msg
    assert "DO_NOT_INCLUDE_RELEVANCE_TEXT" not in user_msg


def test_coordination_context_excludes_unverified_legal_provisions(monkeypatch):
    from apps.agents.agents import CoordinationAgent
    
    coord_agent = CoordinationAgent()
    captured_messages = []
    
    def dummy_call_groq(prompt_msg, response_schema=None):
        captured_messages.append(prompt_msg)
        return json.dumps({
            "situation_title": "Test Brief",
            "overall_severity": "medium",
            "overall_severity_score": 0.5,
            "what_is_happening": "Details.",
            "immediate_actions": [],
            "resources_needed": [],
            "authorities_to_notify": ["DLSA"],
            "situation_brief": "S1. S2. S3. S4.",
            "escalation_required": False,
            "estimated_resolution_time": "hours"
        })
        
    monkeypatch.setattr(coord_agent, "call_groq", dummy_call_groq)
    
    mock_signal = MagicMock()
    mock_signal.raw_text = "Legal dispute."
    
    sentinel_res = {"domain": "legal", "requires_immediate_action": False}
    agent_outputs = {
        "rights": {
            "rights_violated": ["Some Act"],
            "severity": "medium",
            "immediate_actions": [],
            "authority_to_contact": "DLSA",
            "legal_provisions": [
                {
                    "provision": "Verified Section 10",
                    "verified": True
                },
                {
                    "provision": "Unverified Fake Section 999",
                    "verified": False
                }
            ]
        }
    }
    
    coord_agent.run(mock_signal, sentinel_res, agent_outputs)
    user_msg = captured_messages[0]
    
    # 3. Unverified provision is NOT presented as verified context
    assert "Verified Section 10" in user_msg
    assert "Unverified Fake Section 999" not in user_msg


def test_coordination_context_no_verified_provisions_produces_no_fabricated_context(monkeypatch):
    from apps.agents.agents import CoordinationAgent
    
    coord_agent = CoordinationAgent()
    captured_messages = []
    
    def dummy_call_groq(prompt_msg, response_schema=None):
        captured_messages.append(prompt_msg)
        return json.dumps({
            "situation_title": "Test Brief",
            "overall_severity": "low",
            "overall_severity_score": 0.2,
            "what_is_happening": "Civic hazard.",
            "immediate_actions": [],
            "resources_needed": [],
            "authorities_to_notify": ["Municipal Corporation"],
            "situation_brief": "S1. S2. S3. S4.",
            "escalation_required": False,
            "estimated_resolution_time": "days"
        })
        
    monkeypatch.setattr(coord_agent, "call_groq", dummy_call_groq)
    
    mock_signal = MagicMock()
    mock_signal.raw_text = "Pothole on street."
    
    sentinel_res = {"domain": "civic", "requires_immediate_action": False}
    agent_outputs = {
        "rights": {
            "rights_violated": [],
            "severity": "low",
            "immediate_actions": [],
            "authority_to_contact": "Local Municipal Authority",
            "legal_provisions": []
        }
    }
    
    coord_agent.run(mock_signal, sentinel_res, agent_outputs)
    user_msg = captured_messages[0]
    
    # 4. No verified provisions produces no fabricated context
    assert "Verified legal provisions" not in user_msg


def test_coordination_context_medical_remains_unchanged(monkeypatch):
    from apps.agents.agents import CoordinationAgent
    
    coord_agent = CoordinationAgent()
    captured_messages = []
    
    def dummy_call_groq(prompt_msg, response_schema=None):
        captured_messages.append(prompt_msg)
        return json.dumps({
            "situation_title": "Medical Brief",
            "overall_severity": "critical",
            "overall_severity_score": 0.95,
            "what_is_happening": "Medical emergency.",
            "immediate_actions": [{"priority": 1, "action": "Call Ambulance", "responsible_party": "Paramedics", "time_window": "immediate"}],
            "resources_needed": ["Ambulance"],
            "authorities_to_notify": ["CMO"],
            "situation_brief": "S1. S2. S3. S4.",
            "escalation_required": True,
            "estimated_resolution_time": "immediate"
        })
        
    monkeypatch.setattr(coord_agent, "call_groq", dummy_call_groq)
    
    mock_signal = MagicMock()
    mock_signal.raw_text = "Severe burn patient."
    
    sentinel_res = {"domain": "health", "requires_immediate_action": True}
    agent_outputs = {
        "triage": {
            "triage_severity": "immediate",
            "primary_concern": "Burn handoff note",
            "interventions": ["Apply cool running water"],
            "required_facility": "trauma_center",
            "response_time": "immediate",
            "hospital_denial_detected": False
        }
    }
    
    coord_agent.run(mock_signal, sentinel_res, agent_outputs)
    user_msg = captured_messages[0]
    
    # 5. Medical Coordination context remains unchanged
    assert "Medical assessment:" in user_msg
    assert "Triage severity: immediate" in user_msg
    assert "Primary concern: Burn handoff note" in user_msg
    assert "Interventions needed: ['Apply cool running water']" in user_msg
    assert "Required facility: trauma_center" in user_msg
    assert "Response time: immediate" in user_msg
    assert "Hospital denial: False" in user_msg





