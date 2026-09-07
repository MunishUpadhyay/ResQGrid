import logging
import re
from datetime import datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, Http404
from apps.signals.models import Signal
from apps.incidents.models import Incident
from apps.tenants.models import Tenant
from pipeline.tasks import ingest_signal

logger = logging.getLogger(__name__)


def resolve_signal(signal_id):
    """
    Resolves signal_id (which could be a standard UUID string or a PRAH-YYYYMMDD-XXXX string)
    to a Signal object. Raises Http404 if not found or invalid.
    """
    signal_id_str = str(signal_id).strip()
    
    # Check if it matches PRAH-YYYYMMDD-XXXX pattern (case insensitive)
    match = re.match(r'^PRAH-(\d{8})-([0-9A-F]{4})$', signal_id_str, re.IGNORECASE)
    if match:
        date_str = match.group(1)
        prefix = match.group(2).lower()
        try:
            date_obj = datetime.strptime(date_str, "%Y%m%d").date()
        except ValueError:
            raise Http404("Invalid tracking ID format date")
        
        # Filter signals created on that date using timezone-aware timestamp range to ensure PostgreSQL compatibility
        from datetime import time
        from django.utils import timezone
        raw_start = datetime.combine(date_obj, time.min)
        raw_end = datetime.combine(date_obj, time.max)
        start_dt = timezone.make_aware(raw_start) if getattr(settings, "USE_TZ", False) else raw_start
        end_dt = timezone.make_aware(raw_end) if getattr(settings, "USE_TZ", False) else raw_end
        signals_on_date = Signal.objects.filter(created_at__gte=start_dt, created_at__lte=end_dt)
        for sig in signals_on_date:
            if str(sig.id).lower().startswith(prefix):
                return sig
        # Fallback: scan all signals if date range filter yields no match
        for sig in Signal.objects.all().order_by('-created_at')[:100]:
            if str(sig.id).lower().startswith(prefix):
                return sig
        raise Http404("Signal not found by tracking ID")
    else:
        from django.core.exceptions import ValidationError
        try:
            return Signal.objects.get(id=signal_id_str)
        except (Signal.DoesNotExist, ValueError, ValidationError):
            raise Http404("Signal not found by UUID")


def citizen_home(request):
    """
    GET /
    Renders the citizen landing page.
    """
    return render(request, "home.html")


def citizen_submit(request):
    """
    GET /submit/ — Renders submission form.
    POST /submit/ — Processes form, creates signal, and redirects to status.
    """
    if request.method == "POST":
        raw_text = request.POST.get("raw_text", "").strip()
        location = request.POST.get("location", "").strip()
        preferred_language = request.POST.get("preferred_language", "hindi").strip()
        anonymous = request.POST.get("anonymous") == "on"

        if not raw_text:
            return render(request, "submit.html", {"error": "Please describe what is happening."})

        # Resolve the default tenant (first active tenant)
        tenant = Tenant.objects.filter(is_active=True).first()
        if not tenant:
            tenant, _ = Tenant.objects.get_or_create(
                name="Default Tenant",
                defaults={"api_key_hash": Tenant.hash_api_key("default_key")}
            )

        # Save metadata containing location
        metadata = {}
        if location:
            metadata["location"] = location

        # Resolve user if logged in and NOT submitting anonymously
        sig_user = None
        if request.user.is_authenticated and not request.user.is_staff and not anonymous:
            sig_user = request.user

        # Create Signal directly in DB
        signal = Signal.objects.create(
            tenant=tenant,
            raw_text=raw_text,
            source_type="text",
            preferred_language=preferred_language,
            metadata=metadata,
            user=sig_user
        )

        # An unowned report is anonymous and requires a Return Key
        is_anonymous_signal = (sig_user is None)

        if is_anonymous_signal:
            import secrets
            import hashlib
            code = secrets.token_urlsafe(4)[:6].upper()
            code_hash = hashlib.sha256(code.encode()).hexdigest()
            if not isinstance(signal.metadata, dict):
                signal.metadata = {}
            signal.metadata['anonymous_code'] = code_hash
            signal.save(update_fields=['metadata'])
            
            # Pass code to redirect page via session
            request.session[f"anon_code_{signal.id}"] = code
            request.session[f"verified_{signal.id}"] = True
        else:
            # Authenticated user submitting identified report
            request.session[f"verified_{signal.id}"] = True

        # Enqueue the processing task
        try:
            ingest_signal.delay(str(signal.id))
        except Exception as e:
            logger.error("[Citizen Portal] Celery enqueue failed for signal %s: %s", signal.id, e)
            if getattr(settings, "DEBUG", False):
                try:
                    from config.celery import app as celery_app
                    celery_app.conf.task_always_eager = True
                    ingest_signal(str(signal.id))
                except Exception as sync_exc:
                    logger.error("[Citizen Portal] Synchronous development processing failed: %s", sync_exc)
            else:
                signal.status = 'failed'
                if not isinstance(signal.metadata, dict):
                    signal.metadata = {}
                signal.metadata['error'] = 'Processing service temporarily unavailable. Please retry later.'
                signal.save(update_fields=['status', 'metadata'])

        date_str = signal.created_at.strftime("%Y%m%d")
        uuid_first_4 = str(signal.id)[:4].upper()
        tracking_id = f"PRAH-{date_str}-{uuid_first_4}"
        return redirect(f"/report/{tracking_id}/")

    prefilled_text = request.GET.get("raw_text", "").strip()
    return render(request, "submit.html", {"prefilled_text": prefilled_text})


