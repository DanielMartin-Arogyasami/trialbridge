# TrialBridge-Bench — Scientific Data descriptor: ALL DATA (final file)
Companion to system-paper file: `bench_out/results/ALL_PAPER_DATA.md`  
Generated: 2026-08-16  
**Resource-only.** No modeling claims. Numbers below are from the built pipeline artifacts; brackets remain where the deposit is not yet releasable.

---

## Status vs Scientific Data submission gate

| Gate | Status |
|---|---|
| Frozen CT.gov snapshot | **DONE** — 2026-08-16 |
| Atomic criteria + provenance | **DONE** — 1548 records |
| Note synthesis (CC-BY compatible) | **RESOLVED** — template/rule-based (`bench.patients`), not an LLM |
| Clean/degraded paired notes | **DONE** — 12/12 seed patients |
| Hand-authored molecular gold | **IN PROGRESS** — 12 of target 30–60 |
| Synthea bulk cohort | **NOT BUILT** — paper Methods still placeholder |
| Human gold annotations | **NOT DONE** — 0 human; 272 provisional derived only |
| Double-annotation / IAA | **NOT DONE** — A2 = 0 |
| Integrity pass for release | **FAIL** — provenance ≠ human |
| Zenodo/figshare DOI | **NOT DEPOSITED** — cannot submit descriptor until DOI exists |
| Pre-registration of transforms | **NOT DEPOSITED** — transforms chosen in config, registry ID pending |

**Submission rule:** deposit first → then submit this descriptor.

---

## Cross-paper consistency (identical to system paper)

| Decision | Value |
|---|---|
| Flagship cohort | `nsclc` (`bench/config.py`; flip both papers together if changed) |
| Degradation transforms | `abbreviate`, `strip_molecular`, `drop_labs`, `terse` |
| Degraded gold policy | `constant` |
| Note synthesis | Deterministic template renderer (CC-BY clean) |
| Code | https://github.com/DanielMartin-Arogyasami/trialbridge (Apache-2.0) |

---

## Abstract — fillable counts (current seed state)

> We present TrialBridge-Bench, a benchmark of **[12]** synthetic patient records paired with eligibility criteria from **[72]** recruiting oncology trials drawn from a frozen ClinicalTrials.gov snapshot (**2026-08-16**), with **[864]** constructed (patient, criterion, arm) pairs of which **[272]** have auto-derived label *proposals* awaiting human confirmation (releasable annotated pairs = **[0]** until review).

Use **[N]** in the submitted abstract until human gold + full patient scale are done. Do **not** publish 272 as “expert labels.”

---

## Supplement S1 — CT.gov queries + pull date

**API:** `https://clinicaltrials.gov/api/v2/studies`  
**Status filter:** `RECRUITING`  
**Pull / freeze date:** **2026-08-16** (UTC build `2026-08-16T22:14:37+00:00`)  
**Total trials:** 72 · **Total atomic criteria:** 1548

| Cohort key | Descriptor role | Query | Trials | Snapshot file | Size |
|---|---|---|---|---|---|
| `nsclc` | Flagship (current) | `cond=non-small cell lung cancer` + `term=EGFR OR ALK OR KRAS G12C OR PD-L1` | 25 | `nsclc_2026-08-16.json` | 91 KB |
| `neoantigen` | Precision / mRNA comparison | `term=neoantigen mRNA vaccine cancer` | 22 | `neoantigen_2026-08-16.json` | 121 KB |
| `breast` | Common-solid-tumor baseline | `cond=breast cancer` | 25 | `breast_2026-08-16.json` | 81 KB |
| | | | | `manifest.json` | 2.4 KB |

### Per-cohort eligibility / segmentation stats

| Cohort | Elig. chars min / med / max | Criteria/trial min / med / max | Atomic criteria |
|---|---|---|---|
| nsclc | 570 / 2169 / 8994 | 8 / 17 / 63 | 513 |
| neoantigen | 806 / 5375.5 / 9379 | 9 / 25.5 / 51 | 589 |
| breast | 164 / 1473 / 11981 | 2 / 16 / 53 | 446 |

### NLM / licensing (Data Records — paste-ready)

- Authors’ original contributions (patients, notes, annotations, schemas, guidelines, segmentation): **CC-BY-4.0**
- Embedded ClinicalTrials.gov records: **redistributed under NLM terms, NOT relicensed** by authors; sponsor-authored free text; users responsible for third-party rights
- **Dated snapshot notice (required):** records are frozen as of **2026-08-16** and do **not** reflect the current contents of ClinicalTrials.gov
- Attribution string: see `bench_out/trials/manifest.json` → `attribution`

---

## Methods — resolved construction facts

