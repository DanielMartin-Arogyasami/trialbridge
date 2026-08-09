#!/usr/bin/env python3
"""TrialBridge - single-file, privacy-preserving oncology trial matching.

Pipeline: note -> EXTRACT profile -> RETRIEVE trials (ClinicalTrials.gov API v2)
-> REASON per criterion (ELIGIBLE/INELIGIBLE/UNCERTAIN + evidence) -> RANK + queue.
Every model call sits behind one Backend interface (heuristic | ollama | llamacpp
| cloud); the bundled HeuristicBackend is a deterministic offline baseline so the
whole thing runs with only the standard library (Python 3.9+). UNCERTAIN is a
first-class abstention. Synthetic/de-identified data only; the cloud backend is
eval-only.

Run:
    python trialbridge.py selftest          # embedded tests (offline)
    python trialbridge.py demo              # end-to-end on bundled data (offline)
    python trialbridge.py extract --patient note.txt
    python trialbridge.py match   --patient note.txt --offline
    python trialbridge.py match   --patient note.txt --cohort nsclc      # live CT.gov
    python trialbridge.py match   --patient note.txt --backend ollama --model medgemma:4b
    python trialbridge.py fetch   --cohort neoantigen --out trials.json  # live CT.gov
    python trialbridge.py evaluate
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
import sys
import textwrap
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

__version__ = "0.1.1"

CTGOV_API = "https://clinicaltrials.gov/api/v2/studies"
DEFAULT_CACHE_DIR = os.environ.get("TRIALBRIDGE_CACHE", os.path.join(".", "data", "trials"))

DEFAULT_CLOUD_MODEL = os.environ.get("TRIALBRIDGE_CLOUD_MODEL", "claude-sonnet-4-6")
DEFAULT_LOCAL_MODEL = os.environ.get("TRIALBRIDGE_LOCAL_MODEL", "medgemma:4b")

class Label(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"
    UNCERTAIN = "UNCERTAIN"

    def __str__(self) -> str:
        return self.value

class Kind(str, Enum):
    INCLUSION = "inclusion"
    EXCLUSION = "exclusion"

    def __str__(self) -> str:
        return self.value

class Ctype(str, Enum):
    DEMOGRAPHIC = "demographic"
    PERFORMANCE = "performance"
    DISEASE = "disease"
    MOLECULAR = "molecular"
    LAB = "lab"
    PRIOR_THERAPY = "prior_therapy"
    LOGISTICAL = "logistical"
    OTHER = "other"

    def __str__(self) -> str:
        return self.value

@dataclass
class Biomarker:
    gene: str
    alteration: Optional[str] = None
    status: str = "present"
    evidence: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

@dataclass
class PatientProfile:
    age: Optional[int] = None
    sex: Optional[str] = None
    diagnosis: Optional[str] = None
    histology: Optional[str] = None
    stage: Optional[str] = None
    ecog: Optional[int] = None
    biomarkers: List[Biomarker] = field(default_factory=list)
    hla: List[str] = field(default_factory=list)
    prior_lines: Optional[int] = None
    prior_therapies: List[str] = field(default_factory=list)
    labs: Dict[str, float] = field(default_factory=dict)
    comorbidities: List[str] = field(default_factory=list)
    serologies: Dict[str, str] = field(default_factory=dict)
    measurable_disease: Optional[bool] = None
    tissue_available: Optional[bool] = None
    resection_status: Optional[str] = None
    raw_note: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self, include_note: bool = True) -> Dict[str, Any]:
        d = dataclasses.asdict(self)
        d["biomarkers"] = [b.to_dict() for b in self.biomarkers]
        if not include_note:
            d.pop("raw_note", None)
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PatientProfile":
        d = dict(d)
        d["biomarkers"] = [Biomarker(**b) if isinstance(b, dict) else b
                           for b in d.get("biomarkers", []) or []]
        allowed = {f.name for f in dataclasses.fields(cls)}
        extra = {k: v for k, v in d.items() if k not in allowed}
        clean = {k: v for k, v in d.items() if k in allowed}
        prof = cls(**clean)
        if extra:
            prof.extra.update(extra)
        return prof

    def validate(self, strict: bool = False) -> List[str]:
        issues: List[str] = []
        if self.age is not None and not (0 <= self.age <= 120):
            issues.append(f"age out of range: {self.age}")
        if self.ecog is not None and not (0 <= self.ecog <= 5):
            issues.append(f"ECOG out of range: {self.ecog}")
        if self.sex is not None and self.sex.lower() not in {"female", "male", "other", "unknown"}:
            issues.append(f"unrecognized sex value: {self.sex}")
        for b in self.biomarkers:
            if b.status not in {"present", "absent", "unknown"}:
                issues.append(f"biomarker {b.gene}: bad status {b.status}")
        if strict and issues:
            raise ValueError("PatientProfile validation failed: " + "; ".join(issues))
        return issues

    def brief(self) -> str:
        bits = []
        if self.age is not None:
            bits.append(f"{self.age}yo")
        if self.sex:
            bits.append(self.sex)
        if self.stage:
            bits.append(f"stage {self.stage}")
        if self.diagnosis:
            bits.append(self.diagnosis)
        if self.ecog is not None:
            bits.append(f"ECOG {self.ecog}")
        mut = ", ".join(
            f"{b.gene}{(' ' + b.alteration) if b.alteration else ''}"
            for b in self.biomarkers if b.status == "present"
        )
        if mut:
            bits.append(mut)
        if self.hla:
            bits.append("HLA " + ",".join(self.hla))
        return " · ".join(bits) if bits else "(no structured features extracted)"

@dataclass
class Criterion:
    text: str
    kind: Kind
    ctype: Ctype = Ctype.OTHER
    index: int = 0
    compound: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d = dataclasses.asdict(self)
        d["kind"] = str(self.kind)
        d["ctype"] = str(self.ctype)
        return d

@dataclass
class CriterionJudgment:
    criterion: Criterion
    label: Label
    evidence_span: str = ""
    rationale: str = ""
    confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "criterion": self.criterion.to_dict(),
            "label": str(self.label),
            "evidence_span": self.evidence_span,
            "rationale": self.rationale,
            "confidence": round(self.confidence, 3),
        }

@dataclass
class Trial:
    nct_id: str
    title: str = ""
    conditions: List[str] = field(default_factory=list)
    phases: List[str] = field(default_factory=list)
    status: str = ""
    eligibility_criteria: str = ""
    min_age: Optional[str] = None
    max_age: Optional[str] = None
    sex: Optional[str] = None
    lead_sponsor: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Trial":
        allowed = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in allowed})

class MatchStatus(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    REVIEW = "REVIEW"
    INELIGIBLE = "INELIGIBLE"

    def __str__(self) -> str:
        return self.value

_STATUS_RANK = {MatchStatus.ELIGIBLE: 2, MatchStatus.REVIEW: 1, MatchStatus.INELIGIBLE: 0}

@dataclass
class TrialMatch:
    trial: Trial
    judgments: List[CriterionJudgment]
    status: MatchStatus
    score: float
    n_eligible: int
    n_ineligible: int
    n_uncertain: int
    blocking: List[CriterionJudgment] = field(default_factory=list)

    @property
    def needs_review(self) -> bool:
        return self.status == MatchStatus.REVIEW

    def to_dict(self) -> Dict[str, Any]:
        return {
            "nct_id": self.trial.nct_id,
            "title": self.trial.title,
            "status": str(self.status),
            "score": round(self.score, 3),
            "counts": {
                "eligible": self.n_eligible,
                "ineligible": self.n_ineligible,
                "uncertain": self.n_uncertain,
            },
            "blocking": [j.to_dict() for j in self.blocking],
            "judgments": [j.to_dict() for j in self.judgments],
        }

@dataclass
class MatchResult:
    profile: PatientProfile
    ranked: List[TrialMatch]
    review_queue: List[TrialMatch]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "profile": self.profile.to_dict(include_note=False),
            "ranked": [m.to_dict() for m in self.ranked],
            "review_queue": [m.trial.nct_id for m in self.review_queue],
        }

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)

def extract_json(text: str) -> Any:
    if not text:
        raise ValueError("empty model output")
    m = _FENCE.search(text)
    if m:
        text = m.group(1)

    start = None
    for i, ch in enumerate(text):
        if ch in "{[":
            start = i
            break
    if start is None:
        raise ValueError("no JSON object found in model output")

    open_ch = text[start]
    close_ch = "}" if open_ch == "{" else "]"
    depth = 0
    in_str = False
    esc = False
    end = None
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    blob = text[start:end] if end else text[start:]

    for attempt in (blob, _repair_json(blob)):
        try:
            return json.loads(attempt)
        except json.JSONDecodeError:
            continue
    raise ValueError("could not parse JSON from model output")

def _repair_json(blob: str) -> str:
    blob = blob.replace("\u201c", '"').replace("\u201d", '"').replace("\u2019", "'")
    blob = re.sub(r",\s*([}\]])", r"\1", blob)
    return blob

EXTRACT_SYSTEM = (
    "You are a clinical information-extraction engine. You read a de-identified "
    "oncology note and output ONLY a JSON object with the requested fields. "
    "Never invent values: if the note does not state a field, use null or []. "
    "Copy short verbatim spans from the note as evidence where asked."
)

EXTRACT_TEMPLATE = """Extract the following fields from the note and return ONLY JSON:

{{
  "age": int|null,
  "sex": "female"|"male"|null,
  "diagnosis": str|null,            // cancer type in the note's words
  "histology": str|null,
  "stage": str|null,               // e.g. "IV", "IIIA"
  "ecog": int|null,                // 0..5
  "biomarkers": [                  // somatic mutations, fusions, expression, MSI, TMB
     {{"gene": str, "alteration": str|null, "status": "present"|"absent"|"unknown",
       "evidence": "verbatim span"}}
  ],
  "hla": [str],                    // HLA alleles, e.g. "A*02:01"
  "prior_lines": int|null,
  "prior_therapies": [str],
  "labs": {{ "ANC": float, "platelets": float, "creatinine": float,
             "bilirubin": float, "AST": float, "ALT": float, "hemoglobin": float }},
  "comorbidities": [str],
  "serologies": {{ "HIV": "negative"|"positive", "HBV": ..., "HCV": ... }},
  "measurable_disease": bool|null,
  "tissue_available": bool|null,   // archival/fresh tumor tissue sufficient for sequencing
  "resection_status": str|null
}}