def citizen_track_report(request):
    """
    GET /track/ — Renders dedicated Track Report page.
    POST /track/ — Resolves tracking_id + return_key and redirects to report status.
    """
    if request.method == "POST":
        tracking_id = request.POST.get("tracking_id", "").strip()
        return_key = request.POST.get("return_key", "").strip().upper()

        if not tracking_id:
            return render(request, "track.html", {"error": "Please enter a valid Report ID."})

        try:
            signal = resolve_signal(tracking_id)
        except Http404:
            return render(request, "track.html", {"error": "Report ID not found. Please verify your Report ID and try again."})

        date_str = signal.created_at.strftime("%Y%m%d")
        uuid_first_4 = str(signal.id)[:4].upper()
        formatted_id = f"PRAH-{date_str}-{uuid_first_4}"

        if signal.user is not None:
            return redirect(f"/report/{formatted_id}/")

        stored_hash = signal.metadata.get("anonymous_code") if signal.metadata else None
        if return_key and stored_hash:
            import hashlib
            key_hash = hashlib.sha256(return_key.encode()).hexdigest()
            if key_hash == stored_hash:
                request.session[f"verified_{signal.id}"] = True
                return redirect(f"/report/{formatted_id}/")
            else:
                return render(request, "track.html", {
                    "error": "Invalid Return Key. Please check your 6-character key.",
                    "tracking_id": tracking_id
                })

        return redirect(f"/report/{formatted_id}/")

    return render(request, "track.html")


from django.conf import settings


def citizen_report_status(request, signal_id):
    """
    GET /report/<signal_id>/
    Renders the polling progress page for a submitted signal.
    """
    # Ensure signal exists
    signal = resolve_signal(signal_id)
    date_str = signal.created_at.strftime("%Y%m%d")
    uuid_first_4 = str(signal.id)[:4].upper()
    tracking_id = f"PRAH-{date_str}-{uuid_first_4}"
    
    # Check if this signal is anonymous
    is_anonymous = (signal.user is None)
    
    # Enforce access authorization
    verified = False
    if signal.user is not None:
        if request.user.is_authenticated and (request.user == signal.user or request.user.is_staff):
            verified = True
        else:
            raise Http404("Report not found")
    else:
        # Anonymous report: check session verification
        verified = bool(request.session.get(f"verified_{signal.id}"))
    
    # Retrieve raw code from session if it was just set during redirect
    session_key = f"anon_code_{signal.id}"
    raw_code = request.session.pop(session_key, None)
    
    return render(request, "report_status.html", {
        "signal_id": str(signal.id),
        "tracking_id": tracking_id,
        "site_url": settings.SITE_URL,
        "preferred_language": signal.preferred_language or "hindi",
        "is_anonymous": is_anonymous,
        "anonymous_access_code": raw_code,
        "verified": verified,
    })


