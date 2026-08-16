"""
bench.store — THE OUTPUT STORE.

Everything the benchmark produces is written through here, so there is exactly
one definition of each file format and one place that enforces integrity.

Formats are line-delimited JSON (.jsonl): one record per line, append-friendly,
diffable in git, streamable.  Schemas are emitted as JSON Schema so the released
dataset is self-describing.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Iterator, List, Optional

from . import config


def write_jsonl(path: str, records: Iterable[Dict[str, Any]]) -> int:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    n = 0
    with open(path, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
            n += 1
    return n


def append_jsonl(path: str, record: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def read_jsonl(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    out: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def iter_jsonl(path: str) -> Iterator[Dict[str, Any]]:
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def write_json(path: str, obj: Any) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False, sort_keys=True)


def read_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def make_pair_id(patient_id: str, criterion_id: str, arm: str) -> str:
    return f"{patient_id}::{criterion_id}::{arm}"


def annotation_record(
    *,
    pair_id: str,
    patient_id: str,
    criterion_id: str,
    arm: str,
    label: str,
    evidence_span: str = "",
    difficulty: str = "medium",
    annotator_id: str = "A1",
    provenance: str = "human",
    note: str = "",
) -> Dict[str, Any]:
    """One gold annotation.

    provenance:
      'derived'  -> proposed from the patient's construction facts (spec fields)
      'human'    -> a person confirmed or set this label
    Only 'human' records belong in a released gold set.  See autolabel.py.
    """
    return {
        "pair_id": pair_id,
        "patient_id": patient_id,
        "criterion_id": criterion_id,
        "arm": arm,
        "label": label,
        "evidence_span": evidence_span,
        "difficulty": difficulty,
        "annotator_id": annotator_id,
        "provenance": provenance,
        "annotator_note": note,
        "annotated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


PATIENT_SCHEMA: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "TrialBridge-Bench patient",
    "type": "object",
    "required": ["patient_id", "cohort", "profile", "notes"],
    "properties": {
        "patient_id": {"type": "string"},
        "cohort": {"type": "string"},
        "synthetic": {"type": "boolean", "const": True},
        "profile": {
            "type": "object",
            "description": "Ground-truth structured profile, known by construction.",
        },
        "notes": {
            "type": "object",
            "required": ["clean"],
            "properties": {
                "clean": {"type": "string"},
                "degraded": {"type": "string"},
            },
        },
        "degrade_transforms": {"type": "array", "items": {"type": "string"}},
    },
}

CRITERION_SCHEMA: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "TrialBridge-Bench atomic criterion",
    "type": "object",
    "required": ["criterion_id", "nct_id", "text", "kind", "ctype"],
    "properties": {
        "criterion_id": {"type": "string"},
        "nct_id": {"type": "string"},
        "text": {"type": "string"},
        "kind": {"enum": ["inclusion", "exclusion"]},
        "ctype": {"enum": config.REPORT_CTYPES},
        "index": {"type": "integer"},
        "compound": {"type": "boolean"},
        "source_snapshot": {"type": "string"},
    },
}

ANNOTATION_SCHEMA: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "TrialBridge-Bench annotation",
    "type": "object",
    "required": ["pair_id", "patient_id", "criterion_id", "arm", "label"],
    "properties": {
        "pair_id": {"type": "string"},
        "patient_id": {"type": "string"},
        "criterion_id": {"type": "string"},
        "arm": {"enum": ["clean", "degraded"]},
        "label": {"enum": ["ELIGIBLE", "INELIGIBLE", "UNCERTAIN"]},
        "evidence_span": {
            "type": "string",
            "description": "Verbatim substring of the arm's note, or '' when no support exists.",
        },
        "difficulty": {"enum": ["easy", "medium", "hard"]},
        "annotator_id": {"type": "string"},
        "provenance": {"enum": ["human", "derived"]},
    },
}


def check_integrity(
    patients: List[Dict[str, Any]],
    criteria: List[Dict[str, Any]],
    annotations: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Structural checks that must pass before the dataset is deposited."""
    errors: List[str] = []
    warnings: List[str] = []

    pat_by_id = {p["patient_id"]: p for p in patients}
    crit_ids = {c["criterion_id"] for c in criteria}

    for a in annotations:
        if a["patient_id"] not in pat_by_id:
            errors.append(f"{a['pair_id']}: unknown patient_id {a['patient_id']}")
        if a["criterion_id"] not in crit_ids:
            errors.append(f"{a['pair_id']}: unknown criterion_id {a['criterion_id']}")

    for a in annotations:
        span = (a.get("evidence_span") or "").strip()
        if not span:
            continue
        p = pat_by_id.get(a["patient_id"])
        if not p:
            continue
        note = (p.get("notes") or {}).get(a.get("arm", "clean"), "")
        if span not in note:
            errors.append(
                f"{a['pair_id']}: evidence_span is not a verbatim substring of the {a['arm']} note"
            )

    seen = set()
    for a in annotations:
        if a["pair_id"] in seen:
            errors.append(f"duplicate pair_id: {a['pair_id']}")
        seen.add(a["pair_id"])

    derived = [a for a in annotations if a.get("provenance") != "human"]
    if derived:
        errors.append(
            f"{len(derived)} annotations still have provenance != 'human'. "
            "Auto-derived proposals must be confirmed by a person before release "
            "(python -m bench.annotate --review)."
        )

    for p in patients:
        if not p.get("synthetic", False):
            errors.append(f"{p['patient_id']}: not flagged synthetic")

    n_pat = len(patients)
    n_ann = len(annotations)
    if n_pat < config.TARGET_PATIENTS:
        warnings.append(f"only {n_pat} patients (target {config.TARGET_PATIENTS})")
    if n_ann < config.TARGET_PAIRS:
        warnings.append(f"only {n_ann} annotated pairs (target {config.TARGET_PAIRS})")

    dist = label_distribution(annotations)
    for lab, floor in config.BALANCE_FLOOR.items():
        frac = dist.get(lab, {}).get("fraction", 0.0)
        if frac < floor:
            warnings.append(
                f"label {lab} is {frac:.1%} of pairs (floor {floor:.0%}). "
                "A benchmark thin on this label cannot detect a model that never predicts it."
            )

    by_ct = ctype_distribution(annotations, criteria)
    for ct, n in sorted(by_ct.items()):
        if n < config.MIN_PAIRS_PER_CTYPE:
            warnings.append(f"ctype '{ct}' has only {n} pairs (min {config.MIN_PAIRS_PER_CTYPE})")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "n_patients": n_pat,
        "n_criteria": len(criteria),
        "n_annotations": n_ann,
        "label_distribution": dist,
        "ctype_counts": by_ct,
    }


