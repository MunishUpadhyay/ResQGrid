"""
All five Prahari domain agents.

Each agent:
    1. Loads its system prompt from prompts/<name>.txt
    2. Will call the Groq API (logic pending review)
    3. Returns a structured dict stored in Incident.agent_outputs

Agents:
    SentinelAgent    — threat detection, severity scoring, domain classification
    RightsAgent      — legal rights identification and advice
    TriageAgent      — medical/emergency triage and urgency scoring
    CoordinationAgent — resource matching and dispatch recommendations
    LanguageAgent    — multilingual situation brief generation
"""

import logging
import re
from typing import List, Optional, Literal
from pydantic import BaseModel, Field, ConfigDict

from .base import BaseAgent
from .directory import sanitize_contact_number, sanitize_text_contacts
from .legal_reference import validate_legal_citation
from rag.retriever import retrieve_legal_provisions, retrieve_medical_protocols

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic Schemas for Structured Outputs
# ---------------------------------------------------------------------------

class SentinelSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    domain: Literal["legal", "health", "emergency", "civic", "cross_domain"]
    severity_score: float = Field(description="Severity score between 0.0 and 1.0 based on actual risk described in the report")
    severity_label: Literal["critical", "high", "medium", "low"] = Field(description="Severity label: critical (>=0.9), high (>=0.6), medium (>=0.3), low (<0.3)")
    confidence: float = Field(description="Domain classification confidence score between 0.0 and 1.0")
    keywords: List[str] = Field(description="List of 3-5 keywords indicating core issue")
    reasoning: str = Field(description="Single sentence explaining domain classification and severity score")
    requires_immediate_action: bool = Field(description="True ONLY if there is an active life-threatening crisis or ongoing emergency requiring instant intervention")

class GoldenWindowSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    time_remaining: str = Field(description="Time critical limit, e.g. '90 minutes', 'immediate'")
    consequence_of_delay: str = Field(description="Physiological consequence of delay")

class EmergencyContactSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(description="Name of the emergency service/person")
    number: str = Field(description="Emergency helpline/contact number")
    when_to_call: str = Field(description="Criteria/condition for calling this number")

class TriageSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    triage_severity: Literal["immediate", "delayed", "minor", "deceased"]
    primary_concern: str = Field(description="Structured medical handoff note")
    interventions: List[str] = Field(description="List of interventions in [Action]: [Why needed] — [Consequence if skipped] — [How to perform] format")
    required_facility: Literal["trauma_center", "general_hospital", "clinic", "mental_health", "obstetric"]
    response_time: Literal["immediate", "urgent", "semi_urgent", "non_urgent"]
    hospital_denial_detected: bool
    confidence: float
    escalate_to_rights_agent: bool
    golden_window: GoldenWindowSchema
    emergency_contacts: List[EmergencyContactSchema]

class ImmediateActionSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    priority: int
    action: str
    responsible_party: str
    time_window: str

class ConflictResolutionSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    primary_priority: str
    reasoning: str
    sequence: str

class EscalationStepSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    level: int
    authority: str
    trigger: str
    contact: str

class EvidenceItemSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item: str
    why_important: str
    how_to_collect: str
    time_sensitive: bool

class CoordinationSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    situation_title: str
    overall_severity: str
    overall_severity_score: float
    what_is_happening: str
    immediate_actions: List[ImmediateActionSchema]
    resources_needed: List[str]
    authorities_to_notify: List[str]
    situation_brief: str
    escalation_required: bool
    estimated_resolution_time: str
    conflict_resolution: Optional[ConflictResolutionSchema]
    escalation_path: List[EscalationStepSchema]
    evidence_to_collect: List[EvidenceItemSchema]


class LegalTimelineStepSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step: int
    action: str = Field(description="Specific legal action")
    timeframe: str = Field(description="Timeframe for the action")
    why_urgent: str = Field(description="Reason for urgency")

class LegalProvisionItemSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provision: str = Field(description="Dual citation name or section name, e.g. 'BNS Section 101'")
    code: str = Field(description="The code name: 'BNS', 'BNSS', 'BSA', 'Constitution', 'Specific Relief Act', etc.")
    section: str = Field(description="Section number or identifier only, e.g. '101', '115(2)', 'Article 21'")
    description: str = Field(description="Brief summary of the provision")
    relevance: str = Field(description="Detailed explanation. What this provision says, the facts triggering it, what the violating party is doing wrong, and available remedies (4-5 sentences)")
    applicability: Literal["primary", "secondary", "uncertain"] = Field(description="Applicability level of this provision to the incident")

class RightsSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rights_violated: List[str] = Field(description="Names of articles or sections violated (dual citation format where applicable)")
    severity: Literal["critical", "high", "medium", "low"]
    legal_provisions: List[LegalProvisionItemSchema]
    immediate_actions: List[str] = Field(description="Concrete steps the citizen should take immediately")
    authority_to_contact: str = Field(description="Target authority name to contact")
    nearest_authority_type: Literal["DLSA", "High Court", "Consumer Forum", "Labour Court", "Police Complaint Authority", "Magistrate Court"]
    legal_timeline: List[LegalTimelineStepSchema] = Field(description="Timeline of legal actions (max 4 steps)")
    case_strength: float = Field(description="Case strength score between 0.0 and 1.0")


# ---------------------------------------------------------------------------
# Sentinel Agent
# ---------------------------------------------------------------------------

