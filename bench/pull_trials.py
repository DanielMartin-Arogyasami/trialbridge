"""
bench.pull_trials — STEP 1: THE DATA PULL.

Pulls recruiting oncology trials from ClinicalTrials.gov API v2 and freezes them
as DATED SNAPSHOTS. Everything downstream reads the snapshot, never the live API,
so results are reproducible months later even as the registry changes.

Run:
    python -m bench.pull_trials                 # pull all cohorts in config.COHORTS
    python -m bench.pull_trials --cohort nsclc  # just one
    python -m bench.pull_trials --list          # show what is already frozen

Output:
    bench_out/trials/<cohort>_<YYYY-MM-DD>.json   frozen snapshot (commit these)
    bench_out/trials/manifest.json                provenance for the papers
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timezone
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trialbridge import COHORT_QUERIES, CTGovClient, Trial, split_criteria  # noqa: E402

from . import config, store  # noqa: E402


def pull_cohort(cohort: str, page_size: int = None, cache_dir: str = None) -> Dict[str, Any]:
    """Pull one cohort and freeze it. Returns a manifest entry."""
    page_size = page_size or config.TRIALS_PER_COHORT
    if cohort not in COHORT_QUERIES:
        raise SystemExit(
            f"unknown cohort '{cohort}'. Known: {sorted(COHORT_QUERIES)}\n"
            "Add new cohorts to COHORT_QUERIES in trialbridge.py."
        )
    query = COHORT_QUERIES[cohort]
    client = CTGovClient(cache_dir=cache_dir or os.path.join(config.OUT, "cache"))
    print(f"[pull] {cohort}: querying ClinicalTrials.gov ... {query}")
    trials: List[Trial] = client.search(query, page_size=page_size)
    print(f"[pull] {cohort}: {len(trials)} trials returned")

    usable = [t for t in trials if (t.eligibility_criteria or "").strip()]
    dropped = len(trials) - len(usable)
    if dropped:
        print(f"[pull] {cohort}: dropped {dropped} trial(s) with empty eligibility text")

    stamp = date.today().isoformat()
    path = os.path.join(config.TRIALS_DIR, f"{cohort}_{stamp}.json")
    client.save_snapshot(path, usable, query=query)
    print(f"[pull] {cohort}: frozen -> {path}")

    lens = [len(t.eligibility_criteria) for t in usable]
    crit_counts = [len(split_criteria(t.eligibility_criteria)) for t in usable]
    return {
        "cohort": cohort,
        "query": query,
        "snapshot": os.path.relpath(path, config.OUT),
        "pulled": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_trials": len(usable),
        "n_dropped_empty": dropped,
        "eligibility_chars": _stats(lens),
        "atomic_criteria_per_trial": _stats(crit_counts),
        "total_atomic_criteria": sum(crit_counts),
    }


def _stats(xs: List[int]) -> Dict[str, Any]:
    if not xs:
        return {"n": 0}
    s = sorted(xs)
    mid = len(s) // 2
    median = s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2
    return {"n": len(s), "min": s[0], "median": median, "max": s[-1]}


def latest_snapshots() -> Dict[str, str]:
    """Map cohort -> most recent frozen snapshot path."""
    out: Dict[str, str] = {}
    if not os.path.isdir(config.TRIALS_DIR):
        return out
    for fn in sorted(os.listdir(config.TRIALS_DIR)):
        if not fn.endswith(".json") or fn == "manifest.json":
            continue
        cohort = fn.rsplit("_", 1)[0]
        out[cohort] = os.path.join(config.TRIALS_DIR, fn)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Freeze ClinicalTrials.gov snapshots.")
    ap.add_argument("--cohort", action="append", help="cohort name (repeatable)")
    ap.add_argument("--page-size", type=int, default=config.TRIALS_PER_COHORT)
    ap.add_argument("--list", action="store_true", help="list frozen snapshots and exit")
    args = ap.parse_args(argv)

    config.ensure_dirs()

    if args.list:
        snaps = latest_snapshots()
        if not snaps:
            print("no frozen snapshots yet -- run: python -m bench.pull_trials")
        for c, p in snaps.items():
            print(f"{c:12s} {p}")
        return 0

    cohorts = args.cohort or config.COHORTS
    entries = []
    for c in cohorts:
        try:
            entries.append(pull_cohort(c, page_size=args.page_size))
        except Exception as exc:
            print(f"[pull] ERROR on cohort '{c}': {exc}", file=sys.stderr)
            print("       (offline? the API only needs plain HTTPS; retry later)", file=sys.stderr)

    if not entries:
        return 1

    manifest = {
        "dataset": "TrialBridge-Bench",
        "source": "ClinicalTrials.gov API v2 (https://clinicaltrials.gov/api/v2/studies)",
        "status_filter": "RECRUITING",
        "attribution": (
            "Trial records retrieved from ClinicalTrials.gov, U.S. National Library of "
            "Medicine. Free-text content is authored by trial sponsors/investigators, "
            "not NLM."
        ),
        "snapshot_notice": (
            "DATED SNAPSHOT. These records do not reflect the current contents of "
            "ClinicalTrials.gov. Consult the registry directly for current records."
        ),
        "built": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cohorts": entries,
        "total_trials": sum(e["n_trials"] for e in entries),
        "total_atomic_criteria": sum(e["total_atomic_criteria"] for e in entries),
    }
    mpath = os.path.join(config.TRIALS_DIR, "manifest.json")
    store.write_json(mpath, manifest)

    print("\n--- snapshot manifest ---")
    for e in entries:
        print(
            f"{e['cohort']:12s} trials={e['n_trials']:3d}  "
            f"criteria/trial median={e['atomic_criteria_per_trial'].get('median')}  "
            f"chars median={e['eligibility_chars'].get('median')}"
        )
    print(f"total atomic criteria: {manifest['total_atomic_criteria']}")
    print(f"manifest -> {mpath}")
    print("\nNext: python -m bench.build_criteria")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
