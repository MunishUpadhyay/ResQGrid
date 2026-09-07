import logging

logger = logging.getLogger(__name__)

# Master directory database containing official national helplines and verified authorities
# Key format: (authority_name, jurisdiction)
VERIFIED_DIRECTORY = {
    ("National Ambulance", "National"): {
        "authority": "National Ambulance",
        "jurisdiction": "National",
        "contact": "108",
        "source": "Official Gov Helpline",
        "verified": True,
        "last_verified": "2026-08-28"
    },
    ("Janani Shishu Suraksha Karyakram / Pregnancy Ambulance", "National"): {
        "authority": "Janani Shishu Suraksha Karyakram / Pregnancy Ambulance",
        "jurisdiction": "National",
        "contact": "102",
        "source": "Official Gov Helpline",
        "verified": True,
        "last_verified": "2026-08-28"
    },
    ("National Emergency Helpline", "National"): {
        "authority": "National Emergency Helpline",
        "jurisdiction": "National",
        "contact": "112",
        "source": "Official Gov Helpline",
        "verified": True,
        "last_verified": "2026-08-28"
    },
    ("Tele-MANAS Mental Health Helpline", "National"): {
        "authority": "Tele-MANAS Mental Health Helpline",
        "jurisdiction": "National",
        "contact": "14416",
        "source": "Ministry of Health & Family Welfare Helpline",
        "verified": True,
        "last_verified": "2026-08-28"
    },
    ("Kiran Mental Health Helpline", "National"): {
        "authority": "Kiran Mental Health Helpline",
        "jurisdiction": "National",
        "contact": "1800-599-0019",
        "source": "Ministry of Social Justice Helpline",
        "verified": True,
        "last_verified": "2026-08-28"
    },
    ("Childline", "National"): {
        "authority": "Childline",
        "jurisdiction": "National",
        "contact": "1098",
        "source": "Official Gov Helpline",
        "verified": True,
        "last_verified": "2026-08-28"
    },
    ("Women Helpline", "National"): {
        "authority": "Women Helpline",
        "jurisdiction": "National",
        "contact": "1091",
        "source": "Official Gov Helpline",
        "verified": True,
        "last_verified": "2026-08-28"
    },
    ("Police", "National"): {
        "authority": "Police",
        "jurisdiction": "National",
        "contact": "100",
        "source": "Official Gov Helpline",
        "verified": True,
        "last_verified": "2026-09-06"
    },
    ("Fire Emergency", "National"): {
        "authority": "Fire Emergency",
        "jurisdiction": "National",
        "contact": "101",
        "source": "Official Gov Helpline",
        "verified": True,
        "last_verified": "2026-09-06"
    },
    ("NALSA Legal Aid Helpline", "National"): {
        "authority": "NALSA Legal Aid Helpline",
        "jurisdiction": "National",
        "contact": "15100",
        "source": "National Legal Services Authority Helpline",
        "verified": True,
        "last_verified": "2026-09-06"
    },
    ("Ministry of Labour & Employment Helpline", "National"): {
        "authority": "Ministry of Labour & Employment Helpline",
        "jurisdiction": "National",
        "contact": "14434",
        "source": "Ministry of Labour & Employment Toll-Free Helpline",
        "verified": True,
        "last_verified": "2026-09-06"
    },
    ("National Consumer Helpline", "National"): {
        "authority": "National Consumer Helpline",
        "jurisdiction": "National",
        "contact": "1915",
        "source": "Department of Consumer Affairs Helpline",
        "verified": True,
        "last_verified": "2026-09-06"
    },
    ("National Cyber Crime Helpline", "National"): {
        "authority": "National Cyber Crime Helpline",
        "jurisdiction": "National",
        "contact": "1930",
        "source": "Ministry of Home Affairs Cyber Crime Portal",
        "verified": True,
        "last_verified": "2026-09-06"
    }
}

def get_verified_contact(authority_name: str, jurisdiction: str = "National") -> dict:
    """
    Query the verified directory for an authority contact.
    Returns the record dict if found and verified, else returns a dict with 'Verified contact unavailable'.
    """
    if not authority_name:
        return _unavailable_record("Unknown Authority", jurisdiction)

    key = (authority_name.strip(), jurisdiction.strip())
    record = VERIFIED_DIRECTORY.get(key)
    if record and record.get("verified", False):
        return record
    
    return _unavailable_record(authority_name, jurisdiction)


def _unavailable_record(authority_name: str, jurisdiction: str) -> dict:
    return {
        "authority": authority_name,
        "jurisdiction": jurisdiction,
        "contact": "Verified contact unavailable",
        "source": "None",
        "verified": False,
        "last_verified": None
    }