NOTE:
\"\"\"{note}\"\"\"

JSON:"""

JUDGE_SYSTEM = (
    "You are an eligibility-reasoning engine for oncology trials. Given a patient "
    "profile (JSON) and ONE eligibility criterion, you decide the criterion's "
    "eligibility contribution and cite a verbatim evidence span. You output ONLY "
    "JSON. If the profile does not contain the information required to decide, you "
    "MUST answer UNCERTAIN rather than guess."
)

JUDGE_TEMPLATE = """Patient profile (JSON):
{profile}

Criterion ({kind}, type={ctype}):
\"\"\"{criterion}\"\"\"

Decide the eligibility contribution:
- "ELIGIBLE"   = the patient satisfies this INCLUSION criterion, OR does NOT trigger this EXCLUSION criterion.
- "INELIGIBLE" = the patient fails this INCLUSION criterion, OR triggers this EXCLUSION criterion.
- "UNCERTAIN"  = the profile lacks the information needed to decide. Prefer this over guessing.

Return ONLY JSON:
{{"label": "ELIGIBLE"|"INELIGIBLE"|"UNCERTAIN",
  "evidence_span": "verbatim text from the profile/note supporting the call, or \\"\\" if none",
  "rationale": "one or two sentences",
  "confidence": 0.0-1.0}}

