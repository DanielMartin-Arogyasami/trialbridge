"""
bench.patients — STEP 3: author the synthetic molecular gold patients.

Notes are rendered from a template (not a model) for deterministic CC-BY-clean
notes. The spec is ground truth by construction.

Run:
    python -m bench.patients --build
    python -m bench.patients --check
    python -m bench.patients --show P
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trialbridge import Biomarker, PatientProfile, degrade_note  # noqa: E402

from . import config, store  # noqa: E402


@dataclass
class PatientSpec:
    """A hand-authored synthetic patient. Every field is ground truth."""

    pid: str
    cohort: str
    age: int
    sex: str
    diagnosis: str
    stage: str
    ecog: int
    histology: Optional[str] = None
    biomarkers: List[Dict[str, str]] = field(default_factory=list)
    hla: List[str] = field(default_factory=list)
    hla_tested: bool = True
    ngs_performed: bool = True
    prior_lines: int = 0
    prior_therapies: List[str] = field(default_factory=list)
    labs: Dict[str, float] = field(default_factory=dict)
    serologies: Dict[str, str] = field(default_factory=dict)
    comorbidities: List[str] = field(default_factory=list)
    measurable_disease: Optional[bool] = None
    tissue_available: Optional[bool] = None
    resection_status: Optional[str] = None
    brain_mets: str = "none"
    note_intent: str = ""
    ack_warnings: List[str] = field(default_factory=list)

    def render_note(self) -> str:
        """Deterministic academic-style note. No model involved."""
        L: List[str] = []
        L.append("Oncology consult note (synthetic).")
        L.append(f"Patient: {self.age} year old {self.sex}. ECOG PS {self.ecog}.")
        dx = f"Diagnosis: {self.diagnosis}"
        if self.histology:
            dx += f" ({self.histology})"
        dx += f", stage {self.stage}"
        if self.resection_status:
            dx += (", status post resection (resected)"
                   if self.resection_status == "resected" else ", unresected")
        L.append(dx + ".")

        if self.ngs_performed and self.biomarkers:
            parts = []
            for b in self.biomarkers:
                gene = b["gene"]
                alt = b.get("alteration") or ""
                if b.get("status") == "present":
                    parts.append(f"{gene} {alt} mutation".replace("  ", " ").strip()
                                 if alt else f"{gene} positive")
                elif b.get("status") == "absent":
                    parts.append(f"{gene} wild-type" if not alt else f"{gene} {alt} not detected")
                else:
                    parts.append(f"{gene} indeterminate")
            mol = "Molecular: tumor NGS shows " + "; ".join(parts) + "."
            if self.hla_tested and self.hla:
                mol += " HLA typing " + ", ".join(self.hla) + "."
            elif not self.hla_tested:
                mol += " HLA typing not performed."
            L.append(mol)
        elif not self.ngs_performed:
            L.append("Molecular: no tumor NGS performed to date.")

        if self.tissue_available is True:
            L.append("Archival FFPE tumor tissue available and sufficient for sequencing.")
        elif self.tissue_available is False:
            L.append("No archival tumor tissue available; insufficient for sequencing.")

        if self.prior_lines or self.prior_therapies:
            th = f"Prior therapy: {self.prior_lines} prior line(s) of systemic therapy"
            if self.prior_therapies:
                th += " (" + ", ".join(self.prior_therapies) + ")"
            L.append(th + ".")
        else:
            L.append("Prior therapy: treatment naive, no prior systemic therapy.")

        if self.brain_mets == "active":
            L.append("History of brain metastases, currently active and untreated.")
        elif self.brain_mets == "treated_stable":
            L.append("History of brain metastases, treated with SRS, currently stable/controlled.")

        if self.measurable_disease is True:
            L.append("Measurable disease present per RECIST 1.1.")
        elif self.measurable_disease is False:
            L.append("No measurable disease per RECIST 1.1.")

        if self.labs:
            order = ["ANC", "Platelets", "Creatinine", "Bilirubin", "AST", "ALT", "Hemoglobin"]
            keys = [k for k in order if k in self.labs] + \
                   [k for k in self.labs if k not in order]
            L.append("Labs: " + ", ".join(f"{k} {self.labs[k]}" for k in keys) + ".")

        if self.serologies:
            L.append("Serologies: " +
                     ", ".join(f"{k} {v}" for k, v in sorted(self.serologies.items())) + ".")

        L.append("Comorbidities: " +
                 (", ".join(self.comorbidities) if self.comorbidities else "none significant") + ".")
        return "\n".join(L) + "\n"

    def to_profile(self) -> PatientProfile:
        return PatientProfile(
            age=self.age,
            sex=self.sex,
            diagnosis=self.diagnosis,
            histology=self.histology,
            stage=self.stage,
            ecog=self.ecog,
            biomarkers=[
                Biomarker(gene=b["gene"], alteration=b.get("alteration"),
                          status=b.get("status", "present"))
                for b in self.biomarkers
            ] if self.ngs_performed else [],
            hla=list(self.hla) if self.hla_tested else [],
            prior_lines=self.prior_lines,
            prior_therapies=list(self.prior_therapies),
            labs=dict(self.labs),
            comorbidities=list(self.comorbidities),
            serologies=dict(self.serologies),
            measurable_disease=self.measurable_disease,
            tissue_available=self.tissue_available,
            resection_status=self.resection_status,
            raw_note=self.render_note(),
        )

    def validate(self) -> List[str]:
        found: List[tuple] = []
        if not (18 <= self.age <= 100):
            found.append(("age_range", f"implausible age {self.age}"))
        if not (0 <= self.ecog <= 4):
            found.append(("ecog_range", f"ECOG {self.ecog} out of range"))
        if self.sex.lower() not in {"female", "male"}:
            found.append(("sex_value", f"unexpected sex '{self.sex}'"))
        for b in self.biomarkers:
            if b.get("status") not in {"present", "absent", "unknown"}:
                found.append(("biomarker_status", f"biomarker {b.get('gene')} bad status"))
        if self.hla and not self.hla_tested:
            found.append(("hla_contradiction", "hla listed but hla_tested=False"))
        if self.biomarkers and not self.ngs_performed:
            found.append(("ngs_contradiction", "biomarkers listed but ngs_performed=False"))
        if self.prior_lines == 0 and self.prior_therapies:
            found.append(("prior_contradiction", "prior_lines=0 but prior_therapies listed"))
        if self.brain_mets not in {"none", "treated_stable", "active"}:
            found.append(("brain_mets_value", "bad brain_mets value"))
        if self.resection_status == "resected" and self.measurable_disease is True:
            found.append(("resected_with_measurable",
                          "resected but measurable disease present (usually contradictory: "
                          "a fully resected patient has no measurable target lesion)"))
        if self.tissue_available is False and self.ngs_performed and self.biomarkers:
            found.append(("ngs_without_tissue",
                          "NGS results but no archival tissue (plausible only if testing was "
                          "done elsewhere or tissue is exhausted -- ack if intended)"))
        return [f"{self.pid}: [{code}] {msg}"
                for code, msg in found if code not in self.ack_warnings]


_LABS_NORMAL = {"ANC": 3.0, "Platelets": 210, "Creatinine": 0.9,
                "Bilirubin": 0.6, "AST": 24, "ALT": 22, "Hemoglobin": 12.6}
_SERO_NEG = {"HIV": "negative", "HBV": "negative", "HCV": "negative"}

PATIENT_LIBRARY: List[PatientSpec] = [
    PatientSpec(
        pid="nsclc-001", cohort="nsclc", age=67, sex="male",
        diagnosis="non-small cell lung cancer", histology="adenocarcinoma",
        stage="IV", ecog=1,
        biomarkers=[{"gene": "EGFR", "alteration": "exon 21 L858R", "status": "present"},
                    {"gene": "ALK", "status": "absent"}],
        prior_lines=1, prior_therapies=["carboplatin/pemetrexed"],
        labs=dict(_LABS_NORMAL), serologies=dict(_SERO_NEG),
        measurable_disease=True, brain_mets="treated_stable",
        note_intent="Baseline ELIGIBLE case for EGFR-inclusion criteria.",
    ),
    PatientSpec(
        pid="nsclc-002", cohort="nsclc", age=61, sex="female",
        diagnosis="non-small cell lung cancer", histology="adenocarcinoma",
        stage="IV", ecog=1,
        biomarkers=[{"gene": "KRAS", "alteration": "G12C", "status": "present"},
                    {"gene": "EGFR", "status": "absent"},
                    {"gene": "ALK", "status": "absent"}],
        prior_lines=1, prior_therapies=["pembrolizumab"],
        labs=dict(_LABS_NORMAL), serologies=dict(_SERO_NEG),
        measurable_disease=True,
        note_intent="EGFR WILD-TYPE: must be INELIGIBLE on EGFR-mutation inclusion. "
                    "This is the case the faithfulness gate exists for.",
    ),
    PatientSpec(
        pid="nsclc-003", cohort="nsclc", age=54, sex="female",
        diagnosis="non-small cell lung cancer", histology="adenocarcinoma",
        stage="IIIB", ecog=0,
        biomarkers=[{"gene": "ALK", "alteration": "EML4-ALK rearrangement", "status": "present"},
                    {"gene": "EGFR", "status": "absent"}],
        prior_lines=0, labs=dict(_LABS_NORMAL), serologies=dict(_SERO_NEG),
        measurable_disease=True,
        note_intent="ALK-positive: INELIGIBLE on 'known ALK rearrangement' EXCLUSION criteria.",
    ),
    PatientSpec(
        pid="nsclc-004", cohort="nsclc", age=78, sex="male",
        diagnosis="non-small cell lung cancer", histology="squamous cell carcinoma",
        stage="IV", ecog=3,
        biomarkers=[{"gene": "EGFR", "status": "absent"}],
        prior_lines=2, prior_therapies=["carboplatin/paclitaxel", "docetaxel"],
        labs=dict(_LABS_NORMAL), serologies=dict(_SERO_NEG),
        measurable_disease=True, comorbidities=["COPD"],
        note_intent="ECOG 3: INELIGIBLE on 'ECOG 0-1' and 'ECOG 0-2' performance criteria.",
    ),
    PatientSpec(
        pid="nsclc-005", cohort="nsclc", age=70, sex="male",
        diagnosis="non-small cell lung cancer", histology="adenocarcinoma",
        stage="IV", ecog=1,
        biomarkers=[{"gene": "EGFR", "alteration": "exon 19 deletion", "status": "present"}],
        prior_lines=1, prior_therapies=["osimertinib"],
        labs={"ANC": 0.9, "Platelets": 68, "Creatinine": 2.4,
              "Bilirubin": 2.8, "AST": 140, "ALT": 155, "Hemoglobin": 8.1},
        serologies=dict(_SERO_NEG), measurable_disease=True,
        note_intent="Marrow/renal/hepatic failure: INELIGIBLE on adequate-organ-function LAB criteria.",
    ),
    PatientSpec(
        pid="nsclc-006", cohort="nsclc", age=59, sex="female",
        diagnosis="non-small cell lung cancer", histology="adenocarcinoma",
        stage="IV", ecog=2,
        biomarkers=[{"gene": "EGFR", "alteration": "exon 21 L858R", "status": "present"}],
        prior_lines=2, prior_therapies=["osimertinib", "carboplatin/pemetrexed"],
        labs=dict(_LABS_NORMAL), serologies=dict(_SERO_NEG),
        measurable_disease=True, brain_mets="active",
        note_intent="ACTIVE untreated brain mets: INELIGIBLE on that EXCLUSION criterion.",
    ),
    PatientSpec(
        pid="nsclc-007", cohort="nsclc", age=65, sex="male",
        diagnosis="non-small cell lung cancer", histology="adenocarcinoma",
        stage="IV", ecog=1,
        biomarkers=[{"gene": "EGFR", "alteration": "exon 19 deletion", "status": "present"}],
        prior_lines=4,
        prior_therapies=["osimertinib", "carboplatin/pemetrexed", "docetaxel", "ramucirumab"],
        labs=dict(_LABS_NORMAL), serologies=dict(_SERO_NEG), measurable_disease=True,
        note_intent="4 prior lines: INELIGIBLE on 'no more than 2 prior lines' PRIOR_THERAPY criteria.",
    ),
    PatientSpec(
        pid="nsclc-008", cohort="nsclc", age=72, sex="female",
        diagnosis="non-small cell lung cancer", histology="adenocarcinoma",
        stage="IV", ecog=1, ngs_performed=False, biomarkers=[],
        prior_lines=1, prior_therapies=["carboplatin/pemetrexed"],
        labs=dict(_LABS_NORMAL), serologies=dict(_SERO_NEG), measurable_disease=True,
        note_intent="NO NGS: molecular criteria must be UNCERTAIN, not guessed. "
                    "Tests that the model abstains instead of inventing eligibility.",
    ),
    PatientSpec(
        pid="neo-001", cohort="neoantigen", age=58, sex="female",
        diagnosis="cutaneous melanoma", stage="IIIC", ecog=1,
        biomarkers=[{"gene": "TP53", "alteration": "R175H", "status": "present"},
                    {"gene": "KRAS", "status": "absent"}],
        hla=["HLA-A*02:01"], prior_lines=1, prior_therapies=["pembrolizumab"],
        labs=dict(_LABS_NORMAL), serologies=dict(_SERO_NEG),
        measurable_disease=False, tissue_available=True, resection_status="resected",
        note_intent="Baseline ELIGIBLE flagship: HLA-A*02:01, tissue available, fully resected "
                    "(adjuvant setting, so no measurable disease).",
    ),
    PatientSpec(
        pid="neo-002", cohort="neoantigen", age=63, sex="male",
        diagnosis="cutaneous melanoma", stage="IV", ecog=1,
        biomarkers=[{"gene": "BRAF", "alteration": "V600E", "status": "present"}],
        hla=["HLA-A*01:01"], prior_lines=2, prior_therapies=["nivolumab", "dabrafenib/trametinib"],
        labs=dict(_LABS_NORMAL), serologies=dict(_SERO_NEG),
        measurable_disease=True, tissue_available=False,
        ack_warnings=["ngs_without_tissue"],
        note_intent="NO TISSUE (prior NGS done at an outside lab; archival block exhausted): "
                    "INELIGIBLE on tissue-availability inclusion -- a criterion type "
                    "synthetic generators cannot express at all.",
    ),
    PatientSpec(
        pid="neo-003", cohort="neoantigen", age=49, sex="female",
        diagnosis="cutaneous melanoma", stage="IIIB", ecog=0,
        biomarkers=[{"gene": "NRAS", "alteration": "Q61K", "status": "present"}],
        hla=[], hla_tested=False,
        prior_lines=1, prior_therapies=["pembrolizumab"],
        labs=dict(_LABS_NORMAL), serologies=dict(_SERO_NEG),
        measurable_disease=False, tissue_available=True, resection_status="resected",
        note_intent="HLA NOT TYPED: HLA-restriction criteria must be UNCERTAIN.",
    ),
    PatientSpec(
        pid="neo-004", cohort="neoantigen", age=55, sex="male",
        diagnosis="cutaneous melanoma", stage="IIIC", ecog=1,
        biomarkers=[{"gene": "TP53", "alteration": "R248Q", "status": "present"}],
        hla=["HLA-A*02:01"], prior_lines=1, prior_therapies=["ipilimumab/nivolumab"],
        labs=dict(_LABS_NORMAL),
        serologies={"HIV": "positive", "HBV": "negative", "HCV": "negative"},
        measurable_disease=False, tissue_available=True, resection_status="resected",
        note_intent="HIV positive: INELIGIBLE on serology EXCLUSION criteria.",
    ),
]


def build(specs: List[PatientSpec] = None) -> List[Dict[str, Any]]:
    specs = specs or PATIENT_LIBRARY
    records: List[Dict[str, Any]] = []
    all_issues: List[str] = []
    seen = set()
    for s in specs:
        if s.pid in seen:
            raise SystemExit(f"duplicate patient id: {s.pid}")
        seen.add(s.pid)
        issues = s.validate()
        all_issues.extend(issues)
        clean = s.render_note()
        degraded = degrade_note(clean, config.DEGRADE_TRANSFORMS)
        records.append(
            {
                "patient_id": s.pid,
                "cohort": s.cohort,
                "synthetic": True,
                "profile": s.to_profile().to_dict(include_note=False),
                "notes": {"clean": clean, "degraded": degraded},
                "degrade_transforms": list(config.DEGRADE_TRANSFORMS),
                "authoring_intent": s.note_intent,
                "spec": dataclasses.asdict(s),
            }
        )
    store.write_jsonl(config.PATIENTS_FILE, records)
    print(f"[patients] wrote {len(records)} patients -> {config.PATIENTS_FILE}")
    if all_issues:
        print("\n--- CONSISTENCY ISSUES (fix before annotating) ---")
        for i in all_issues:
            print("  !", i)
    else:
        print("[patients] all specs internally consistent")
    if len(records) < config.TARGET_PATIENTS:
        print(f"\n[patients] NOTE: {len(records)} patients, target is {config.TARGET_PATIENTS}.")
        print("           Copy entries in PATIENT_LIBRARY and vary them to scale up.")
    print("\nNext: python -m bench.autolabel")
    return records


def load_specs() -> Dict[str, PatientSpec]:
    return {s.pid: s for s in PATIENT_LIBRARY}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Author and render synthetic patients.")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--show", metavar="PID")
    args = ap.parse_args(argv)
    config.ensure_dirs()

    if args.show:
        s = load_specs().get(args.show)
        if not s:
            raise SystemExit(f"unknown patient {args.show}. Known: {sorted(load_specs())}")
        clean = s.render_note()
        print("=" * 66 + "\nCLEAN (academic-style)\n" + "=" * 66)
        print(clean)
        print("=" * 66 + "\nDEGRADED (community-style)\n" + "=" * 66)
        print(degrade_note(clean, config.DEGRADE_TRANSFORMS))
        print("intent:", s.note_intent)
        return 0

    if args.check:
        issues = [i for s in PATIENT_LIBRARY for i in s.validate()]
        print(f"{len(PATIENT_LIBRARY)} patients, {len(issues)} issue(s)")
        for i in issues:
            print("  !", i)
        return 1 if issues else 0

    build()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