class SentinelAgent(BaseAgent):
    """
    Threat detection and initial classification agent.

    Responsibilities:
        - Analyse the raw signal for threat indicators.
        - Assign a severity score in [0.0, 1.0].
        - Classify the domain (legal / health / emergency / cross).
        - Flag whether the signal requires immediate escalation.

    Output schema:
        {
            "severity_score": float,       # 0.0–1.0
            "severity_label": str,         # "low" | "medium" | "high" | "critical"
            "domain": str,                 # "legal" | "health" | "emergency" | "cross"
            "escalate": bool,
            "reasoning": str
        }
    """

    prompt_name = "sentinel"

    def run(self, signal) -> dict:
        logger.info("[SentinelAgent] Running domain classification on signal %s", getattr(signal, 'id', 'mock_id'))
        
        user_message = f"Classify this signal:\n\nText: {signal.raw_text}\nSource: {signal.source_type}"
        raw_response = self.call_groq(user_message, response_schema=SentinelSchema)
        result = self.parse_json_response(raw_response)
        
        valid_domains = {"legal", "health", "emergency", "civic", "cross_domain"}
        domain = result.get("domain")
        
        # Normalize cross_domain to civic if all content concerns municipal hazards without health/legal/emergency factors
        if domain in ["cross_domain", "cross"]:
            text_lower = signal.raw_text.lower()
            reasoning_lower = str(result.get("reasoning", "")).lower()
            combined_text = f"{text_lower} {reasoning_lower}"
            
            has_health_kw = any(w in combined_text for w in ["hospital", "doctor", "patient", "bleeding", "injury", "medical", "ambulance"])
            has_legal_kw = any(w in combined_text for w in ["arrest", "jail", "lawyer", "legal notice", "wages", "salary", "fir", "police custody", "court"])
            has_emergency_kw = any(w in combined_text for w in ["fire", "flood", "disaster", "earthquake", "landslide", "explosion", "building collapse"])
            has_civic_kw = any(w in combined_text for w in ["pothole", "streetlight", "road", "garbage", "drain", "waterlogging", "municipal", "traffic", "sewage", "civic"])
            
            if has_civic_kw and not (has_health_kw or has_legal_kw or has_emergency_kw):
                result["domain"] = "civic"
                domain = "civic"

        if domain not in valid_domains:
            if not domain:
                domain = "cross_domain"
            elif "|" in domain or "and" in domain or "cross" in domain:
                domain = "cross_domain"
            elif "medical" in domain or "hospital" in domain:
                domain = "health"
            else:
                domain = "cross_domain"
            result["domain"] = domain

        # Ensure severity_score is present, float, and clamped in [0.0, 1.0]
        if "severity_score" not in result or result["severity_score"] is None:
            if result.get("requires_immediate_action"):
                result["severity_score"] = 0.75
            else:
                result["severity_score"] = 0.35
        else:
            try:
                result["severity_score"] = max(0.0, min(1.0, float(result["severity_score"])))
            except (ValueError, TypeError):
                result["severity_score"] = 0.75 if result.get("requires_immediate_action") else 0.35

        # Align severity_label with severity_score thresholds if missing or invalid
        valid_labels = {"critical", "high", "medium", "low"}
        sev_label = str(result.get("severity_label", "")).lower()
        score = result["severity_score"]

        if sev_label not in valid_labels:
            if score >= 0.9:
                result["severity_label"] = "critical"
            elif score >= 0.6:
                result["severity_label"] = "high"
            elif score >= 0.3:
                result["severity_label"] = "medium"
            else:
                result["severity_label"] = "low"
        else:
            result["severity_label"] = sev_label

        return result


# ---------------------------------------------------------------------------
# Rights Agent
# ---------------------------------------------------------------------------

