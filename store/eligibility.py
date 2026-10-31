"""Eligibility engine — EU261 / Turkish SHY / Canadian APPR."""
from __future__ import annotations
import math
from dataclasses import dataclass
from decimal import Decimal


def great_circle_km(a, b):
    R = 6371.0
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 2 * R * math.asin(math.sqrt(h))


EU_COUNTRIES = {
    "AT","BE","BG","HR","CY","CZ","DK","EE","FI","FR","DE","GR","HU","IE",
    "IT","LV","LT","LU","MT","NL","PL","PT","RO","SK","SI","ES","SE",
    "IS","LI","NO","CH",
}


@dataclass
class EligibilityResult:
    jurisdiction: str
    eligible_amount: Decimal | None
    currency_display: str
    notes: list[str]

    def to_claim_fields(self):
        return {
            "jurisdiction": self.jurisdiction,
            "eligible_amount": self.eligible_amount,
            "currency_display": self.currency_display,
            "eligibility_notes": "\n".join(self.notes),
        }


def detect_jurisdiction(claim):
    dep = (claim.from_country or "").upper()
    arr = (claim.to_country or "").upper()
    air = (claim.airline_country or "").upper()
    if dep in EU_COUNTRIES:
        return "EU261"
    if arr in EU_COUNTRIES and air in EU_COUNTRIES:
        return "EU261"
    if dep == "TR" or (air == "TR" and arr == "TR"):
        return "TR_SHY"
    if dep == "CA" or arr == "CA":
        return "CA_APPR"
    return "NONE"


def _is_delay_long_enough(text):
    if not text:
        return False, "Delay duration not provided."
    d = text.lower()
    if any(k in d for k in ["more than 4", "4+", ">4", "over 4"]):
        return True, "Delay exceeds 4 hours."
    if any(k in d for k in ["3 to 4", "3+", ">3", "over 3"]):
        return True, "Delay exceeds 3 hours."
    if "2 to 3" in d:
        return False, "Delay 2–3 hours — below EU261 threshold."
    if any(k in d for k in ["less than 2", "<2"]):
        return False, "Delay under 2 hours — below EU261 threshold."
    return False, "Delay duration unrecognised."


def _notice_too_late(text):
    if not text:
        return False, "Notice period unknown — assuming short-notice."
    n = text.lower()
    if "more than 14" in n or "14+" in n:
        return True, "Airline gave 14+ days notice — no fixed compensation."
    return False, "Notice was short (<14 days) — compensation applies."


def _hours_from_text(text):
    if not text:
        return 0
    d = text.lower()
    if any(k in d for k in ["more than 4", "4+", ">4", "over 4"]):
        return 4
    if "3 to 4" in d:
        return 3
    if "2 to 3" in d:
        return 2
    if any(k in d for k in ["less than 2", "<2"]):
        return 1
    return 0


def _eu261_tier(km):
    if km <= 1500:
        return Decimal("250.00")
    if km <= 3500:
        return Decimal("400.00")
    return Decimal("600.00")


def _evaluate_eu261(claim, km):
    notes = ["Applicable regulation: EU Regulation 261/2004.",
             f"Route distance: {km:,.0f} km."]
    if claim.claim_type == "cancelled":
        late, why = _notice_too_late(claim.cancellation_notice)
        notes.append(why)
        if late:
            return EligibilityResult("EU261", None, "EUR", notes)
        amt = _eu261_tier(km)
        notes.append(f"EU261 Article 7 tier: €{amt}.")
        return EligibilityResult("EU261", amt, "EUR", notes)
    if claim.claim_type == "delayed":
        ok, why = _is_delay_long_enough(claim.delay_duration)
        notes.append(why)
        if not ok:
            return EligibilityResult("EU261", None, "EUR", notes)
        amt = _eu261_tier(km)
        notes.append(f"EU261 Article 7 tier: €{amt}.")
        return EligibilityResult("EU261", amt, "EUR", notes)
    if claim.claim_type == "connection":
        ok, why = _is_delay_long_enough(claim.delay_duration or claim.connection_time)
        notes.append(why)
        if not ok:
            return EligibilityResult("EU261", None, "EUR", notes)
        amt = _eu261_tier(km)
        notes.append(f"EU261 Article 7 tier: €{amt}.")
        return EligibilityResult("EU261", amt, "EUR", notes)
    if claim.claim_type == "denied":
        amt = _eu261_tier(km)
        notes.append(f"Denied boarding → EU261 Article 7 tier: €{amt}.")
        return EligibilityResult("EU261", amt, "EUR", notes)
    if claim.claim_type == "baggage":
        notes.append("Baggage handled under Montreal Convention (up to ~€1,300), receipt-based — not auto-calculated.")
        return EligibilityResult("EU261", None, "EUR", notes)
    return EligibilityResult("EU261", None, "EUR", notes)


