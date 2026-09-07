"""
Prahari Celery Task Chain
=========================

Execution flow:

    ingest_signal(signal_id)
        └─► classify_domain(signal_id)
                └─► route_to_agents(signal_id)
                        └─► coordination_agent(signal_id)
                                └─► push_to_websocket(incident_id)

"""

import logging
from datetime import datetime, timezone
from asgiref.sync import async_to_sync
from celery import shared_task
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)


def is_retryable_exception(exc) -> bool:
    """
    Classify whether an exception is retryable (transient) or not (permanent).
    """
    status_code = getattr(exc, "status_code", None)
    exc_str = str(exc).lower()
    
    # 400 Bad Request is NOT retryable
    if status_code == 400 or "400" in str(exc) or "bad request" in exc_str:
        return False
        
    # Auth errors are NOT retryable
    if status_code in (401, 403) or "401" in str(exc) or "403" in str(exc) or "unauthorized" in exc_str or "forbidden" in exc_str:
        return False
        
    # Model decommissioned/not found is NOT retryable
    if status_code == 404 or "decommissioned" in exc_str or "not found" in exc_str or "unknown model" in exc_str:
        return False
        
    # Standard application/programming or lookup errors are NOT retryable
    if isinstance(exc, (KeyError, ValueError, AttributeError, TypeError, ImportError, NameError)):
        return False
        
    return True


def handle_task_failure(signal_id: str, exc: Exception):
    """
    Helper to mark a Signal status as failed and store error details in metadata.
    """
    from apps.signals.models import Signal
    try:
        signal = Signal.objects.get(id=signal_id)
        signal.status = 'failed'
        if not isinstance(signal.metadata, dict):
            signal.metadata = {}
        signal.metadata['error'] = str(exc)
        signal.save(update_fields=['status', 'metadata'])
        logger.info("[Pipeline] Signal %s marked as FAILED. Error details saved.", signal_id)
    except Exception as e:
        logger.error("[Pipeline] Failed to mark signal %s as failed: %s", signal_id, e)


def handle_incident_task_failure(incident_id: str, exc: Exception):
    """
    Helper to handle failure using incident_id.
    """
    from apps.incidents.models import Incident
    try:
        incident = Incident.objects.select_related('signal').get(id=incident_id)
        handle_task_failure(str(incident.signal.id), exc)
    except Exception as e:
        logger.error("[Pipeline] Failed to load incident %s for failure tracking: %s", incident_id, e)


# ---------------------------------------------------------------------------
# 1. Ingest
# ---------------------------------------------------------------------------

@shared_task(bind=True, max_retries=3, default_retry_delay=2, name="pipeline.ingest_signal")
def ingest_signal(self, signal_id: str):
    """
    Step 1 — Entry point for the processing pipeline.
    """
    logger.info("[Pipeline: %s] [ingest_signal] Processing signal", signal_id)
    from apps.signals.models import Signal
    try:
        signal = Signal.objects.get(id=signal_id)
        signal.status = 'processing'
        signal.save(update_fields=['status'])
        return classify_domain.delay(signal_id)
    except Exception as exc:
        retries_exhausted = self.request.retries >= self.max_retries
        if is_retryable_exception(exc) and not retries_exhausted:
            logger.warning("[Pipeline: %s] [ingest_signal] Retryable error: %s. Retrying attempt %d/%d...", 
                           signal_id, exc, self.request.retries + 1, self.max_retries)
            raise self.retry(exc=exc, countdown=2 * (2 ** self.request.retries))
        else:
            handle_task_failure(signal_id, exc)
            raise exc


# ---------------------------------------------------------------------------
# 2. Classify
# ---------------------------------------------------------------------------