class RightsAgent(BaseAgent):
    """
    Legal rights identification and advisory agent.

    Responsibilities:
        - Identify which legal rights are relevant to the signal.
        - Cite applicable laws, articles, or provisions.
        - Suggest immediate legal actions the affected party can take.

    Output schema:
        {
            "rights_violated": [str],
            "severity": str,             # "critical" | "high" | "medium" | "low"
            "legal_provisions": [
                {
                    "provision": str,
                    "description": str,
                    "relevance": str,
                    "applicability": str
                }
            ],
            "immediate_actions": [str],
            "authority_to_contact": str,
            "case_strength": float       # 0.0 - 1.0
        }
    """

    prompt_name = "rights"
    max_tokens = 2000

    def extract_search_query(self, raw_text: str) -> str:
        """
        Extract key search terms and legal keywords from raw citizen text.
        Uses Groq with fallbacks.
        """
        from groq import Groq
        from django.conf import settings
        
        api_keys = [k for k in [
            getattr(settings, "GROQ_API_KEY", ""),
            getattr(settings, "GROQ_API_KEY_2", ""),
        ] if k]
        if not api_keys:
            return raw_text
            
        models = [self.model, "openai/gpt-oss-20b"]
        system_prompt = (
            "You are a legal assistant. Your task is to analyze the user's citizen report and extract the core legal facts and keywords. "
            "Provide a space-separated list of 3-7 search terms and legal keywords (e.g. 'theft robbery knife threat' or 'eviction lockout landlord lease' or 'police arrest custody detention'). "
            "Do NOT include any conversational text, explanations, or quotes. Output ONLY the keywords."
        )
        
        for model in models:
            for api_key in api_keys:
                try:
                    client = Groq(api_key=api_key, max_retries=0)
                    response = client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": f"Citizen report:\n\n{raw_text}"},
                        ],
                        temperature=0.1,
                        max_tokens=64
                    )
                    keywords = response.choices[0].message.content.strip()
                    if keywords:
                        logger.info("[RightsAgent] Extracted search keywords: '%s'", keywords)
                        return keywords
                except Exception as e:
                    logger.warning("[RightsAgent] Keyword extraction fallback triggered for model %s: %s", model, e)
                    continue
        return raw_text

    def run(self, signal, sentinel_result=None) -> dict:
        logger.info("[RightsAgent] Running on signal %s", getattr(signal, 'id', 'mock_id'))

        # 1. Extract clean legal keywords from the raw citizen report
        search_query = self.extract_search_query(signal.raw_text)

        # 2. Retrieve relevant legal provisions from local ChromaDB vector store
        from rag.retriever import retrieve_legal_provisions
        provisions = retrieve_legal_provisions(search_query, n_results=5)
        
        # 3. Format provisions context
        if not provisions:
            provisions_text = "No sufficiently relevant knowledge-base material was retrieved."
        else:
            provisions_text = ""
            for i, prov in enumerate(provisions, 1):
                provisions_text += f"\nProvision {i}:\n"
                provisions_text += f"Text: {prov['text']}\n"
                provisions_text += f"Metadata: {prov['metadata']}\n"

        # 4. Construct user message
        sentinel_domain = sentinel_result.get("domain") if sentinel_result else "legal"
        user_message = (
            f"Signal text: {signal.raw_text}\n"
            f"Sentinel domain classification: {sentinel_domain}\n\n"
            f"Retrieved relevant Indian legal provisions context:\n{provisions_text}"
        )

        # 5. Call Groq LLM with strict RightsSchema structured output
        raw_response = self.call_groq(user_message, response_schema=RightsSchema)
        result = self.parse_json_response(raw_response)

        # 6. Validate output schema structure and sanitize
        if not isinstance(result.get("rights_violated"), list):
            result["rights_violated"] = []
        if not isinstance(result.get("legal_provisions"), list):
            result["legal_provisions"] = []
        if not isinstance(result.get("immediate_actions"), list):
            result["immediate_actions"] = []
        if "severity" not in result:
            result["severity"] = "medium"
            
        # Sanitize authority_to_contact
        raw_auth = result.get("authority_to_contact") or "Local Legal Services Authority"
        result["authority_to_contact"] = sanitize_text_contacts(raw_auth)
        
        try:
            result["case_strength"] = float(result.get("case_strength", 0.5))
        except (ValueError, TypeError):
            result["case_strength"] = 0.5

        # Validate nearest_authority_type
        valid_authorities = {"DLSA", "High Court", "Consumer Forum", "Labour Court", "Police Complaint Authority", "Magistrate Court"}
        nat = result.get("nearest_authority_type")
        if nat not in valid_authorities:
            result["nearest_authority_type"] = "DLSA"

        # Validate and sanitize legal_timeline
        timeline = result.get("legal_timeline")
        if not isinstance(timeline, list):
            actions = result.get("immediate_actions") or ["Consult DLSA panel advocate for legal guidance."]
            timeline = []
            for i, act in enumerate(actions[:4], 1):
                timeline.append({
                    "step": i,
                    "action": act,
                    "timeframe": "As soon as reasonably practicable",
                    "why_urgent": "Prompt action helps establish a clear written record."
                })
        else:
            validated_timeline = []
            for item in timeline:
                if isinstance(item, dict):
                    try:
                        step_num = int(item.get("step", len(validated_timeline) + 1))
                    except (ValueError, TypeError):
                        step_num = len(validated_timeline) + 1

                    action_str = str(item.get("action", "Consult DLSA panel advocate.")).strip()
                    timeframe_str = str(item.get("timeframe", "As soon as reasonably practicable")).strip()
                    why_urgent_str = str(item.get("why_urgent", "Prompt action avoids unnecessary procedural delays.")).strip()

                    # Check if the step cites a statutorily verified timeframe (e.g., 24-hour magistrate production or statutory lease notice)
                    combined_str = f"{action_str} {timeframe_str}".lower()
                    is_statutory = any(kw in combined_str for kw in ["bnss", "crpc", "section 58", "section 106", "section 80 cpc", "constitution", "24 hours", "15 days notice", "6 months notice"])

                    if not is_statutory:
                        # Clean arbitrary numerical deadlines like "within 3 days", "within 7 days", "within 30 days"
                        timeframe_str = re.sub(r'within \d+ days?( of [^,\.]*)?', 'as soon as reasonably practicable', timeframe_str, flags=re.IGNORECASE)
                        timeframe_str = re.sub(r'within \d+ hours?( of [^,\.]*)?', 'without unnecessary delay', timeframe_str, flags=re.IGNORECASE)
                        timeframe_str = re.sub(r'after \d+ days?', 'without unnecessary delay', timeframe_str, flags=re.IGNORECASE)

                        # Clean ungrounded limitation claims
                        why_urgent_str = re.sub(r'preserves your claim within the limitation period', 'establishes a documented demand record', why_urgent_str, flags=re.IGNORECASE)
                        why_urgent_str = re.sub(r'prevents loss of rights due to limitation periods', 'provides an official channel for dispute resolution if informal steps fail', why_urgent_str, flags=re.IGNORECASE)
                        why_urgent_str = re.sub(r'limitation period', 'procedural timing', why_urgent_str, flags=re.IGNORECASE)

                    validated_timeline.append({
                        "step": step_num,
                        "action": action_str,
                        "timeframe": timeframe_str,
                        "why_urgent": why_urgent_str
                    })
            timeline = validated_timeline[:4]
        result["legal_timeline"] = timeline

        # 7. Apply deterministic citation validation and statutory grounding
        validated_provisions = []
        for item in result.get("legal_provisions", []):
            if isinstance(item, dict):
                code = item.get("code")
                sec = item.get("section")
                
                # Perform lookup in controlled database
                cit = validate_legal_citation(code, sec)
                
                item_copy = dict(item)
                item_copy["verified"] = cit["verified"]
                item_copy["legacy_code"] = cit["legacy_code"]
                item_copy["legacy_section"] = cit["legacy_section"]
                item_copy["provision_type"] = cit["type"]
                # Programmatically overwrite description with official statutory text if verified
                if cit["verified"]:
                    item_copy["description"] = cit["statutory_text"]
                validated_provisions.append(item_copy)
        result["legal_provisions"] = validated_provisions

        return result


# ---------------------------------------------------------------------------
# Triage Agent
# ---------------------------------------------------------------------------

