"""
bench.annotate — STEP 5: human gold annotation.

Modes: --review (A1), --second (blinded A2), --iaa (Cohen's kappa).
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from . import config, store  # noqa: E402

LABELS = {"1": "ELIGIBLE", "2": "INELIGIBLE", "3": "UNCERTAIN",
          "e": "ELIGIBLE", "i": "INELIGIBLE", "u": "UNCERTAIN"}


def _load_notes() -> Dict[str, Dict[str, str]]:
    return {p["patient_id"]: p["notes"] for p in store.read_jsonl(config.PATIENTS_FILE)}


def _done_ids(path: str) -> set:
    return {a["pair_id"] for a in store.read_jsonl(path)}


def _banner(txt: str) -> None:
    print("\n" + "=" * 72)
    print(txt)
    print("=" * 72)


def review(only_proposed: bool = False, ctype: Optional[str] = None,
           arm: Optional[str] = None, limit: Optional[int] = None,
           annotator: str = "A1", out_path: Optional[str] = None,
           blind: bool = False) -> int:
    out_path = out_path or config.ANNOTATIONS_FILE
    pairs = store.read_jsonl(config.PAIRS_FILE)
    if not pairs:
        raise SystemExit("no pairs. Run: python -m bench.autolabel")
    props = {p["pair_id"]: p for p in store.read_jsonl(config.PROPOSALS_FILE)}
    notes = _load_notes()
    done = _done_ids(out_path)

    queue = [p for p in pairs if p["pair_id"] not in done]
    if only_proposed:
        queue = [p for p in queue if p["pair_id"] in props]
    if ctype:
        queue = [p for p in queue if p["ctype"] == ctype]
    if arm:
        queue = [p for p in queue if p["arm"] == arm]
    if limit:
        queue = queue[:limit]
    if not queue:
        print("nothing left to annotate with those filters.")
        return 0

    _banner(f"ANNOTATION  ({annotator})   {len(queue)} pair(s) queued"
            + ("   [BLIND: no proposals shown]" if blind else ""))
    print("Labels:  1/e = ELIGIBLE   2/i = INELIGIBLE   3/u = UNCERTAIN")
    print("Other:   s = skip   q = save and quit   ? = show full note")
    print("\nUNCERTAIN means the NOTE lacks the information to decide -- it is a")
    print("real label, not a missing value. Use it deliberately.\n")

    n_saved = 0
    for i, pair in enumerate(queue, 1):
        note = notes.get(pair["patient_id"], {}).get(pair["arm"], "")
        prop = props.get(pair["pair_id"]) if not blind else None
        print("-" * 72)
        print(f"[{i}/{len(queue)}] {pair['patient_id']}  arm={pair['arm']}  "
              f"{pair['kind']}/{pair['ctype']}  ({pair['nct_id']})")
        print(f"\nNOTE ({pair['arm']}):\n{note.strip()}\n")
        print(f"CRITERION [{pair['kind']}]:\n  {pair['criterion_text']}\n")
        if prop:
            print(f"  proposed: {prop['proposed_label']}   ({prop['derivation']})")
        default = prop["proposed_label"] if prop else None
        while True:
            hint = f" [Enter={default}]" if default else ""
            raw = input(f"label{hint} > ").strip().lower()
            if raw == "q":
                print(f"\nsaved {n_saved} annotation(s) -> {out_path}")
                return 0
            if raw == "s":
                label = None
                break
            if raw == "?":
                print("\n" + note + "\n")
                continue
            if raw == "" and default:
                label = default
                break
            if raw in LABELS:
                label = LABELS[raw]
                break
            print("  ? use 1/2/3 (or e/i/u), s=skip, q=quit")
        if label is None:
            continue
        span = input("evidence span (verbatim from note, blank if none) > ").strip()
        if span and span not in note:
            print("  ! that text is not verbatim in the note -- storing empty span instead.")
            print("    (spans must be exact substrings; the integrity check enforces this)")
            span = ""
        store.append_jsonl(
            out_path,
            store.annotation_record(
                pair_id=pair["pair_id"],
                patient_id=pair["patient_id"],
                criterion_id=pair["criterion_id"],
                arm=pair["arm"],
                label=label,
                evidence_span=span,
                difficulty=(prop or {}).get("difficulty", "medium"),
                annotator_id=annotator,
                provenance="human",
                note=("confirmed derived proposal"
                      if prop and label == prop["proposed_label"] else ""),
            ),
        )
        n_saved += 1

    print(f"\ndone. saved {n_saved} annotation(s) -> {out_path}")
    print("\nNext: python -m bench.build_dataset")
    return 0


def second_pass(slice_frac: float = 0.2, seed: int = 11) -> int:
    pairs = store.read_jsonl(config.PAIRS_FILE)
    primary = _done_ids(config.ANNOTATIONS_FILE)
    eligible = [p for p in pairs if p["pair_id"] in primary]
    if not eligible:
        raise SystemExit("annotate with --review first; A2 re-labels a slice of A1's pairs.")
    rng = random.Random(seed)
    k = max(1, int(len(eligible) * slice_frac))
    chosen = set(p["pair_id"] for p in rng.sample(eligible, k))
    subset = [p for p in pairs if p["pair_id"] in chosen]
    store.write_jsonl(os.path.join(config.WORK_DIR, "_a2_queue.jsonl"), subset)
    print(f"[a2] blinded slice: {k} pair(s) ({slice_frac:.0%} of A1's work)")
    print("[a2] A2 sees NO proposals and NO A1 labels.\n")
    orig = config.PAIRS_FILE
    try:
        config.PAIRS_FILE = os.path.join(config.WORK_DIR, "_a2_queue.jsonl")
        return review(annotator="A2", out_path=config.ANNOTATIONS_A2_FILE, blind=True)
    finally:
        config.PAIRS_FILE = orig


def cohens_kappa(a: List[str], b: List[str]) -> float:
    labels = sorted(set(a) | set(b))
    n = len(a)
    if n == 0:
        return float("nan")
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pe = 0.0
    for lab in labels:
        pa = sum(1 for x in a if x == lab) / n
        pb = sum(1 for y in b if y == lab) / n
        pe += pa * pb
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def iaa() -> int:
    a1 = {a["pair_id"]: a for a in store.read_jsonl(config.ANNOTATIONS_FILE)}
    a2 = {a["pair_id"]: a for a in store.read_jsonl(config.ANNOTATIONS_A2_FILE)}
    shared = sorted(set(a1) & set(a2))
    if not shared:
        raise SystemExit("no overlapping pairs. Run: python -m bench.annotate --second")
    crit = {c["criterion_id"]: c for c in store.read_jsonl(config.CRITERIA_FILE)}
    x = [a1[p]["label"] for p in shared]
    y = [a2[p]["label"] for p in shared]
    _banner("INTER-ANNOTATOR AGREEMENT")
    print(f"overlapping pairs : {len(shared)}")
    print(f"raw agreement     : {sum(1 for i, j in zip(x, y) if i == j) / len(shared):.3f}")
    print(f"Cohen's kappa     : {cohens_kappa(x, y):.3f}")

    by_ct: Dict[str, Any] = {}
    for p in shared:
        ct = crit.get(a1[p]["criterion_id"], {}).get("ctype", "other")
        by_ct.setdefault(ct, [[], []])
        by_ct[ct][0].append(a1[p]["label"])
        by_ct[ct][1].append(a2[p]["label"])
    print("\nby criterion type (n < 20 -> treat kappa as indicative only):")
    rows = []
    for ct, (xs, ys) in sorted(by_ct.items()):
        k = cohens_kappa(xs, ys)
        flag = "  (low n)" if len(xs) < 20 else ""
        print(f"  {ct:14s} n={len(xs):4d}  kappa={k:6.3f}{flag}")
        rows.append({"ctype": ct, "n": len(xs), "kappa": round(k, 3)})

    both = [(a1[p].get("evidence_span", ""), a2[p].get("evidence_span", "")) for p in shared]
    both = [(s1, s2) for s1, s2 in both if s1 or s2]
    if both:
        exact = sum(1 for s1, s2 in both if s1.strip() == s2.strip()) / len(both)
        overlap = sum(1 for s1, s2 in both if s1 and s2 and (s1 in s2 or s2 in s1)) / len(both)
        print(f"\nspan exact match  : {exact:.3f}  (n={len(both)})")
        print(f"span containment  : {overlap:.3f}")

    store.write_json(
        os.path.join(config.RESULTS_DIR, "iaa.json"),
        {"n_shared": len(shared),
         "raw_agreement": sum(1 for i, j in zip(x, y) if i == j) / len(shared),
         "cohens_kappa": cohens_kappa(x, y),
         "by_ctype": rows},
    )
    print(f"\nwrote {os.path.join(config.RESULTS_DIR, 'iaa.json')}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Human gold annotation.")
    ap.add_argument("--review", action="store_true", help="primary annotator pass")
    ap.add_argument("--second", action="store_true", help="blinded second-annotator pass")
    ap.add_argument("--iaa", action="store_true", help="compute inter-annotator agreement")
    ap.add_argument("--only-proposed", action="store_true")
    ap.add_argument("--ctype")
    ap.add_argument("--arm", choices=config.ARMS)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--slice", type=float, default=0.2)
    args = ap.parse_args(argv)
    config.ensure_dirs()
    if args.iaa:
        return iaa()
    if args.second:
        return second_pass(slice_frac=args.slice)
    if args.review:
        return review(only_proposed=args.only_proposed, ctype=args.ctype,
                      arm=args.arm, limit=args.limit)
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
