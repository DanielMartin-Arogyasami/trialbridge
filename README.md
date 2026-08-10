# TrialBridge

A local, explainable oncology clinical-trial matcher in a single Python file. No install,
no third-party packages — standard library only, Python 3.9+.

```
note → EXTRACT profile → RETRIEVE trials (ClinicalTrials.gov API v2)
     → REASON per criterion (ELIGIBLE / INELIGIBLE / UNCERTAIN + evidence)
     → RANK + human-review queue
```

Every model call sits behind one `Backend` interface (`heuristic` | `ollama` | `llamacpp` |
`cloud`), so the reasoning engine can be swapped without touching the pipeline.
`UNCERTAIN` is a first-class abstention: when the profile lacks the information needed to
decide a criterion, the system says so and routes the trial to human review rather than
guessing.

## Safety

- **Synthetic / de-identified data only.** Never put real patient data (PHI) in this repo,
  in prompts, in cached pulls, or in commits. `note.txt` and `data/trials/` are gitignored
  for this reason.
- The `cloud` backend is **evaluation-only**. Never route real patient notes to it.
- `UNCERTAIN` is deliberate. Do not "fix" it into a forced yes/no.

## Quick start

```bash
python trialbridge.py selftest        # embedded tests, offline    -> 82 passed, 0 failed
python trialbridge.py demo            # end-to-end on bundled synthetic data, offline
python trialbridge.py demo --degrade  # equity slice: degraded community-style note
```

Run it on your own de-identified note:

```bash
python trialbridge.py extract --patient note.txt            # note -> structured profile
python trialbridge.py match   --patient note.txt --offline  # match vs bundled snapshot
```

Run against live ClinicalTrials.gov (needs network):

```bash
python trialbridge.py match  --patient note.txt --cohort nsclc
python trialbridge.py fetch  --cohort neoantigen --out data/trials/neoantigen.json
```

Per-criterion accuracy, abstention rate, and a risk-coverage curve on the bundled gold set:

```bash
python trialbridge.py evaluate
```

## Measured results

Every decided verdict now passes through a **faithfulness gate**
(`evidence_faithful` / `apply_faithfulness_gate`): empty spans, criterion-echo "evidence",
spans not grounded in the note or structured profile, and molecular contradictions
(e.g. citing «EGFR wild-type» for an ELIGIBLE call on an EGFR-mutation inclusion) are
downgraded to `UNCERTAIN` with confidence 0. The gate is backend-agnostic — it wraps
`reason_criterion` — so heuristic and LLM paths are both covered.

On the bundled 15-criterion gold set (`python trialbridge.py evaluate`):

| Backend | Coverage | Selective accuracy | Abstention rate | Gate downgrades | Molecular accuracy |
|---|---|---|---|---|---|
| `heuristic` | 0.87 | 1.00 | 0.13 | 0 | 1.00 |
| `medgemma:4b` | 1.00 | 0.93 | 0.00 | 0* | 0.75 |

\*The bundled gold patients both carry the alterations their criteria ask about, so the
gate has nothing to reject there. The failure it is built for shows up on a different
input: the KRAS-G12C / EGFR-**wild-type** note against the bundled EGFR trial. Before the
gate, MedGemma labeled that EGFR-mutation inclusion **ELIGIBLE** and cited
«EGFR wild-type»; after the gate the criterion is **UNCERTAIN** and the trial drops from
ELIGIBLE into the human-review queue. That is the intended failure mode — ask a human
rather than invent eligibility.

Remaining gaps:

- **The heuristic backend still abstains too much** on live ClinicalTrials.gov text
  (~79% UNCERTAIN), mostly on consent / washout / concurrent-med criteria that no
  regex table will cover. Closing that needs a calibrated model, not more rules.
- **The gate does not yet catch merely irrelevant evidence** (e.g. citing the diagnosis
  for a brain-metastases criterion). Contradiction and grounding are covered; topical
  relevance is not.
- **The gold set is still too weak to referee this:** 15 records, 2 patients, labeled
  14 ELIGIBLE / 1 UNCERTAIN / **0 INELIGIBLE**, so a stub that always answers ELIGIBLE
  scores 0.93.

Throughput: roughly 5 s per criterion on a local 4B model, so a real trial with 20+
criteria takes minutes. Use `--max-criteria` while iterating.

## Backends

| Backend | Use | Notes |
|---|---|---|
| `heuristic` (default) | offline, deterministic | Baseline, no model. Abstains on molecular/HLA by design, routing hard trials to review. **Not the product.** |
| `ollama` | local MedGemma via Ollama | `--backend ollama --model medgemma:4b` |
| `llamacpp` | local MedGemma via llama.cpp server or in-process GGUF | set `LLAMACPP_URL` or `LLAMACPP_MODEL_PATH` |
| `cloud` | frontier reference model | eval-only, needs `ANTHROPIC_API_KEY` |

With a real local model:

```bash
ollama pull medgemma:4b
python trialbridge.py match --patient note.txt --offline --backend ollama --model medgemma:4b
```

MedGemma is published in Ollama's official library as `medgemma:4b` (3.3 GB) and
`medgemma:27b`. Judging a trial costs one model call per criterion, so real trials with
20+ criteria take noticeably longer than the heuristic baseline — use `--max-criteria` to
cap the work while iterating.

The `heuristic` backend is a deterministic **baseline**, not the product. On real trial
text it abstains often and makes some keyword-driven mistakes; use a real model backend
for anything you intend to measure.

## Data

All live data comes from ClinicalTrials.gov **API v2**
(`https://clinicaltrials.gov/api/v2/studies`), filtered to `RECRUITING`. Pulls are cached
to `./data/trials/` and can be frozen into a dated snapshot for reproducibility.

Built-in cohorts live in `COHORT_QUERIES`: `neoantigen`, `nsclc`, `breast`. Add a cohort by
adding an entry there with any `query.*` / `filter.*` params the API accepts. When no
cohort is given, `auto_query(profile)` builds a query from the patient's condition and
biomarkers.

```python
from trialbridge import CTGovClient, COHORT_QUERIES

client = CTGovClient()
trials = client.search(COHORT_QUERIES["nsclc"], page_size=20)
client.save_snapshot("data/trials/nsclc.json", trials, query=COHORT_QUERIES["nsclc"])

frozen = CTGovClient.from_snapshot("data/trials/nsclc.json")  # offline & reproducible
```

Commit small snapshots if you want reproducibility; never commit the cache or any PHI.

## Architecture

`trialbridge.py`, in order: schema (dataclasses/enums) → JSON utils → model backends,
prompts, heuristic rules, `get_backend()` → Stage 1 `extract` → Stage 2 CT.gov client
(fetch/cache/snapshot) → criteria splitter → Stage 3 `reason` → Stage 4 `rank` → equity
degradation transforms → calibration / selective prediction → `TrialBridge` pipeline →
`evaluate()` harness → bundled synthetic data → report → CLI → embedded `run_tests()`.

To add a backend, subclass `LLMBackend`, implement `complete(prompt, system)`, and register
it in `get_backend()`. The `EXTRACT_*` / `JUDGE_*` prompts already request strict JSON.

## Development

Keep it single-file and standard-library only. After any change, run:

```bash
python trialbridge.py selftest
```

When you change a regex or rule, add or adjust an assertion in `run_tests()`.
