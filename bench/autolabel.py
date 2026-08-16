"""
bench.autolabel — STEP 4: build (patient x criterion) pairs and PROPOSE gold labels.

Proposals derive from the patient SPEC (ground truth by construction), never from
model output. Records are written provenance="derived"; only human confirmation
makes them releasable.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from . import config, store  # noqa: E402
from .patients import PatientSpec, load_specs  # noqa: E402

ELIGIBLE, INELIGIBLE, UNCERTAIN = "ELIGIBLE", "INELIGIBLE", "UNCERTAIN"
Proposal = Tuple[str, str, str]


def _gene_status(spec: PatientSpec, gene: str) -> Optional[str]:
    if not spec.ngs_performed:
        return None
    for b in spec.biomarkers:
        if b["gene"].upper() == gene.upper():
            return b.get("status", "present")
    return None


_GENES = ["EGFR", "ALK", "ROS1", "KRAS", "BRAF", "NRAS", "TP53", "MET", "RET",
          "HER2", "ERBB2", "BRCA1", "BRCA2", "PIK3CA", "PD-L1"]


def _genes_in(text: str) -> List[str]:
    up = text.upper()
    return [g for g in _GENES if re.search(r"\b" + re.escape(g) + r"\b", up)]


def _num(pattern: str, text: str) -> Optional[float]:
    m = re.search(pattern, text, re.I)
    return float(m.group(1)) if m else None


def _finish(satisfied: Optional[bool], kind: str, why: str,
            difficulty: str = "easy") -> Optional[Proposal]:
    if satisfied is None:
        return None
    if kind == "inclusion":
        return (ELIGIBLE if satisfied else INELIGIBLE, why, difficulty)
    return (INELIGIBLE if satisfied else ELIGIBLE, why, difficulty)


def propose(spec: PatientSpec, text: str, kind: str, ctype: str) -> Optional[Proposal]:
    low = text.lower()

    if ctype == "demographic" or re.search(r"\bage[ds]?\b|\byears? of age\b", low):
        lo = _num(r"(?:at least|>=|≥|older than|minimum(?: age)? of)\s*(\d{1,3})\s*year", low)
        if lo is None:
            lo = _num(r"(\d{1,3})\s*years?\s*(?:of age )?or older", low)
        hi = _num(r"(?:<=|≤|no older than|under|younger than|up to)\s*(\d{1,3})\s*year", low)
        if lo is not None or hi is not None:
            ok = True
            if lo is not None:
                ok = ok and spec.age >= lo
            if hi is not None:
                ok = ok and spec.age <= hi
            return _finish(ok, kind, f"age {spec.age} vs bounds lo={lo} hi={hi}")

    if "ecog" in low or "performance status" in low or "karnofsky" in low:
        if "karnofsky" in low:
            return None
        rng = re.search(r"(\d)\s*(?:-|to|–)\s*(\d)", low)
        single = re.search(r"(?:of|≤|<=|less than or equal to)\s*(\d)", low)
        if rng:
            lo, hi = int(rng.group(1)), int(rng.group(2))
            return _finish(lo <= spec.ecog <= hi, kind, f"ECOG {spec.ecog} vs {lo}-{hi}")
        if single:
            hi = int(single.group(1))
            return _finish(spec.ecog <= hi, kind, f"ECOG {spec.ecog} vs <= {hi}")
        return None

    if ctype == "molecular" or _genes_in(text) or "hla" in low:
        if "hla" in low:
            if not spec.hla_tested:
                return (UNCERTAIN, "HLA typing not performed -- cannot decide", "medium")
            m = re.search(r"hla[- ]?([abc])\s*\*?\s*(\d{2}:?\d{2})", low)
            if m and spec.hla:
                want = (f"HLA-{m.group(1).upper()}*"
                        f"{m.group(2) if ':' in m.group(2) else m.group(2)[:2] + ':' + m.group(2)[2:]}")
                has = any(h.upper().replace(" ", "") == want.upper() for h in spec.hla)
                return _finish(has, kind, f"HLA {spec.hla} vs required {want}", "medium")
            return None
        genes = _genes_in(text)
        if not genes:
            return None
        if not spec.ngs_performed:
            return (UNCERTAIN, "no NGS performed -- molecular status unknown", "medium")
        statuses = {g: _gene_status(spec, g) for g in genes}
        if any(v is None for v in statuses.values()):
            return (UNCERTAIN, f"gene(s) not on the report: "
                               f"{[g for g, v in statuses.items() if v is None]}", "medium")
        wild = bool(re.search(r"wild[- ]?type|negative|absence of|without", low))
        present = any(v == "present" for v in statuses.values())
        satisfied = (not present) if wild else present
        return _finish(satisfied, kind,
                       f"{statuses} (criterion asks for {'wild-type' if wild else 'alteration'})",
                       "medium")

    if ctype == "lab":
        checks = [
            ("ANC", r"(?:anc|absolute neutrophil count)\D{0,20}?([\d.]+)", "min"),
            ("Platelets", r"platelet\D{0,25}?([\d,]+)", "min"),
            ("Creatinine", r"creatinine\D{0,25}?([\d.]+)", "max"),
            ("Bilirubin", r"bilirubin\D{0,25}?([\d.]+)", "max"),
        ]
        for labname, pat, direction in checks:
            m = re.search(pat, low)
            if not m or labname not in spec.labs:
                continue
            try:
                thresh = float(m.group(1).replace(",", ""))
            except ValueError:
                continue
            val = spec.labs[labname]
            if labname == "Platelets" and thresh > 1000:
                thresh = thresh / 1000.0
            ok = val >= thresh if direction == "min" else val <= thresh
            return _finish(ok, kind, f"{labname} {val} vs {direction} {thresh}", "medium")
        return None

    if ctype == "prior_therapy" or "prior line" in low or "prior systemic" in low:
        mx = _num(r"(?:no more than|at most|<=|≤|maximum of|up to)\s*(\d)\s*prior", low)
        mn = _num(r"(?:at least|minimum of|>=|≥)\s*(\d)\s*prior", low)
        if mx is not None:
            return _finish(spec.prior_lines <= mx, kind,
                           f"{spec.prior_lines} prior lines vs max {int(mx)}")
        if mn is not None:
            return _finish(spec.prior_lines >= mn, kind,
                           f"{spec.prior_lines} prior lines vs min {int(mn)}")
        if re.search(r"treatment[- ]na(i|ï)ve|no prior systemic", low):
            return _finish(spec.prior_lines == 0, kind,
                           f"{spec.prior_lines} prior lines vs treatment-naive")
        return None

    if "brain metasta" in low or "cns metasta" in low or "leptomeningeal" in low:
        active_wanted = bool(re.search(r"active|untreated|symptomatic|progressing", low))
        if active_wanted:
            return _finish(spec.brain_mets == "active", kind,
                           f"brain_mets={spec.brain_mets} vs 'active/untreated'", "medium")
        return _finish(spec.brain_mets != "none", kind,
                       f"brain_mets={spec.brain_mets} vs 'any brain mets'", "medium")

    if "measurable disease" in low or "recist" in low:
        if spec.measurable_disease is None:
            return (UNCERTAIN, "measurable disease not documented", "medium")
        return _finish(spec.measurable_disease, kind,
                       f"measurable_disease={spec.measurable_disease}")

    if re.search(r"tumor tissue|archival|ffpe|tissue (?:sample|specimen|available)", low):
        if spec.tissue_available is None:
            return (UNCERTAIN, "tissue availability not documented", "medium")
        return _finish(spec.tissue_available, kind,
                       f"tissue_available={spec.tissue_available}", "medium")

    for virus in ("hiv", "hepatitis b", "hepatitis c", "hbv", "hcv"):
        if virus in low:
            key = {"hiv": "HIV", "hepatitis b": "HBV", "hbv": "HBV",
                   "hepatitis c": "HCV", "hcv": "HCV"}[virus]
            val = spec.serologies.get(key)
            if val is None:
                return (UNCERTAIN, f"{key} serology not documented", "medium")
            return _finish(val.lower() == "positive", kind, f"{key}={val}", "medium")

    return None


def build_pairs(per_trial: int = 12, trials_per_patient: int = 3) -> List[Dict[str, Any]]:
    patients = store.read_jsonl(config.PATIENTS_FILE)
    criteria = store.read_jsonl(config.CRITERIA_FILE)
    if not patients:
        raise SystemExit("no patients. Run: python -m bench.patients --build")
    if not criteria:
        raise SystemExit("no criteria. Run: python -m bench.build_criteria")

    specs = load_specs()
    by_cohort: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    for c in criteria:
        by_cohort.setdefault(c["cohort"], {}).setdefault(c["nct_id"], []).append(c)

    pairs: List[Dict[str, Any]] = []
    proposals: List[Dict[str, Any]] = []

    for p in patients:
        pid = p["patient_id"]
        spec = specs.get(pid)
        if spec is None:
            print(f"[autolabel] WARNING: no spec for {pid}; skipping")
            continue
        trials = by_cohort.get(p["cohort"], {})
        for nct in sorted(trials)[:trials_per_patient]:
            for c in sorted(trials[nct], key=lambda x: x["index"])[:per_trial]:
                for arm in config.ARMS:
                    pair_id = store.make_pair_id(pid, c["criterion_id"], arm)
                    pairs.append(
                        {
                            "pair_id": pair_id,
                            "patient_id": pid,
                            "criterion_id": c["criterion_id"],
                            "nct_id": nct,
                            "arm": arm,
                            "criterion_text": c["text"],
                            "kind": c["kind"],
                            "ctype": c["ctype"],
                        }
                    )
                    prop = propose(spec, c["text"], c["kind"], c["ctype"])
                    if prop is not None:
                        label, why, diff = prop
                        proposals.append(
                            {
                                "pair_id": pair_id,
                                "patient_id": pid,
                                "criterion_id": c["criterion_id"],
                                "arm": arm,
                                "proposed_label": label,
                                "derivation": why,
                                "difficulty": diff,
                                "criterion_text": c["text"],
                                "kind": c["kind"],
                                "ctype": c["ctype"],
                            }
                        )

    store.write_jsonl(config.PAIRS_FILE, pairs)
    store.write_jsonl(config.PROPOSALS_FILE, proposals)
    print(f"[autolabel] {len(pairs)} pairs -> {config.PAIRS_FILE}")
    print(f"[autolabel] {len(proposals)} auto-derived proposals -> {config.PROPOSALS_FILE}")
    todo = len(pairs) - len(proposals)
    print(f"[autolabel] {todo} pairs ({todo / max(1, len(pairs)):.0%}) need a human label")
    _stats(proposals, len(pairs))
    print("\nNext: python -m bench.annotate --review")
    return pairs


def _stats(proposals: List[Dict[str, Any]], n_pairs: int = 0) -> None:
    counts: Dict[str, int] = {}
    for p in proposals:
        counts[p["proposed_label"]] = counts.get(p["proposed_label"], 0) + 1
    total = max(1, len(proposals))
    print("\n--- proposed label balance (proposals only) ---")
    for lab in (ELIGIBLE, INELIGIBLE, UNCERTAIN):
        n = counts.get(lab, 0)
        flag = ""
        floor = config.BALANCE_FLOOR.get(lab)
        if floor and n / total < floor:
            flag = f"  <-- below {floor:.0%} floor; author more patients that trigger it"
        print(f"  {lab:11s} {n:5d} ({n / total:5.1%}){flag}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build pairs and propose gold labels.")
    ap.add_argument("--per-trial", type=int, default=12)
    ap.add_argument("--trials-per-patient", type=int, default=3)
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args(argv)
    config.ensure_dirs()
    if args.stats:
        _stats(store.read_jsonl(config.PROPOSALS_FILE))
        return 0
    build_pairs(per_trial=args.per_trial, trials_per_patient=args.trials_per_patient)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