### Criteria segmentation
- **Method:** rule-based `trialbridge.split_criteria` + `classify_ctype` (stdlib; not CriteriaLogic dependency)
- **Human review of every atomic criterion:** pending (spot-check sample written)
- Spot-check file: `bench_out/work/segmentation_spotcheck.md`
- Compound flag rate: **851 / 1548 (55.0%)** — report this segmentation caveat in Technical Validation

### Free-text note synthesis (licensing-critical — RESOLVED)
- **Method:** deterministic template in `bench/patients.py` (`PatientSpec.render_note`)
- **No LLM** used to generate notes → CC-BY deposit unencumbered
- Degraded notes: `trialbridge.degrade_note` with the four pre-registered transforms

### Synthea bulk cohort
- **Status:** not generated in this repo yet  
- Abstract / Table 1 “Bulk synthetic patients” remains **[N] / 0** until Synthea run is added

### Hand-authored molecular gold subset
- **Current N:** 12 (target 30–60; config target 40)
- Authoring template + `validate()` consistency checks in `bench/patients.py`
- Clinician plausibility review: **not yet done**

### Annotation protocol (as implemented)
- Labels: ELIGIBLE / INELIGIBLE / UNCERTAIN
- Evidence span: verbatim substring of the arm’s note (integrity-enforced)
- Criterion type + difficulty on records
- CLI: `python -m bench.annotate --review` / `--second --slice 0.2` / `--iaa`
- Default double-annotate slice: **20%** of A1 pairs
- **Human A1:** 0 · **A2:** 0 · **IAA:** not computed

---

## Data Records — Table 1 (fill what exists)

| Item | Count | Notes |
|---|---|---|
| Trials (frozen snapshot) | **72** | 3 cohorts |
| Atomic criteria | **1548** | `criteria_atomic.jsonl` (~786 KB) |
| Bulk synthetic patients (Synthea) | **0** | not built |
| Molecular gold patients | **12** | all seed patients are molecular hand-authored |
| (Patient, criterion) *constructed* pairs | **864** | both arms; awaiting human gold |
| Auto-derived proposals | **272** | not releasable gold |
| Releasable annotated pairs (provenance=human) | **0** | integrity blocks release |
| Pairs double-annotated | **0** (0%) | |
| Note variants clean / degraded | **12 / 12** | |

### Criterion-type distribution (all 1548 atomic criteria)

| Type | Count | % |
|---|---|---|
| other | 761 | 49.2% |
| demographic | 142 | 9.2% |
| disease | 126 | 8.1% |
| molecular | 123 | 7.9% |
| lab | 118 | 7.6% |
| logistical | 117 | 7.6% |
| prior_therapy | 109 | 7.0% |
| performance | 52 | 3.4% |

| Kind | Count |
|---|---|
| inclusion | 748 |
| exclusion | 800 |

### Proposal label balance (272 proposals — coverage characterization only, not gold)

| Label | Count | % |
|---|---|---|
| ELIGIBLE | 116 | 42.6% |
| INELIGIBLE | 94 | 34.6% |
| UNCERTAIN | 62 | 22.8% |

Difficulty among proposals: easy 128 · medium 144 · hard 0

### Constructed pairs by criterion type (864)

| Type | n pairs |
|---|---|
| other | 184 |
| molecular | 128 |
| demographic | 120 |
| prior_therapy | 120 |
| logistical | 120 |
| disease | 88 |
| performance | 56 |
| lab | 48 |

---

## Molecular gold patient inventory (Technical Validation — plausibility)

All 12 pass `bench.patients --check` (0 consistency issues). Specs carry mutation / HLA / tissue fields Synthea cannot emit.

| ID | Cohort | Biomarkers | HLA | NGS | Tissue | Intent |
|---|---|---|---|---|---|---|
| nsclc-001 | nsclc | 2 | — | Y | — | EGFR+ eligible |
| nsclc-002 | nsclc | 3 | — | Y | — | EGFR WT ineligible |
| nsclc-003 | nsclc | 2 | — | Y | — | ALK+ exclusion |
| nsclc-004 | nsclc | 1 | — | Y | — | ECOG 3 |
| nsclc-005 | nsclc | 1 | — | Y | — | Lab failure |
| nsclc-006 | nsclc | 1 | — | Y | — | Active brain mets |
| nsclc-007 | nsclc | 1 | — | Y | — | 4 prior lines |
| nsclc-008 | nsclc | 0 | — | N | — | No NGS → uncertain |
| neo-001 | neoantigen | 2 | A*02:01 | Y | Y | Flagship eligible |
| neo-002 | neoantigen | 1 | A*01:01 | Y | N | No tissue |
| neo-003 | neoantigen | 1 | not typed | Y | Y | HLA uncertain |
| neo-004 | neoantigen | 1 | A*02:01 | Y | Y | HIV+ exclusion |