@shared_task(bind=True, max_retries=3, default_retry_delay=5, name="pipeline.classify_domain")
def classify_domain(self, signal_id: str):
    """
    Step 2 — Domain classification.
    """
    logger.info("[Pipeline: %s] [classify_domain] Classifying signal", signal_id)
    from apps.signals.models import Signal
    from apps.agents.agents import SentinelAgent
    
    try:
        signal = Signal.objects.get(id=signal_id)
        
        # Idempotency check: if already classified, skip running SentinelAgent again
        if signal.domain and signal.status in ['classified', 'processed']:
            logger.info("[Pipeline: %s] [classify_domain] Signal already classified (domain=%s). Resuming chain.", signal_id, signal.domain)
            sentinel_result = {
                'domain': signal.domain,
                'requires_immediate_action': True,
                'keywords': [],
                'timing': {'start': '', 'end': '', 'duration_ms': 0}
            }
            return route_to_agents.delay(signal_id, sentinel_result)
            
        agent = SentinelAgent()
        start_time = datetime.now(timezone.utc)
        result = agent.run(signal)
        end_time = datetime.now(timezone.utc)
        duration_ms = int((end_time - start_time).total_seconds() * 1000)
        
        # Add timing info to sentinel result dictionary
        result['timing'] = {
            'start': start_time.isoformat(),
            'end': end_time.isoformat(),
            'duration_ms': duration_ms
        }
        
        domain_val = result.get('domain')
        if domain_val == 'cross_domain':
            domain_val = 'cross'
        
        signal.domain = domain_val
        signal.status = 'classified'
        signal.save(update_fields=['domain', 'status'])
        
        return route_to_agents.delay(signal_id, result)
    except Exception as exc:
        retries_exhausted = self.request.retries >= self.max_retries
        if is_retryable_exception(exc) and not retries_exhausted:
            logger.warning("[Pipeline: %s] [classify_domain] Retryable error: %s. Retrying attempt %d/%d...", 
                           signal_id, exc, self.request.retries + 1, self.max_retries)
            raise self.retry(exc=exc, countdown=5 * (2 ** self.request.retries))
        else:
            logger.error("[Pipeline: %s] [classify_domain] Terminal failure or retries exhausted for signal: %s", signal_id, exc)
            handle_task_failure(signal_id, exc)
            raise exc


# ---------------------------------------------------------------------------
# 3. Route to agents
# ---------------------------------------------------------------------------

