"""Proposal triage prototype: extract (AI) -> verify -> gate -> score -> rank (rules).

Offline (default): uses recorded extractions in fixtures/extractions.json, no API key needed.
    python prototype/triage.py

Live: calls Claude once per proposal using the prompt in PROMPTS.md.
    Put extracted proposal text in prototype/samples/A.txt ... E.txt (gitignored), set ANTHROPIC_API_KEY, then
    python prototype/triage.py --live
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MODEL = "claude-opus-5-5"
PROMPT_VERSION = "extract-v1"

# Rule-based injection scan on raw text, independent of the model (defence in depth).
INJECTION_PATTERNS = [
    r"automated (review|reviewer|scoring|system)",
    r"\b(AI|LLM|language model|assistant)\b.{0,40}\b(rank|score|ignore|assign)",
    r"ignore (all |any )?(previous|prior|above|the) (instructions|rules|criteria)",
    r"rank (it|this|us|this proposal) (first|#?1|highest)",
    r"(full|maximum|top) marks",
    r"pre-?approved",
    r"system prompt",
]


def sha256(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]


# ---------- config and prompt loading ----------

def load_criteria(path: Path) -> dict:
    criteria = json.loads(path.read_text(encoding="utf-8"))
    weights = sum(c["weight"] for c in criteria["criteria"] if c["type"] == "weighted")
    if weights != 100:
        sys.exit(f"Criteria config invalid: weighted criteria sum to {weights}, not 100.")
    return criteria


def load_prompts() -> tuple[str, str]:
    """Read the system prompt and user template verbatim from PROMPTS.md."""
    md = (ROOT / "PROMPTS.md").read_text(encoding="utf-8")

    def block_after(heading: str) -> str:
        m = re.search(re.escape(heading) + r".*?```text\n(.*?)\n```", md, re.S)
        if not m:
            sys.exit(f"Could not find prompt block after '{heading}' in PROMPTS.md")
        return m.group(1)

    return block_after("### System prompt"), block_after("### User message template")


def all_fields(criteria: dict) -> list[dict]:
    return [f for c in criteria["criteria"] for f in c["fields"]]


def render_field_list(criteria: dict) -> str:
    return "\n".join(f"- {f['id']} ({f['type']}): {f['description']}" for f in all_fields(criteria))


def output_schema(criteria: dict) -> dict:
    nullable_str = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    field_props = {
        f["id"]: {
            "type": "object",
            "properties": {
                "value": {"anyOf": [{"type": f["type"]}, {"type": "null"}]},
                "evidence": nullable_str,
                "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                "note": nullable_str,
            },
            "required": ["value", "evidence", "confidence", "note"],
            "additionalProperties": False,
        }
        for f in all_fields(criteria)
    }
    return {
        "type": "object",
        "properties": {
            "proposal_id": {"type": "string"},
            "bidder_name": nullable_str,
            "fields": {
                "type": "object",
                "properties": field_props,
                "required": list(field_props),
                "additionalProperties": False,
            },
            "suspicious_content": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"quote": {"type": "string"}, "location": nullable_str, "why": {"type": "string"}},
                    "required": ["quote", "location", "why"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["proposal_id", "bidder_name", "fields", "suspicious_content"],
        "additionalProperties": False,
    }


# ---------- AI step (live mode only) ----------

def extract_live(pid: str, text: str, criteria: dict, system: str, template: str, cache_dir: Path) -> dict:
    """One Claude call per proposal. Cached by input/prompt/criteria/model so re-runs are identical and free."""
    key = sha256("|".join([text, system, template, str(criteria["version"]), MODEL]))
    cached = cache_dir / f"{pid}-{key}.json"
    if cached.exists():
        return json.loads(cached.read_text(encoding="utf-8"))

    import anthropic  # only needed in live mode

    user = template.format(
        category_name=criteria["category_name"],
        project_description=criteria["project"]["description"],
        criteria_version=criteria["version"],
        field_list=render_field_list(criteria),
        proposal_id=pid,
        proposal_text=text,
    )
    client = anthropic.Anthropic()
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": user}],
        thinking={"type": "adaptive"},
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": output_schema(criteria)}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"Extraction for {pid} stopped early: {response.stop_reason}")
    data = json.loads(next(b.text for b in response.content if b.type == "text"))
    data["_model"] = response.model
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


# ---------- rule steps ----------

def normalise(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def verify_evidence(extraction: dict, text: str | None) -> list[str]:
    """Discard any value whose evidence quote is not found verbatim in the source. Returns issues."""
    if text is None:
        return []  # offline fixtures carry no quotes
    issues, source = [], normalise(text)
    for fid, f in extraction["fields"].items():
        if f["value"] is None:
            continue
        if not f.get("evidence") or normalise(f["evidence"]) not in source:
            issues.append(f"{fid}: evidence quote not found in source; value discarded")
            f["value"], f["confidence"] = None, "low"
    return issues


def scan_injection(text: str | None) -> list[str]:
    if text is None:
        return []
    return [p for p in INJECTION_PATTERNS if re.search(p, text, re.I)]


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def check_mandatory(c: dict, v: dict) -> str:
    """'pass', 'fail' or 'unknown' (missing data goes to a human, it is never assumed)."""
    rule, values = c["rule"], [v.get(f) for f in c["rule"]["fields"]]
    for field, minimum in rule.get("min", {}).items():
        values.append(None if v.get(field) is None else v[field] >= minimum)
    if any(x is False for x in values):
        return "fail"
    if any(x is None for x in values):
        return "unknown"
    return "pass"


def score_criterion(c: dict, v: dict, project: dict, eval_year: int) -> tuple[float, list[str], str | None]:
    """Return (fraction 0-1, warnings, hard_limit_breach)."""
    rule, kind, warnings = c["rule"], c["rule"]["kind"], []

    if kind == "count_to_cap":
        n = v.get(rule["field"])
        earliest = v.get(rule.get("window_field"))
        if earliest is not None and earliest < eval_year - project["experience_window_years"]:
            warnings.append(f"experience counted since {earliest}; some projects may be older than "
                            f"{project['experience_window_years']} years - verify")
        return (0.0 if n is None else clamp01(n / rule["cap"])), warnings, None

    if kind == "lower_is_better":
        x = v.get(rule["field"])
        zero = project[rule["zero_at"]]
        full = rule["full_at"] if "full_at" in rule else zero * (1 - rule["full_at_fraction_below"])
        if x is None:
            warnings.append(f"{c['name'].lower()} not committed in proposal; scored 0 - request clarification")
            return 0.0, warnings, None
        if rule.get("hard_limit") and x > zero:
            return 0.0, warnings, f"{c['name']} {x:g} exceeds limit {zero:g}"
        return clamp01((zero - x) / (zero - full)), warnings, None

    if kind == "average_to_cap":
        parts = [clamp01((v.get(f) or 0) / cap) for f, cap in rule["caps"].items()]
        return sum(parts) / len(parts), warnings, None

    if kind == "checklist":
        return sum(1 for f in rule["fields"] if v.get(f) is True) / len(rule["fields"]), warnings, None

    raise ValueError(f"Unknown rule kind: {kind}")


def evaluate(pid: str, extraction: dict, text: str | None, criteria: dict, eval_year: int) -> dict:
    project = criteria["project"]
    issues = verify_evidence(extraction, text)
    v = {fid: f["value"] for fid, f in extraction["fields"].items()}
    flags = [f"suspicious content at {s.get('location') or 'unknown location'}: {s['why']}"
             for s in extraction.get("suspicious_content", [])]
    flags += [f"raw-text scan matched pattern '{p}'" for p in scan_injection(text)]

    gates, breaches, warnings, breakdown = {}, [], list(issues), {}
    for c in criteria["criteria"]:
        if c["type"] == "mandatory":
            gates[c["id"]] = check_mandatory(c, v)
            continue
        frac, warns, breach = score_criterion(c, v, project, eval_year)
        breakdown[c["id"]] = round(frac * c["weight"], 2)
        warnings += warns
        if breach:
            breaches.append(breach)
    warnings += [f"{fid}: {f['note']}" for fid, f in extraction["fields"].items()
                 if f["confidence"] != "high" and f.get("note")]
    total = round(sum(breakdown.values()), 1)

    failed = [c["name"] for c in criteria["criteria"] if gates.get(c["id"]) == "fail"]
    unknown = [c["name"] for c in criteria["criteria"] if gates.get(c["id"]) == "unknown"]
    if flags:
        status = "flagged"   # manipulation attempt: a human must clear it before any score is shown
    elif failed or breaches:
        status = "ineligible"
    elif unknown:
        status = "flagged"   # mandatory evidence missing/unverified: never assume pass
    else:
        status = "ranked"

    return {
        "proposal": pid,
        "bidder": extraction.get("bidder_name"),
        "status": status,
        "score": total if status == "ranked" else None,
        "shadow_score": total,  # visible to the reviewer only, for flagged items
        "breakdown": breakdown,
        "gates": gates,
        "failed_mandatory": failed,
        "unverified_mandatory": unknown,
        "hard_limit_breaches": breaches,
        "flags": flags,
        "warnings": warnings,
        "facts": v,
    }


def reason(r: dict, criteria: dict) -> str:
    names = {c["id"]: c["name"] for c in criteria["criteria"]}
    if r["status"] == "ineligible":
        return "Ineligible: fails " + "; ".join(r["failed_mandatory"] + r["hard_limit_breaches"]) + "."
    if r["status"] == "flagged":
        why = r["flags"][0] if r["flags"] else "unverified: " + ", ".join(r["unverified_mandatory"])
        return f"Held for human review ({why}). Score withheld; would be {r['shadow_score']} if cleared."
    weights = {c["id"]: c["weight"] for c in criteria["criteria"] if c["type"] == "weighted"}
    strong = [names[k] for k, p in r["breakdown"].items() if p >= 0.8 * weights[k]]
    weak = [names[k] for k, p in r["breakdown"].items() if p < 0.4 * weights[k]]
    text = f"{r['score']}/100"
    if strong:
        text += "; strongest: " + ", ".join(strong)
    if weak:
        text += "; weakest: " + ", ".join(weak)
    if r["warnings"]:
        text += f"; {len(r['warnings'])} note(s) for reviewer"
    return text + "."


def rank(results: list[dict]) -> list[dict]:
    ranked = sorted((r for r in results if r["status"] == "ranked"),
                    key=lambda r: (-r["score"], r["facts"].get("total_price_cad") or 0, r["proposal"]))
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
    for r in results:
        r.setdefault("rank", None)
    order = {"ranked": 0, "flagged": 1, "ineligible": 2}
    return sorted(results, key=lambda r: (order[r["status"]], r["rank"] or 0, r["proposal"]))


# ---------- main ----------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true", help="call Claude on prototype/samples/*.txt")
    ap.add_argument("--criteria", type=Path, default=HERE / "criteria.json")
    ap.add_argument("--samples", type=Path, default=HERE / "samples")
    ap.add_argument("--eval-date", default="2026-10-29", help="date used for the experience window")
    ap.add_argument("--out", type=Path, default=HERE / "output")
    args = ap.parse_args()

    criteria = load_criteria(args.criteria)
    system, template = load_prompts()
    eval_year = dt.date.fromisoformat(args.eval_date).year
    args.out.mkdir(parents=True, exist_ok=True)

    if args.live:
        files = sorted(args.samples.glob("*.txt"))
        if not files:
            sys.exit(f"No proposal text files in {args.samples}")
        inputs = {f.stem: f.read_text(encoding="utf-8") for f in files}
        extractions = {pid: extract_live(pid, t, criteria, system, template, args.out / "cache")
                       for pid, t in inputs.items()}
    else:
        fixtures = json.loads((HERE / "fixtures" / "extractions.json").read_text(encoding="utf-8"))
        extractions = {k: v for k, v in fixtures.items() if not k.startswith("_")}
        inputs = {pid: None for pid in extractions}

    results = rank([evaluate(pid, extractions[pid], inputs[pid], criteria, eval_year) for pid in extractions])
    ranking = [{"proposal": r["proposal"], "rank": r["rank"], "status": r["status"],
                "score": r["score"], "reason": reason(r, criteria)} for r in results]
    (args.out / "ranking.json").write_text(json.dumps(ranking, indent=2), encoding="utf-8")

    run = {
        "run_id": str(uuid.uuid4()),
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "mode": "live" if args.live else "offline-fixtures",
        "model": MODEL if args.live else None,
        "prompt_version": PROMPT_VERSION,
        "prompt_hash": sha256(system + template),
        "criteria_category": criteria["category_id"],
        "criteria_version": criteria["version"],
        "criteria_hash": sha256(json.dumps(criteria, sort_keys=True)),
        "input_hashes": {pid: sha256(t) for pid, t in inputs.items() if t is not None},
        "eval_date": args.eval_date,
        "results": results,
        "human_decision": None,  # filled in by the reviewer step (confirm / override + reason)
    }
    with (args.out / "audit_log.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(run) + "\n")

    print(f"{'Prop':<5}{'Status':<11}{'Rank':<6}{'Score':<7}Reason")
    for row in ranking:
        print(f"{row['proposal']:<5}{row['status']:<11}{str(row['rank'] or '-'):<6}"
              f"{str(row['score'] if row['score'] is not None else '-'):<7}{row['reason']}")
    print("\nPer-criterion points (reviewer view):")
    for r in results:
        print(f"  {r['proposal']}: {r['breakdown']}  gates={r['gates']}")
        for w in r["flags"] + r["warnings"]:
            print(f"     - {w}")
    print(f"\nWrote {args.out / 'ranking.json'} and appended run {run['run_id'][:8]} to audit_log.jsonl")


if __name__ == "__main__":
    main()
