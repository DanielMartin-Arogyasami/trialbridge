"""
bench.config — one place for every knob in the benchmark build.

Edit THIS file rather than hunting through the pipeline. Everything downstream
(pull -> criteria -> patients -> annotate -> evaluate -> tables) reads from here.
"""

from __future__ import annotations

import os

# --------------------------------------------------------------------------
# Paths.  Everything is written under ./bench_out (gitignore the big stuff;
# commit the frozen snapshots + the released dataset).
# --------------------------------------------------------------------------

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "bench_out")

TRIALS_DIR = os.path.join(OUT, "trials")           # frozen CT.gov snapshots
WORK_DIR = os.path.join(OUT, "work")               # intermediates
PRED_DIR = os.path.join(OUT, "predictions")        # model outputs per backend/arm
RESULTS_DIR = os.path.join(OUT, "results")         # metrics + paper tables
DATASET_DIR = os.path.join(OUT, "trialbridge-bench")  # the releasable dataset

CRITERIA_FILE = os.path.join(WORK_DIR, "criteria_atomic.jsonl")
PATIENTS_FILE = os.path.join(WORK_DIR, "patients.jsonl")
PAIRS_FILE = os.path.join(WORK_DIR, "pairs.jsonl")          # unlabeled (patient x criterion)
PROPOSALS_FILE = os.path.join(WORK_DIR, "proposals.jsonl")  # auto-derived label proposals
ANNOTATIONS_FILE = os.path.join(WORK_DIR, "annotations.jsonl")        # primary annotator
ANNOTATIONS_A2_FILE = os.path.join(WORK_DIR, "annotations_a2.jsonl")  # second annotator (IAA)

# --------------------------------------------------------------------------
# Cohorts.  Keys must exist in trialbridge.COHORT_QUERIES, or be added there.
#
# DECISION (must match BOTH papers): which cohort is the flagship?
#   "neoantigen" -> harder, more novel, more authoring effort
#   "nsclc"      -> easier to author, still precision-oncology
# --------------------------------------------------------------------------

FLAGSHIP_COHORT = "nsclc"          # <-- change to "neoantigen" if that's your call
COHORTS = ["nsclc", "neoantigen"]  # cohorts to pull and freeze
TRIALS_PER_COHORT = 25             # page size per cohort pull

# --------------------------------------------------------------------------
# Benchmark size + balance targets.
#
# Your README flagged the real problem: 15 pairs / 2 patients / 0 INELIGIBLE
# means a stub that always says ELIGIBLE scores 0.93.  These targets exist to
# force a benchmark that can actually discriminate.  build_dataset warns loudly
# if you are under target.
# --------------------------------------------------------------------------

TARGET_PATIENTS = 40          # hand-authored molecular patients (paper says 30-60)
TARGET_PAIRS = 600            # (patient, criterion) annotated pairs
MIN_PAIRS_PER_CTYPE = 25      # every criterion type needs enough to report

# Label balance floors, as a fraction of all annotated pairs.
# A benchmark with no INELIGIBLE cannot detect a model that never says no.
BALANCE_FLOOR = {
    "ELIGIBLE": 0.30,
    "INELIGIBLE": 0.25,
    "UNCERTAIN": 0.10,
}

# Criterion types we report separately in the papers.
REPORT_CTYPES = [
    "demographic", "performance", "disease", "molecular",
    "lab", "prior_therapy", "logistical", "other",
]

# --------------------------------------------------------------------------
# Evaluation.
# --------------------------------------------------------------------------

# Backends to evaluate.  (name, model, label-for-tables)
# 'heuristic' needs nothing.  'ollama' needs `ollama pull medgemma:4b`.
# 'cloud' is EVALUATION-ONLY and must never see real patient data.
EVAL_BACKENDS = [
    ("heuristic", None, "heuristic (baseline)"),
    ("ollama", "medgemma:4b", "MedGemma 1.5 4B (product, local)"),
    # ("ollama", "gemma3:4b", "Gemma 3 4B (no medical tuning)"),
    # ("ollama", "medgemma:27b", "MedGemma 27B (ceiling)"),
    # ("cloud", None, "Cloud frontier (eval only)"),
]

ARMS = ["clean", "degraded"]   # the equity slice

# Degradation transforms for the community-style arm.
# PRE-REGISTER THIS LIST before running.  Changing it after seeing results is
# exactly the p-hacking reviewers will look for.
DEGRADE_TRANSFORMS = ["abbreviate", "strip_molecular", "drop_labs", "terse"]

# Bootstrap settings for confidence intervals (clustered by patient).
BOOTSTRAP_N = 2000
BOOTSTRAP_SEED = 20260815

# --------------------------------------------------------------------------
# Gold-label philosophy for the degraded arm.  PRE-REGISTER THIS TOO.
#
# "constant"  -> gold stays the patient's TRUE eligibility in both arms.
#                Abstaining on a degraded note counts as lost coverage.
#                (Recommended: measures the real-world cost of thin notes.)
# "recompute" -> gold becomes UNCERTAIN when degradation removed the evidence.
#                Measures whether the model tracks available information.
# --------------------------------------------------------------------------

DEGRADED_GOLD_POLICY = "constant"


def ensure_dirs() -> None:
    for d in (OUT, TRIALS_DIR, WORK_DIR, PRED_DIR, RESULTS_DIR, DATASET_DIR):
        os.makedirs(d, exist_ok=True)