@shared_task(bind=True, max_retries=3, default_retry_delay=5, name="pipeline.route_to_agents")
def route_to_agents(self, signal_id: str, sentinel_result: dict = None):
    """
    Step 3 — Parallel agent dispatch.
    """
    logger.info("[route_to_agents] signal_id=%s, sentinel_result=%s", signal_id, sentinel_result)
    from apps.signals.models import Signal
    from apps.incidents.models import Incident, SeverityLevel
    from apps.agents.agents import RightsAgent, TriageAgent

    try:
        signal = Signal.objects.get(id=signal_id)
        sentinel_result = sentinel_result or {}
        domain = sentinel_result.get("domain") or signal.domain

        # Normalize domain to match Domain choices
        domain_val = domain
        if domain_val == 'cross_domain':
            domain_val = 'cross'

        is_legal_domain = domain_val in ["legal", "cross"]
        is_health_domain = domain_val in ["health", "emergency", "cross"]
        
        # Extract sentinel timing from sentinel result
        sentinel_timing = sentinel_result.pop('timing', None)

        agent_outputs = {
            "sentinel": sentinel_result,
            "timing": {}
        }
        if sentinel_timing:
            agent_outputs["timing"]["sentinel"] = sentinel_timing

        # Idempotency check: Reuse existing outputs to avoid redundant LLM calls on retry
        try:
            incident = Incident.objects.get(signal=signal)
            existing_outputs = incident.agent_outputs or {}
            logger.info("[route_to_agents] Existing incident found. Checking for existing agent outputs.")
        except Incident.DoesNotExist:
            incident = None
            existing_outputs = {}

        # Run TriageAgent if health, emergency, or cross and not already run
        if is_health_domain:
            if "triage" in existing_outputs:
                logger.info("[route_to_agents] Reusing existing triage output.")
                agent_outputs["triage"] = existing_outputs["triage"]
                if "timing" in existing_outputs and "triage" in existing_outputs["timing"]:
                    agent_outputs["timing"]["triage"] = existing_outputs["timing"]["triage"]
            else:
                logger.info("[route_to_agents] Running TriageAgent for signal_id=%s", signal_id)
                triage_agent = TriageAgent()
                start_time = datetime.now(timezone.utc)
                triage_result = triage_agent.run(signal, sentinel_result)
                end_time = datetime.now(timezone.utc)
                duration_ms = int((end_time - start_time).total_seconds() * 1000)
                
                agent_outputs["triage"] = triage_result
                agent_outputs["timing"]["triage"] = {
                    'start': start_time.isoformat(),
                    'end': end_time.isoformat(),
                    'duration_ms': duration_ms
                }

        # Run RightsAgent if legal or cross and not already run
        if is_legal_domain:
            if "rights" in existing_outputs:
                logger.info("[route_to_agents] Reusing existing rights output.")
                agent_outputs["rights"] = existing_outputs["rights"]
                if "timing" in existing_outputs and "rights" in existing_outputs["timing"]:
                    agent_outputs["timing"]["rights"] = existing_outputs["timing"]["rights"]
            else:
                logger.info("[route_to_agents] Running RightsAgent for signal_id=%s", signal_id)
                rights_agent = RightsAgent()
                start_time = datetime.now(timezone.utc)
                rights_result = rights_agent.run(signal, sentinel_result)
                end_time = datetime.now(timezone.utc)
                duration_ms = int((end_time - start_time).total_seconds() * 1000)
                
                agent_outputs["rights"] = rights_result
                agent_outputs["timing"]["rights"] = {
                    'start': start_time.isoformat(),
                    'end': end_time.isoformat(),
                    'duration_ms': duration_ms
                }

        # Cross-domain handoff/escalation: if triage results recommend rights escalation and rights agent has not run
        if "triage" in agent_outputs and agent_outputs["triage"].get("escalate_to_rights_agent") and "rights" not in agent_outputs:
            if "rights" in existing_outputs:
                logger.info("[route_to_agents] Reusing existing rights output for triage-recommended rights agent run.")
                agent_outputs["rights"] = existing_outputs["rights"]
                if "timing" in existing_outputs and "rights" in existing_outputs["timing"]:
                    agent_outputs["timing"]["rights"] = existing_outputs["timing"]["rights"]
            else:
                logger.info("[route_to_agents] Escalating to RightsAgent for signal_id=%s due to triage recommendation", signal_id)
                rights_agent = RightsAgent()
                start_time = datetime.now(timezone.utc)
                rights_result = rights_agent.run(signal, sentinel_result)
                end_time = datetime.now(timezone.utc)
                duration_ms = int((end_time - start_time).total_seconds() * 1000)
                
                agent_outputs["rights"] = rights_result
                agent_outputs["timing"]["rights"] = {
                    'start': start_time.isoformat(),
                    'end': end_time.isoformat(),
                    'duration_ms': duration_ms
                }

        # Determine severity properties using a predefined mapping
        SEVERITY_MAP = {
            "critical": 1.0,
            "high": 0.75,
            "medium": 0.5,
            "low": 0.25,
            "immediate": 1.0,
            "delayed": 0.75,
            "minor": 0.25,
            "deceased": 0.0,
        }

        scores = []
        
        # 1. Sentinel Score
        sent_score = sentinel_result.get("severity_score")
        if sent_score is not None:
            try:
                scores.append(float(sent_score))
            except (ValueError, TypeError):
                pass

        # 1b. Sentinel Requires Immediate Action
        if sentinel_result.get("requires_immediate_action") is True:
            scores.append(0.75)
        
        # 2. Sentinel Label Mapped
        sent_label = sentinel_result.get("severity_label", "").lower()
        if sent_label in SEVERITY_MAP:
            scores.append(SEVERITY_MAP[sent_label])

        # 3. Triage Severity Mapped
        if "triage" in agent_outputs:
            triage_sev = agent_outputs["triage"].get("triage_severity", "").lower()
            if triage_sev in SEVERITY_MAP:
                scores.append(SEVERITY_MAP[triage_sev])

        # 4. Rights Severity Mapped
        if "rights" in agent_outputs:
            rights_sev = agent_outputs["rights"].get("severity", "").lower()
            if rights_sev in SEVERITY_MAP:
                scores.append(SEVERITY_MAP[rights_sev])

        # Fallback if no scores collected
        if not scores:
            scores.append(0.0)

        severity_score = max(scores)

        # Normalize back to SeverityLevel choices
        if severity_score >= 0.9:
            severity_label = "critical"
        elif severity_score >= 0.6:
            severity_label = "high"
        elif severity_score >= 0.3:
            severity_label = "medium"
        else:
            severity_label = "low"

        incident, created = Incident.objects.update_or_create(
            signal=signal,
            defaults={
                "severity_score": severity_score,
                "severity_label": severity_label,
                "domain": domain_val,
                "agent_outputs": agent_outputs,
            }
        )

        if created:
            from apps.audit.models import AuditLog
            AuditLog.log_event(
                incident=incident,
                action='incident_created',
                performed_by='system/pipeline'
            )

        logger.info("[route_to_agents] Incident %s (created=%s) updated with domain=%s, severity=%s (score=%s). Chaining to coordination_agent.", 
                    incident.id, created, domain_val, severity_label, severity_score)

        return coordination_agent.delay(str(incident.id), agent_outputs)

    except Exception as exc:
        retries_exhausted = self.request.retries >= self.max_retries
        if is_retryable_exception(exc) and not retries_exhausted:
            logger.warning("[route_to_agents] Retryable error encountered. Retry attempt %d/%d...", self.request.retries + 1, self.max_retries)
            # Use exponential backoff: countdown = 5 * (2 ** self.request.retries)
            raise self.retry(exc=exc, countdown=5 * (2 ** self.request.retries))
        else:
            logger.error("[route_to_agents] Non-retryable error encountered or retries exhausted.")
            handle_task_failure(signal_id, exc)
            raise exc


