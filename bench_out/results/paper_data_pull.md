# TrialBridge paper — frozen CT.gov data (fill Results §4.1 / Methods §3.3)

**Snapshot pull date:** 2026-08-16 (UTC build stamped in `bench_out/trials/manifest.json`)
**Source:** ClinicalTrials.gov API v2, `filter.overallStatus=RECRUITING`
**Flagship decision (config):** `FLAGSHIP_COHORT = "nsclc"` (change in `bench/config.py` if papers switch to neoantigen)

## Table 1 skeleton (trials only — patients/pairs still pending annotation)

| Cohort (paper role) | Query key | Trials | Median criteria/trial | Atomic criteria | Median elig. chars |
|---|---|---|---|---|---|
| Flagship (current: NSCLC biomarker) | `nsclc` | 25 | 17 | 513 | 2169 |
| Precision comparison (neoantigen / mRNA) | `neoantigen` | 22 | 25.5 | 589 | 5375.5 |
| Common-solid-tumor baseline | `breast` | 25 | 16 | 446 | 1473 |
| **Total** | | **72** | | **1548** | |

Patients / patient–criterion pairs: still `[N]` until `patients --build` + annotation (seed library today: 12 patients).

## Methods §3.3 — replace the “[8] recruiting flagship trials…” draft range

Against the frozen flagship (`nsclc`) snapshot of **25** recruiting trials:
- Eligibility blob length: **570–8994** characters (median **2169**)
- Atomic criteria per trial: **8–63** (median **17**)

Neoantigen cohort (if flagship switches): **22** trials, chars **806–9379** (median **5375.5**), criteria/trial **9–51** (median **25.5**).

## Supplement S1 — query definitions (from COHORT_QUERIES)

```
nsclc:       query.cond = "non-small cell lung cancer"
             query.term = "EGFR OR ALK OR KRAS G12C OR PD-L1"
neoantigen:  query.term = "neoantigen mRNA vaccine cancer"
breast:      query.cond = "breast cancer"
```

Files: `bench_out/trials/{nsclc,neoantigen,breast}_2026-08-16.json` + `manifest.json`
