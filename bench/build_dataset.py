"""
bench.build_dataset — assemble the releasable TrialBridge-Bench deposit.

Refuses to build if annotations are still machine-derived rather than human-confirmed.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from . import config, store  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build the releasable dataset.")
    ap.add_argument("--force", action="store_true",
                    help="write even if integrity checks fail (NOT for release)")
    args = ap.parse_args(argv)
    config.ensure_dirs()

    patients = store.read_jsonl(config.PATIENTS_FILE)
    criteria = store.read_jsonl(config.CRITERIA_FILE)
    annotations = store.read_jsonl(config.ANNOTATIONS_FILE)
    if not patients or not criteria:
        raise SystemExit("run bench.pull_trials -> bench.build_criteria -> bench.patients first")

    manifest_path = os.path.join(config.TRIALS_DIR, "manifest.json")
    trials_manifest = store.read_json(manifest_path) if os.path.exists(manifest_path) else None

    integrity = store.check_integrity(patients, criteria, annotations)
    print("=" * 72)
    print("INTEGRITY REPORT")
    print("=" * 72)
    print(f"patients    : {integrity['n_patients']}")
    print(f"criteria    : {integrity['n_criteria']}")
    print(f"annotations : {integrity['n_annotations']}")
    print("\nlabel distribution:")
    for lab, v in sorted(integrity["label_distribution"].items()):
        print(f"  {lab:11s} {v['count']:5d} ({v['fraction']:.1%})")
    print("\npairs per criterion type:")
    for ct, n in sorted(integrity["ctype_counts"].items(), key=lambda x: -x[1]):
        print(f"  {ct:14s} {n:5d}")
    if integrity["warnings"]:
        print("\n--- WARNINGS (these gate CLAIMS, not validity) ---")
        for w in integrity["warnings"]:
            print("  ~", w)
    if integrity["errors"]:
        print("\n--- ERRORS (these block release) ---")
        for e in integrity["errors"][:30]:
            print("  !", e)
        if len(integrity["errors"]) > 30:
            print(f"  ... and {len(integrity['errors']) - 30} more")

    if not integrity["ok"] and not args.force:
        print("\nNOT BUILT. Fix the errors above, or pass --force to inspect anyway.")
        return 1

    dest = store.write_dataset(patients, criteria, annotations,
                               trials_manifest=trials_manifest, integrity=integrity)
    print(f"\ndataset -> {dest}")
    if not integrity["ok"]:
        print("!! built with --force and FAILING checks. Do not deposit or cite this build.")
    else:
        print("\nReady to deposit (Zenodo/figshare) once you have:")
        print("  - annotation guidelines in guidelines/")
        print("  - the pre-registration of degradation transforms")
        print("  - IAA from a second annotator (python -m bench.annotate --iaa)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