### Clean vs degraded note lengths (chars / words)

| ID | Clean chars | Deg chars | Clean words | Deg words |
|---|---|---|---|---|
| nsclc-001 | 587 | 464 | 80 | 61 |
| nsclc-002 | 511 | 395 | 71 | 53 |
| nsclc-003 | 506 | 390 | 69 | 51 |
| nsclc-004 | 491 | 375 | 68 | 50 |
| nsclc-005 | 488 | 372 | 69 | 51 |
| nsclc-006 | 572 | 449 | 78 | 59 |
| nsclc-007 | 536 | 420 | 72 | 54 |
| nsclc-008 | 485 | 369 | 67 | 49 |
| neo-001 | 595 | 336 | 82 | 45 |
| neo-002 | 562 | 330 | 76 | 43 |
| neo-003 | 580 | 336 | 81 | 45 |
| neo-004 | 584 | 341 | 80 | 45 |

Degradation consistently shortens notes (esp. neoantigen after `strip_molecular`).

---

## Technical Validation — Table 2 & placeholders

| Check | Value |
|---|---|
| Overall label IAA (κ) | **[X.XX]** — not run |
| Per-type IAA | **[X.XX]** — not run |
| Span exact / containment | **[X.XX]** — not run |
| Double-annotated n | **0** |
| Schema + span integrity (release build) | **FAIL** — 272 derived provenance |
| Integrity error | “annotations still have provenance ≠ human” |
| Size warnings | 12 < 40 patients; 272 < 600 pairs; disease ctype 24 < min 25 |
| Clinician molecular review | **[not done]** |
| Degradation face validity vs real notes | **[not done]** |
| Baseline usability (descriptor-only, single open model) | Point to system paper; provisional heuristic numbers must **not** be presented as the deposited baseline |

---

## Deposit layout vs what exists today

```
trialbridge-bench/          <- produced by: python -m bench.build_dataset
├── README.md               <- store.write_dataset emits
├── DATA_DICTIONARY.md
├── LICENSE                 <- layered CC-BY + NLM terms
├── NOTICE_NO_PHI.txt
├── INTEGRITY_REPORT.json   <- blocked until human gold
├── trials/manifest.json
├── patients/patients.jsonl
├── criteria/criteria_atomic.jsonl
├── annotations/pairs.jsonl
└── schema/*.schema.json
```

**Missing vs descriptor sketch (still to author):** `CHANGELOG.md`, `guidelines/`, `splits/`, `preregistration/` (or OSF link), Synthea `patients/bulk/`, separate `molecular_gold/` split folders.

**Code Availability URL:** https://github.com/DanielMartin-Arogyasami/trialbridge

---

## Working artifact sizes (pre-deposit)

| Path under `bench_out/` | Bytes |
|---|---|
| trials/nsclc_2026-08-16.json | 91,394 |
| trials/neoantigen_2026-08-16.json | 120,752 |
| trials/breast_2026-08-16.json | 81,098 |
| trials/manifest.json | 2,364 |
| work/criteria_atomic.jsonl | 786,099 |
| work/patients.jsonl | 32,646 |
| work/pairs.jsonl | 324,920 |
| work/proposals.jsonl | 126,928 |
| work/annotations.jsonl | 101,929 (provisional) |
| work/segmentation_spotcheck.md | 6,179 |

---

## What to paste into the descriptor now vs leave bracketed

**Paste now**
- Pull date **2026-08-16**; trials **72**; atomic criteria **1548**
- Query strings (S1 table above)
- Segmentation method = rule-based `split_criteria`; compound rate **55%**
- Note synthesis = template renderer (CC-BY)
- Transforms list + `constant` gold policy
- Molecular seed **12** patients; clean/degraded **12/12**
- Layered licensing + NLM snapshot notice
- Code URL

**Keep as [N] / [X.XX]**
- Releasable annotated pair count
- Double-annotated % and all IAA cells (Table 2)
- Synthea bulk N
- Final molecular N after scaling to 30–60
- Zenodo DOI
- Clinician review outcome
- Any baseline accuracy in Technical Validation (until human gold + intentional baseline run)

---

## Reproduce / next steps for a releasable deposit

```bash
python -m bench.pull_trials          # already done (3 cohorts)
python -m bench.build_criteria
python -m bench.patients --build     # scale PATIENT_LIBRARY toward 40
python -m bench.autolabel
python -m bench.smoke --clear        # if provisional labels present
python -m bench.annotate --review
python -m bench.annotate --second --slice 0.2
python -m bench.annotate --iaa
python -m bench.build_dataset        # must pass integrity
# then Zenodo/figshare deposit → insert DOI into descriptor
```

---

*End of descriptor data file. System-paper counterpart: `bench_out/results/ALL_PAPER_DATA.md`.*