class TriageAgent(BaseAgent):
    """
    Medical and emergency triage agent.

    Responsibilities:
        - Assess the medical or physical urgency of the signal.
        - Recommend appropriate emergency response level.
        - Identify symptoms or emergency indicators.

    Output schema:
        {
            "triage_severity": str,       # "immediate" | "delayed" | "minor" | "deceased"
            "primary_concern": str,
            "interventions": [str],
            "required_facility": str,     # "trauma_center" | "general_hospital" | "clinic" | "mental_health" | "obstetric"
            "response_time": str,         # "immediate" | "urgent" | "semi_urgent" | "non_urgent"
            "hospital_denial_detected": bool,
            "confidence": float,
            "escalate_to_rights_agent": bool
        }
    """

    prompt_name = "triage"
    max_tokens = 2000

    def extract_symptoms_and_keywords(self, raw_text: str) -> str:
        """
        Extract clean medical symptoms, injuries, or emergency keywords from raw text.
        Uses Groq with fallbacks.
        """
        from groq import Groq
        from django.conf import settings
        
        api_keys = [k for k in [
            getattr(settings, "GROQ_API_KEY", ""),
            getattr(settings, "GROQ_API_KEY_2", ""),
        ] if k]
        if not api_keys:
            return raw_text
            
        models = [self.model, "openai/gpt-oss-20b"]
        system_prompt = (
            "You are a medical triage assistant. Your task is to analyze the user's citizen report and extract the core physical symptoms, injuries, or medical crisis keywords. "
            "Provide a space-separated list of 3-7 symptoms or medical/emergency terms (e.g. 'bleeding cut thigh wound' or 'chest pain pressure radiation sweat' or 'face droop arm weakness slurred speech'). "
            "Do NOT include any conversational text, explanations, or quotes. Output ONLY the keywords."
        )
        
        for model in models:
            for api_key in api_keys:
                try:
                    client = Groq(api_key=api_key, max_retries=0)
                    response = client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": f"Citizen report:\n\n{raw_text}"},
                        ],
                        temperature=0.1,
                        max_tokens=64
                    )
                    keywords = response.choices[0].message.content.strip()
                    if keywords:
                        logger.info("[TriageAgent] Extracted symptoms and keywords: '%s'", keywords)
                        return keywords
                except Exception as e:
                    logger.warning("[TriageAgent] Symptoms extraction fallback triggered for model %s: %s", model, e)
                    continue
        return raw_text

    def run(self, signal, sentinel_result=None) -> dict:
        logger.info("[TriageAgent] Running on signal %s", getattr(signal, 'id', 'mock_id'))

        # 1. Extract clean medical keywords/symptoms
        search_query = self.extract_symptoms_and_keywords(signal.raw_text)

        # 2. Retrieve relevant medical protocols from local ChromaDB vector store
        from rag.retriever import retrieve_medical_protocols
        protocols = retrieve_medical_protocols(search_query, n_results=3)
        
        # 3. Format protocols context
        if not protocols:
            protocols_text = "No sufficiently relevant knowledge-base material was retrieved."
        else:
            protocols_text = ""
            for i, prot in enumerate(protocols, 1):
                protocols_text += f"\nProtocol {i}:\n"
                protocols_text += f"Text: {prot['text']}\n"
                protocols_text += f"Metadata: {prot['metadata']}\n"

        # 4. Construct user message
        sentinel_domain = sentinel_result.get("domain") if sentinel_result else "health"
        user_message = (
            f"Signal text: {signal.raw_text}\n"
            f"Sentinel domain classification: {sentinel_domain}\n\n"
            f"Retrieved relevant medical protocols context:\n{protocols_text}"
        )

        # 5. Call Groq LLM
        raw_response = self.call_groq(user_message, response_schema=TriageSchema)
        result = self.parse_json_response(raw_response)

        # 6. Validate output schema structure and fallback
        if "triage_severity" not in result:
            result["triage_severity"] = "minor"
        if "primary_concern" not in result:
            result["primary_concern"] = "Unknown medical concern."
        if not isinstance(result.get("interventions"), list):
            result["interventions"] = []
        if "required_facility" not in result:
            result["required_facility"] = "general_hospital"
        if "response_time" not in result:
            result["response_time"] = "non_urgent"
        if "hospital_denial_detected" not in result:
            result["hospital_denial_detected"] = False
        try:
            result["confidence"] = float(result.get("confidence", 0.5))
        except (ValueError, TypeError):
            result["confidence"] = 0.5
        if "escalate_to_rights_agent" not in result:
            result["escalate_to_rights_agent"] = bool(result.get("hospital_denial_detected", False))
        else:
            result["escalate_to_rights_agent"] = bool(result["escalate_to_rights_agent"])

        # Validate golden_window
        gw = result.get("golden_window")
        if not isinstance(gw, dict):
            result["golden_window"] = {
                "time_remaining": "Unknown",
                "consequence_of_delay": "Delayed treatment may cause the patient's condition to deteriorate."
            }
        else:
            result["golden_window"] = {
                "time_remaining": str(gw.get("time_remaining", "Unknown")),
                "consequence_of_delay": str(gw.get("consequence_of_delay", "Delayed treatment may cause the patient's condition to deteriorate."))
            }

        # Validate emergency_contacts
        contacts = result.get("emergency_contacts")
        if not isinstance(contacts, list):
            result["emergency_contacts"] = [
                {"name": "National Ambulance", "number": "108", "when_to_call": "Immediately for life threatening medical emergencies"},
                {"name": "Police", "number": "100", "when_to_call": "If access is being blocked or safety is threatened"}
            ]
        else:
            validated_contacts = []
            for item in contacts:
                if isinstance(item, dict):
                    raw_num = str(item.get("number", "108"))
                    sanitized_num = sanitize_contact_number(raw_num)
                    validated_contacts.append({
                        "name": str(item.get("name", "Emergency Service")),
                        "number": sanitized_num,
                        "when_to_call": str(item.get("when_to_call", "Immediately"))
                    })
            result["emergency_contacts"] = validated_contacts

        # 7. Explicit Self-Harm Verification Check to prevent false positives
        raw_text_lower = signal.raw_text.lower()
        self_harm_terms = ["suicide", "end my life", "kill myself", "harm myself", "cut myself", "hang myself", "jump off", "want to die", "poison myself"]
        has_explicit_self_harm = any(term in raw_text_lower for term in self_harm_terms)
        
        if result.get("required_facility") == "mental_health" and not has_explicit_self_harm:
            logger.warning("[TriageAgent] False mental_health facility classification downgraded due to lack of explicit self-harm indicators.")
            result["required_facility"] = "clinic" if result.get("triage_severity") == "minor" else "general_hospital"

        # 8. Deterministic Medical Grounding and Safety Override
        grounded_interventions = []
        raw_text_lower = signal.raw_text.lower()
        protocols_title_lower = " ".join([p.get('metadata', {}).get('title', '').lower() for p in protocols]) if protocols else ""

        for intervention in result.get("interventions", []):
            inter_lower = intervention.lower()
            
            # Burns Protocol (only if actual burn/scald/fire is in raw text or protocol title)
            if ("burn" in raw_text_lower or "scald" in raw_text_lower or "fire" in raw_text_lower or "burn" in protocols_title_lower):
                if "burn" in inter_lower or "scald" in inter_lower or "toothpaste" in inter_lower or "butter" in inter_lower:
                    action = "Apply cool running water for 20 minutes"
                    why = "cooling stops tissue damage and relieves pain"
                    skipped = "trapped heat worsens tissue destruction and increases infection risk"
                    how = "Apply cool, gently running water over the burn area for a minimum of 20 minutes immediately. Do NOT apply ice, butter, toothpaste, oil, or home remedies."
                    intervention = f"{action}: {why} — {skipped} — {how}"
                
            # Snake Bite Protocol (only if snake/venom/bite is in raw text or protocol title)
            elif ("snake" in raw_text_lower or "venom" in raw_text_lower or "snake" in protocols_title_lower):
                if "snake" in inter_lower or "bite" in inter_lower or "venom" in inter_lower:
                    action = "Immobilize limb and seek anti-venom"
                    why = "slowing venom spread is vital before hospital arrival"
                    skipped = "increased circulation spreads venom rapidly throughout the body"
                    how = "Keep the affected limb completely immobilized and at or below heart level. Do NOT cut the wound, do NOT attempt to suck venom out, and do NOT apply a tight arterial tourniquet. Reach hospital with ASV within 2 hours."
                    intervention = f"{action}: {why} — {skipped} — {how}"
                
            # Spinal Injury Protocol (only if spinal trauma/neck injury/fall is in raw text or protocol title)
            elif ("spinal" in raw_text_lower or "spine" in raw_text_lower or "neck" in raw_text_lower or "spinal" in protocols_title_lower):
                if "spinal" in inter_lower or "spine" in inter_lower or "neck" in inter_lower or "immobil" in inter_lower:
                    action = "Strict spinal immobilization"
                    why = "prevents secondary permanent neurological damage"
                    skipped = "improper movement can cause permanent spinal cord transection and irreversible paralysis"
                    how = "Do NOT move the patient without proper spinal precautions. Apply a hard cervical collar immediately. Use the 'log roll' technique only, requiring at least three trained responders."
                    intervention = f"{action}: {why} — {skipped} — {how}"
                
            # STEMI Heart Attack (only if heart attack/stemi/chest pain is in raw text or protocol title)
            elif ("stemi" in raw_text_lower or "heart attack" in raw_text_lower or "chest pain" in raw_text_lower or "stemi" in protocols_title_lower):
                if "stemi" in inter_lower or "heart attack" in inter_lower or "chest pain" in inter_lower or "aspirin" in inter_lower:
                    action = "Administer Aspirin 325mg to chew"
                    why = "Aspirin prevents further platelet aggregation and arterial clotting"
                    skipped = "blocked coronary artery continues to starve cardiac muscle leading to permanent necrosis"
                    how = "Administer Aspirin 325mg orally (to be chewed immediately, not swallowed whole)."
                    intervention = f"{action}: {why} — {skipped} — {how}"

            # Hospital Denial Treatment
            elif result.get("hospital_denial_detected") and ("deny" in inter_lower or "refuse" in inter_lower or "admission" in inter_lower):
                action = "Escalate denial and seek immediate treatment"
                why = "emergency aid is an absolute obligation under Supreme Court Parmanand Katara ruling"
                skipped = "withholding life-saving care causes irreversible damage or death"
                how = "Document the denial, escalate to Chief Medical Officer (CMO), request immediate transfer details, and contact DLSA for intervention."
                intervention = f"{action}: {why} — {skipped} — {how}"
                
            grounded_interventions.append(intervention)
            
        result["interventions"] = grounded_interventions

        return result