# ---------------------------------------------------------------------------
# 4. Coordination
# ---------------------------------------------------------------------------

@shared_task(bind=True, max_retries=3, default_retry_delay=5, name="pipeline.coordination_agent")
def coordination_agent(self, incident_id: str, agent_outputs: dict = None):
    """
    Step 4 — Resource coordination and situation brief.

    Args:
        incident_id (str): UUID string of the Incident.
        agent_outputs (dict): Outputs of preceding agents.
    """
    logger.info("[coordination_agent] Running for incident_id=%s", incident_id)
    from apps.incidents.models import Incident
    from apps.signals.models import Signal
    from apps.agents.agents import CoordinationAgent

    try:
        agent_outputs = agent_outputs or {}

        incident = Incident.objects.select_related('signal').get(
            id=incident_id
        )
        signal = incident.signal

        # Idempotency check: Reuse existing coordination output if already present
        existing_outputs = incident.agent_outputs or {}
        if 'coordination' in existing_outputs:
            logger.info("[coordination_agent] Reusing existing coordination output.")
            coord_result = existing_outputs['coordination']
        else:
            # Rebuild sentinel result from signal fields and preceding outputs
            sentinel_from_outputs = agent_outputs.get('sentinel', {})
            requires_immediate = sentinel_from_outputs.get('requires_immediate_action', True)
            keywords = sentinel_from_outputs.get('keywords', [])

            sentinel_result = {
                'domain': signal.domain,
                'requires_immediate_action': requires_immediate,
                'keywords': keywords
            }

            coord_agent = CoordinationAgent()
            start_time = datetime.now(timezone.utc)
            coord_result = coord_agent.run(signal, sentinel_result, agent_outputs)
            end_time = datetime.now(timezone.utc)
            duration_ms = int((end_time - start_time).total_seconds() * 1000)

            # Update incident with coordination output
            agent_outputs['coordination'] = coord_result
            if 'timing' not in agent_outputs:
                agent_outputs['timing'] = {}
            agent_outputs['timing']['coordination'] = {
                'start': start_time.isoformat(),
                'end': end_time.isoformat(),
                'duration_ms': duration_ms
            }
            new_score = coord_result.get('overall_severity_score', incident.severity_score)
            incident.severity_score = new_score
            if new_score >= 0.9:
                incident.severity_label = "critical"
            elif new_score >= 0.6:
                incident.severity_label = "high"
            elif new_score >= 0.35:
                incident.severity_label = "medium"
            else:
                incident.severity_label = "low"

        incident.agent_outputs = agent_outputs
        incident.situation_brief = coord_result.get('situation_brief', '')
        incident.save(update_fields=[
            'agent_outputs', 'situation_brief', 'severity_score', 'severity_label'
        ])

        # Chain to websocket/language agent
        return push_to_websocket.delay(str(incident_id), coord_result)

    except Exception as exc:
        retries_exhausted = self.request.retries >= self.max_retries
        if is_retryable_exception(exc) and not retries_exhausted:
            logger.warning("[coordination_agent] Retryable error encountered. Retry attempt %d/%d...", self.request.retries + 1, self.max_retries)
            raise self.retry(exc=exc, countdown=5 * (2 ** self.request.retries))
        else:
            logger.error("[coordination_agent] Non-retryable error encountered or retries exhausted.")
            handle_incident_task_failure(incident_id, exc)
            raise exc


