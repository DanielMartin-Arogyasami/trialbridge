"""
bench.run_eval — THE END-TO-END RUN.

For each backend x arm: extract -> judge -> faithfulness gate -> predictions.
Resumable: finished pair_ids are skipped.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trialbridge import (  # noqa: E402
    Criterion, CriterionJudgment, Ctype, Kind, Label, PatientProfile,
    apply_faithfulness_gate, extract_profile, get_backend,
)
from . import config, store  # noqa: E402


def _pred_path(backend: str, model: str, arm: str) -> str:
    tag = f"{backend}-{model}".replace(":", "_").replace("/", "_") if model else backend
    return os.path.join(config.PRED_DIR, f"{tag}__{arm}.jsonl")


def _extract_path(backend: str, model: str, arm: str) -> str:
    tag = f"{backend}-{model}".replace(":", "_").replace("/", "_") if model else backend
    return os.path.join(config.PRED_DIR, f"extraction__{tag}__{arm}.jsonl")


def score_extraction(gold: Dict[str, Any], got: PatientProfile) -> Dict[str, Any]:
    """Field-level extraction fidelity, including correct-null behaviour."""
    g = dict(gold)
    p = got.to_dict(include_note=False)
    scalar_fields = ["age", "sex", "diagnosis", "stage", "ecog",
                     "prior_lines", "measurable_disease", "tissue_available"]
    res: Dict[str, Any] = {}
    tp = fp = fn = correct_null = null_ops = 0
    for f in scalar_fields:
        gv, pv = g.get(f), p.get(f)
        if gv is None:
            null_ops += 1
            if pv is None:
                correct_null += 1
                res[f] = "correct_null"
            else:
                fp += 1
                res[f] = "hallucinated"
            continue
        if pv is None:
            fn += 1
            res[f] = "missed"
        elif str(gv).lower().strip() in str(pv).lower().strip() or \
                str(pv).lower().strip() in str(gv).lower().strip():
            tp += 1
            res[f] = "ok"
        else:
            fp += 1
            res[f] = f"wrong({pv} != {gv})"

    def _bm(d):
        return {(b.get("gene", "").upper(), b.get("status", "present"))
                for b in (d.get("biomarkers") or [])}

    gb, pb = _bm(g), _bm(p)
    bm_tp = len(gb & pb)
    bm_fp = len(pb - gb)
    bm_fn = len(gb - pb)
    tp += bm_tp
    fp += bm_fp
    fn += bm_fn
    prec = tp / (tp + fp) if (tp + fp) else None
    rec = tp / (tp + fn) if (tp + fn) else None
    return {
        "precision": prec,
        "recall": rec,
        "tp": tp, "fp": fp, "fn": fn,
        "correct_null_rate": (correct_null / null_ops) if null_ops else None,
        "biomarker_tp": bm_tp, "biomarker_fp": bm_fp, "biomarker_fn": bm_fn,
        "fields": res,
    }


def run(backend_name: str, model: str = None, arms: List[str] = None,
        limit: int = None, max_pairs_per_patient: int = None) -> int:
    arms = arms or config.ARMS
    patients = {p["patient_id"]: p for p in store.read_jsonl(config.PATIENTS_FILE)}
    criteria = {c["criterion_id"]: c for c in store.read_jsonl(config.CRITERIA_FILE)}
    annotations = store.read_jsonl(config.ANNOTATIONS_FILE)
    if not annotations:
        raise SystemExit(
            "no annotations. The benchmark needs gold labels before it can grade anything.\n"
            "Run: python -m bench.autolabel && python -m bench.annotate --review"
        )

    backend = get_backend(backend_name, model)
    print(f"[eval] backend={backend_name} model={model or '(default)'}")

    for arm in arms:
        gold = [a for a in annotations if a["arm"] == arm]
        if not gold:
            print(f"[eval] no gold for arm '{arm}', skipping")
            continue
        ppath = _pred_path(backend_name, model, arm)
        epath = _extract_path(backend_name, model, arm)
        done = {r["pair_id"] for r in store.read_jsonl(ppath)}
        todo = [a for a in gold if a["pair_id"] not in done]
        if limit:
            todo = todo[:limit]
        print(f"[eval] arm={arm}: {len(todo)} to judge ({len(done)} already done)")
        if not todo:
            continue

        profiles: Dict[str, PatientProfile] = {}
        ex_done = {r["patient_id"] for r in store.read_jsonl(epath)}
        for pid in sorted({a["patient_id"] for a in todo}):
            note = patients[pid]["notes"][arm]
            t0 = time.time()
            prof = extract_profile(note, backend)
            profiles[pid] = prof
            if pid not in ex_done:
                fid = score_extraction(patients[pid]["profile"], prof)
                fid.update({"patient_id": pid, "arm": arm,
                            "backend": backend_name, "model": model or "",
                            "seconds": round(time.time() - t0, 2)})
                store.append_jsonl(epath, fid)

        t_start = time.time()
        for i, a in enumerate(todo, 1):
            c = criteria.get(a["criterion_id"])
            if not c:
                continue
            crit = Criterion(text=c["text"], kind=Kind(c["kind"]), ctype=Ctype(c["ctype"]))
            prof = profiles[a["patient_id"]]
            t0 = time.time()
            try:
                raw = backend.judge(prof.to_dict(), crit.text, crit.kind, crit.ctype)
            except Exception as exc:
                print(f"  ! judge failed on {a['pair_id']}: {exc}")
                continue
            label_raw = str(raw.get("label", "UNCERTAIN")).upper()
            if label_raw not in Label.__members__:
                label_raw = "UNCERTAIN"
            pre = CriterionJudgment(
                criterion=crit,
                label=Label[label_raw],
                evidence_span=str(raw.get("evidence_span", "") or ""),
                rationale=str(raw.get("rationale", "") or ""),
                confidence=float(raw.get("confidence", 0.5) or 0.0),
            )
            post = apply_faithfulness_gate(pre, prof)
            store.append_jsonl(
                ppath,
                {
                    "pair_id": a["pair_id"],
                    "patient_id": a["patient_id"],
                    "criterion_id": a["criterion_id"],
                    "arm": arm,
                    "ctype": c["ctype"],
                    "kind": c["kind"],
                    "backend": backend_name,
                    "model": model or "",
                    "pred_label": str(post.label),
                    "pred_label_pregate": str(pre.label),
                    "gate_downgraded": post.label != pre.label,
                    "evidence_span": post.evidence_span,
                    "confidence": round(post.confidence, 3),
                    "rationale": post.rationale[:400],
                    "gold_label": a["label"],
                    "seconds": round(time.time() - t0, 2),
                },
            )
            if i % 10 == 0 or i == len(todo):
                el = time.time() - t_start
                rate = el / i
                print(f"  {i}/{len(todo)}  {rate:.1f}s/criterion  "
                      f"eta {(len(todo) - i) * rate / 60:.1f} min")
        print(f"[eval] arm={arm} -> {ppath}")

    print("\nNext: python -m bench.metrics && python -m bench.make_tables")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run backends over the benchmark.")
    ap.add_argument("--backend", default="heuristic")
    ap.add_argument("--model", default=None)
    ap.add_argument("--arm", action="append", choices=config.ARMS)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--all", action="store_true", help="run every backend in config.EVAL_BACKENDS")
    args = ap.parse_args(argv)
    config.ensure_dirs()
    if args.all:
        for name, model, _label in config.EVAL_BACKENDS:
            try:
                run(name, model, arms=args.arm, limit=args.limit)
            except Exception as exc:
                print(f"[eval] backend {name} failed: {exc}", file=sys.stderr)
                print("       (is ollama running? `ollama serve` / `ollama pull medgemma:4b`)",
                      file=sys.stderr)
        return 0
    return run(args.backend, args.model, arms=args.arm, limit=args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
