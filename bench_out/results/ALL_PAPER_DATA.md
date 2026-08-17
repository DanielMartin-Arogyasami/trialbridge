# TrialBridge / TrialBridge-Bench — ALL DATA (single file)
Generated: 2026-08-16  
Source of truth for frozen trials: `bench_out/trials/manifest.json`  
**Integrity:** Synthetic patients only. Provisional smoke metrics are NOT paper results.

---

## 0. Protocol decisions (must match BOTH papers)

| Knob | Value | Notes |
|---|---|---|
| `FLAGSHIP_COHORT` | `nsclc` | Change to `neoantigen` in both papers if flipped |
| Cohorts pulled | `nsclc`, `neoantigen`, `breast` | Flagship / precision comparison / common-tumor baseline |
| `TRIALS_PER_COHORT` | 25 | API page size |
| `DEGRADED_GOLD_POLICY` | `constant` | Same gold on clean & degraded arms |
| `DEGRADE_TRANSFORMS` | `abbreviate`, `strip_molecular`, `drop_labs`, `terse` | Pre-register before real eval |
| `TARGET_PATIENTS` | 40 | Hand-authored molecular gold |
| `TARGET_PAIRS` | 600 | Annotated pairs target |
| `BALANCE_FLOOR` | ELIGIBLE ≥30%, INELIGIBLE ≥25%, UNCERTAIN ≥10% | |
| Bootstrap | N=2000, seed=20260815, clustered by patient | |
| Product model (paper) | MedGemma 1.5 4B via Ollama (`medgemma:4b`) | Not yet run for real gold |
| Snapshot date | **2026-08-16** | Dated CT.gov freeze |

---

## 1. ClinicalTrials.gov frozen snapshots (Supplement S1 + Results §4.1 trials)

**API:** `https://clinicaltrials.gov/api/v2/studies`  
**Filter:** `RECRUITING`  
**Built (UTC):** 2026-08-16T22:14:37+00:00  
**Total trials:** 72  
**Total atomic criteria:** 1548  

### Queries

| Cohort | Paper role | Query |
|---|---|---|
| `nsclc` | Flagship (current) | `query.cond=non-small cell lung cancer` + `query.term=EGFR OR ALK OR KRAS G12C OR PD-L1` |
| `neoantigen` | Precision / mRNA comparison | `query.term=neoantigen mRNA vaccine cancer` |
| `breast` | Common-solid-tumor baseline | `query.cond=breast cancer` |

### Snapshot files (commit these)

- `bench_out/trials/nsclc_2026-08-16.json`
- `bench_out/trials/neoantigen_2026-08-16.json`
- `bench_out/trials/breast_2026-08-16.json`
- `bench_out/trials/manifest.json`

### Per-cohort trial statistics

| Cohort | Trials | Dropped empty | Elig. chars min / median / max | Criteria/trial min / median / max | Total atomic criteria |
|---|---|---|---|---|---|
| nsclc | 25 | 0 | 570 / 2169 / 8994 | 8 / 17 / 63 | 513 |
| neoantigen | 22 | 0 | 806 / 5375.5 / 9379 | 9 / 25.5 / 51 | 589 |
| breast | 25 | 0 | 164 / 1473 / 11981 | 2 / 16 / 53 | 446 |
| **TOTAL** | **72** | **0** | | | **1548** |

### Methods §3.3 flagship characterization (nsclc, n=25)

Replace draft “[8] recruiting flagship trials…” with:

- Eligibility blobs: **570–8994** characters (median **2169**)
- Atomic criteria per trial: **8–63** (median **17**)

If flagship switches to neoantigen (n=22): chars **806–9379** (median **5375.5**); criteria/trial **9–51** (median **25.5**).

### Licensing / redistribution (Declarations)

- Sponsor-authored eligibility text; NLM does **not** clear copyright or assert uniform PD.
- Redistribute under NLM terms as a **dated snapshot** with attribution — **not** relicensed CC-BY by authors.
- Attribution text in `manifest.json`.

---

## 2. Atomic criteria (after `bench.build_criteria`)

**File:** `bench_out/work/criteria_atomic.jsonl`  
**N:** 1548  
**Spot-check:** `bench_out/work/segmentation_spotcheck.md` (read before Methods)

| Kind | Count |
|---|---|
| inclusion | 748 |
| exclusion | 800 |

| Criterion type (`ctype`) | Count | % of 1548 |
|---|---|---|
| other | 761 | 49.2% |
| demographic | 142 | 9.2% |
| disease | 126 | 8.1% |
| molecular | 123 | 7.9% |
| lab | 118 | 7.6% |
| logistical | 117 | 7.6% |
| prior_therapy | 109 | 7.0% |
| performance | 52 | 3.4% |

| Segmentation flag | Count | % |
|---|---|---|
| compound=true | 851 | **55.0%** |

---

## 3. Synthetic patients (hand-authored seed library)

**File:** `bench_out/work/patients.jsonl`  
**N:** 12 (target 40)  
**Notes:** Template-rendered (deterministic), not model-generated → CC-BY clean  
**Arms:** `clean` + `degraded` via transforms above  
**Gold policy:** `constant`