def label_distribution(annotations: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    total = len(annotations) or 1
    counts: Dict[str, int] = {}
    for a in annotations:
        counts[a["label"]] = counts.get(a["label"], 0) + 1
    return {
        lab: {"count": c, "fraction": c / total}
        for lab, c in sorted(counts.items())
    }


def ctype_distribution(
    annotations: List[Dict[str, Any]], criteria: List[Dict[str, Any]]
) -> Dict[str, int]:
    ct_by_id = {c["criterion_id"]: c.get("ctype", "other") for c in criteria}
    out: Dict[str, int] = {}
    for a in annotations:
        ct = ct_by_id.get(a["criterion_id"], "other")
        out[ct] = out.get(ct, 0) + 1
    return out


_LICENSE_TEXT = """\
TrialBridge-Bench — licensing is LAYERED BY PROVENANCE. Read this before reuse.

1. AUTHORS' ORIGINAL CONTRIBUTIONS -- CC BY 4.0
   Synthetic patient profiles and notes (clean and degraded), per-criterion
   annotations and evidence spans, criteria segmentation, schemas, annotation
   guidelines, and all documentation.
   https://creativecommons.org/licenses/by/4.0/

2. EMBEDDED ClinicalTrials.gov RECORDS -- NOT RELICENSED BY THE AUTHORS
   Files under trials/ contain records retrieved from ClinicalTrials.gov
   (U.S. National Library of Medicine). This free-text content is authored and
   submitted by trial sponsors and investigators, not by NLM; NLM does not
   clear copyright or assert uniform public-domain status for it. These records
   are redistributed under NLM's terms with attribution. Reusers are
   responsible for any third-party rights in that content.
   SNAPSHOT NOTICE (required by NLM's redistribution terms): the trial records
   in this dataset are a DATED SNAPSHOT and do NOT reflect the current contents
   of ClinicalTrials.gov. See trials/manifest.json for the pull date. For
   current records, consult https://clinicaltrials.gov directly.

3. MODEL WEIGHTS -- NOT INCLUDED
   No model weights are redistributed here. MedGemma weights are obtained by
   users directly under the Health AI Developer Foundations terms.

NOT FOR CLINICAL USE. This is a research benchmark built from synthetic
patients. It must not be used to make eligibility determinations for real
patients.
"""

_NOTICE_NO_PHI = """\
NO PROTECTED HEALTH INFORMATION.

Every patient record in this dataset is synthetic: profiles are authored from a
written template and notes are rendered deterministically from those structured
fields. No record derives from a real person. Contributors must never add real
patient data to this dataset.
"""


def write_dataset(
    patients: List[Dict[str, Any]],
    criteria: List[Dict[str, Any]],
    annotations: List[Dict[str, Any]],
    trials_manifest: Optional[Dict[str, Any]] = None,
    integrity: Optional[Dict[str, Any]] = None,
    dest: Optional[str] = None,
) -> str:
    """Write the releasable dataset directory. Returns the path."""
    dest = dest or config.DATASET_DIR
    os.makedirs(dest, exist_ok=True)

    write_jsonl(os.path.join(dest, "patients", "patients.jsonl"), patients)
    write_jsonl(os.path.join(dest, "criteria", "criteria_atomic.jsonl"), criteria)
    write_jsonl(os.path.join(dest, "annotations", "pairs.jsonl"), annotations)

    write_json(os.path.join(dest, "schema", "patient.schema.json"), PATIENT_SCHEMA)
    write_json(os.path.join(dest, "schema", "criterion.schema.json"), CRITERION_SCHEMA)
    write_json(os.path.join(dest, "schema", "annotation.schema.json"), ANNOTATION_SCHEMA)

    write_text(os.path.join(dest, "LICENSE"), _LICENSE_TEXT)
    write_text(os.path.join(dest, "NOTICE_NO_PHI.txt"), _NOTICE_NO_PHI)

    if trials_manifest:
        write_json(os.path.join(dest, "trials", "manifest.json"), trials_manifest)
    if integrity:
        write_json(os.path.join(dest, "INTEGRITY_REPORT.json"), integrity)

    write_text(os.path.join(dest, "README.md"), _dataset_readme(integrity, trials_manifest))
    write_text(os.path.join(dest, "DATA_DICTIONARY.md"), _data_dictionary())
    return dest


def _dataset_readme(integrity, trials_manifest) -> str:
    n_pat = (integrity or {}).get("n_patients", "[N]")
    n_crit = (integrity or {}).get("n_criteria", "[N]")
    n_ann = (integrity or {}).get("n_annotations", "[N]")
    pulled = (trials_manifest or {}).get("built", "[pull date]")
    dist = (integrity or {}).get("label_distribution", {})
    dist_lines = "\n".join(
        f"  - {k}: {v['count']} ({v['fraction']:.1%})" for k, v in sorted(dist.items())
    ) or "  - [to be filled]"
    return f"""# TrialBridge-Bench

A synthetic patient--trial benchmark with per-criterion eligibility annotations
and evidence spans for oncology.

- Patients (synthetic): {n_pat}
- Atomic criteria: {n_crit}
- Annotated (patient, criterion) pairs: {n_ann}
- ClinicalTrials.gov snapshot pulled: {pulled}

Label distribution:
{dist_lines}

## Layout

```
patients/patients.jsonl        one record per synthetic patient (profile + clean/degraded notes)
criteria/criteria_atomic.jsonl one record per atomic eligibility criterion, with provenance to its trial
annotations/pairs.jsonl        one gold record per (patient, criterion, arm)
trials/manifest.json           frozen CT.gov snapshot provenance (queries, pull date, counts)
schema/                        JSON Schema for each record type
INTEGRITY_REPORT.json          automated checks run at build time
LICENSE                        LAYERED -- read before reuse
```

## The equity arm

Each patient has two notes describing the SAME underlying person: `clean`
(academic-style) and `degraded` (community-style, produced by pre-registered
transforms). Gold labels are shared across arms under the
`{config.DEGRADED_GOLD_POLICY}` policy, so paired comparisons isolate the effect
of documentation style. Compare arms with a paired analysis, never unpaired.

## Reuse limits

Patients are synthetic and hand-authored, not a real clinical population. The
degraded arm is a principled proxy for, not a sample of, real community
documentation. Oncology-only, English-only, one dated trial snapshot.

NOT FOR CLINICAL USE.
"""


def _data_dictionary() -> str:
    return """# Data dictionary

## patients.jsonl

| field | type | meaning |
|---|---|---|
| `patient_id` | string | stable id, e.g. `nsclc-012` |
| `cohort` | string | `nsclc` \\| `neoantigen` |
| `synthetic` | bool | always true |
| `profile` | object | ground-truth structured profile, known by construction |
| `profile.biomarkers[]` | object | `{{gene, alteration, status, evidence}}`; status = present\\|absent\\|unknown |
| `profile.hla[]` | string | HLA alleles, e.g. `HLA-A*02:01` |
| `profile.labs` | object | numeric labs (ANC, Platelets, Creatinine, Bilirubin, AST, ALT, Hemoglobin) |
| `notes.clean` | string | academic-style note, deterministically rendered from `profile` |
| `notes.degraded` | string | community-style note, same patient |
| `degrade_transforms[]` | string | transforms applied to produce `notes.degraded` |

## criteria_atomic.jsonl

| field | type | meaning |
|---|---|---|
| `criterion_id` | string | `<nct_id>-c<index>` |
| `nct_id` | string | source trial |
| `text` | string | atomic criterion text |
| `kind` | string | `inclusion` \\| `exclusion` |
| `ctype` | string | demographic\\|performance\\|disease\\|molecular\\|lab\\|prior_therapy\\|logistical\\|other |
| `compound` | bool | flagged as likely compound (segmentation may be imperfect) |
| `source_snapshot` | string | snapshot file the criterion came from |

## pairs.jsonl (annotations)

| field | type | meaning |
|---|---|---|
| `pair_id` | string | `<patient_id>::<criterion_id>::<arm>` |
| `arm` | string | `clean` \\| `degraded` |
| `label` | string | ELIGIBLE \\| INELIGIBLE \\| UNCERTAIN |
| `evidence_span` | string | VERBATIM substring of that arm's note; `""` when no support exists |
| `difficulty` | string | easy \\| medium \\| hard |
| `annotator_id` | string | `A1` primary, `A2` second annotator (IAA slice) |
| `provenance` | string | `human` (required for release) or `derived` (proposal only) |

`UNCERTAIN` means the note does not contain the information needed to decide --
it is a real gold label, not a missing value.
"""
