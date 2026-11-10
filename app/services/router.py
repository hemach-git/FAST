"""Question router: explainable keyword rules + classifier (LLM or mock), with a safety override.

Safety override: any hazard signal forces the safety source in, regardless of classifier output.
"""
from __future__ import annotations

import re

from app.config import get_config
from app.models.schemas import RouteDecision
from app.services import llm, retriever

RULES: dict[str, list[str]] = {
    "safety": [r"lock ?out", r"tag ?out", r"\bloto\b", r"\bppe\b", r"glove", r"glasses", r"respirator",
               r"hearing", r"helmet", r"injur", r"\bfire\b", r"spill", r"chemical", r"\bsds\b", r"hazard",
               r"\bguard", r"light curtain", r"interlock", r"forklift", r"pedestrian", r"evacuat",
               r"emergency", r"first aid", r"\bsafe", r"energy", r"\bgas\b", r"solvent", r"wear"],
    "maintenance": [r"repair", r"replac", r"\bfix", r"\bjam", r"belt", r"bearing", r"lubric", r"grease",
                    r"\boil\b", r"leak", r"alarm", r"fault", r"overheat", r"spindle", r"hydraulic",
                    r"pressure", r"compressor", r"filter", r"maintenance", r"\bpm\b", r"tension",
                    r"motor", r"overload", r"noise", r"vibrat", r"tip dress", r"die change", r"coolant",
                    r"tracking", r"wire feed", r"drain", r"reset", r"trip", r"chiller"],
    "quality": [r"torque", r"tolerance", r"inspect", r"defect", r"gauge", r"\bcmm\b", r"caliper",
                r"nonconform", r"\bncr\b", r"hold tag", r"\bhold\b", r"reject", r"scrap", r"\bspc\b",
                r"control chart", r"\bcpk\b", r"out of control", r"finish", r"orange peel",
                r"film thickness", r"porosity", r"undercut", r"accept", r"\bspec", r"measur",
                r"first article", r"bore", r"nugget", r"quarantine", r"dimension", r"run.? and sag", r"fish ?eye"],
}
# Hazard terms: work that may expose a person to energy/chemicals -> always include safety.
HAZARD = [r"clear(ing)? (a |the )?jam", r"\bjam", r"reach (in|into)", r"die change", r"change (the )?die",
          r"enter(ing)? (the )?(cell|enclosure|booth)", r"hydraulic leak", r"oil leak", r"electrical",
          r"energi[sz]ed", r"chemical", r"solvent", r"spill", r"\bfire\b", r"injur", r"guard", r"forklift",
          r"light curtain", r"interlock", r"replace (the )?(belt|bearing|tip|roller)", r"lock ?out", r"\bloto\b",
          r"\bstuck\b", r"\bunjam", r"\bclear(ing)?\b", r"\b(fix|repair|adjust|unclog)\b", r"\bclean(ing)?\b",
          r"\bremov(e|ing)\b", r"\bpiling up\b", r"\bpressure (loss|drop)", r"\boverheat", r"\brunning hot\b"]


def rule_scores(question: str) -> dict[str, float]:
    q = question.lower()
    out = {}
    for cat, pats in RULES.items():
        hits = sum(1 for p in pats if re.search(p, q))
        out[cat] = hits / (hits + 1.0)          # 0, .5, .67, .75 ...
    return out


def hazard_flag(question: str) -> bool:
    q = question.lower()
    return any(re.search(p, q) for p in HAZARD)


def route(question: str) -> tuple[RouteDecision, dict]:
    cfg = get_config()["routing"]
    rules = rule_scores(question)
    clf, usage = llm.classify(question, retriever.category_scores(question))
    w = cfg["rule_weight"]
    blended = {c: round(w * rules[c] + (1 - w) * clf.get(c, 0.0), 3) for c in rules}
    ranked = sorted(blended, key=blended.get, reverse=True)
    top = blended[ranked[0]]
    safety_flag = hazard_flag(question)

    if top < cfg["min_confidence"]:
        cats = list(ranked)
        broad, reason = True, "Low routing confidence - searched all three sources."
    else:
        cats = [c for c in ranked if blended[c] >= cfg["secondary_ratio"] * top and blended[c] > 0.15]
        broad, reason = False, f"Best match: {ranked[0]} ({top:.2f})."
    if safety_flag and "safety" not in cats:
        cats.append("safety")
        reason += " Safety procedure added because the task involves a hazard."
    elif safety_flag:
        reason += " Hazard detected - safety procedure included."

    decision = RouteDecision(categories=cats, primary=cats[0], confidence=min(1.0, top),
                             safety_flag=safety_flag, broad_search=broad,
                             method=f"rules+{usage.get('classifier', 'classifier')}",
                             scores=blended, reason=reason.strip())
    return decision, usage