# ---------------------------------------------------------------------------
# 5. WebSocket push
# ---------------------------------------------------------------------------

@shared_task(bind=True, max_retries=3, default_retry_delay=5, name="pipeline.push_to_websocket")
def push_to_websocket(self, incident_id: str, coord_result: dict = None):
    """
    Step 5 — Push live update to all dashboard WebSocket clients.
    """
    logger.info("[push_to_websocket] incident_id=%s, coord_result=%s", incident_id, coord_result)
    from apps.incidents.models import Incident
    from apps.agents.agents import LanguageAgent
    from channels.layers import get_channel_layer
    from asgiref.sync import async_to_sync
    import json

    try:
        incident = Incident.objects.select_related('signal').get(
            id=incident_id
        )
        agent_outputs = incident.agent_outputs or {}
        coord = (coord_result or {} if isinstance(coord_result, dict) else {}) or agent_outputs.get('coordination', {})
        brief = coord.get('situation_brief', '') if isinstance(coord, dict) else ''
        if brief:
            incident.situation_brief = brief

        # Idempotency check: Reuse existing language translation if already present
        if 'language' in agent_outputs and 'hindi' in agent_outputs['language']:
            logger.info("[push_to_websocket] Reusing existing language translation outputs.")
            hindi_brief = agent_outputs['language']['hindi']
        else:
            # Run Language Agent to get Hindi translation
            hindi_brief = None
            if coord:
                if 'timing' not in agent_outputs:
                    agent_outputs['timing'] = {}
                
                start_time = datetime.now(timezone.utc)
                try:
                    lang_agent = LanguageAgent()
                    
                    # Construct unified translation payload containing all user-facing text fields
                    triage_out = agent_outputs.get("triage", {})
                    rights_out = agent_outputs.get("rights", {})
                    
                    is_legal_dom = incident.domain in ["legal", "cross"]
                    default_auth_type = "DLSA" if is_legal_dom else ("Chief Medical Officer (CMO)" if incident.domain in ["health", "emergency"] else "Municipal Corporation")
                    default_auth_contact = "National Legal Services Authority (NALSA)" if is_legal_dom else ("District Health Department (CMO Office)" if incident.domain in ["health", "emergency"] else "Local Municipal Authority")

                    nearest_authority_type = (rights_out or {}).get("nearest_authority_type") or (triage_out or {}).get("nearest_authority_type") or default_auth_type
                    authority_to_contact = (rights_out or {}).get("authority_to_contact") or (triage_out or {}).get("authority_to_contact") or default_auth_contact

                    translation_payload = {
                        "situation_title": coord.get("situation_title", ""),
                        "what_is_happening": coord.get("what_is_happening", ""),
                        "situation_brief": coord.get("situation_brief", ""),
                        "resources_needed": coord.get("resources_needed", []),
                        "authorities_to_notify": coord.get("authorities_to_notify", []),
                        "conflict_resolution": coord.get("conflict_resolution"),
                        "escalation_path": coord.get("escalation_path", []),
                        "immediate_actions": coord.get("immediate_actions", []),
                        "evidence_to_collect": coord.get("evidence_to_collect", []),
                        "nearest_authority_type": nearest_authority_type,
                        "authority_to_contact": authority_to_contact,
                    }
                    
                    if triage_out:
                        translation_payload["golden_window"] = triage_out.get("golden_window", {})
                        translation_payload["emergency_contacts"] = triage_out.get("emergency_contacts", [])
                        translation_payload["primary_concern"] = triage_out.get("primary_concern", "")
                        
                    if rights_out:
                        translation_payload["legal_provisions"] = rights_out.get("legal_provisions", [])
                        translation_payload["legal_timeline"] = rights_out.get("legal_timeline", [])

                    signal = incident.signal
                    print(f"[Language Agent] Running for language: {signal.preferred_language}")
                    preferred_lang = signal.preferred_language or 'hindi'
                    
                    def extract_json_from_text(text: str) -> dict:
                        import json
                        
                        def find_json_substring(t: str) -> str:
                            start = t.find("{")
                            if start == -1:
                                return ""
                            brace_count = 0
                            in_string = False
                            escaped = False
                            for idx in range(start, len(t)):
                                char = t[idx]
                                if char == '"' and not escaped:
                                    in_string = not in_string
                                elif in_string:
                                    if char == '\\':
                                        escaped = not escaped
                                    else:
                                        escaped = False
                                else:
                                    if char == '{':
                                        brace_count += 1
                                    elif char == '}':
                                        brace_count -= 1
                                        if brace_count == 0:
                                            return t[start:idx+1]
                                    escaped = False
                            return ""

                        def clean_json_string(t: str) -> str:
                            devanagari_escapes = {
                                '\\u0966': '0', '\\u0967': '1', '\\u0968': '2', '\\u0969': '3', '\\u096a': '4',
                                '\\u096b': '5', '\\u096c': '6', '\\u096d': '7', '\\u096e': '8', '\\u096f': '9'
                            }
                            for k, v in devanagari_escapes.items():
                                t = t.replace(k, v)
                                t = t.replace(k.upper(), v)
                            devanagari_chars = {
                                '०': '0', '१': '1', '२': '2', '३': '3', '४': '4',
                                '५': '5', '६': '6', '७': '7', '८': '8', '९': '9'
                            }
                            for k, v in devanagari_chars.items():
                                t = t.replace(k, v)
                            return t

                        json_block = find_json_substring(text)
                        if not json_block:
                            raise ValueError("No curly braces found in raw LLM response.")
                            
                        cleaned_block = clean_json_string(json_block)
                        try:
                            return json.loads(cleaned_block)
                        except Exception as first_err:
                            try:
                                cleaned_for_json = []
                                in_string = False
                                escaped = False
                                for char in cleaned_block:
                                    if char == '"' and not escaped:
                                        in_string = not in_string
                                        cleaned_for_json.append(char)
                                    elif in_string:
                                        if char == '\\':
                                            escaped = not escaped
                                            cleaned_for_json.append(char)
                                        else:
                                            if char == '\n':
                                                cleaned_for_json.append('\\n')
                                            elif char == '\r':
                                                cleaned_for_json.append('\\r')
                                            elif char == '\t':
                                                cleaned_for_json.append('\\t')
                                            else:
                                                cleaned_for_json.append(char)
                                            escaped = False
                                    else:
                                        cleaned_for_json.append(char)
                                        escaped = False
                                return json.loads("".join(cleaned_for_json))
                            except Exception:
                                raise first_err

                    def run_translation_safe(lang):
                        try:
                            return lang_agent.run(translation_payload, lang)
                        except ValueError as ve:
                            err_msg = str(ve)
                            if "Raw content:" in err_msg:
                                raw_content = err_msg.split("Raw content:", 1)[1].strip()
                                try:
                                    extracted = extract_json_from_text(raw_content)
                                    logger.info("Successfully extracted JSON translation after parsing failure.")
                                    return extracted
                                except Exception as extract_err:
                                    logger.error("Failed to extract JSON translation: %s", extract_err)
                                    raise ve
                            else:
                                raise ve

                    # Always translate to Hindi so it is available for toggling
                    hindi_brief = None
                    try:
                        hindi_brief = run_translation_safe("hindi")
                    except Exception as he:
                        logger.error("Failed to translate to Hindi: %s", he)
                        hindi_brief = translation_payload

                    # Check if preferred language is different from Hindi/English
                    preferred_lang = (signal.preferred_language or 'hindi').lower()
                    pref_brief = None
                    if preferred_lang != 'hindi' and preferred_lang != 'english':
                        try:
                            pref_brief = run_translation_safe(preferred_lang)
                        except Exception as pe:
                            logger.error("Failed to translate to preferred %s: %s", preferred_lang, pe)
                            pref_brief = translation_payload

                    # Store translated briefs in agent_outputs
                    agent_outputs['language'] = {
                        'hindi': hindi_brief,
                        'preferred': preferred_lang
                    }
                    if pref_brief:
                        agent_outputs['language'][preferred_lang] = pref_brief
                except Exception as e:
                    # Language translation is non-critical — log and continue
                    logger.error("Language agent error (non-critical): %s", e)
                    pref_lang = signal.preferred_language or 'hindi'
                    agent_outputs['language'] = {
                        pref_lang: translation_payload,
                        'preferred': pref_lang
                    }
                finally:
                    end_time = datetime.now(timezone.utc)
                    duration_ms = int((end_time - start_time).total_seconds() * 1000)
                    agent_outputs['timing']['language'] = {
                        'start': start_time.isoformat(),
                        'end': end_time.isoformat(),
                        'duration_ms': duration_ms
                    }
                    # Ingest incident to history collection for RAG
                    try:
                        from rag.ingest import ingest_incident_to_history
                        ingest_incident_to_history(
                            str(incident.id),
                            incident.situation_brief or '',
                            incident.domain,
                            incident.severity_label,
                            incident.is_resolved
                        )
                    except Exception as e:
                        logger.error("Failed to ingest incident to history (non-critical): %s", e)

        if brief:
            incident.situation_brief = brief
        incident.agent_outputs = agent_outputs
        incident.save(update_fields=['agent_outputs', 'situation_brief'])

        # Push to WebSocket dashboard
        channel_layer = get_channel_layer()
        tenant_id = str(incident.signal.tenant_id)

        message = {
            'type': 'incident_update',
            'incident_id': str(incident.id),
            'severity': str(incident.severity_score),
            'domain': incident.domain,
            'situation_brief': incident.situation_brief,
            'situation_brief_hindi': hindi_brief.get('situation_brief', '') if hindi_brief else '',
            'agent_outputs': incident.agent_outputs,
            'timestamp': incident.updated_at.isoformat() if incident.updated_at else incident.created_at.isoformat()
        }

        try:
            async_to_sync(channel_layer.group_send)(
                f"dashboard_{tenant_id}",
                {
                    'type': 'dashboard.update',
                    'message': message
                }
            )
            incident.signal.status = 'processed'
            incident.signal.save(update_fields=['status'])
        except Exception as e:
            logger.error("WebSocket push error (non-critical): %s", e)
            incident.signal.status = 'processed'
            incident.signal.save(update_fields=['status'])

        # Log pipeline completion event if not already logged
        from apps.audit.models import AuditLog
        if not AuditLog.objects.filter(incident=incident, action='pipeline_completed').exists():
            AuditLog.log_event(
                incident=incident,
                action='pipeline_completed',
                performed_by='system/pipeline'
            )

        return {'incident_id': str(incident_id), 'status': 'complete'}

    except Exception as exc:
        retries_exhausted = self.request.retries >= self.max_retries
        if is_retryable_exception(exc) and not retries_exhausted:
            logger.warning("[push_to_websocket] Retryable error encountered. Retry attempt %d/%d...", self.request.retries + 1, self.max_retries)
            raise self.retry(exc=exc, countdown=5 * (2 ** self.request.retries))
        else:
            logger.error("[push_to_websocket] Non-retryable error encountered or retries exhausted.")
            handle_incident_task_failure(incident_id, exc)
            raise exc