# Master canonical domain authority mapping
# Used by resolve_authority() as the centralized source of truth.
CANONICAL_DOMAIN_AUTHORITIES = {
    "legal": {
        "primary": {
            "authority_to_contact": "National Legal Services Authority (NALSA)",
            "nearest_authority_type": "DLSA",
            "contact": "15100",
            "verified": True
        },
        "subtypes": {
            "dlsa": {
                "authority_to_contact": "National Legal Services Authority (NALSA)",
                "nearest_authority_type": "DLSA",
                "contact": "15100",
                "verified": True
            },
            "nalsa": {
                "authority_to_contact": "National Legal Services Authority (NALSA)",
                "nearest_authority_type": "DLSA",
                "contact": "15100",
                "verified": True
            },
            "labour": {
                "authority_to_contact": "Ministry of Labour & Employment (Labour Commissioner)",
                "nearest_authority_type": "Labour Court",
                "contact": "14434",
                "verified": True
            },
            "consumer": {
                "authority_to_contact": "National Consumer Helpline / Consumer Commission",
                "nearest_authority_type": "Consumer Forum",
                "contact": "1915",
                "verified": True
            },
            "high court": {
                "authority_to_contact": "High Court Legal Services Committee",
                "nearest_authority_type": "High Court",
                "contact": "15100",
                "verified": True
            },
            "police complaint": {
                "authority_to_contact": "Police Complaint Authority",
                "nearest_authority_type": "Police Complaint Authority",
                "contact": "100",
                "verified": True
            },
            "magistrate": {
                "authority_to_contact": "Judicial Magistrate Court",
                "nearest_authority_type": "Magistrate Court",
                "contact": "15100",
                "verified": True
            }
        }
    },
    "health": {
        "primary": {
            "authority_to_contact": "District Health Department (CMO Office)",
            "nearest_authority_type": "Chief Medical Officer (CMO)",
            "contact": "108",
            "verified": True
        },
        "subtypes": {
            "cmo": {
                "authority_to_contact": "District Health Department (CMO Office)",
                "nearest_authority_type": "Chief Medical Officer (CMO)",
                "contact": "108",
                "verified": True
            },
            "ambulance": {
                "authority_to_contact": "National Ambulance Service",
                "nearest_authority_type": "Chief Medical Officer (CMO)",
                "contact": "108",
                "verified": True
            },
            "pregnancy": {
                "authority_to_contact": "Janani Shishu Suraksha Karyakram (JSSK)",
                "nearest_authority_type": "Chief Medical Officer (CMO)",
                "contact": "102",
                "verified": True
            },
            "mental": {
                "authority_to_contact": "Tele-MANAS Mental Health Helpline",
                "nearest_authority_type": "Chief Medical Officer (CMO)",
                "contact": "14416",
                "verified": True
            },
            "phc": {
                "authority_to_contact": "Primary Health Center (PHC)",
                "nearest_authority_type": "Chief Medical Officer (CMO)",
                "contact": "Verified contact unavailable",
                "verified": False
            }
        }
    },
    "emergency": {
        "primary": {
            "authority_to_contact": "National Emergency Helpline",
            "nearest_authority_type": "Emergency Response Center",
            "contact": "112",
            "verified": True
        },
        "subtypes": {
            "police": {
                "authority_to_contact": "Police Control Room",
                "nearest_authority_type": "Police Control Room",
                "contact": "100",
                "verified": True
            },
            "fire": {
                "authority_to_contact": "Fire Emergency Services",
                "nearest_authority_type": "Fire Control Room",
                "contact": "101",
                "verified": True
            },
            "ambulance": {
                "authority_to_contact": "National Ambulance Service",
                "nearest_authority_type": "Chief Medical Officer (CMO)",
                "contact": "108",
                "verified": True
            },
            "disaster": {
                "authority_to_contact": "National Emergency Helpline",
                "nearest_authority_type": "Disaster Management Authority",
                "contact": "112",
                "verified": True
            }
        }
    },
    "civic": {
        "primary": {
            "authority_to_contact": "Local Municipal Authority",
            "nearest_authority_type": "Municipal Corporation",
            "contact": "Verified contact unavailable",
            "verified": False
        },
        "subtypes": {
            "municipal": {
                "authority_to_contact": "Local Municipal Authority",
                "nearest_authority_type": "Municipal Corporation",
                "contact": "Verified contact unavailable",
                "verified": False
            },
            "pwd": {
                "authority_to_contact": "Public Works Department (PWD)",
                "nearest_authority_type": "Public Works Department (PWD)",
                "contact": "Verified contact unavailable",
                "verified": False
            },
            "traffic": {
                "authority_to_contact": "Local Traffic Police Division",
                "nearest_authority_type": "Traffic Police",
                "contact": "100",
                "verified": True
            },
            "ward": {
                "authority_to_contact": "Municipal Ward Office",
                "nearest_authority_type": "Municipal Corporation",
                "contact": "Verified contact unavailable",
                "verified": False
            }
        }
    }
}