def citizen_signal_status_api(request, signal_id):
    """
    GET /report/<signal_id>/status/
    Unauthenticated API endpoint for AJAX polling of pipeline status.
    """
    signal = resolve_signal(signal_id)
    
    # Enforce access checks
    if signal.user is not None:
        if not request.user.is_authenticated or (request.user != signal.user and not request.user.is_staff):
            return JsonResponse({"status": "unauthorized", "message": "Access denied."}, status=403)
    else:
        # Anonymous: check code verification
        stored_hash = signal.metadata.get("anonymous_code") if signal.metadata else None
        if stored_hash:
            if not request.session.get(f"verified_{signal.id}"):
                return JsonResponse({
                    "status": "unauthorized",
                    "message": "Anonymous access code verification required."
                }, status=403)
    
    incident = getattr(signal, "incident", None)

    # Base steps status mapping
    steps = {
        "received": True,
        "classified": bool(signal.domain),
        "analyzed": False,
        "coordinated": False,
        "translated": False
    }

    result = None

    if incident:
        # Check completed agent outputs
        outputs = incident.agent_outputs or {}
        
        # sentinel is done (pre-requisite for incident creation)
        steps["classified"] = True
        
        # Rights / Triage analysis:
        # - In cross domain, both triage and rights must finish (or triage recommends rights escalation)
        # - In health/emergency only, triage must finish
        # - In legal only, rights must finish
        is_legal = incident.domain in ["legal", "cross"]
        is_health = incident.domain in ["health", "emergency", "cross"]
        
        rights_done = not is_legal or ("rights" in outputs)
        triage_done = not is_health or ("triage" in outputs)
        
        steps["analyzed"] = (rights_done and triage_done)
        
        # Coordination brief:
        steps["coordinated"] = ("coordination" in outputs)
        
        # Translation:
        steps["translated"] = ("language" in outputs)

        # If language and coordination are both done, or signal is processed, compile the final response
        if steps["translated"] or signal.status == "processed":
            coord_out = outputs.get("coordination", {}) if isinstance(outputs.get("coordination"), dict) else {}
            lang_data = outputs.get("language", {}) if isinstance(outputs.get("language"), dict) else {}
            pref_lang = lang_data.get("preferred", "hindi") if isinstance(lang_data, dict) else "hindi"
            lang_out = lang_data.get(pref_lang, {}) if isinstance(lang_data.get(pref_lang), dict) else (lang_data.get("hindi", {}) if isinstance(lang_data.get("hindi"), dict) else {})
            rights_out = outputs.get("rights", {}) if isinstance(outputs.get("rights"), dict) else {}
            triage_out = outputs.get("triage", {}) if isinstance(outputs.get("triage"), dict) else {}
            from apps.agents.directory import resolve_authority

            eff_dom = incident.domain or "civic"
            cr = coord_out.get("conflict_resolution") if isinstance(coord_out.get("conflict_resolution"), dict) else {}
            p_prio = str(cr.get("primary_priority", "")).lower()

            if eff_dom in ["cross", "cross_domain"]:
                if "health" in p_prio or "medical" in p_prio:
                    eff_dom = "health"
                elif "legal" in p_prio:
                    eff_dom = "legal"
                elif rights_out.get("authority_to_contact"):
                    eff_dom = "legal"
                elif triage_out.get("authority_to_contact"):
                    eff_dom = "health"
                else:
                    eff_dom = "health" if "triage" in outputs else "legal"

            raw_auth_hint = rights_out.get("authority_to_contact") or triage_out.get("authority_to_contact")
            raw_type_hint = rights_out.get("nearest_authority_type") or triage_out.get("nearest_authority_type")
            auth_resolved = resolve_authority(eff_dom, authority_hint=raw_auth_hint, nearest_type_hint=raw_type_hint)

            res_nearest_type = auth_resolved["nearest_authority_type"]
            res_auth_contact = auth_resolved["authority_to_contact"]

            result = {
                "incident_id": str(incident.id),
                "severity_label": incident.severity_label,
                "severity_score": incident.severity_score,
                "domain": incident.domain,
                "title_en": coord_out.get("situation_title", ""),
                "title_hi": lang_out.get("situation_title", ""),
                "brief_en": incident.situation_brief,
                "brief_hi": lang_out.get("situation_brief", ""),
                "what_is_happening_en": coord_out.get("what_is_happening", ""),
                "what_is_happening_hi": lang_out.get("what_is_happening", ""),
                
                "rights_violated": rights_out.get("rights_violated", []),
                "legal_provisions": rights_out.get("legal_provisions", []),
                "legal_provisions_hi": lang_out.get("legal_provisions", []),
                "legal_timeline": rights_out.get("legal_timeline", []),
                "legal_timeline_hi": lang_out.get("legal_timeline", []),
                "nearest_authority_type": res_nearest_type,
                "nearest_authority_type_hi": lang_out.get("nearest_authority_type") or res_nearest_type,
                "authority_to_contact": res_auth_contact,
                "authority_to_contact_hi": lang_out.get("authority_to_contact") or res_auth_contact,
                "triage_severity": triage_out.get("triage_severity", ""),
                "hospital_denial_detected": triage_out.get("hospital_denial_detected", False),
                
                "golden_window": triage_out.get("golden_window", {}),
                "golden_window_hi": lang_out.get("golden_window", {}),
                "emergency_contacts": triage_out.get("emergency_contacts", []),
                "emergency_contacts_hi": lang_out.get("emergency_contacts", []),
                "primary_concern": triage_out.get("primary_concern", ""),
                "primary_concern_hi": lang_out.get("primary_concern", ""),
                
                "conflict_resolution": coord_out.get("conflict_resolution"),
                "conflict_resolution_hi": lang_out.get("conflict_resolution"),
                "escalation_path": coord_out.get("escalation_path", []),
                "escalation_path_hi": lang_out.get("escalation_path", []),
                "escalation_required": coord_out.get("escalation_required", False),
                
                "immediate_actions": coord_out.get("immediate_actions", []),
                "immediate_actions_hi": lang_out.get("immediate_actions", []),
                "authorities_to_notify": coord_out.get("authorities_to_notify", []),
                "authorities_to_notify_hi": lang_out.get("authorities_to_notify", []),
                "resources_needed": coord_out.get("resources_needed", []),
                "resources_needed_hi": lang_out.get("resources_needed", []),
                "evidence_to_collect": coord_out.get("evidence_to_collect", []),
                "evidence_to_collect_hi": lang_out.get("evidence_to_collect") or coord_out.get("evidence_to_collect", []),
                "coordinator_status": incident.coordinator_status,
                "coordinator_notes": incident.coordinator_notes,
                "preferred_language": signal.preferred_language
            }

    # Stuck pipeline check: if signal.status in ['processing', 'classified']
    from django.utils import timezone
    from datetime import timedelta
    is_stuck = False
    now = timezone.now()
    if signal.status in ['processing', 'classified']:
        if (now - signal.created_at) > timedelta(minutes=10):
            is_stuck = True
            if incident and incident.updated_at and (now - incident.updated_at) < timedelta(minutes=4):
                is_stuck = False
                
    if is_stuck:
        return JsonResponse({
            'status': 'pipeline_error',
            'message': 'Pipeline timed out',
            'steps': steps,
        })

    # Signal status mapper
    pipeline_status = "processing"
    if steps["translated"] or signal.status == "processed":
        pipeline_status = "processed"
        steps["received"] = True
        steps["classified"] = True
        steps["analyzed"] = True
        steps["coordinated"] = True
        steps["translated"] = True
    elif signal.status == "failed":
        pipeline_status = "failed"

    return JsonResponse({
        "signal_id": str(signal.id),
        "status": pipeline_status,
        "steps": steps,
        "result": result,
        "preferred_language": signal.preferred_language,
    })


def health_check(request):
    """
    GET /health/ or /api/health/
    Lightweight health check endpoint for production orchestrators.
    """
    from django.db import connection
    db_ok = True
    try:
        connection.ensure_connection()
    except Exception:
        db_ok = False

    status_code = 200 if db_ok else 503
    return JsonResponse({
        "status": "healthy" if db_ok else "unhealthy",
        "database": "connected" if db_ok else "disconnected",
    }, status=status_code)