# ---------------------------------------------------------------------------
# Coordination Agent
# ---------------------------------------------------------------------------

def reorder_actions_by_safety(actions: list, raw_text: str) -> list:
    """
    Obvious emergency actions must not be displaced by administrative/legal follow-up.
    """
    raw_lower = raw_text.lower()
    is_emergency = any(kw in raw_lower for kw in [
        "shoot", "fire", "bleed", "unconscious", "heart", "gun",
        "stab", "hospital", "ambulance", "medical", "accident", "trauma", "attack"
    ])
    if not is_emergency:
        return actions
        
    emergency_actions = []
    other_actions = []
    
    for act in actions:
        action_text = act.get("action", "").lower()
        is_life_saving = any(kw in action_text for kw in [
            "ambulance", "hospital", "cpr", "first aid", "stabilize",
            "medical", "admit", "doctor", "airway", "hemorrhage", "tourniquet",
            "evacuate", "treat", "wound"
        ])
        if is_life_saving:
            emergency_actions.append(act)
        else:
            other_actions.append(act)
            
    if not emergency_actions:
        return actions
        
    sorted_actions = []
    for idx, act in enumerate(emergency_actions, 1):
        act["priority"] = idx
        sorted_actions.append(act)
    for idx, act in enumerate(other_actions, len(emergency_actions) + 1):
        act["priority"] = idx
        sorted_actions.append(act)
        
    return sorted_actions


