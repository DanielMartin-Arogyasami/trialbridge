"""
bench.smoke — verify the whole pipeline runs BEFORE you spend hours annotating.

Writes PROVISIONAL annotations from auto-derived proposals. Not gold, not results.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from . import config, store  # noqa: E402

BANNER = """
################################################################################
#  PROVISIONAL ANNOTATIONS -- NOT GOLD, NOT RESULTS                            #
#                                                                              #
#  These came from rule-based proposals, not a human. Grading the heuristic     #
#  backend against them is partly circular. Use them only to confirm the        #
#  pipeline runs end to end. Delete before real annotation:                     #
#      python -m bench.smoke --clear                                            #
################################################################################
"""


def write() -> int:
    props = store.read_jsonl(config.PROPOSALS_FILE)
    if not props:
        raise SystemExit("no proposals. Run: python -m bench.autolabel")
    existing = store.read_jsonl(config.ANNOTATIONS_FILE)
    human = [a for a in existing if a.get("provenance") == "human"]
    if human:
        raise SystemExit(
            f"{len(human)} REAL human annotations already exist. Refusing to overwrite "
            "them with provisional ones. Smoke-testing is done; just run bench.run_eval."
        )

    patients = {p["patient_id"]: p for p in store.read_jsonl(config.PATIENTS_FILE)}
    recs = []
    for p in props:
        note = patients.get(p["patient_id"], {}).get("notes", {}).get(p["arm"], "")
        span = ""
        for line in note.splitlines():
            if any(w in line.lower() for w in p["criterion_text"].lower().split()[:6] if len(w) > 4):
                span = line.strip()
                break
        recs.append(
            store.annotation_record(
                pair_id=p["pair_id"],
                patient_id=p["patient_id"],
                criterion_id=p["criterion_id"],
                arm=p["arm"],
                label=p["proposed_label"],
                evidence_span=span if span and span in note else "",
                difficulty=p.get("difficulty", "medium"),
                annotator_id="PROVISIONAL",
                provenance="derived",
                note="PROVISIONAL smoke-test label -- not gold",
            )
        )
    store.write_jsonl(config.ANNOTATIONS_FILE, recs)
    print(BANNER)
    print(f"wrote {len(recs)} provisional annotations -> {config.ANNOTATIONS_FILE}")
    print("\nNow verify the chain:")
    print("  python -m bench.run_eval --backend heuristic")
    print("  python -m bench.metrics")
    print("  python -m bench.make_tables")
    return 0


def clear() -> int:
    existing = store.read_jsonl(config.ANNOTATIONS_FILE)
    human = [a for a in existing if a.get("provenance") == "human"]
    prov = [a for a in existing if a.get("provenance") != "human"]
    if not prov:
        print("no provisional annotations to clear.")
        return 0
    store.write_jsonl(config.ANNOTATIONS_FILE, human)
    print(f"removed {len(prov)} provisional annotations; kept {len(human)} human ones.")
    if os.path.isdir(config.PRED_DIR):
        n = 0
        for fn in os.listdir(config.PRED_DIR):
            if fn.endswith(".jsonl"):
                os.remove(os.path.join(config.PRED_DIR, fn))
                n += 1
        print(f"cleared {n} prediction file(s) -- they were graded against provisional gold.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Pipeline smoke test (not gold).")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--clear", action="store_true")
    args = ap.parse_args(argv)
    config.ensure_dirs()
    if args.clear:
        return clear()
    if args.write:
        return write()
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