def resolve_authority(
    domain: str,
    authority_hint: str = None,
    nearest_type_hint: str = None
) -> dict:
    """
    Deterministic domain-aware authority resolution.
    
    Resolves authority suggestions against canonical domain authority records.
    Never trusts arbitrary LLM authority strings as final truth.
    Rejects domain-incompatible authority hints (e.g. NALSA for civic domain).
    Returns dict:
        {
            "authority_to_contact": str,
            "nearest_authority_type": str,
            "contact": str,
            "verified": bool
        }
    """
    dom_clean = str(domain or "").strip().lower()
    if dom_clean in ["cross", "cross_domain"]:
        hint_text = f"{authority_hint or ''} {nearest_type_hint or ''}".lower()
        if any(w in hint_text for w in ["cmo", "hospital", "medical", "ambulance", "doctor", "health"]):
            dom_clean = "health"
        elif any(w in hint_text for w in ["dlsa", "nalsa", "court", "legal", "lawyer", "police complaint"]):
            dom_clean = "legal"
        elif any(w in hint_text for w in ["fire", "disaster", "emergency 112"]):
            dom_clean = "emergency"
        elif any(w in hint_text for w in ["municipal", "pwd", "pothole", "civic", "garbage"]):
            dom_clean = "civic"
        else:
            dom_clean = "legal"

    if dom_clean not in CANONICAL_DOMAIN_AUTHORITIES:
        dom_clean = "civic"

    domain_config = CANONICAL_DOMAIN_AUTHORITIES[dom_clean]
    primary_rec = domain_config["primary"].copy()

    combined_hint = f"{authority_hint or ''} {nearest_type_hint or ''}".strip().lower()
    if not combined_hint:
        return primary_rec

    # Reject domain-incompatible authority hints
    incompatible_keywords = {
        "civic": ["nalsa", "dlsa", "legal services", "15100", "court", "labour court", "consumer forum", "cmo", "hospital", "ambulance"],
        "health": ["nalsa", "dlsa", "legal services", "15100", "labour court", "consumer forum", "pwd", "municipal"],
        "legal": ["cmo office", "cmo department", "hospital admission", "pwd", "municipal corporation"],
        "emergency": ["nalsa", "dlsa", "legal aid", "15100", "consumer forum", "labour court"]
    }

    bad_kws = incompatible_keywords.get(dom_clean, [])
    if any(bad_kw in combined_hint for bad_kw in bad_kws):
        return primary_rec

    # Check for canonical subtype match
    subtypes = domain_config.get("subtypes", {})
    for key, record in subtypes.items():
        if key in combined_hint:
            return record.copy()

    # If hint matches a verified helpline record in VERIFIED_DIRECTORY, format output cleanly
    if authority_hint:
        record_from_ver = get_verified_contact(authority_hint)
        if record_from_ver and record_from_ver.get("verified"):
            return {
                "authority_to_contact": record_from_ver["authority"],
                "nearest_authority_type": primary_rec["nearest_authority_type"],
                "contact": record_from_ver["contact"],
                "verified": True
            }

    return primary_rec


def sanitize_contact_number(number: str) -> str:
    """
    Deterministic safety check to filter out fake/placeholder numbers.
    Any unverified phone number is converted to 'Verified contact unavailable'.
    """
    if not number:
        return "Verified contact unavailable"
    
    num_clean = number.strip().lower()
    
    # Common placeholders to catch immediately
    placeholders = [
        "01234", "56789", "home-sec", "how to reach",
        "contact number", "placeholder", "unavailable"
    ]
    for p in placeholders:
        if p in num_clean:
            return "Verified contact unavailable"
            
    # Known official national emergency/support lines are allowed
    known_emergency = {
        "108", "100", "101", "102", "1091", "112", "1098", "14416", "14434", "15100", "1915", "1930", "181",
        "1800-599-0019", "18005990019", "1800-891-4416", "18008914416"
    }
    if num_clean in known_emergency:
        return num_clean
        
    # Since they are not verified in our master database/structure,
    # return the explicit unavailable string to prevent fake phone numbers.
    return "Verified contact unavailable"


def sanitize_text_contacts(text: str) -> str:
    """
    Scans a text string for phone numbers/placeholders and sanitizes them
    without corrupting section numbers, case numbers, or dates.
    """
    if not text:
        return text
    
    import re
    
    # Explicitly catch and replace the known placeholder number patterns case-insensitively
    placeholders = [
        r"\b01234[- ]\d{5,6}\b",
        r"\b1800[- ][a-zA-Z0-9_-]{7,10}\b",
        r"\b1800[- ]home[- ]sec\b",
        r"\b01234[- ]567890\b",
        r"\b98765[- ]43210\b",
        r"\b9876543210\b"
    ]
    
    res = text
    for pattern in placeholders:
        res = re.sub(pattern, "Verified contact unavailable", res, flags=re.IGNORECASE)
        
    return res