class CoordinationAgent(BaseAgent):
    """
    Resource matching and dispatch coordination agent.

    Responsibilities:
        - Receives outputs from Sentinel, Triage, and Rights agents.
        - Synthesizes all inputs into a single situation brief.
        - Produces a prioritized list of immediate actions.
        - Recommends resources and authorities to notify.

    Output schema:
        {
            "situation_title": str,
            "overall_severity": str,
            "overall_severity_score": float,
            "what_is_happening": str,
            "immediate_actions": [dict],
            "resources_needed": [str],
            "authorities_to_notify": [str],
            "situation_brief": str,
            "escalation_required": bool,
            "estimated_resolution_time": str
        }
    """

    prompt_name = "coordination"
    max_tokens = 2000

    def run(self, signal, sentinel_result: dict, agent_outputs: dict) -> dict:
        logger.info("[CoordinationAgent] Running on signal %s", getattr(signal, 'id', 'mock_id'))

        # Build context from all available agent outputs
        context_parts = [
            f"Original signal: {signal.raw_text}",
            f"Domain: {sentinel_result.get('domain')}",
            f"Requires immediate action: {sentinel_result.get('requires_immediate_action')}",
        ]

        if 'rights' in agent_outputs:
            r = agent_outputs['rights']
            context_parts.append(
                f"Legal assessment:\n"
                f"  Rights violated: {r.get('rights_violated', [])}\n"
                f"  Severity: {r.get('severity')}\n"
                f"  Immediate actions: {r.get('immediate_actions', [])}\n"
                f"  Authority: {r.get('authority_to_contact')}"
            )

        if 'triage' in agent_outputs:
            t = agent_outputs['triage']
            context_parts.append(
                f"Medical assessment:\n"
                f"  Triage severity: {t.get('triage_severity')}\n"
                f"  Primary concern: {t.get('primary_concern')}\n"
                f"  Interventions needed: {t.get('interventions', [])}\n"
                f"  Required facility: {t.get('required_facility')}\n"
                f"  Response time: {t.get('response_time')}\n"
                f"  Hospital denial: {t.get('hospital_denial_detected')}"
            )

        user_message = "\n\n".join(context_parts)
        user_message += "\n\nSynthesize all of the above into a unified coordination brief."

        raw = self.call_groq(user_message, response_schema=CoordinationSchema)
        result = self.parse_json_response(raw)

        # Fallbacks/Validations for coordination output schema
        if "situation_title" not in result:
            result["situation_title"] = "Unified Situation Brief"
        if "overall_severity" not in result:
            result["overall_severity"] = "medium"
        try:
            result["overall_severity_score"] = float(result.get("overall_severity_score", 0.5))
        except (ValueError, TypeError):
            result["overall_severity_score"] = 0.5
        if "what_is_happening" not in result:
            result["what_is_happening"] = signal.raw_text
        
        # Determine domain context for post-processing
        eff_domain = (sentinel_result.get("domain") if sentinel_result else getattr(signal, "domain", "")) or ""
        is_civic_domain = eff_domain == "civic" or (eff_domain not in ["emergency", "health", "cross", "legal"] and not (sentinel_result and sentinel_result.get("requires_immediate_action")))

        if not isinstance(result.get("immediate_actions"), list):
            result["immediate_actions"] = []
        else:
            validated_actions = []
            for action in result["immediate_actions"]:
                if isinstance(action, dict):
                    try:
                        prio = int(action.get("priority", 1))
                    except (ValueError, TypeError):
                        prio = 1
                    time_win = str(action.get("time_window", "")).strip()
                    if is_civic_domain:
                        # Clean manufactured SLA time windows in civic/infrastructure cases
                        time_win = re.sub(r'within \d+ minutes?', 'as soon as reasonably practicable', time_win, flags=re.IGNORECASE)
                        time_win = re.sub(r'within \d+ hours?', 'promptly', time_win, flags=re.IGNORECASE)
                        time_win = re.sub(r'within \d+ days?', 'after submitting the complaint', time_win, flags=re.IGNORECASE)
                        time_win = re.sub(r'\b\d+ mins?\b', 'promptly', time_win, flags=re.IGNORECASE)
                        if not time_win or any(w in time_win.lower() for w in ["minute", "min", "10", "15", "30", "45", "60"]):
                            time_win = "as soon as reasonably practicable"
                    validated_actions.append({
                        "priority": prio,
                        "action": str(action.get("action", "")),
                        "responsible_party": str(action.get("responsible_party", "")),
                        "time_window": time_win
                    })
            validated_actions = reorder_actions_by_safety(validated_actions, signal.raw_text)
            validated_actions.sort(key=lambda x: x["priority"])
            result["immediate_actions"] = validated_actions[:5]

        if not isinstance(result.get("resources_needed"), list):
            result["resources_needed"] = []
            
        # Clean authorities to notify: remove non-relevant 112 / legal-aid helplines for routine civic cases
        auths = result.get("authorities_to_notify")
        if not isinstance(auths, list):
            auths = []
        is_health_only = eff_domain in ["health", "emergency"] and eff_domain not in ["cross_domain", "cross"]

        cleaned_auths = []
        for a in auths:
            a_str = str(a).strip()
            a_lower = a_str.lower()
            if is_civic_domain:
                if any(bad in a_lower for bad in ["112", "emergency help", "108", "ambulance", "15100", "nalsa", "dlsa", "legal services"]):
                    continue
            elif is_health_only:
                if any(bad in a_lower for bad in ["15100", "nalsa", "dlsa", "legal services", "legal aid", "labour court"]):
                    continue
            cleaned_auths.append(a_str)

        if is_civic_domain and not cleaned_auths:
            cleaned_auths = ["Municipal Corporation / Public Works Department (PWD)", "Local Traffic Police"]
        elif is_health_only and not cleaned_auths:
            cleaned_auths = ["Chief Medical Officer (CMO)", "Primary Health Center (PHC)"]

        result["authorities_to_notify"] = cleaned_auths

        if not result.get("situation_brief"):
            result["situation_brief"] = result.get("what_is_happening", "")[:100] or getattr(signal, "raw_text", "")[:100]
        if "escalation_required" not in result:
            result["escalation_required"] = False
        else:
            result["escalation_required"] = bool(result["escalation_required"])
        if "estimated_resolution_time" not in result:
            result["estimated_resolution_time"] = "hours"

        # Validate conflict_resolution for cross_domain/cross signals
        is_cross = sentinel_result.get("domain") in ["cross", "cross_domain"] if sentinel_result else False
        cr = result.get("conflict_resolution")
        if is_cross:
            if not isinstance(cr, dict):
                result["conflict_resolution"] = {
                    "primary_priority": "medical",
                    "reasoning": "A life-threatening medical emergency takes absolute precedence over legal proceedings.",
                    "sequence": "First, stabilize patient and secure admission. Second, initiate legal/police complaint against the hospital's denial."
                }
            else:
                result["conflict_resolution"] = {
                    "primary_priority": str(cr.get("primary_priority", "medical")),
                    "reasoning": str(cr.get("reasoning", "Medical stabilization is prioritized over legal remedy.")),
                    "sequence": str(cr.get("sequence", "Handle medical needs first, then proceed with legal remedies."))
                }
        else:
            result["conflict_resolution"] = None

        # Validate escalation_path
        ep = result.get("escalation_path")
        if not isinstance(ep, list) or len(ep) == 0:
            if is_civic_domain:
                result["escalation_path"] = [
                    {
                        "level": 1,
                        "authority": "Municipal Commissioner / Ward Officer",
                        "trigger": "If initial report remains unaddressed",
                        "contact": "Verified contact unavailable"
                    }
                ]
            else:
                result["escalation_path"] = [
                    {
                        "level": 1,
                        "authority": "District Magistrate / Competent Authority",
                        "trigger": "If initial report remains unaddressed",
                        "contact": "Verified contact unavailable"
                    }
                ]
        else:
            validated_ep = []
            for item in ep:
                if isinstance(item, dict):
                    try:
                        level_num = int(item.get("level", len(validated_ep) + 1))
                    except (ValueError, TypeError):
                        level_num = len(validated_ep) + 1
                    raw_contact = str(item.get("contact", "Verified contact unavailable")).strip()
                    
                    trigger_str = str(item.get("trigger", "If report remains unaddressed")).strip()
                    if is_civic_domain:
                        # Clean SLA triggers like "after 24 hours" / "after 48 hours" in civic cases
                        trigger_str = re.sub(r'after \d+ (hours?|days?)', 'if initial complaint remains unaddressed', trigger_str, flags=re.IGNORECASE)
                        trigger_str = re.sub(r'within \d+ (minutes?|hours?)', 'if initial complaint remains unaddressed', trigger_str, flags=re.IGNORECASE)
                        if any(bad in raw_contact.lower() for bad in ["112", "15100", "dlsa", "nalsa"]):
                            raw_contact = "Verified contact unavailable"
                            
                    sanitized_contact = sanitize_contact_number(raw_contact)
                    validated_ep.append({
                        "level": level_num,
                        "authority": str(item.get("authority", "District Authority")),
                        "trigger": trigger_str,
                        "contact": sanitized_contact
                    })
            result["escalation_path"] = validated_ep[:3]

        # Validate evidence_to_collect
        etc = result.get("evidence_to_collect")
        if not isinstance(etc, list):
            result["evidence_to_collect"] = []
        else:
            validated_etc = []
            for item in etc:
                if isinstance(item, dict) and "item" in item:
                    validated_etc.append({
                        "item": str(item.get("item", "")),
                        "why_important": str(item.get("why_important", "")),
                        "how_to_collect": str(item.get("how_to_collect", "")),
                        "time_sensitive": bool(item.get("time_sensitive", False))
                    })
            result["evidence_to_collect"] = validated_etc[:5]

        # Harmonize severity score with Sentinel assessment
        if sentinel_result:
            sent_score = sentinel_result.get("severity_score")
            sent_req_imm = sentinel_result.get("requires_immediate_action", False)
            if sent_score is not None:
                try:
                    s_score_flt = float(sent_score)
                    if is_civic_domain and not sent_req_imm:
                        # For non-emergency civic cases, keep score harmonized with Sentinel rather than inflating to high/critical
                        result["overall_severity_score"] = s_score_flt
                    elif result["overall_severity_score"] < s_score_flt:
                        # Protect Sentinel severity score from LLM downgrades in non-civic cases
                        result["overall_severity_score"] = s_score_flt
                except (ValueError, TypeError):
                    pass

        # Keep overall_severity mapping strictly aligned with overall_severity_score
        if result["overall_severity_score"] >= 0.8:
            result["overall_severity"] = "critical"
        elif result["overall_severity_score"] >= 0.6:
            result["overall_severity"] = "high"
        elif result["overall_severity_score"] >= 0.35:
            result["overall_severity"] = "medium"
        else:
            result["overall_severity"] = "low"

        return result