| patient_id | cohort | note_intent (short) |
|---|---|---|
| nsclc-001 | nsclc | EGFR+ ELIGIBLE baseline |
| nsclc-002 | nsclc | EGFR wild-type → INELIGIBLE on EGFR inclusion |
| nsclc-003 | nsclc | ALK+ → INELIGIBLE on ALK exclusion |
| nsclc-004 | nsclc | ECOG 3 → performance INELIGIBLE |
| nsclc-005 | nsclc | Organ failure → lab INELIGIBLE |
| nsclc-006 | nsclc | Active brain mets → exclusion |
| nsclc-007 | nsclc | 4 prior lines → prior_therapy INELIGIBLE |
| nsclc-008 | nsclc | No NGS → molecular UNCERTAIN |
| neo-001 | neoantigen | HLA-A*02:01 + tissue + resected ELIGIBLE |
| neo-002 | neoantigen | No tissue → INELIGIBLE |
| neo-003 | neoantigen | HLA not typed → UNCERTAIN |
| neo-004 | neoantigen | HIV+ → serology INELIGIBLE |

| Cohort | Patients |
|---|---|
| nsclc | 8 |
| neoantigen | 4 |
| breast | 0 (no patients yet) |

---

## 4. Pairs & label proposals (`bench.autolabel`)

Built with: 3 trials/patient × 12 criteria/trial × 2 arms (nsclc+neoantigen patients only).

| Artifact | Path | N |
|---|---|---|
| Unlabeled pairs | `bench_out/work/pairs.jsonl` | **864** |
| Auto-derived proposals | `bench_out/work/proposals.jsonl` | **272** |
| Need human label | | 592 (69%) |

### Proposed label balance (proposals only — NOT gold)

| Label | Count | % of 272 |
|---|---|---|
| ELIGIBLE | 116 | 42.6% |
| INELIGIBLE | 94 | 34.6% |
| UNCERTAIN | 62 | 22.8% |

`provenance=derived` until human confirms via `bench.annotate --review`.  
`build_dataset` refuses release while any non-human provenance remains.

---

## 5. Paper Table 1 — fillable now vs still placeholder

| Cohort | Trials | Median criteria/trial | Patients | Annotated pairs |
|---|---|---|---|---|
| Flagship (`nsclc`) | **25** | **17** | **8** (seed) | pending human gold |
| Neoantigen comparison | **22** | **25.5** | **4** (seed) | pending human gold |
| Breast baseline | **25** | **16** | **0** | — |
| **Total** | **72** | | **12** | provisional smoke used 272 derived labels only |

---

## 6. Provisional plumbing run (NOT for Abstract / Results / Conclusion)

Smoke labels from proposals; heuristic backend only.  
**Do not cite as findings.** Clear with `python -m bench.smoke --clear` before real annotation.

### Heuristic per-run (n=136 pairs / 12 patients per arm)

| Metric | Clean | Degraded |
|---|---|---|
| Selective accuracy | 58.1% [47.2–69.0] | 53.8% [39.0–67.9] |
| Overall accuracy | 53.7% [43.4–63.6] | 47.8% [33.8–61.4] |
| Coverage | 63.2% (abstain 36.8%) | 57.4% (abstain 42.6%) |
| Span support (decided) | 100% | 100% |
| Always-ELIGIBLE stub | 42.6% | 42.6% |
| Gate downgrades | 0 | 0 |

### Equity slice (paired, primary outcome shape)

| | Value |
|---|---|
| n paired | 136 |
| Clean overall acc | 53.7% (abstain 36.8%) |
| Degraded overall acc | 47.8% (abstain 42.6%) |
| Δ (clean − degraded) | **5.9% [1.5–10.5]** |

### Extraction fidelity (Stage 1)

| Arm | Precision | Recall | Correct-null |
|---|---|---|---|
| clean | 93.4% | 99.1% | 100% |
| degraded | 93.2% | 90.0% | 100% |

### Still missing for real paper Results

- [ ] Human gold (`bench.annotate --review`)
- [ ] Second annotator + IAA (`--second`, `--iaa`)
- [ ] Scale patients to ~40
- [ ] MedGemma 1.5 4B eval (`run_eval --backend ollama --model medgemma:4b`)
- [ ] Ablations: Gemma 3 4B, MedGemma 27B, cloud (eval only)
- [ ] Deposit degradation-transform pre-registration (OSF)
- [ ] Synthea bulk cohort (paper §3.3) — not in bench yet
- [ ] Retrieval recall@k / precision@k gold set

---

## 7. Quick paste — Abstract Results placeholders (structure only)

Until real MedGemma + human gold runs:

- overall per-criterion accuracy → `[XX.X%]`
- molecular-criterion accuracy → `[XX.X%]`
- span-support rate → `[XX.X%]`
- abstention operating point → `[XX%]`
- retrieval recall@[k] → `[XX.X%]`
- clean−degraded Δ (primary) → `[XX.X points]`

---

## 8. File map

```
bench_out/
  trials/
    manifest.json
    nsclc_2026-08-16.json
    neoantigen_2026-08-16.json
    breast_2026-08-16.json
  work/
    criteria_atomic.jsonl      (1548)
    patients.jsonl             (12)
    pairs.jsonl                (864)
    proposals.jsonl            (272)
    annotations.jsonl          (provisional / derived — not gold)
    segmentation_spotcheck.md
  results/
    metrics.json               (provisional heuristic)
    paper_tables.md            (provisional)
    paper_data_pull.md         (earlier short note)
    ALL_PAPER_DATA.md          <- THIS FILE (canonical one-pager)
```

---

## 9. Reproduce

```bash
python -m bench.pull_trials
python -m bench.build_criteria
python -m bench.patients --build
python -m bench.autolabel
# real path: python -m bench.annotate --review
# plumbing only: python -m bench.smoke --write && python -m bench.run_eval --backend heuristic
python -m bench.metrics && python -m bench.make_tables
```
