"""
bench.metrics — turn predictions into the numbers the papers report.

Every estimate comes with a bootstrap CI CLUSTERED BY PATIENT.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from . import config, store  # noqa: E402

UNCERTAIN = "UNCERTAIN"


def bootstrap_ci(records: List[Dict[str, Any]], statistic, n: int = None,
                 seed: int = None, alpha: float = 0.05) -> Tuple[Optional[float],
                                                                Optional[float],
                                                                Optional[float]]:
    """Returns (point, lo, hi). Resamples PATIENTS with replacement."""
    n = n or config.BOOTSTRAP_N
    seed = seed if seed is not None else config.BOOTSTRAP_SEED
    if not records:
        return (None, None, None)
    point = statistic(records)
    by_pat: Dict[str, List[Dict[str, Any]]] = {}
    for r in records:
        by_pat.setdefault(r["patient_id"], []).append(r)
    pats = list(by_pat)
    if len(pats) < 2:
        return (point, None, None)
    rng = random.Random(seed)
    vals: List[float] = []
    for _ in range(n):
        boot: List[Dict[str, Any]] = []
        for _ in range(len(pats)):
            boot.extend(by_pat[rng.choice(pats)])
        v = statistic(boot)
        if v is not None:
            vals.append(v)
    if not vals:
        return (point, None, None)
    vals.sort()
    lo = vals[int((alpha / 2) * len(vals))]
    hi = vals[min(len(vals) - 1, int((1 - alpha / 2) * len(vals)))]
    return (point, lo, hi)


def stat_selective_accuracy(recs: List[Dict[str, Any]]) -> Optional[float]:
    dec = [r for r in recs if r["pred_label"] != UNCERTAIN]
    if not dec:
        return None
    return sum(1 for r in dec if r["pred_label"] == r["gold_label"]) / len(dec)


def stat_overall_accuracy(recs: List[Dict[str, Any]]) -> Optional[float]:
    if not recs:
        return None
    return sum(1 for r in recs if r["pred_label"] == r["gold_label"]) / len(recs)


def stat_coverage(recs: List[Dict[str, Any]]) -> Optional[float]:
    if not recs:
        return None
    return sum(1 for r in recs if r["pred_label"] != UNCERTAIN) / len(recs)


def stat_span_support(recs: List[Dict[str, Any]]) -> Optional[float]:
    dec = [r for r in recs if r["pred_label"] != UNCERTAIN]
    if not dec:
        return None
    return sum(1 for r in dec if (r.get("evidence_span") or "").strip()) / len(dec)


def stat_stub_baseline(recs: List[Dict[str, Any]]) -> Optional[float]:
    if not recs:
        return None
    return sum(1 for r in recs if r["gold_label"] == "ELIGIBLE") / len(recs)


def risk_coverage(recs: List[Dict[str, Any]], steps: int = 20) -> List[Dict[str, float]]:
    rs = sorted(recs, key=lambda r: r.get("confidence", 0.0), reverse=True)
    out = []
    for i in range(1, steps + 1):
        k = max(1, int(len(rs) * i / steps))
        sub = rs[:k]
        acc = sum(1 for r in sub if r["pred_label"] == r["gold_label"]) / len(sub)
        out.append({"coverage": k / len(rs), "accuracy": acc})
    return out


def load_predictions() -> Dict[Tuple[str, str], List[Dict[str, Any]]]:
    out: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    if not os.path.isdir(config.PRED_DIR):
        return out
    for fn in sorted(os.listdir(config.PRED_DIR)):
        if not fn.endswith(".jsonl") or fn.startswith("extraction__"):
            continue
        base = fn[:-6]
        if "__" not in base:
            continue
        tag, arm = base.rsplit("__", 1)
        recs = store.read_jsonl(os.path.join(config.PRED_DIR, fn))
        if recs:
            out[(tag, arm)] = recs
    return out


def load_extraction() -> Dict[Tuple[str, str], List[Dict[str, Any]]]:
    out: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    if not os.path.isdir(config.PRED_DIR):
        return out
    for fn in sorted(os.listdir(config.PRED_DIR)):
        if not fn.startswith("extraction__") or not fn.endswith(".jsonl"):
            continue
        base = fn[len("extraction__"):-6]
        tag, arm = base.rsplit("__", 1)
        out[(tag, arm)] = store.read_jsonl(os.path.join(config.PRED_DIR, fn))
    return out


def _fmt(point, lo, hi, pct=True) -> str:
    if point is None:
        return "n/a"
    if pct:
        s = f"{point:.1%}"
        if lo is not None:
            s += f" [{lo:.1%}, {hi:.1%}]"
    else:
        s = f"{point:.3f}"
        if lo is not None:
            s += f" [{lo:.3f}, {hi:.3f}]"
    return s


def compute_all() -> Dict[str, Any]:
    preds = load_predictions()
    if not preds:
        raise SystemExit("no predictions. Run: python -m bench.run_eval --backend heuristic")
    report: Dict[str, Any] = {"runs": {}, "paired_equity": {}, "extraction": {}}

    for (tag, arm), recs in sorted(preds.items()):
        key = f"{tag}::{arm}"
        entry: Dict[str, Any] = {"n_pairs": len(recs),
                                 "n_patients": len({r["patient_id"] for r in recs})}
        for name, fn in [("selective_accuracy", stat_selective_accuracy),
                         ("overall_accuracy", stat_overall_accuracy),
                         ("coverage", stat_coverage),
                         ("span_support", stat_span_support)]:
            p, lo, hi = bootstrap_ci(recs, fn)
            entry[name] = {"point": p, "lo": lo, "hi": hi}
        entry["abstention_rate"] = 1 - (stat_coverage(recs) or 0)
        entry["gate_downgrades"] = sum(1 for r in recs if r.get("gate_downgraded"))
        entry["always_eligible_stub"] = stat_stub_baseline(recs)

        by_ct: Dict[str, Any] = {}
        for ct in config.REPORT_CTYPES:
            sub = [r for r in recs if r.get("ctype") == ct]
            if not sub:
                continue
            p, lo, hi = bootstrap_ci(sub, stat_selective_accuracy)
            by_ct[ct] = {"n": len(sub), "point": p, "lo": lo, "hi": hi,
                         "underpowered": len({r['patient_id'] for r in sub}) < 5}
        entry["by_ctype"] = by_ct
        entry["risk_coverage"] = risk_coverage(recs)
        report["runs"][key] = entry

    tags = sorted({t for (t, _a) in preds})
    for tag in tags:
        clean = {r["pair_id"].rsplit("::", 1)[0]: r
                 for r in preds.get((tag, "clean"), [])}
        deg = {r["pair_id"].rsplit("::", 1)[0]: r
               for r in preds.get((tag, "degraded"), [])}
        shared = sorted(set(clean) & set(deg))
        if not shared:
            continue
        paired = [{"patient_id": clean[k]["patient_id"],
                   "ctype": clean[k].get("ctype", "other"),
                   "clean_ok": clean[k]["pred_label"] == clean[k]["gold_label"],
                   "deg_ok": deg[k]["pred_label"] == deg[k]["gold_label"],
                   "clean_abst": clean[k]["pred_label"] == UNCERTAIN,
                   "deg_abst": deg[k]["pred_label"] == UNCERTAIN}
                  for k in shared]

        def delta(rs):
            if not rs:
                return None
            return (sum(1 for r in rs if r["clean_ok"]) -
                    sum(1 for r in rs if r["deg_ok"])) / len(rs)

        p, lo, hi = bootstrap_ci(paired, delta)
        ent = {
            "n_paired": len(paired),
            "clean_accuracy": sum(1 for r in paired if r["clean_ok"]) / len(paired),
            "degraded_accuracy": sum(1 for r in paired if r["deg_ok"]) / len(paired),
            "delta": {"point": p, "lo": lo, "hi": hi},
            "clean_abstention": sum(1 for r in paired if r["clean_abst"]) / len(paired),
            "degraded_abstention": sum(1 for r in paired if r["deg_abst"]) / len(paired),
            "by_ctype": {},
        }
        for ct in config.REPORT_CTYPES:
            sub = [r for r in paired if r["ctype"] == ct]
            if not sub:
                continue
            dp, dlo, dhi = bootstrap_ci(sub, delta)
            ent["by_ctype"][ct] = {
                "n": len(sub),
                "clean": sum(1 for r in sub if r["clean_ok"]) / len(sub),
                "degraded": sum(1 for r in sub if r["deg_ok"]) / len(sub),
                "delta": {"point": dp, "lo": dlo, "hi": dhi},
            }
        report["paired_equity"][tag] = ent

    for (tag, arm), recs in sorted(load_extraction().items()):
        precs = [r["precision"] for r in recs if r.get("precision") is not None]
        anrecs = [r["recall"] for r in recs if r.get("recall") is not None]
        nulls = [r["correct_null_rate"] for r in recs if r.get("correct_null_rate") is not None]
        report["extraction"][f"{tag}::{arm}"] = {
            "n_patients": len(recs),
            "precision": sum(precs) / len(precs) if precs else None,
            "recall": sum(anrecs) / len(anrecs) if anrecs else None,
            "correct_null_rate": sum(nulls) / len(nulls) if nulls else None,
        }
    return report


def print_report(rep: Dict[str, Any]) -> None:
    print("\n" + "=" * 72)
    print("PER-RUN METRICS   (95% CI, bootstrap clustered by patient)")
    print("=" * 72)
    for key, e in sorted(rep["runs"].items()):
        print(f"\n{key}   n={e['n_pairs']} pairs / {e['n_patients']} patients")
        sa = e["selective_accuracy"]
        print(f"  selective accuracy : {_fmt(sa['point'], sa['lo'], sa['hi'])}")
        oa = e["overall_accuracy"]
        print(f"  overall accuracy   : {_fmt(oa['point'], oa['lo'], oa['hi'])}")
        cv = e["coverage"]
        print(f"  coverage           : {_fmt(cv['point'], cv['lo'], cv['hi'])}"
              f"   (abstention {e['abstention_rate']:.1%})")
        ss = e["span_support"]
        print(f"  span support       : {_fmt(ss['point'], ss['lo'], ss['hi'])}")
        print(f"  gate downgrades    : {e['gate_downgrades']}")
        stub = e["always_eligible_stub"]
        warn = "   <-- BENCHMARK TOO WEAK" if stub and stub > 0.6 else ""
        print(f"  always-ELIGIBLE stub: {stub:.1%}{warn}" if stub is not None else "")
        if e["by_ctype"]:
            print("  by criterion type:")
            for ct, v in sorted(e["by_ctype"].items()):
                flag = "  (underpowered)" if v.get("underpowered") else ""
                print(f"    {ct:14s} n={v['n']:4d}  {_fmt(v['point'], v['lo'], v['hi'])}{flag}")

    if rep["paired_equity"]:
        print("\n" + "=" * 72)
        print("EQUITY SLICE  (paired, same patients, clean vs degraded)")
        print("=" * 72)
        for tag, e in sorted(rep["paired_equity"].items()):
            d = e["delta"]
            print(f"\n{tag}   n={e['n_paired']} paired")
            print(f"  clean    accuracy : {e['clean_accuracy']:.1%}"
                  f"   (abstention {e['clean_abstention']:.1%})")
            print(f"  degraded accuracy : {e['degraded_accuracy']:.1%}"
                  f"   (abstention {e['degraded_abstention']:.1%})")
            print(f"  DELTA (clean-deg) : {_fmt(d['point'], d['lo'], d['hi'])}")
            if d["lo"] is not None and d["lo"] <= 0 <= d["hi"]:
                print("    -> CI includes 0: robustness NOT shown to differ. Report as such.")

    if rep["extraction"]:
        print("\n" + "=" * 72)
        print("EXTRACTION FIDELITY (Stage 1)")
        print("=" * 72)
        for key, e in sorted(rep["extraction"].items()):
            pr = f"{e['precision']:.1%}" if e["precision"] is not None else "n/a"
            rc = f"{e['recall']:.1%}" if e["recall"] is not None else "n/a"
            cn = f"{e['correct_null_rate']:.1%}" if e["correct_null_rate"] is not None else "n/a"
            print(f"  {key:38s} P={pr}  R={rc}  correct-null={cn}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Compute paper metrics.")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    config.ensure_dirs()
    rep = compute_all()
    path = os.path.join(config.RESULTS_DIR, "metrics.json")
    store.write_json(path, rep)
    if args.json:
        print(json.dumps(rep, indent=2)[:4000])
    else:
        print_report(rep)
    print(f"\nwrote {path}")
    print("\nNext: python -m bench.make_tables")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