class LanguageAgent(BaseAgent):
    """
    Multilingual situation brief generation agent.

    Responsibilities:
        - Receives the English coordination brief JSON object.
        - Translates specific fields into the target language (e.g. Hindi).
        - Keeps severity scores, priority labels, time windows, and booleans in their original format.

    Output schema:
        Matches the input coordination brief structure.
    """

    prompt_name = "language"
    max_tokens = 2500

    def _translate_payload(self, payload: dict, target_language: str) -> dict:
        import json
        if not payload:
            return payload
        user_message = f"""Translate all text values in the following JSON object into {target_language} (Devanagari script for Hindi).
Return a valid JSON object matching the input structure exactly with translated values.
Translate all texts, descriptions, names of laws/sections, legal provisions, citations, and time values (like "within 24 hours", "immediate", "immediately", "within 10 minutes", etc.) into natural {target_language}.
Do NOT keep legal references, names of laws, or timeframes in English. Translate them fully.
Keep only numeric values, severity labels, and boolean values unchanged.

Input JSON:
{json.dumps(payload, indent=2, ensure_ascii=False)}

Return only the valid JSON object."""
        try:
            raw = self.call_groq(user_message)
            return self.parse_json_response(raw)
        except Exception as exc:
            logger.error("[LanguageAgent] Sub-translation failed: %s", exc)
            return payload

    def _force_hindi_translation(self, val: str) -> str:
        import re
        if not isinstance(val, str):
            return val
        replacements = [
            # Laws/Sections/Citations
            (r'\b[Ss]ection\s+(\d+)\s+[Cc]r[Pp][Cc]\s*\(\s*now\s+[Ss]ection\s+(\d+)\s+[Bb][Nn][Ss][Ss]\s*\)', r'धारा \1 सीआरपीसी (अब धारा \2 बीएनएसएस)'),
            (r'\b[Ss]ection\s+(\d+)\s+[Cc]r[Pp][Cc]', r'धारा \1 सीआरपीसी'),
            (r'\b[Ss]ection\s+(\d+)\s+[Bb][Nn][Ss][Ss]', r'धारा \1 बीएनएसएस'),
            (r'\b[Aa]rticle\s+(\d+)\s+of\s+[Cc]onstitution', r'संविधान का अनुच्छेद \1'),
            (r'\b[Aa]rticle\s+(\d+)', r'अनुच्छेद \1'),
            (r'\b[Dd]\.?[Kk]\.?\s+[Bb]asu\s+[Gg]uidelines\b', 'डी.के. बसु दिशानिर्देश'),
            (r'\b[Cc]r[Pp][Cc]\b', 'सीआरपीसी'),
            (r'\b[Bb][Nn][Ss][Ss]\b', 'बीएनएसएस'),
            
            # Timeframes
            (r'\b[Ww]ithin\s+(\d+)\s+hours\b', r'\1 घंटे के भीतर'),
            (r'\b[Ww]ithin\s+(\d+)\s+hour\b', r'\1 घंटे के भीतर'),
            (r'\b[Ww]ithin\s+(\d+)\s+minutes\b', r'\1 मिनट के भीतर'),
            (r'\b[Ww]ithin\s+(\d+)\s+minute\b', r'\1 मिनट के भीतर'),
            (r'\b[Ww]ithin\s+(\d+)-(\d+)\s+minutes\b', r'\1-\2 मिनट के भीतर'),
            (r'\b[Ww]ithin\s+(\d+)-(\d+)\s+hours\b', r'\1-\2 घंटे के भीतर'),
            (r'\b[Ii]mmediately\b', 'तुरंत'),
            (r'\b[Ii]mmediate\b', 'तुरंत'),
            (r'\b[Nn]ow\b', 'अब'),
            (r'\b(\d+)\s+minutes\b', r'\1 मिनट'),
            (r'\b(\d+)\s+hours\b', r'\1 घंटे'),
            (r'\b(\d+)\s+days\b', r'\1 दिन'),
            
            # Authorities
            (r'\bDistrict Legal Services Authority\s*\(\s*DLSA\s*\)', 'जिला कानूनी सेवा प्राधिकरण (डीएलएसए)'),
            (r'\bNational Legal Services Authority\s*\(\s*NALSA\s*\)', 'राष्ट्रीय कानूनी सेवा प्राधिकरण (एनएएलएसए)'),
            (r'\bChief Medical Officer\s*\(\s*CMO\s*\)', 'मुख्य चिकित्सा अधिकारी (सीएमओ)'),
            (r'\bSuperintendent of Police\s*\(\s*SP\s*\)', 'पुलिस अधीक्षक (एसपी)'),
            (r'\bSenior Superintendent of Police\s*\(\s*SSP\s*\)', 'वरिष्ठ पुलिस अधीक्षक (एसएसपी)'),
            (r'\bDistrict Collector\b', 'जिला कलेक्टर'),
            (r'\bDLSA\b', 'डीएलएसए'),
            (r'\bNALSA\b', 'एनएएलएसए'),
            (r'\bCMO\b', 'सीएमओ'),
            (r'\bSP\b', 'एसपी'),
            (r'\bSSP\b', 'एसएसपी'),
            (r'\bEmergency Response Coordinator\b', 'आपातकालीन प्रतिक्रिया समन्वयक'),
            (r'\bNational Ambulance\b', 'राष्ट्रीय एम्बुलेंस'),
            (r'\bFire Brigade\b', 'दमकल केंद्र'),
            (r'\bPolice\b', 'पुलिस'),
            (r'\bAmbulance\b', 'एम्बुलेंस'),
        ]
        res = val
        for pattern, repl in replacements:
            res = re.sub(pattern, repl, res, flags=re.IGNORECASE)
        return res

    def _post_process_translate(self, obj: any, target_language: str) -> any:
        if target_language != "hindi":
            return obj
        if isinstance(obj, dict):
            return {k: self._post_process_translate(v, target_language) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._post_process_translate(x, target_language) for x in obj]
        elif isinstance(obj, str):
            return self._force_hindi_translation(obj)
        else:
            return obj

    def run(self, coord_result: dict, target_language: str = "hindi") -> dict:
        logger.info("[LanguageAgent] Running translation in separate sub-payloads to prevent truncation and ensure 100%% translation")

        translated_result = {}

        # 1. Overview
        overview_payload = {
            "situation_title": coord_result.get("situation_title", ""),
            "what_is_happening": coord_result.get("what_is_happening", ""),
            "situation_brief": coord_result.get("situation_brief", ""),
            "primary_concern": coord_result.get("primary_concern", ""),
            "nearest_authority_type": coord_result.get("nearest_authority_type", ""),
            "authority_to_contact": coord_result.get("authority_to_contact", ""),
            "golden_window": coord_result.get("golden_window", {}),
            "conflict_resolution": coord_result.get("conflict_resolution", {})
        }
        overview_translated = self._translate_payload(overview_payload, target_language)
        translated_result.update(overview_translated)

        # 2. Basic lists
        lists_payload = {
            "resources_needed": coord_result.get("resources_needed", []),
            "authorities_to_notify": coord_result.get("authorities_to_notify", []),
            "emergency_contacts": coord_result.get("emergency_contacts", [])
        }
        lists_translated = self._translate_payload(lists_payload, target_language)
        translated_result.update(lists_translated)

        # 3. legal_provisions
        if "legal_provisions" in coord_result:
            provs_payload = {"legal_provisions": coord_result["legal_provisions"]}
            provs_translated = self._translate_payload(provs_payload, target_language)
            translated_result.update(provs_translated)

        # 4. legal_timeline
        if "legal_timeline" in coord_result:
            timeline_payload = {"legal_timeline": coord_result["legal_timeline"]}
            timeline_translated = self._translate_payload(timeline_payload, target_language)
            translated_result.update(timeline_translated)

        # 5. immediate_actions
        if "immediate_actions" in coord_result:
            actions_payload = {"immediate_actions": coord_result["immediate_actions"]}
            actions_translated = self._translate_payload(actions_payload, target_language)
            translated_result.update(actions_translated)

        # 6. evidence_to_collect
        if "evidence_to_collect" in coord_result:
            evidence_payload = {"evidence_to_collect": coord_result["evidence_to_collect"]}
            evidence_translated = self._translate_payload(evidence_payload, target_language)
            translated_result.update(evidence_translated)

        # 7. escalation_path
        if "escalation_path" in coord_result:
            escalation_payload = {"escalation_path": coord_result["escalation_path"]}
            escalation_translated = self._translate_payload(escalation_payload, target_language)
            translated_result.update(escalation_translated)

        # General fallbacks for any other keys
        for key, val in coord_result.items():
            if key not in translated_result:
                translated_result[key] = val

        # Apply recursive post-processing to force Hindi translation for legal & time terms
        translated_result = self._post_process_translate(translated_result, target_language)

        return translated_result


