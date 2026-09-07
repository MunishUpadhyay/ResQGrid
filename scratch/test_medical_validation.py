import os
import sys
import django
import json
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
django.setup()

from apps.tenants.models import Tenant
from apps.signals.models import Signal, SourceType
from apps.incidents.models import Incident
from pipeline.tasks import ingest_signal
from apps.signals.citizen_views import citizen_signal_status_api
from django.test import RequestFactory

tenant = Tenant.objects.first() or Tenant.objects.create(name="Validation Tenant", is_active=True)

def run_test_case(title, raw_text):
    print(f"\n==================================================")
    print(f"RUNNING TEST CASE: {title}")
    print(f"Signal: {raw_text}")
    print(f"==================================================")
    
    signal = Signal.objects.create(
        tenant=tenant,
        raw_text=raw_text,
        source_type=SourceType.TEXT,
        preferred_language="english"
    )
    
    # Execute pipeline steps
    ingest_signal.delay(str(signal.id))
    
    incident = Incident.objects.get(signal=signal)
    outputs = incident.agent_outputs or {}
    
    # Build API view response data structure
    factory = RequestFactory()
    request = factory.get(f'/api/signals/{signal.id}/status/')
    request.session = {'verified_' + str(signal.id): True}
    
    response = citizen_signal_status_api(request, signal_id=str(signal.id))
    report_json = json.loads(response.content)
    
    res_data = report_json.get("result", {}) or {}
    
    case_summary = {
        "title": title,
        "raw_text": raw_text,
        "domain": incident.domain,
        "severity_label": incident.severity_label,
        "severity_score": incident.severity_score,
        "situation_brief": incident.situation_brief,
        "triage": outputs.get("triage", {}),
        "coordination": outputs.get("coordination", {}),
        "ui_result": {
            "nearest_authority_type": res_data.get("nearest_authority_type"),
            "authority_to_contact": res_data.get("authority_to_contact"),
            "golden_window": res_data.get("golden_window"),
            "emergency_contacts": res_data.get("emergency_contacts"),
            "authorities_to_notify": res_data.get("authorities_to_notify"),
            "immediate_actions": res_data.get("immediate_actions")
        }
    }
    
    print("\n--- INCIDENT ANALYSIS ---")
    print(f"Domain: {incident.domain}")
    print(f"Severity Score: {incident.severity_score} ({incident.severity_label})")
    print(f"Situation Brief: {incident.situation_brief}")
    
    print("\n--- TRIAGE OUTPUT ---")
    triage = outputs.get("triage", {})
    print(f"Triage Severity: {triage.get('triage_severity')}")
    print(f"Primary Concern: {triage.get('primary_concern')}")
    print(f"Interventions: {json.dumps(triage.get('interventions'), indent=2)}")
    print(f"Golden Window: {triage.get('golden_window')}")
    print(f"Emergency Contacts: {json.dumps(triage.get('emergency_contacts'), indent=2)}")
    
    print("\n--- COORDINATION OUTPUT ---")
    coord = outputs.get("coordination", {})
    print(f"Authorities to Notify: {coord.get('authorities_to_notify')}")
    print(f"Immediate Actions: {json.dumps(coord.get('immediate_actions'), indent=2)}")
    
    print("\n--- CITIZEN REPORT JSON (FOR FRONTEND) ---")
    print(f"Nearest Authority Type: {res_data.get('nearest_authority_type')}")
    print(f"Authority to Contact: {res_data.get('authority_to_contact')}")
    print(f"Golden Window UI: {res_data.get('golden_window')}")
    print(f"Emergency Contacts UI: {res_data.get('emergency_contacts')}")
    print(f"Authorities to Notify UI: {res_data.get('authorities_to_notify')}")
    
    return case_summary

if __name__ == "__main__":
    case1 = run_test_case(
        "CASE 1: PERSISTENT LOWER BACK PAIN",
        "My father has had persistent lower back pain for about three weeks. It gets worse when he stands for a long time. He has no breathing difficulty, severe bleeding, loss of consciousness, or major recent injury. We have not consulted a doctor yet. What kind of medical professional should we see, what warning signs should we watch for, and what should we do next?"
    )
    
    case2 = run_test_case(
        "CASE 2: EARACHE AND DIZZINESS",
        "My sister has had mild persistent ear pain and dizziness for 4 days after a cold. No fever, no discharge, no confusion, no sudden hearing loss. We haven't seen a doctor yet. Who should we consult and what precautions should we take?"
    )
    
    results_path = BASE_DIR / "scratch" / "validation_results.json"
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump({"case1": case1, "case2": case2}, f, indent=2, ensure_ascii=False)
    print(f"\nSaved validation results to {results_path}")
