"""
bench.build_criteria — STEP 2: segment frozen trials into atomic criteria.

Reads the frozen snapshots, runs trialbridge.split_criteria over each trial's
eligibility blob, tags each criterion with a type, and writes one record per
atomic criterion with provenance back to its trial.

Run:
    python -m bench.build_criteria
    python -m bench.build_criteria --sample 40
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trialbridge import CTGovClient, classify_ctype, split_criteria  # noqa: E402

from . import config, store  # noqa: E402
from .pull_trials import latest_snapshots  # noqa: E402


def build(cohorts: List[str] = None, sample_n: int = 25, seed: int = 7) -> List[Dict[str, Any]]:
    snaps = latest_snapshots()
    if not snaps:
        raise SystemExit("no frozen snapshots. Run: python -m bench.pull_trials")

    cohorts = cohorts or sorted(snaps)
    records: List[Dict[str, Any]] = []

    for cohort in cohorts:
        path = snaps.get(cohort)
        if not path:
            print(f"[criteria] no snapshot for cohort '{cohort}', skipping")
            continue
        client = CTGovClient.from_snapshot(path)
        trials = client._snapshot
        print(f"[criteria] {cohort}: {len(trials)} trials from {os.path.basename(path)}")
        for tr in trials:
            crits = split_criteria(tr.eligibility_criteria)
            for i, c in enumerate(crits):
                ctype = c.ctype
                if str(ctype) == "other":
                    ctype = classify_ctype(c.text)
                records.append(
                    {
                        "criterion_id": f"{tr.nct_id}-c{i:03d}",
                        "nct_id": tr.nct_id,
                        "trial_title": tr.title,
                        "cohort": cohort,
                        "text": c.text,
                        "kind": str(c.kind),
                        "ctype": str(ctype),
                        "index": i,
                        "compound": bool(c.compound),
                        "source_snapshot": os.path.basename(path),
                    }
                )

    store.write_jsonl(config.CRITERIA_FILE, records)
    print(f"[criteria] wrote {len(records)} atomic criteria -> {config.CRITERIA_FILE}")

    by_ct: Dict[str, int] = {}
    by_kind: Dict[str, int] = {}
    n_compound = 0
    for r in records:
        by_ct[r["ctype"]] = by_ct.get(r["ctype"], 0) + 1
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
        n_compound += 1 if r["compound"] else 0

    print("\n--- criterion type distribution ---")
    for ct, n in sorted(by_ct.items(), key=lambda x: -x[1]):
        print(f"  {ct:14s} {n:5d}")
    print(f"  {'(inclusion)':14s} {by_kind.get('inclusion', 0):5d}")
    print(f"  {'(exclusion)':14s} {by_kind.get('exclusion', 0):5d}")
    print(f"  flagged compound: {n_compound} ({n_compound / max(1, len(records)):.1%})")

    rng = random.Random(seed)
    sample = rng.sample(records, min(sample_n, len(records)))
    lines = [
        "# Segmentation spot-check",
        "",
        "Read these. If a line is not a single testable condition, or the kind/type is",
        "wrong, fix the splitter in trialbridge.py (`split_criteria` / `classify_ctype`)",
        "and re-run. Report the error rate you find in the papers' Methods.",
        "",
    ]
    for r in sample:
        lines.append(
            f"- [{r['kind']}/{r['ctype']}]{' [COMPOUND]' if r['compound'] else ''} "
            f"({r['criterion_id']})\n    {r['text']}"
        )
    sc = os.path.join(config.WORK_DIR, "segmentation_spotcheck.md")
    store.write_text(sc, "\n".join(lines) + "\n")
    print(f"\n[criteria] spot-check sample -> {sc}  (READ IT)")
    print("\nNext: python -m bench.patients --build")
    return records


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Segment frozen trials into atomic criteria.")
    ap.add_argument("--cohort", action="append")
    ap.add_argument("--sample", type=int, default=25, help="spot-check sample size")
    args = ap.parse_args(argv)
    config.ensure_dirs()
    build(cohorts=args.cohort, sample_n=args.sample)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