class LegalNoticeAgent(BaseAgent):
    """
    Agent for generating legal notice drafts based on incident data and rights assessments.
    """
    prompt_name = "legal_notice"
    max_tokens = 3000

    def run(self, signal, rights_result: dict, target_language: str = "english") -> str:
        logger.info("[LegalNoticeAgent] Running on signal %s with target_language %s", getattr(signal, 'id', 'mock_id'), target_language)
        
        # Format rights result context
        rights_context = ""
        if rights_result:
            rights_context += f"Rights Violated: {', '.join(rights_result.get('rights_violated', []))}\n"
            rights_context += "Legal Provisions:\n"
            for prov in rights_result.get("legal_provisions", []):
                rights_context += f"- Provision: {prov.get('provision')}\n"
                rights_context += f"  Description: {prov.get('description')}\n"
                rights_context += f"  Relevance: {prov.get('relevance')}\n"
            rights_context += f"Recommended Immediate Actions: {', '.join(rights_result.get('immediate_actions', []))}\n"
            rights_context += f"Authority to Contact: {rights_result.get('authority_to_contact')}\n"

        lang_instruction = ""
        if target_language == "hindi":
            lang_instruction = "Generate the complete legal notice in fluent, professional, and authoritative legal HINDI (Devanagari script), translating all sections, facts, demands, and compliance details fully. Maintain a high-quality formal tone."
        else:
            lang_instruction = "Generate the complete legal notice in fluent, professional, and authoritative legal English."

        user_message = (
            f"Signal text: {signal.raw_text}\n\n"
            f"Rights Assessment context:\n{rights_context}\n\n"
            f"{lang_instruction}\n\n"
            f"Please generate the complete legal notice draft based on the above information."
        )

        raw_response = self.call_groq(user_message)
        # We do NOT parse it as JSON, just return the raw string response after contact sanitizing
        return sanitize_text_contacts(raw_response.strip())