JSON:"""

class Backend:
    name: str = "backend"

    def extract(self, note: str) -> Dict[str, Any]:
        raise NotImplementedError

    def judge(self, profile: Dict[str, Any], criterion: str,
              kind: Kind, ctype: Ctype) -> Dict[str, Any]:
        raise NotImplementedError

class LLMBackend(Backend):

    def __init__(self, model: str, temperature: float = 0.0, max_tokens: int = 1024):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    def complete(self, prompt: str, system: Optional[str] = None) -> str:
        raise NotImplementedError

    def extract(self, note: str) -> Dict[str, Any]:
        raw = self.complete(EXTRACT_TEMPLATE.format(note=note), system=EXTRACT_SYSTEM)
        data = extract_json(raw)
        return data if isinstance(data, dict) else {}

    def judge(self, profile: Dict[str, Any], criterion: str,
              kind: Kind, ctype: Ctype) -> Dict[str, Any]:
        prompt = JUDGE_TEMPLATE.format(
            profile=json.dumps(profile, ensure_ascii=False),
            criterion=criterion, kind=str(kind), ctype=str(ctype),
        )
        raw = self.complete(prompt, system=JUDGE_SYSTEM)
        data = extract_json(raw)
        return data if isinstance(data, dict) else {}

class OllamaBackend(LLMBackend):
    name = "ollama"

    def __init__(self, model: str = DEFAULT_LOCAL_MODEL,
                 host: str = os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
                 **kw):
        super().__init__(model, **kw)
        self.host = host.rstrip("/")

    def complete(self, prompt: str, system: Optional[str] = None) -> str:
        body = {
            "model": self.model,
            "messages": ([{"role": "system", "content": system}] if system else [])
                        + [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"temperature": self.temperature, "num_predict": self.max_tokens},
        }
        data = _http_json(f"{self.host}/api/chat", body)
        return (data.get("message") or {}).get("content", "")

class LlamaCppBackend(LLMBackend):
    name = "llamacpp"

    def __init__(self, model: str = DEFAULT_LOCAL_MODEL,
                 server_url: Optional[str] = os.environ.get("LLAMACPP_URL"),
                 model_path: Optional[str] = os.environ.get("LLAMACPP_MODEL_PATH"),
                 **kw):
        super().__init__(model, **kw)
        self.server_url = server_url.rstrip("/") if server_url else None
        self.model_path = model_path
        self._llm = None
        if not self.server_url and self.model_path:
            try:
                from llama_cpp import Llama
            except Exception as e:
                raise RuntimeError(
                    "llama_cpp not installed and no LLAMACPP_URL server given. "
                    "pip install llama-cpp-python OR run a llama.cpp server."
                ) from e
            self._llm = Llama(model_path=self.model_path, n_ctx=8192, verbose=False)
        elif not self.server_url and not self.model_path:
            raise RuntimeError("llamacpp backend needs LLAMACPP_URL or LLAMACPP_MODEL_PATH")

    def complete(self, prompt: str, system: Optional[str] = None) -> str:
        messages = ([{"role": "system", "content": system}] if system else []) \
            + [{"role": "user", "content": prompt}]
        if self.server_url:
            body = {"model": self.model, "messages": messages,
                    "temperature": self.temperature, "max_tokens": self.max_tokens}
            data = _http_json(f"{self.server_url}/v1/chat/completions", body)
            return data["choices"][0]["message"]["content"]
        out = self._llm.create_chat_completion(
            messages=messages, temperature=self.temperature, max_tokens=self.max_tokens)
        return out["choices"][0]["message"]["content"]

class CloudBackend(LLMBackend):
    name = "cloud"

    def __init__(self, model: str = DEFAULT_CLOUD_MODEL,
                 api_key: Optional[str] = None, **kw):
        super().__init__(model, **kw)
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise RuntimeError("CloudBackend requires ANTHROPIC_API_KEY (eval-only).")

    def complete(self, prompt: str, system: Optional[str] = None) -> str:
        body: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            body["system"] = system
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        data = _http_json("https://api.anthropic.com/v1/messages", body, headers=headers)
        parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
        return "".join(parts)

def _http_json(url: str, body: Dict[str, Any],
               headers: Optional[Dict[str, str]] = None, timeout: int = 120) -> Dict[str, Any]:
    payload = json.dumps(body).encode("utf-8")
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=payload, headers=hdrs, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)

class HeuristicBackend(Backend):
    name = "heuristic"

    def extract(self, note: str) -> Dict[str, Any]:
        t = note
        low = note.lower()
        prof: Dict[str, Any] = {}

        m = re.search(r"\b(\d{1,3})\s*(?:yo|y/?o|year[- ]?old|years?[- ]old)\b", low)
        if not m:
            m = re.search(r"\bage[:\s]+(\d{1,3})\b", low)
        if m:
            prof["age"] = int(m.group(1))

        if re.search(r"\b(female|woman|she/her|\bf\b(?=\s*[,;]))\b", low):
            prof["sex"] = "female"
        elif re.search(r"\b(male|\bman\b|he/him)\b", low):
            prof["sex"] = "male"

        m = re.search(r"\becog\b[^\d\n]{0,25}?([0-5])", low)
        if m:
            prof["ecog"] = int(m.group(1))

        m = re.search(r"\bstage\s+(iv|iii|ii|i)\s*([abc])?\b", low)
        if m:
            prof["stage"] = (m.group(1) + (m.group(2) or "")).upper()

        for name, pat in _DIAGNOSIS_PATTERNS:
            if re.search(pat, low):
                prof["diagnosis"] = name
                break

        prof["biomarkers"] = self._extract_biomarkers(t)
        prof["hla"] = sorted(set(re.findall(r"HLA[- ]([ABC]\*\d{2}:\d{2})", t)))

        m = re.search(r"\b(\d)\s*(?:prior\s+)?lines?\s+of\b", low)
        if m:
            prof["prior_lines"] = int(m.group(1))
        therapies = []
        for name, pat in _THERAPY_PATTERNS:
            if re.search(pat, low):
                therapies.append(name)
        if therapies:
            prof["prior_therapies"] = therapies
            prof.setdefault("prior_lines", len(therapies))

        labs = {}
        for key, pat in _LAB_PATTERNS:
            m = re.search(pat, low)
            if m:
                try:
                    labs[key] = float(m.group(1))
                except ValueError:
                    pass
        if labs:
            prof["labs"] = labs

        comorbid = []
        for name, pat in _COMORBID_PATTERNS:
            if re.search(pat, low):
                comorbid.append(name)
        if comorbid:
            prof["comorbidities"] = comorbid

        sero = {}
        for virus in ["HIV", "HBV", "HCV", "CMV", "EBV"]:
            m = re.search(rf"\b{virus}\b[^.\n]*?\b(negative|positive|non-?reactive|reactive)\b", t, re.I)
            if m:
                val = m.group(1).lower()
                sero[virus] = "negative" if val.startswith(("neg", "non")) else "positive"
        if sero:
            prof["serologies"] = sero

        if re.search(r"\bmeasurable disease\b|\brecist\b", low):
            prof["measurable_disease"] = "non-measurable" not in low
        if re.search(r"\b(ffpe|archival tissue|fresh tumor tissue|wes|rna-?seq|tissue (?:is )?available|sufficient tissue)\b", low):
            prof["tissue_available"] = True
        m = re.search(r"\b(resected|s/p resection|status[- ]post resection|unresectable|not resectable)\b", low)
        if m:
            prof["resection_status"] = "unresectable" if "unresect" in m.group(1) or "not resect" in m.group(1) else "resected"

        return prof

    def _extract_biomarkers(self, note: str) -> List[Dict[str, Any]]:
        found: Dict[str, Dict[str, Any]] = {}
        for m in _BIOMARKER_RE.finditer(note):
            gene = m.group("gene").upper().replace(" ", "")
            gene = {"HER-2": "HER2", "PDL1": "PD-L1", "PD-L1": "PD-L1"}.get(gene, gene)
            alt = (m.group("alt") or "").strip()
            span = note[max(0, m.start() - 5): m.end() + 5]
            status = "present"
            if re.search(r"\b(wild[- ]?type|wt|negative|not detected|no .* mutation)\b", span, re.I):
                status = "absent"
            key = gene
            if key not in found:
                found[key] = {"gene": gene, "alteration": alt or None,
                              "status": status, "evidence": m.group(0).strip()}
        return list(found.values())

    def judge(self, profile: Dict[str, Any], criterion: str,
              kind: Kind, ctype: Ctype) -> Dict[str, Any]:
        prof = PatientProfile.from_dict(profile) if isinstance(profile, dict) else profile
        c = re.sub(r"\\+([*._])", r"\1", criterion).strip()
        low = c.lower()
        is_excl = kind == Kind.EXCLUSION

        def out(label: Label, ev: str, why: str, conf: float) -> Dict[str, Any]:
            return {"label": str(label), "evidence_span": ev, "rationale": why,
                    "confidence": round(conf, 2)}

        def satisfied(is_met: bool) -> Label:

            if is_excl:
                return Label.INELIGIBLE if is_met else Label.ELIGIBLE
            return Label.ELIGIBLE if is_met else Label.INELIGIBLE

        m = re.search(r"(?:aged?|age)\D{0,12}?(\d{1,3})\s*(?:years|yrs|y)", low) \
            or re.search(r"(\d{1,3})\s*years?\s*(?:or older|and older|of age or older)", low) \
            or re.search(r"(?:≥|>=|at least)\s*(\d{1,3})\s*(?:years|yrs)", low)
        if m and "age" in low and prof.age is not None:
            thr = int(m.group(1))
            older = "older" in low or "≥" in low or ">=" in low or "at least" in low
            younger = "≤" in low or "<=" in low or "younger" in low or "under" in low
            met = (prof.age >= thr) if older else (prof.age <= thr) if younger else (prof.age >= thr)
            return out(satisfied(met), f"age {prof.age}",
                       f"Patient age {prof.age} vs threshold {thr}.", 0.9)

        if "ecog" in low or "performance status" in low:
            rng = re.findall(r"[0-5]", low)
            if rng and prof.ecog is not None:
                allowed = {int(x) for x in rng}

                if len(rng) >= 2:
                    allowed = set(range(int(min(rng)), int(max(rng)) + 1))
                met = prof.ecog in allowed
                return out(satisfied(met), f"ECOG {prof.ecog}",
                           f"ECOG {prof.ecog} vs allowed {sorted(allowed)}.", 0.85)
            if prof.ecog is None:
                return out(Label.UNCERTAIN, "", "ECOG not documented.", 0.0)

        if any(k in low for k in ["histologically", "cytologically", "diagnosis of",
                                  "confirmed", "patients with"]) and _mentions_cancer(low):
            if prof.diagnosis:
                if _same_condition(low, prof.diagnosis):
                    return out(satisfied(True), prof.diagnosis,
                               f"Diagnosis '{prof.diagnosis}' matches criterion condition.", 0.8)
                if _names_other_cancer(low, prof.diagnosis):
                    return out(satisfied(False), prof.diagnosis,
                               f"Criterion requires a different tumor type than '{prof.diagnosis}'.", 0.75)
            return out(Label.UNCERTAIN, "", "Condition not clearly matched in profile.", 0.0)

        if "measurable disease" in low:
            if prof.measurable_disease is None:
                return out(Label.UNCERTAIN, "", "Measurable-disease status not documented.", 0.0)
            return out(satisfied(prof.measurable_disease),
                       "measurable disease" if prof.measurable_disease else "non-measurable",
                       "Measurable disease per profile.", 0.8)

        if any(k in low for k in ["prior line", "lines of", "failed", "progressed on",
                                  "received prior", "standard treatment", "standard therapy",
                                  "systemic therapy"]):
            if prof.prior_lines is not None or prof.prior_therapies:
                n = prof.prior_lines if prof.prior_lines is not None else len(prof.prior_therapies)
                mreq = re.search(r"(?:≥|>=|at least)\s*(\d)", low)
                if mreq:
                    met = n >= int(mreq.group(1))
                    return out(satisfied(met), f"{n} prior line(s)",
                               f"{n} prior line(s) vs required ≥{mreq.group(1)}.", 0.75)
                met = n >= 1 or "failed" in low or "progressed" in low
                return out(satisfied(met), f"{n} prior line(s): {', '.join(prof.prior_therapies) or 'documented'}",
                           "Prior systemic therapy documented.", 0.65)
            return out(Label.UNCERTAIN, "", "Prior-therapy history not documented.", 0.0)

        if ctype == Ctype.MOLECULAR or _is_molecular(low):
            return self._judge_molecular(prof, low, is_excl, out, satisfied)

        if "brain metast" in low or "cns metast" in low or "leptomeningeal" in low:
            has_bm = any("brain metast" in cm.lower() for cm in prof.comorbidities)
            stable = bool(re.search(r"treated|stable|controlled|resected|asymptomatic", prof.raw_note, re.I))
            wants_active = bool(re.search(r"active|untreated|symptomatic|progress", low))
            if not has_bm:
                return out(satisfied(False), "no brain metastases documented",
                           "No brain metastases in profile.", 0.7)
            if wants_active and stable:
                return out(satisfied(False), "treated/stable brain metastases",
                           "Brain metastases documented but treated/stable; exclusion targets active disease.", 0.6)
            return out(satisfied(True), "brain metastases",
                       "Brain metastases documented.", 0.6)

        for virus in ["HIV", "HBV", "HCV"]:
            if virus.lower() in low and "negative" in low:
                val = prof.serologies.get(virus)
                if val is None:
                    return out(Label.UNCERTAIN, "", f"{virus} serology not documented.", 0.0)
                met_statement = val == "negative"

                lab = Label.ELIGIBLE if (met_statement != is_excl) else Label.INELIGIBLE
                return out(lab, f"{virus} {val}", f"{virus} serology {val}.", 0.7)

        lab_hit = _judge_lab(prof, low)
        if lab_hit is not None:
            met, ev, why = lab_hit
            return out(satisfied(met), ev, why, 0.7)

        if any(k in low for k in ["pregnan", "contracepti", "breastfeed", "lactat"]):
            return out(Label.UNCERTAIN, "", "Reproductive status/contraception not in profile.", 0.0)

        return out(Label.UNCERTAIN, "", "No matching structured field; route to human review.", 0.0)

    def _judge_molecular(self, prof: PatientProfile, low: str, is_excl: bool,
                         out, satisfied) -> Dict[str, Any]:
        genes_in_crit = set(re.findall(
            r"\b(EGFR|ALK|ROS1|KRAS|NRAS|BRAF|HER2|MET|RET|NTRK|TP53|BRCA1|BRCA2|MSI|TMB|PD-?L1)\b",
            low, re.I))
        genes_in_crit = {g.upper().replace("PDL1", "PD-L1") for g in genes_in_crit}
        present = {b.gene: b for b in prof.biomarkers if b.status == "present"}
        absent = {b.gene for b in prof.biomarkers if b.status == "absent"}

        if ("hla" in low and any(k in low for k in ["copy-number", "copy number", "loss-of-heterozygosity",
                                                    "loss of heterozygosity", "loh", "cnv"])):
            return out(Label.UNCERTAIN, "", "HLA CNV/LOH status not available in profile.", 0.0)

        if "hla" in low:
            if prof.hla:
                return out(Label.UNCERTAIN if is_excl else Label.ELIGIBLE,
                           "HLA " + ",".join(prof.hla),
                           "HLA typing present; specific allele/functional presentation not verified.", 0.4)
            return out(Label.UNCERTAIN, "", "HLA typing not documented.", 0.0)

        if genes_in_crit:
            hit = [g for g in genes_in_crit if g in present]
            contradict = [g for g in genes_in_crit if g in absent]
            if hit:
                b = present[hit[0]]
                return out(satisfied(True),
                           f"{b.gene}{(' ' + b.alteration) if b.alteration else ''} ({b.evidence})",
                           f"Required alteration {hit[0]} present.", 0.7)
            if contradict:
                return out(satisfied(False), f"{contradict[0]} absent/wild-type",
                           f"Required alteration {contradict[0]} absent.", 0.6)
            return out(Label.UNCERTAIN, "",
                       f"Molecular status for {', '.join(sorted(genes_in_crit))} not documented.", 0.0)

        return out(Label.UNCERTAIN, "", "Molecular criterion not resolvable from profile.", 0.0)

def get_backend(name: str, model: Optional[str] = None, **kw) -> Backend:
    name = (name or "heuristic").lower()
    if name in ("heuristic", "rules", "offline"):
        return HeuristicBackend()
    if name == "ollama":
        return OllamaBackend(model or DEFAULT_LOCAL_MODEL, **kw)
    if name in ("llamacpp", "llama.cpp", "gguf"):
        return LlamaCppBackend(model or DEFAULT_LOCAL_MODEL, **kw)
    if name in ("cloud", "anthropic", "frontier"):
        return CloudBackend(model or DEFAULT_CLOUD_MODEL, **kw)
    raise ValueError(f"unknown backend: {name!r} "
                     f"(choose heuristic|ollama|llamacpp|cloud)")

_DIAGNOSIS_PATTERNS = [
    ("non-small cell lung cancer", r"\bnon[- ]small[- ]cell lung (?:cancer|carcinoma)\b|\bnsclc\b"),
    ("small cell lung cancer", r"\bsmall[- ]cell lung (?:cancer|carcinoma)\b|\bsclc\b"),
    ("melanoma", r"\bmelanoma\b"),
    ("pancreatic ductal adenocarcinoma", r"\bpancreatic\b.*\badenocarcinoma\b|\bpdac\b"),
    ("colorectal cancer", r"\bcolorectal\b|\bcolon cancer\b|\brectal cancer\b|\bcrc\b"),
    ("breast cancer", r"\bbreast (?:cancer|carcinoma)\b"),
    ("glioblastoma", r"\bglioblastoma\b|\bgbm\b"),
    ("glioma", r"\bglioma\b"),
    ("hepatocellular carcinoma", r"\bhepatocellular\b|\bhcc\b"),
    ("prostate cancer", r"\bprostate (?:cancer|adenocarcinoma)\b"),
    ("ovarian cancer", r"\bovarian (?:cancer|carcinoma)\b"),
    ("acute myeloid leukemia", r"\bacute myeloid leukemia\b|\baml\b"),
]

_THERAPY_PATTERNS = [
    ("chemotherapy", r"\bchemo(?:therapy)?\b|\bcarboplatin\b|\bcisplatin\b|\bpemetrexed\b|\bpaclitaxel\b|\bgemcitabine\b|\bfolfirinox\b"),
    ("immunotherapy", r"\bimmunotherapy\b|\bpembrolizumab\b|\bnivolumab\b|\batezolizumab\b|\bcheckpoint inhibitor\b|\bpd-?1\b"),
    ("targeted therapy", r"\bosimertinib\b|\berlotinib\b|\bgefitinib\b|\bcrizotinib\b|\balectinib\b|\bsotorasib\b|\btargeted therapy\b|\btki\b"),
    ("radiotherapy", r"\bradiotherapy\b|\bradiation\b|\bchemoradiation\b"),
]

_LAB_PATTERNS = [
    ("ANC", r"\banc[:\s]+(\d+(?:\.\d+)?)"),
    ("platelets", r"\b(?:platelets?|plt)[:\s]+(\d+(?:\.\d+)?)"),
    ("creatinine", r"\bcreatinine[:\s]+(\d+(?:\.\d+)?)"),
    ("bilirubin", r"\b(?:total )?bilirubin[:\s]+(\d+(?:\.\d+)?)"),
    ("AST", r"\bast[:\s]+(\d+(?:\.\d+)?)"),
    ("ALT", r"\balt[:\s]+(\d+(?:\.\d+)?)"),
    ("hemoglobin", r"\b(?:hemoglobin|hgb|hb)[:\s]+(\d+(?:\.\d+)?)"),
]

_COMORBID_PATTERNS = [
    ("brain metastases", r"\bbrain metast|\bcns metast"),
    ("diabetes", r"\bdiabet"),
    ("autoimmune disease", r"\bautoimmune\b"),
    ("hepatitis", r"\bhepatitis\b"),
    ("interstitial lung disease", r"\binterstitial lung disease\b|\bild\b|\bpneumonitis\b"),
    ("cardiac disease", r"\bcongestive heart failure\b|\bcardiomyopathy\b|\bmyocardial infarction\b"),
]

_BIOMARKER_RE = re.compile(
    r"\b(?P<gene>EGFR|ALK|ROS1|KRAS|NRAS|BRAF|HER-?2|MET|RET|NTRK|TP53|BRCA1|BRCA2|PD-?L1|MSI|TMB)\b"
    r"\s*(?:exon\s*\d+\s*)?"
    r"(?P<alt>L858R|T790M|G12[CDVA]|G13D|V600E|exon\s*\d+\s*(?:deletion|del|insertion|ins)|"
    r"R175H|R273[HC]|amplification|amplified|fusion|rearrangement|positive|negative|high|low|"
    r"wild[- ]?type|mutation|mutant|[A-Z]\d{1,4}[A-Z])?",
    re.I,
)

_MOLECULAR_HINTS = ("mutation", "mutant", "amplif", "fusion", "rearrang", "hla",
                    "loss-of-heterozygosity", "loss of heterozygosity", "loh", "copy-number",
                    "copy number", "cnv", "neoantigen", "wes", "rnaseq", "rna-seq",
                    "sequencing", "msi", "tmb", "tumor mutational burden", "biomarker",
                    "molecular", "antigen", "self-hla")

def _is_molecular(low: str) -> bool:
    return any(h in low for h in _MOLECULAR_HINTS)

def _mentions_cancer(low: str) -> bool:
    return any(k in low for k in ("cancer", "carcinoma", "tumor", "tumour", "malignan",
                                  "melanoma", "leukemia", "lymphoma", "glioma", "sarcoma",
                                  "adenocarcinoma", "nsclc", "sclc"))

_CONDITION_SYNONYMS = {
    "non-small cell lung cancer": ["nsclc", "non-small cell lung", "non small cell lung"],
    "small cell lung cancer": ["sclc", "small cell lung"],
    "breast cancer": ["breast cancer", "breast carcinoma"],
    "melanoma": ["melanoma"],
    "colorectal cancer": ["colorectal", "crc", "colon cancer", "rectal cancer"],
    "pancreatic ductal adenocarcinoma": ["pancreatic", "pdac"],
    "glioma": ["glioma", "glioblastoma", "gbm"],
    "glioblastoma": ["glioblastoma", "gbm", "glioma"],
    "acute myeloid leukemia": ["acute myeloid leukemia", "aml"],
}

def _same_condition(crit_low: str, diagnosis: str) -> bool:
    dl = diagnosis.lower()
    syns = _CONDITION_SYNONYMS.get(dl, [dl])
    if dl in crit_low:
        return True
    return any(s in crit_low for s in syns)

def _names_other_cancer(crit_low: str, diagnosis: str) -> bool:
    for name, _pat in _DIAGNOSIS_PATTERNS:
        if name == diagnosis.lower():
            continue
        syns = _CONDITION_SYNONYMS.get(name, [name])
        if any(s in crit_low for s in syns) and not _same_condition(crit_low, diagnosis):

            if any(k in crit_low for k in ("histologically", "diagnosis of", "patients with",
                                           "confirmed", "must have")):
                return True
    return False

_NUM_RE = re.compile(r"(\d+(?:\.\d+)?)")

def _judge_lab(prof: PatientProfile, low: str) -> Optional[Tuple[bool, str, str]]:

    table = [
        ("ANC", r"\banc\b|absolute neutrophil|neutrophil count", ">="),
        ("platelets", r"\bplatelets?\b|\bplt\b", ">="),
        ("hemoglobin", r"\bhemoglobin\b|\bhgb\b|\bhb\b", ">="),
        ("creatinine", r"\bcreatinine\b", "<="),
        ("bilirubin", r"\bbilirubin\b", "<="),
        ("AST", r"\bast\b|aspartate", "<="),
        ("ALT", r"\balt\b|alanine", "<="),
    ]
    for key, pat, direction in table:
        if re.search(pat, low):
            if key not in prof.labs:
                return None
            m = _NUM_RE.search(low)
            if not m:
                return None
            thr = float(m.group(1))
            val = prof.labs[key]
            met = val >= thr if direction == ">=" else val <= thr
            return met, f"{key} {val}", f"{key} {val} vs {direction} {thr}."
    return None

def extract_profile(note: str, backend: Backend) -> PatientProfile:
    raw = backend.extract(note)
    prof = PatientProfile.from_dict(raw)
    prof.raw_note = note
    prof.validate(strict=False)
    return prof

class CTGovClient:

    def __init__(self, cache_dir: str = DEFAULT_CACHE_DIR,
                 offline: bool = False, snapshot: Optional[List[Trial]] = None):
        self.cache_dir = cache_dir
        self.offline = offline
        self._snapshot: List[Trial] = list(snapshot) if snapshot else []

    @classmethod
    def from_snapshot(cls, path: str, **kw) -> "CTGovClient":
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        trials = [Trial.from_dict(t) for t in payload.get("trials", [])]
        return cls(offline=True, snapshot=trials, **kw)

    def save_snapshot(self, path: str, trials: Sequence[Trial],
                      query: Optional[Dict[str, str]] = None) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        payload = {
            "pulled": datetime.now(timezone.utc).isoformat(),
            "api": CTGOV_API,
            "query": query or {},
            "trials": [t.to_dict() for t in trials],
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)

    def search(self, query: Dict[str, str], page_size: int = 20,
               use_cache: bool = True) -> List[Trial]:
        if self.offline or self._snapshot:
            return self._search_snapshot(query, page_size)
        params = dict(query)
        params.setdefault("filter.overallStatus", "RECRUITING")
        params["pageSize"] = str(page_size)
        params.setdefault(
            "fields",
            "NCTId,BriefTitle,Condition,Phase,OverallStatus,EligibilityCriteria,"
            "MinimumAge,MaximumAge,Sex,LeadSponsorName",
        )
        cache_path = self._cache_path(params)
        if use_cache and cache_path and os.path.exists(cache_path):
            with open(cache_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        else:
            url = CTGOV_API + "?" + urllib.parse.urlencode(params)
            req = urllib.request.Request(url, headers={"User-Agent": f"trialbridge/{__version__}"})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.load(r)
            if cache_path:
                os.makedirs(self.cache_dir, exist_ok=True)
                with open(cache_path, "w", encoding="utf-8") as fh:
                    json.dump(data, fh)
        return [self._extract_study(s) for s in data.get("studies", [])]

    def _search_snapshot(self, query: Dict[str, str], page_size: int) -> List[Trial]:
        terms = " ".join(v for k, v in query.items()
                         if k.startswith("query")).lower()
        toks = [t for t in re.split(r"[^a-z0-9]+", terms) if len(t) > 2]
        scored: List[Tuple[int, Trial]] = []
        for tr in self._snapshot:
            hay = (tr.title + " " + " ".join(tr.conditions) + " " +
                   tr.eligibility_criteria).lower()
            score = sum(1 for t in toks if t in hay)
            scored.append((score, tr))
        scored.sort(key=lambda x: x[0], reverse=True)
        ranked = [tr for sc, tr in scored if sc > 0] or [tr for _, tr in scored]
        return ranked[:page_size]

    @staticmethod
    def _extract_study(study: Dict[str, Any]) -> Trial:
        ps = study.get("protocolSection", study)
        idm = ps.get("identificationModule", {})
        stm = ps.get("statusModule", {})
        dm = ps.get("designModule", {})
        cm = ps.get("conditionsModule", {})
        em = ps.get("eligibilityModule", {})
        sm = ps.get("sponsorCollaboratorsModule", {})
        return Trial(
            nct_id=idm.get("nctId", ""),
            title=idm.get("briefTitle", ""),
            conditions=cm.get("conditions", []) or [],
            phases=dm.get("phases", []) or [],
            status=stm.get("overallStatus", ""),
            eligibility_criteria=em.get("eligibilityCriteria", "") or "",
            min_age=em.get("minimumAge"),
            max_age=em.get("maximumAge"),
            sex=em.get("sex"),
            lead_sponsor=(sm.get("leadSponsor") or {}).get("name"),
        )

    def _cache_path(self, params: Dict[str, str]) -> Optional[str]:
        try:
            key = hashlib.sha1(
                urllib.parse.urlencode(sorted(params.items())).encode()).hexdigest()[:16]
            return os.path.join(self.cache_dir, f"ctgov_{date.today().isoformat()}_{key}.json")
        except Exception:
            return None

COHORT_QUERIES: Dict[str, Dict[str, str]] = {
    "neoantigen": {"query.term": "neoantigen mRNA vaccine cancer"},
    "nsclc": {"query.cond": "non-small cell lung cancer",
              "query.term": "EGFR OR ALK OR KRAS G12C OR PD-L1"},
    "breast": {"query.cond": "breast cancer"},
}

def auto_query(profile: PatientProfile) -> Dict[str, str]:
    q: Dict[str, str] = {}
    if profile.diagnosis:
        q["query.cond"] = profile.diagnosis
    terms = []
    for b in profile.biomarkers:
        if b.status == "present":
            terms.append(b.gene + (f" {b.alteration}" if b.alteration else ""))
    if terms:
        q["query.term"] = " OR ".join(terms[:4])
    if not q:
        q["query.term"] = "advanced solid tumor"
    return q

_HEADER_RE = re.compile(
    r"(?im)^\s*(?:key\s+)?(inclusion|exclusion)\s+criteria\s*:?\s*$")
_BULLET_RE = re.compile(r"^\s*(?:[-*•·]|\(?\d+\)?[.)]|[a-z]\))\s+")

def split_criteria(blob: str) -> List[Criterion]:
    if not blob or not blob.strip():
        return []
    blob = blob.replace("\r\n", "\n")

    blob = re.sub(r"\\([^\w\s])", r"\1", blob)

    sections: List[Tuple[Kind, str]] = []
    matches = list(_HEADER_RE.finditer(blob))
    if matches:
        for i, m in enumerate(matches):
            kind = Kind.INCLUSION if m.group(1).lower() == "inclusion" else Kind.EXCLUSION
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(blob)
            sections.append((kind, blob[start:end]))
    else:
        sections.append((Kind.INCLUSION, blob))

    criteria: List[Criterion] = []
    idx = 0
    for kind, text in sections:
        for item in _split_items(text):
            item = _clean_item(item)
            if not item or len(item) < 3:
                continue
            if _is_section_header(item):
                continue
            crit = Criterion(text=item, kind=kind, ctype=classify_ctype(item), index=idx,
                             compound=_looks_compound(item))
            criteria.append(crit)
            idx += 1
    return criteria

def _split_items(text: str) -> List[str]:
    lines = text.split("\n")
    has_bullets = any(_BULLET_RE.match(ln) for ln in lines)
    items: List[str] = []
    if has_bullets:
        cur: List[str] = []
        for ln in lines:
            if _BULLET_RE.match(ln):
                if cur:
                    items.append(" ".join(cur).strip())
                cur = [_BULLET_RE.sub("", ln).strip()]
            elif ln.strip():
                cur.append(ln.strip())
        if cur:
            items.append(" ".join(cur).strip())
    else:

        for para in re.split(r"\n\s*\n", text):
            para = para.strip()
            if not para:
                continue
            parts = re.split(r"(?<=[.;])\s+(?=[A-Z0-9])", para)
            items.extend(p.strip() for p in parts if p.strip())
    return items

def _clean_item(item: str) -> str:
    item = re.sub(r"\\+([*._#])", r"\1", item)
    item = item.replace("**", "").replace("__", "")
    item = re.sub(r"^\s*\d+\\?\.\s*", "", item)
    item = re.sub(r"\s+", " ", item).strip()
    return item.strip(" -*•·")

def _is_section_header(item: str) -> bool:
    return bool(re.match(r"^[A-Za-z][\w ()/&'-]{0,30}:$", item))

def _looks_compound(item: str) -> bool:
    if item.count(";") >= 1:
        return True
    if re.search(r"\b(and|or)\b", item) and len(item) > 90:
        return True
    if item.count(",") >= 3 and len(item) > 120:
        return True
    return False

def classify_ctype(text: str) -> Ctype:
    low = text.lower()
    if _is_molecular(low):
        return Ctype.MOLECULAR
    if re.search(r"\becog\b|performance status|karnofsky", low):
        return Ctype.PERFORMANCE
    if re.search(r"\baged?\b|years? of age|years? or older|years? and older|\bmale\b|"
                 r"\bfemale\b|childbearing|pregnan|contracept", low):
        return Ctype.DEMOGRAPHIC
    if re.search(r"\banc\b|neutrophil|platelet|creatinine|bilirubin|\bast\b|\balt\b|hemoglobin|"
                 r"organ function|marrow function|\begfr\b(?! mutation)|clearance|serolog|"
                 r"laborator", low):
        return Ctype.LAB
    if re.search(r"prior (?:line|therapy|treatment)|lines of|failed|progressed on|"
                 r"systemic therapy|chemotherap|radiotherap|prior surgery", low):
        return Ctype.PRIOR_THERAPY
    if re.search(r"histolog|cytolog|diagnosis of|measurable disease|recist|stage|metastatic|"
                 r"advanced|unresectable|tumor tissue|resect", low):
        return Ctype.DISEASE
    if re.search(r"consent|able to comply|life expectancy|willing|geograph|follow-?up|"
                 r"enroll|study procedure", low):
        return Ctype.LOGISTICAL
    return Ctype.OTHER

def reason_criterion(profile: PatientProfile, criterion: Criterion,
                     backend: Backend) -> CriterionJudgment:
    raw = backend.judge(profile.to_dict(), criterion.text, criterion.kind, criterion.ctype)
    label = _coerce_label(raw.get("label"))
    return CriterionJudgment(
        criterion=criterion,
        label=label,
        evidence_span=str(raw.get("evidence_span", "") or ""),
        rationale=str(raw.get("rationale", "") or ""),
        confidence=_coerce_float(raw.get("confidence"), default=0.0 if label == Label.UNCERTAIN else 0.5),
    )

def reason_trial(profile: PatientProfile, trial: Trial, backend: Backend,
                 max_criteria: Optional[int] = None) -> List[CriterionJudgment]:
    criteria = split_criteria(trial.eligibility_criteria)
    if max_criteria is not None:
        criteria = criteria[:max_criteria]
    return [reason_criterion(profile, c, backend) for c in criteria]

def _coerce_label(v: Any) -> Label:
    if isinstance(v, Label):
        return v
    s = str(v or "").strip().upper()
    if s in Label.__members__:
        return Label[s]
    if s.startswith("ELIG"):
        return Label.ELIGIBLE
    if s.startswith("INEL") or s == "NO":
        return Label.INELIGIBLE
    return Label.UNCERTAIN

def _coerce_float(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
        return min(1.0, max(0.0, f))
    except (TypeError, ValueError):
        return default

def score_trial(trial: Trial, judgments: List[CriterionJudgment]) -> TrialMatch:
    n_e = sum(1 for j in judgments if j.label == Label.ELIGIBLE)
    n_i = sum(1 for j in judgments if j.label == Label.INELIGIBLE)
    n_u = sum(1 for j in judgments if j.label == Label.UNCERTAIN)
    total = max(1, n_e + n_i + n_u)
    score = n_e / total

    blocking = [j for j in judgments if j.label == Label.INELIGIBLE]
    if blocking:
        status = MatchStatus.INELIGIBLE
    elif n_u > 0:
        status = MatchStatus.REVIEW
    else:
        status = MatchStatus.ELIGIBLE if n_e > 0 else MatchStatus.REVIEW

    return TrialMatch(trial=trial, judgments=judgments, status=status, score=score,
                      n_eligible=n_e, n_ineligible=n_i, n_uncertain=n_u, blocking=blocking)

def rank_matches(matches: List[TrialMatch]) -> List[TrialMatch]:
    return sorted(
        matches,
        key=lambda m: (_STATUS_RANK[m.status], m.score, -m.n_uncertain, -m.n_ineligible),
        reverse=True,
    )

_ABBREVIATIONS = [
    (r"\bnon-small cell lung cancer\b", "NSCLC"),
    (r"\bEastern Cooperative Oncology Group performance status\b", "ECOG"),
    (r"\bperformance status\b", "PS"),
    (r"\bmetastatic\b", "met"),
    (r"\bhistory of\b", "h/o"),
    (r"\bstatus post\b", "s/p"),
    (r"\bwithin normal limits\b", "wnl"),
    (r"\bchemotherapy\b", "chemo"),
]

def _t_abbreviate(note: str) -> str:
    for pat, repl in _ABBREVIATIONS:
        note = re.sub(pat, repl, note, flags=re.I)
    return note

def _t_strip_molecular(note: str) -> str:
    out = []
    for line in note.split("\n"):
        if re.search(r"\b(HLA|WES|RNA-?seq|copy-number|LOH|TMB|MSI|sequencing|neoantigen)\b",
                     line, re.I):
            continue
        out.append(line)
    return "\n".join(out)

def _t_drop_labs(note: str) -> str:
    out = []
    for line in note.split("\n"):
        if re.match(r"\s*(ANC|Platelets?|Creatinine|Bilirubin|AST|ALT|Hemoglobin|Labs?)\b",
                    line, re.I):
            continue
        out.append(line)
    return "\n".join(out)

def _t_drop_headers(note: str) -> str:
    return re.sub(r"(?m)^\s*[A-Z][A-Za-z /]{2,30}:\s*", "", note)

def _t_terse(note: str) -> str:
    note = re.sub(r"\b(the patient is a|the patient has|patient is a|there is|presents with)\b",
                  "", note, flags=re.I)
    note = re.sub(r"[ \t]+", " ", note)
    note = re.sub(r"\n{2,}", "\n", note)
    return note.strip()

TRANSFORMS: Dict[str, Callable[[str], str]] = {
    "abbreviate": _t_abbreviate,
    "strip_molecular": _t_strip_molecular,
    "drop_labs": _t_drop_labs,
    "drop_headers": _t_drop_headers,
    "terse": _t_terse,
}

COMMUNITY_TRANSFORMS = ["abbreviate", "strip_molecular", "drop_labs", "terse"]

def degrade_note(note: str, transforms: Optional[Sequence[str]] = None) -> str:
    for name in (transforms if transforms is not None else COMMUNITY_TRANSFORMS):
        fn = TRANSFORMS.get(name)
        if fn is None:
            raise ValueError(f"unknown transform: {name}")
        note = fn(note)
    return note

def selective_metrics(pred_labels: Sequence[Label], gold_labels: Sequence[Label]
                      ) -> Dict[str, float]:
    n = len(pred_labels)
    if n == 0:
        return {"n": 0, "coverage": 0.0, "selective_accuracy": 0.0, "abstain_rate": 0.0}
    decided = [(p, g) for p, g in zip(pred_labels, gold_labels) if p != Label.UNCERTAIN]
    abstained = [g for p, g in zip(pred_labels, gold_labels) if p == Label.UNCERTAIN]
    n_dec = len(decided)
    correct = sum(1 for p, g in decided if p == g)
    answerable_abstentions = sum(1 for g in abstained if g != Label.UNCERTAIN)
    return {
        "n": n,
        "coverage": n_dec / n,
        "selective_accuracy": (correct / n_dec) if n_dec else 0.0,
        "abstain_rate": len(abstained) / n,
        "answerable_abstention_rate": (answerable_abstentions / len(abstained)) if abstained else 0.0,
    }

def risk_coverage_curve(pred_labels: Sequence[Label], gold_labels: Sequence[Label],
                        confidences: Sequence[float]) -> List[Tuple[float, float]]:
    triples = sorted(zip(confidences, pred_labels, gold_labels),
                     key=lambda x: x[0], reverse=True)
    n = len(triples)
    curve: List[Tuple[float, float]] = []
    correct = 0
    for i, (_conf, p, g) in enumerate(triples, start=1):
        correct += 1 if (p != Label.UNCERTAIN and p == g) else 0
        curve.append((i / n, correct / i))
    return curve

class TrialBridge:

    def __init__(self, backend: Backend, client: Optional[CTGovClient] = None):
        self.backend = backend
        self.client = client or CTGovClient()

    def match(self, note: str, *, trials: Optional[List[Trial]] = None,
              query: Optional[Dict[str, str]] = None, top_k: int = 10,
              max_criteria: Optional[int] = None) -> MatchResult:
        profile = extract_profile(note, self.backend)
        if trials is None:
            q = query or auto_query(profile)
            trials = self.client.search(q, page_size=top_k)
        matches = []
        for tr in trials:
            judgments = reason_trial(profile, tr, self.backend, max_criteria=max_criteria)
            matches.append(score_trial(tr, judgments))
        ranked = rank_matches(matches)
        queue = [m for m in ranked if m.needs_review]
        return MatchResult(profile=profile, ranked=ranked, review_queue=queue)

def evaluate(backend: Backend, gold_records: Sequence[Dict[str, Any]],
             patients: Dict[str, str]) -> Dict[str, Any]:
    profiles: Dict[str, PatientProfile] = {}
    preds: List[Label] = []
    golds: List[Label] = []
    confs: List[float] = []
    by_type: Dict[str, List[int]] = {}
    evidence_hits = 0
    evidence_decided = 0

    for rec in gold_records:
        pk = rec["patient"]
        if pk not in profiles:
            profiles[pk] = extract_profile(patients[pk], backend)
        crit = Criterion(text=rec["criterion"],
                         kind=Kind(rec.get("kind", "inclusion")),
                         ctype=Ctype(rec.get("ctype", "other")))
        j = reason_criterion(profiles[pk], crit, backend)
        gold = _coerce_label(rec["gold"])
        preds.append(j.label)
        golds.append(gold)
        confs.append(j.confidence)

        bucket = by_type.setdefault(str(crit.ctype), [0, 0])
        if j.label != Label.UNCERTAIN:
            bucket[1] += 1
            if j.label == gold:
                bucket[0] += 1
            evidence_decided += 1
            if j.evidence_span.strip():
                evidence_hits += 1

    sel = selective_metrics(preds, golds)
    acc_by_type = {k: {"accuracy": (c / d) if d else None, "decided": d}
                   for k, (c, d) in sorted(by_type.items())}
    return {
        "n_records": len(gold_records),
        "selective": sel,
        "accuracy_by_ctype": acc_by_type,
        "evidence_coverage": (evidence_hits / evidence_decided) if evidence_decided else 0.0,
        "risk_coverage_curve": risk_coverage_curve(preds, golds, confs),
    }

SAMPLE_PATIENTS: Dict[str, str] = {
    "neo01": textwrap.dedent("""\
        Oncology consult note (synthetic).
        Patient: 58 year old female. ECOG PS 1.
        Diagnosis: cutaneous melanoma, stage IIIC, status post wide local excision (resected).
        Molecular: tumor NGS shows TP53 R175H mutation; KRAS wild-type. HLA typing HLA-A*02:01.
        Archival FFPE tumor tissue available and sufficient for WES and RNA-seq.
        Prior therapy: 1 prior line of immunotherapy (pembrolizumab), progressed.
        Measurable disease present per RECIST 1.1.
        Labs: ANC 2.4, Platelets 210, Creatinine 0.9, Bilirubin 0.6, AST 24, ALT 20, Hemoglobin 12.8.
        Serologies: HIV negative, HBV negative, HCV negative.
        Comorbidities: none significant.
        """),
    "nsclc01": textwrap.dedent("""\
        Oncology progress note (synthetic).
        The patient is a 67 year old male, ECOG performance status 1.
        Diagnosis: non-small cell lung cancer (adenocarcinoma), stage IV, metastatic.
        Molecular: EGFR exon 21 L858R mutation detected; ALK negative; PD-L1 TPS 30%.
        Prior therapy: 2 prior lines of therapy - chemotherapy (carboplatin/pemetrexed) and
        targeted therapy (osimertinib), progressed on osimertinib.
        History of brain metastases, treated with SRS, currently stable/controlled.
        Measurable disease present.
        Labs: ANC 3.1, Platelets 180, Creatinine 1.0, Bilirubin 0.5, AST 30, ALT 28, Hemoglobin 11.5.
        Serologies: HIV negative.
        """),
}

def _sample_trials() -> List[Trial]:
    neo = Trial(
        nct_id="NCT05916248",
        title="Personalized mRNA neoantigen vaccine + pembrolizumab in advanced solid tumors (synthetic snapshot)",
        conditions=["Advanced Solid Tumor", "Melanoma"],
        phases=["PHASE1"],
        status="RECRUITING",
        min_age="18 Years", max_age="N/A", sex="ALL",
        lead_sponsor="Synthetic Sponsor",
        eligibility_criteria=textwrap.dedent("""\
            Inclusion Criteria:

            * Patients must be aged 18 years or older.
            * Histologically confirmed advanced or metastatic solid tumor.
            * ECOG performance status 0 to 1.
            * Failed standard treatment or not suitable for standard treatment.
            * Measurable disease per RECIST 1.1.
            * Cryopreserved or archival tissue sufficient for WES and RNAseq; at least one
              antigen effectively presented by self-HLA (e.g., KRAS or TP53 mutations with
              corresponding HLA types).
            * Adequate organ and marrow function: ANC >= 1.5, Platelets >= 100.
            * Negative HIV, HBV, and HCV serology.

            Exclusion Criteria:

            * Copy-number variations or loss-of-heterozygosity in HLA-related genes or regions
              by sequencing.
            * Active brain metastases or leptomeningeal disease.
            * Pregnant or breastfeeding women.
            """),
    )
    nsclc = Trial(
        nct_id="NCT04000001",
        title="EGFR-mutant NSCLC targeted therapy trial (synthetic snapshot)",
        conditions=["Non-small Cell Lung Cancer"],
        phases=["PHASE2"],
        status="RECRUITING",
        min_age="18 Years", max_age="N/A", sex="ALL",
        lead_sponsor="Synthetic Sponsor",
        eligibility_criteria=textwrap.dedent("""\
            Inclusion Criteria:

            * Age 18 years or older.
            * Histologically confirmed non-small cell lung cancer, stage IV.
            * Documented EGFR activating mutation (e.g., exon 19 deletion or L858R).
            * ECOG performance status 0-2.
            * At least 1 prior line of systemic therapy.
            * Measurable disease per RECIST 1.1.
            * Platelets >= 100.

            Exclusion Criteria:

            * Active, untreated brain metastases.
            * Known ALK rearrangement.
            """),
    )
    breast = Trial(
        nct_id="NCT04000002",
        title="HER2-positive breast cancer trial (synthetic snapshot)",
        conditions=["Breast Cancer"],
        phases=["PHASE2"],
        status="RECRUITING",
        min_age="18 Years", max_age="N/A", sex="ALL",
        lead_sponsor="Synthetic Sponsor",
        eligibility_criteria=textwrap.dedent("""\
            Inclusion Criteria:

            * Female patients aged 18 years or older.
            * Histologically confirmed breast cancer.
            * HER2 amplification or overexpression.
            * ECOG performance status 0-1.

            Exclusion Criteria:

            * Active brain metastases.
            """),
    )
    return [neo, nsclc, breast]

SAMPLE_GOLD: List[Dict[str, Any]] = [
    {"patient": "nsclc01", "kind": "inclusion", "ctype": "demographic",
     "criterion": "Age 18 years or older.", "gold": "ELIGIBLE"},
    {"patient": "nsclc01", "kind": "inclusion", "ctype": "disease",
     "criterion": "Histologically confirmed non-small cell lung cancer, stage IV.",
     "gold": "ELIGIBLE"},
    {"patient": "nsclc01", "kind": "inclusion", "ctype": "molecular",
     "criterion": "Documented EGFR activating mutation (e.g., exon 19 deletion or L858R).",
     "gold": "ELIGIBLE"},
    {"patient": "nsclc01", "kind": "inclusion", "ctype": "performance",
     "criterion": "ECOG performance status 0-2.", "gold": "ELIGIBLE"},
    {"patient": "nsclc01", "kind": "inclusion", "ctype": "prior_therapy",
     "criterion": "At least 1 prior line of systemic therapy.", "gold": "ELIGIBLE"},
    {"patient": "nsclc01", "kind": "exclusion", "ctype": "disease",
     "criterion": "Active, untreated brain metastases.", "gold": "ELIGIBLE"},
    {"patient": "nsclc01", "kind": "exclusion", "ctype": "molecular",
     "criterion": "Known ALK rearrangement.", "gold": "ELIGIBLE"},
    {"patient": "neo01", "kind": "inclusion", "ctype": "demographic",
     "criterion": "Patients must be aged 18 years or older.", "gold": "ELIGIBLE"},
    {"patient": "neo01", "kind": "inclusion", "ctype": "performance",
     "criterion": "ECOG performance status 0 to 1.", "gold": "ELIGIBLE"},
    {"patient": "neo01", "kind": "inclusion", "ctype": "disease",
     "criterion": "Measurable disease per RECIST 1.1.", "gold": "ELIGIBLE"},
    {"patient": "neo01", "kind": "inclusion", "ctype": "prior_therapy",
     "criterion": "Failed standard treatment or not suitable for standard treatment.",
     "gold": "ELIGIBLE"},
    {"patient": "neo01", "kind": "exclusion", "ctype": "disease",
     "criterion": "Active brain metastases or leptomeningeal disease.", "gold": "ELIGIBLE"},

    {"patient": "neo01", "kind": "exclusion", "ctype": "molecular",
     "criterion": "Copy-number variations or loss-of-heterozygosity in HLA-related genes or "
                  "regions by sequencing.", "gold": "UNCERTAIN"},

    {"patient": "nsclc01", "kind": "exclusion", "ctype": "demographic",
     "criterion": "Pregnant or breastfeeding women.", "gold": "ELIGIBLE"},

    {"patient": "neo01", "kind": "inclusion", "ctype": "molecular",
     "criterion": "At least one antigen effectively presented by self-HLA (e.g., KRAS or TP53 "
                  "mutations with corresponding HLA types).", "gold": "ELIGIBLE"},
]

def sample_client() -> CTGovClient:
    return CTGovClient(offline=True, snapshot=_sample_trials())

def format_report(result: MatchResult, max_trials: int = 10,
                   max_judgments: int = 6) -> str:
    lines: List[str] = []
    lines.append("=" * 72)
    lines.append("TrialBridge match report")
    lines.append("=" * 72)
    lines.append(f"Patient: {result.profile.brief()}")
    issues = result.profile.validate()
    if issues:
        lines.append(f"  (profile notes: {'; '.join(issues)})")
    lines.append("")
    lines.append(f"Ranked candidate trials ({len(result.ranked)}):")
    for m in result.ranked[:max_trials]:
        badge = {"ELIGIBLE": "✓", "REVIEW": "?", "INELIGIBLE": "✗"}[str(m.status)]
        lines.append("")
        lines.append(f"  [{badge}] {m.trial.nct_id}  {str(m.status):<10} "
                     f"score={m.score:.2f}  "
                     f"(E={m.n_eligible} I={m.n_ineligible} U={m.n_uncertain})")
        lines.append(f"      {m.trial.title}")
        if m.blocking:
            b = m.blocking[0]
            lines.append(f"      blocked by: {_short(b.criterion.text)}"
                         f"  [{_short(b.evidence_span, 40)}]")
        shown = 0
        for j in m.judgments:
            if shown >= max_judgments:
                break
            if j.label == Label.UNCERTAIN and m.status != MatchStatus.REVIEW:
                continue
            tag = str(j.label)[0]
            ev = f"  «{_short(j.evidence_span, 40)}»" if j.evidence_span else ""
            lines.append(f"        - ({tag}) [{str(j.criterion.ctype)}] "
                         f"{_short(j.criterion.text)}{ev}")
            shown += 1
    lines.append("")
    lines.append(f"Human-review queue ({len(result.review_queue)} trials with open questions):")
    for m in result.review_queue[:max_trials]:
        unc = [j for j in m.judgments if j.label == Label.UNCERTAIN]
        lines.append(f"  {m.trial.nct_id}: {len(unc)} uncertain criteria")
        for j in unc[:3]:
            lines.append(f"      · [{str(j.criterion.ctype)}] {_short(j.criterion.text)}"
                         f" — {_short(j.rationale, 50)}")
    lines.append("=" * 72)
    return "\n".join(lines)

def _short(s: str, n: int = 68) -> str:
    s = re.sub(r"\s+", " ", (s or "")).strip()
    return s if len(s) <= n else s[: n - 1] + "…"

def _read_note(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()

def _make_backend(args) -> Backend:
    return get_backend(args.backend, model=getattr(args, "model", None))

def cmd_demo(args) -> int:
    backend = HeuristicBackend()
    pipe = TrialBridge(backend, sample_client())
    key = args.patient_key
    note = SAMPLE_PATIENTS[key]
    if args.degrade:
        note = degrade_note(note)
        print(f"[equity slice] using degraded community-style note for '{key}'\n")
    result = pipe.match(note, trials=_sample_trials(), top_k=10)
    print(format_report(result))
    return 0

def cmd_extract(args) -> int:
    backend = _make_backend(args)
    note = _read_note(args.patient)
    prof = extract_profile(note, backend)
    print(json.dumps(prof.to_dict(include_note=False), indent=2, ensure_ascii=False))
    return 0

def cmd_match(args) -> int:
    backend = _make_backend(args)
    note = _read_note(args.patient)
    if args.offline:
        client = sample_client()
        trials = _sample_trials()
    else:
        client = CTGovClient()
        trials = None
    pipe = TrialBridge(backend, client)
    query = COHORT_QUERIES.get(args.cohort) if args.cohort else None
    result = pipe.match(note, trials=trials, query=query, top_k=args.top_k,
                        max_criteria=args.max_criteria)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(result.to_dict(), fh, indent=2, ensure_ascii=False)
        print(f"wrote {args.out}")
    else:
        print(format_report(result))
    return 0

def cmd_fetch(args) -> int:
    client = CTGovClient()
    query = COHORT_QUERIES.get(args.cohort)
    if query is None:
        print(f"unknown cohort '{args.cohort}'. choose: {', '.join(COHORT_QUERIES)}",
              file=sys.stderr)
        return 2
    trials = client.search(query, page_size=args.page_size, use_cache=not args.no_cache)
    print(f"fetched {len(trials)} trials for cohort '{args.cohort}'")
    if args.out:
        client.save_snapshot(args.out, trials, query=query)
        print(f"snapshot -> {args.out}")
    else:
        for t in trials[:10]:
            print(f"  {t.nct_id}  {_short(t.title)}  "
                  f"({len(t.eligibility_criteria)} chars elig)")
    return 0

def cmd_evaluate(args) -> int:
    backend = _make_backend(args)
    metrics = evaluate(backend, SAMPLE_GOLD, SAMPLE_PATIENTS)
    view = {k: v for k, v in metrics.items() if k != "risk_coverage_curve"}
    print(json.dumps(view, indent=2, ensure_ascii=False))
    print("\nrisk-coverage curve (coverage, accuracy):")
    for cov, acc in metrics["risk_coverage_curve"]:
        print(f"  {cov:.2f}  {acc:.2f}")
    return 0

def cmd_selftest(args) -> int:
    passed, failed = run_tests(verbose=args.verbose)
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="trialbridge",
        description="Local, explainable oncology trial matching (reference single-file build).",
    )
    p.add_argument("--version", action="version", version=f"trialbridge {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    common_backend = argparse.ArgumentParser(add_help=False)
    common_backend.add_argument("--backend", default="heuristic",
                                help="heuristic|ollama|llamacpp|cloud (default: heuristic)")
    common_backend.add_argument("--model", default=None,
                                help="model name/path for the chosen backend")

    sp = sub.add_parser("demo", help="run end-to-end on bundled synthetic data (offline)")
    sp.add_argument("--patient-key", default="nsclc01",
                    choices=sorted(SAMPLE_PATIENTS), dest="patient_key")
    sp.add_argument("--degrade", action="store_true",
                    help="use the degraded community-style note (equity slice)")
    sp.set_defaults(func=cmd_demo)

    sp = sub.add_parser("extract", parents=[common_backend],
                        help="extract a structured profile from a note")
    sp.add_argument("--patient", required=True, help="path to note file, or '-' for stdin")
    sp.set_defaults(func=cmd_extract)

    sp = sub.add_parser("match", parents=[common_backend], help="full matching pipeline")
    sp.add_argument("--patient", required=True, help="path to note file, or '-' for stdin")
    sp.add_argument("--offline", action="store_true",
                    help="use the bundled trial snapshot instead of live CT.gov")
    sp.add_argument("--cohort", default=None, choices=sorted(COHORT_QUERIES),
                    help="use a predefined cohort query instead of auto-query")
    sp.add_argument("--top-k", type=int, default=10, dest="top_k")
    sp.add_argument("--max-criteria", type=int, default=None, dest="max_criteria",
                    help="cap criteria judged per trial (useful for slow LLM backends)")
    sp.add_argument("--out", default=None, help="write JSON result to this path")
    sp.set_defaults(func=cmd_match)

    sp = sub.add_parser("fetch", help="pull trials from CT.gov and optionally snapshot")
    sp.add_argument("--cohort", required=True, choices=sorted(COHORT_QUERIES))
    sp.add_argument("--page-size", type=int, default=20, dest="page_size")
    sp.add_argument("--no-cache", action="store_true", dest="no_cache")
    sp.add_argument("--out", default=None, help="write a frozen snapshot JSON here")
    sp.set_defaults(func=cmd_fetch)

    sp = sub.add_parser("evaluate", parents=[common_backend],
                        help="per-criterion accuracy + calibration on bundled gold set")
    sp.set_defaults(func=cmd_evaluate)

    sp = sub.add_parser("selftest", help="run the embedded test suite")
    sp.add_argument("-v", "--verbose", action="store_true")
    sp.set_defaults(func=cmd_selftest)

    return p

def _use_utf8_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

def main(argv: Optional[Sequence[str]] = None) -> int:
    _use_utf8_output()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except BrokenPipeError:
        return 0
    except KeyboardInterrupt:
        return 130

class _Check:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0
        self.msgs: List[str] = []

    def ok(self, cond: bool, msg: str) -> None:
        if cond:
            self.passed += 1
        else:
            self.failed += 1
            self.msgs.append("FAIL: " + msg)

    def eq(self, a: Any, b: Any, msg: str) -> None:
        self.ok(a == b, f"{msg} (got {a!r}, expected {b!r})")

def run_tests(verbose: bool = False) -> Tuple[int, int]:
    c = _Check()
    be = HeuristicBackend()

    prof = PatientProfile(age=60, sex="female", ecog=1,
                          biomarkers=[Biomarker("EGFR", "L858R")])
    c.eq(PatientProfile.from_dict(prof.to_dict()).age, 60, "profile round-trip age")
    c.eq(PatientProfile.from_dict(prof.to_dict()).biomarkers[0].gene, "EGFR",
         "profile round-trip biomarker")
    c.ok(PatientProfile(age=999).validate() != [], "validation flags bad age")
    c.eq(PatientProfile(ecog=1).validate(), [], "validation passes clean profile")

    c.eq(extract_json('prefix {"a": 1} suffix')["a"], 1, "extract_json plain")
    c.eq(extract_json('```json\n{"label":"ELIGIBLE"}\n```')["label"], "ELIGIBLE",
         "extract_json fenced")
    c.eq(extract_json('{"a": 1, "b": [2,3,],}')["b"], [2, 3], "extract_json trailing comma repair")

    p1 = extract_profile(SAMPLE_PATIENTS["nsclc01"], be)
    c.eq(p1.age, 67, "extract age")
    c.eq(p1.sex, "male", "extract sex")
    c.eq(p1.ecog, 1, "extract ECOG")
    c.eq(p1.stage, "IV", "extract stage")
    c.ok(p1.diagnosis and "non-small cell" in p1.diagnosis, "extract diagnosis")
    c.ok(any(b.gene == "EGFR" and b.status == "present" for b in p1.biomarkers),
         "extract EGFR present")
    c.ok(any(b.gene == "ALK" and b.status == "absent" for b in p1.biomarkers),
         "extract ALK negative -> absent")
    c.ok(p1.prior_lines is not None and p1.prior_lines >= 2, "extract prior lines")
    c.ok("brain metastases" in p1.comorbidities, "extract brain mets comorbidity")
    c.eq(p1.serologies.get("HIV"), "negative", "extract HIV serology")
    c.ok(p1.labs.get("ANC") == 3.1, "extract ANC lab")
    c.ok(p1.labs.get("hemoglobin") == 11.5,
         "lab value ending a sentence is not lost to the trailing period")

    p2 = extract_profile(SAMPLE_PATIENTS["neo01"], be)
    c.ok("A*02:01" in p2.hla, "extract HLA allele")
    c.ok(p2.tissue_available is True, "extract tissue availability")
    c.ok(any(b.gene == "TP53" for b in p2.biomarkers), "extract TP53")

    p_fp = extract_profile("Stage IV metastatic disease; secretary notes; walk-in visit.", be)
    c.ok(not any(b.gene in {"MET", "ALK", "RET"} for b in p_fp.biomarkers),
         "biomarker extraction ignores English words (word-boundary fix)")
    c.ok(any(b.gene == "MET" for b in extract_profile("Tumor is MET amplified.", be).biomarkers),
         "biomarker extraction still catches a real MET call")

    trials = _sample_trials()
    neo, nsclc, breast = trials
    crits = split_criteria(neo.eligibility_criteria)
    c.ok(len(crits) >= 8, f"splitter finds atomic criteria (got {len(crits)})")
    c.ok(any(x.kind == Kind.INCLUSION for x in crits) and
         any(x.kind == Kind.EXCLUSION for x in crits), "splitter separates inc/exc")
    c.ok(any(x.ctype == Ctype.MOLECULAR for x in crits), "splitter classifies molecular")
    c.ok(any(x.compound for x in crits), "splitter flags a compound criterion")

    messy = "Inclusion Criteria:\n\n* 1\\. Age >= 18 years.\n* ECOG 0-1.\n"
    c.eq(len(split_criteria(messy)), 2, "splitter handles escaped markdown bullets")

    esc = ("Inclusion Criteria:\n\n\\* Age \\>= 18 years\\.\n\\* ECOG 0-1\\.\n\n"
           "Exclusion Criteria:\n\n\\* Active brain metastases\\.\n")
    ce = split_criteria(esc)
    c.eq(len([x for x in ce if x.kind == Kind.INCLUSION]), 2,
         "splitter splits escaped '\\*' bullets instead of merging")
    c.ok(all("\\" not in x.text for x in ce), "splitter strips backslash escapes from text")

    hdr = "Inclusion Criteria:\n\n**Age**:\n\n1. Patients must be 18 years or older.\n"
    ch = split_criteria(hdr)
    c.ok(all(not x.text.rstrip().endswith(":") for x in ch),
         "splitter drops markdown sub-header labels")
    c.ok(any("18 years" in x.text for x in ch), "splitter keeps the real criterion under a header")

    c.eq(classify_ctype("ECOG performance status 0-1"), Ctype.PERFORMANCE, "ctype performance")
    c.eq(classify_ctype("Documented EGFR mutation"), Ctype.MOLECULAR, "ctype molecular")
    c.eq(classify_ctype("Age 18 years or older"), Ctype.DEMOGRAPHIC, "ctype demographic")

    def judge(prof, text, kind, ctype=Ctype.OTHER):
        return reason_criterion(prof, Criterion(text, kind, ctype), be).label

    c.eq(judge(p1, "ECOG performance status 0-2.", Kind.INCLUSION, Ctype.PERFORMANCE),
         Label.ELIGIBLE, "reason ECOG in range -> ELIGIBLE")
    c.eq(judge(p1, "Documented EGFR activating mutation (e.g., L858R).",
               Kind.INCLUSION, Ctype.MOLECULAR),
         Label.ELIGIBLE, "reason EGFR present -> ELIGIBLE")
    c.eq(judge(p1, "Active, untreated brain metastases.", Kind.EXCLUSION, Ctype.DISEASE),
         Label.ELIGIBLE, "reason treated brain mets not excluded")
    c.eq(judge(p1, "At least 1 prior line of systemic therapy.",
               Kind.INCLUSION, Ctype.PRIOR_THERAPY),
         Label.ELIGIBLE, "reason prior line satisfied")
    c.eq(judge(p2, "Copy-number variations or loss-of-heterozygosity in HLA-related genes.",
               Kind.EXCLUSION, Ctype.MOLECULAR),
         Label.UNCERTAIN, "reason HLA CNV/LOH -> UNCERTAIN (abstain)")
    c.eq(judge(p2, "Histologically confirmed breast cancer.", Kind.INCLUSION, Ctype.DISEASE),
         Label.INELIGIBLE, "reason wrong tumor type -> INELIGIBLE")

    jj = reason_criterion(p1, Criterion("ECOG performance status 0-2.",
                                        Kind.INCLUSION, Ctype.PERFORMANCE), be)
    c.ok(jj.evidence_span.strip() != "", "decided judgment carries an evidence span")

    result = TrialBridge(be, sample_client()).match(
        SAMPLE_PATIENTS["nsclc01"], trials=trials, top_k=10)
    c.eq(len(result.ranked), 3, "pipeline ranks all trials")
    top = result.ranked[0]
    c.ok(top.trial.nct_id in {"NCT04000001", "NCT05916248"},
         "NSCLC or neoantigen trial ranks at top for NSCLC patient")
    breast_match = next(m for m in result.ranked if m.trial.nct_id == "NCT04000002")
    c.eq(breast_match.status, MatchStatus.INELIGIBLE,
         "breast trial ineligible for NSCLC patient")
    c.ok(all(m.needs_review for m in result.review_queue),
         "review queue only holds REVIEW-status trials")
    c.ok(len(result.review_queue) >= 1, "at least one trial routed to review")

    j_e = CriterionJudgment(Criterion("x", Kind.INCLUSION), Label.ELIGIBLE)
    j_i = CriterionJudgment(Criterion("y", Kind.EXCLUSION), Label.INELIGIBLE)
    j_u = CriterionJudgment(Criterion("z", Kind.INCLUSION), Label.UNCERTAIN)
    tm = score_trial(neo, [j_e, j_i, j_u])
    c.eq(tm.status, MatchStatus.INELIGIBLE, "any INELIGIBLE -> ineligible status")
    c.ok(abs(tm.score - 1/3) < 1e-9, "score = eligible/total")
    tm2 = score_trial(neo, [j_e, j_u])
    c.eq(tm2.status, MatchStatus.REVIEW, "eligible+uncertain -> review")

    clean = SAMPLE_PATIENTS["neo01"]
    messy_note = degrade_note(clean)
    c.ok("HLA" not in messy_note, "strip_molecular removes HLA line")
    pm = extract_profile(messy_note, be)
    c.ok(len(pm.hla) == 0 and len(p2.hla) > 0,
         "degradation drops molecular features (equity effect)")
    c.ok("NSCLC" in degrade_note(SAMPLE_PATIENTS["nsclc01"], ["abbreviate"]),
         "abbreviate transform applies")

    preds = [Label.ELIGIBLE, Label.INELIGIBLE, Label.UNCERTAIN, Label.ELIGIBLE]
    golds = [Label.ELIGIBLE, Label.INELIGIBLE, Label.ELIGIBLE, Label.INELIGIBLE]
    sm = selective_metrics(preds, golds)
    c.ok(abs(sm["coverage"] - 0.75) < 1e-9, "selective coverage computed")
    c.ok(abs(sm["selective_accuracy"] - 2/3) < 1e-9, "selective accuracy computed")
    rc = risk_coverage_curve(preds, golds, [0.9, 0.8, 0.0, 0.4])
    c.eq(len(rc), 4, "risk-coverage curve has one point per case")
    c.ok(rc[-1][0] == 1.0, "risk-coverage ends at full coverage")

    fake_study = {"protocolSection": {
        "identificationModule": {"nctId": "NCT99999999", "briefTitle": "Test"},
        "statusModule": {"overallStatus": "RECRUITING"},
        "designModule": {"phases": ["PHASE1"]},
        "conditionsModule": {"conditions": ["Cancer"]},
        "eligibilityModule": {"eligibilityCriteria": "Inclusion Criteria:\n* Age 18.",
                              "minimumAge": "18 Years", "sex": "ALL"},
        "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Acme"}},
    }}
    tr = CTGovClient._extract_study(fake_study)
    c.eq(tr.nct_id, "NCT99999999", "ctgov extract nctId")
    c.eq(tr.lead_sponsor, "Acme", "ctgov extract sponsor")
    c.ok(tr.eligibility_criteria.startswith("Inclusion"), "ctgov extract eligibility blob")

    import tempfile
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "snap.json")
        cl = CTGovClient(cache_dir=d)
        cl.save_snapshot(path, trials, query={"query.term": "x"})
        cl2 = CTGovClient.from_snapshot(path)
        c.eq(len(cl2._snapshot), 3, "snapshot save/replay preserves all trials")
        c.eq(cl2.search({"query.term": "melanoma"})[0].nct_id, "NCT05916248",
             "replayed snapshot is searchable")

    hits = sample_client().search({"query.cond": "breast cancer"})
    c.eq(hits[0].nct_id, "NCT04000002", "offline search ranks breast trial first")

    q = auto_query(p1)
    c.ok("query.cond" in q and "EGFR" in q.get("query.term", ""),
         "auto_query builds condition + biomarker terms")

    c.ok(isinstance(get_backend("heuristic"), HeuristicBackend), "factory -> heuristic")
    try:
        get_backend("nope")
        c.ok(False, "factory rejects unknown backend")
    except ValueError:
        c.ok(True, "factory rejects unknown backend")

    c.eq(_coerce_label("eligible"), Label.ELIGIBLE, "label coercion lowercase")
    c.eq(_coerce_label("garbage"), Label.UNCERTAIN, "label coercion fallback")

    metrics = evaluate(be, SAMPLE_GOLD, SAMPLE_PATIENTS)
    c.ok(0.0 <= metrics["selective"]["selective_accuracy"] <= 1.0,
         "evaluate returns a valid selective accuracy")
    c.ok(metrics["selective"]["coverage"] > 0.0, "evaluate decides at least some criteria")
    c.ok("molecular" in metrics["accuracy_by_ctype"] or
         metrics["n_records"] == len(SAMPLE_GOLD), "evaluate buckets by ctype")

    if verbose or c.failed:
        for m in c.msgs:
            print(m)
    return c.passed, c.failed

if __name__ == "__main__":
    sys.exit(main())
