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
python trialbridge.py selftest        # embedded tests, offline    -> 72 passed, 0 failed
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