def _tr_shy_tier(km, domestic):
    if domestic:
        return Decimal("100.00")
    if km <= 1500:
        return Decimal("250.00")
    if km <= 3500:
        return Decimal("400.00")
    return Decimal("600.00")


def _evaluate_tr_shy(claim, km, domestic):
    notes = ["Applicable regulation: Turkish SHY Passenger Rights Regulation.",
             f"Route distance: {km:,.0f} km.",
             "Domestic flight." if domestic else "International flight."]
    if claim.claim_type == "delayed":
        ok, why = _is_delay_long_enough(claim.delay_duration)
        notes.append(why)
        if not ok:
            return EligibilityResult("TR_SHY", None, "EUR", notes)
        amt = _tr_shy_tier(km, domestic)
        notes.append(f"SHY tier: €{amt}.")
        return EligibilityResult("TR_SHY", amt, "EUR", notes)
    if claim.claim_type == "cancelled":
        late, why = _notice_too_late(claim.cancellation_notice)
        notes.append(why)
        if late:
            return EligibilityResult("TR_SHY", None, "EUR", notes)
        amt = _tr_shy_tier(km, domestic)
        notes.append(f"SHY tier: €{amt}.")
        return EligibilityResult("TR_SHY", amt, "EUR", notes)
    if claim.claim_type == "denied":
        amt = _tr_shy_tier(km, domestic)
        notes.append(f"Denied boarding → SHY tier: €{amt}.")
        return EligibilityResult("TR_SHY", amt, "EUR", notes)
    if claim.claim_type == "connection":
        notes.append("SHY has no missed-connection tier — assess as delay.")
        return EligibilityResult("TR_SHY", None, "EUR", notes)
    if claim.claim_type == "baggage":
        notes.append("Baggage under SHY is receipt-based — not auto-calculated.")
        return EligibilityResult("TR_SHY", None, "EUR", notes)
    return EligibilityResult("TR_SHY", None, "EUR", notes)


def _ca_appr_tier(hours, large):
    fx = Decimal("0.68")
    if large:
        cad = Decimal("1000") if hours >= 9 else Decimal("700") if hours >= 6 else Decimal("400") if hours >= 3 else Decimal("0")
    else:
        cad = Decimal("500") if hours >= 9 else Decimal("250") if hours >= 6 else Decimal("125") if hours >= 3 else Decimal("0")
    return (cad * fx).quantize(Decimal("0.01"))


def _evaluate_ca_appr(claim, hours, large):
    notes = ["Applicable regulation: Canada Air Passenger Protection Regulations (APPR).",
             f"Airline category: {'large' if large else 'small'}.",
             f"Delay at final destination: {hours}h."]
    if claim.claim_type in ("delayed", "cancelled", "connection"):
        if hours < 3:
            notes.append("APPR requires 3+ hour delay.")
            return EligibilityResult("CA_APPR", None, "EUR", notes)
        amt = _ca_appr_tier(hours, large)
        notes.append(f"APPR tier: €{amt}.")
        return EligibilityResult("CA_APPR", amt, "EUR", notes)
    if claim.claim_type == "denied":
        cad = Decimal("900") if hours < 6 else Decimal("1800") if hours < 9 else Decimal("2400")
        amt = (cad * Decimal("0.68")).quantize(Decimal("0.01"))
        notes.append(f"APPR denied-boarding tier: €{amt}.")
        return EligibilityResult("CA_APPR", amt, "EUR", notes)
    if claim.claim_type == "baggage":
        notes.append("APPR baggage is receipt-based — not auto-calculated.")
        return EligibilityResult("CA_APPR", None, "EUR", notes)
    return EligibilityResult("CA_APPR", None, "EUR", notes)


def evaluate(claim):
    km = claim.route_distance_km()
    jur = detect_jurisdiction(claim)
    if jur == "NONE" or km is None:
        why = []
        if jur == "NONE":
            why.append("No supported regulation applies for this route.")
        if km is None:
            why.append("Airport coordinates missing — cannot compute distance tier.")
        return EligibilityResult("NONE", None, "EUR", why)
    if jur == "EU261":
        return _evaluate_eu261(claim, km)
    if jur == "TR_SHY":
        domestic = (claim.from_country or "").upper() == "TR" and (claim.to_country or "").upper() == "TR"
        return _evaluate_tr_shy(claim, km, domestic)
    if jur == "CA_APPR":
        hours = _hours_from_text(claim.delay_duration or claim.connection_time)
        large = (claim.airline_iata or "").upper() in {"AC","WS","AA","DL","UA","BA","AF","KL"}
        return _evaluate_ca_appr(claim, hours, large)
    return EligibilityResult("NONE", None, "EUR", ["Unhandled jurisdiction."])