# ---------------------------------------------------------------------------
# 6. Periodic Maintenance & Cleanup
# ---------------------------------------------------------------------------

@shared_task(name="pipeline.cleanup_stale_signals")
def cleanup_stale_signals(timeout_minutes: int = 15):
    """
    Periodic maintenance task to clean up signals stuck in 'processing' or 'classified'
    state for longer than `timeout_minutes` without recent updates.
    """
    from datetime import timedelta
    from django.utils import timezone
    from apps.signals.models import Signal
    
    threshold = timezone.now() - timedelta(minutes=timeout_minutes)
    stuck_signals = Signal.objects.filter(
        status__in=['pending', 'processing', 'classified'],
        created_at__lt=threshold
    )
    
    cleaned_count = 0
    for signal in stuck_signals:
        incident = getattr(signal, "incident", None)
        # Check if there is an active incident that was updated recently
        if incident and incident.updated_at and incident.updated_at >= threshold:
            continue
        
        signal.status = 'failed'
        if not isinstance(signal.metadata, dict):
            signal.metadata = {}
        signal.metadata['error'] = 'Pipeline processing timed out'
        signal.save(update_fields=['status', 'metadata'])
        logger.info("[Pipeline: %s] [cleanup] Stale signal marked as failed due to inactivity.", signal.id)
        cleaned_count += 1
        
    return cleaned_count
