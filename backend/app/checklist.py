"""Checklist configuration schema + weighted scoring with N/A redistribution and gross-error flags."""

import re
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


class Scale(BaseModel):
    min: int = 0
    max: int = 1
    labels: dict[str, str] = Field(default_factory=dict)  # {"0": "не выполнено", "1": "выполнено"}


class Section(BaseModel):
    id: str
    title: str


class CheckField(BaseModel):
    """Typed helper field the model fills before scoring (Qwen v3: "atomic checks")."""

    type: Literal["bool", "list[int]", "int|null", "list[str]", "list[obj]"]
    instruction: str
    example: Any = None


class Criterion(BaseModel):
    id: str
    section_id: str | None = None
    title: str
    description: str = ""  # what exactly the evaluator must check (goes into the AI prompt)
    type: Literal["binary", "scale"] = "binary"
    scale: Scale = Field(default_factory=Scale)
    weight: float = Field(ge=0)
    na_allowed: bool = False
    na_rule: str = ""  # when N/A applies
    critical: bool = False  # "грубая ошибка": failing this criterion is flagged separately
    critical_rule: str = ""  # optional: what exactly counts as a gross error
    voice_rule: str = ""  # extra instruction used only for voice calls
    # Qwen v3: fields the model fills first, and the name of a code rule (services/qwen.py RULES) that turns
    # them + computed facts into the final score. Both optional: without `derive` the model's score is used.
    check_fields: dict[str, CheckField] = Field(default_factory=dict)
    derive: str | None = None


class Scoring(BaseModel):
    max_total: float = 100
    decimals: int = Field(0, ge=0, le=4)
    rounding: Literal["half_up", "half_even", "floor", "ceil"] = "half_up"
    critical_policy: Literal["flag", "zero_total"] = "flag"  # gross error: only flag, or zero the total


class ChecklistConfig(BaseModel):
    name: str
    description: str = ""
    sections: list[Section] = Field(default_factory=list)
    criteria: list[Criterion] = Field(min_length=1)
    scoring: Scoring = Field(default_factory=Scoring)

    @model_validator(mode="after")
    def _validate(self):
        ids = [c.id for c in self.criteria]
        for cid in ids:
            if not _ID.match(cid):
                raise ValueError(f"Invalid criterion id '{cid}' (letters, digits, _ . - only)")
        if len(set(ids)) != len(ids):
            raise ValueError("Criterion ids must be unique")
        section_ids = {s.id for s in self.sections}
        if len(section_ids) != len(self.sections):
            raise ValueError("Section ids must be unique")
        for c in self.criteria:
            if c.section_id and c.section_id not in section_ids:
                raise ValueError(f"Criterion '{c.id}' references unknown section '{c.section_id}'")
            if c.type == "binary":
                c.scale = Scale(min=0, max=1, labels=c.scale.labels or {"0": "Нет", "1": "Да"})
            elif c.scale.max <= c.scale.min:
                raise ValueError(f"Criterion '{c.id}': scale max must be greater than min")
        if sum(c.weight for c in self.criteria) <= 0:
            raise ValueError("Total weight must be positive")
        return self


_ROUNDING = {
    "half_up": ROUND_HALF_UP,
    "half_even": ROUND_HALF_EVEN,
    "floor": ROUND_FLOOR,
    "ceil": ROUND_CEILING,
}


def round_score(value: float, scoring: Scoring) -> float:
    q = Decimal(1).scaleb(-scoring.decimals)
    return float(Decimal(str(value)).quantize(q, rounding=_ROUNDING[scoring.rounding]))


def compute_scores(config: ChecklistConfig, items: list[dict]) -> dict:
    """Weighted total on `max_total` scale. N/A criteria drop out and their weight is
    redistributed proportionally across applicable criteria. Critical failures are flagged."""
    by_id = {i["criterion_id"]: i for i in items}
    sc = config.scoring

    def applicable(c: Criterion) -> bool:
        it = by_id.get(c.id)
        return bool(it) and it.get("applicable", True) and it.get("value") is not None

    applicable_weight = sum(c.weight for c in config.criteria if applicable(c))
    rows, critical = [], []
    raw_total = 0.0
    for c in config.criteria:
        it = by_id.get(c.id)
        row = {
            "criterion_id": c.id,
            "weight": c.weight,
            "max_value": c.scale.max,
            "min_value": c.scale.min,
            "na": not applicable(c),
            "value": None,
            "ratio": None,
            "effective_weight": 0.0,
            "points": None,
            "max_points": 0.0,
            "missing": it is None,
        }
        if applicable(c):
            value = max(c.scale.min, min(c.scale.max, int(it["value"])))
            ratio = (value - c.scale.min) / (c.scale.max - c.scale.min)
            eff = c.weight / applicable_weight * sc.max_total if applicable_weight else 0.0
            row.update(value=value, ratio=ratio, effective_weight=round(eff, 4), max_points=round(eff, 4))
            row["points"] = round(ratio * eff, 4)
            raw_total += ratio * eff
            if c.critical and value == c.scale.min:
                critical.append(c.id)
        rows.append(row)

    total = round_score(raw_total, sc) if applicable_weight else None
    if critical and sc.critical_policy == "zero_total" and total is not None:
        total = round_score(0, sc)

    sections = []
    for s in config.sections:
        crits = [c for c in config.criteria if c.section_id == s.id]
        app = [c for c in crits if applicable(c)]
        w = sum(c.weight for c in app)
        pts = sum(r["points"] or 0 for r in rows if r["criterion_id"] in {c.id for c in app})
        max_pts = sum(r["max_points"] for r in rows if r["criterion_id"] in {c.id for c in app})
        pct = (
            sum(c.weight * next(r["ratio"] for r in rows if r["criterion_id"] == c.id) for c in app) / w * 100
            if w
            else None
        )
        sections.append(
            {
                "id": s.id,
                "title": s.title,
                "percent": round_score(pct, sc) if pct is not None else None,
                "points": round(pts, 2),
                "max_points": round(max_pts, 2),
                "na": not app,
                "critical": any(cid in critical for cid in (c.id for c in crits)),
            }
        )

    return {
        "total": total,
        "max_total": sc.max_total,
        "percent": round(total / sc.max_total * 100, 1) if total is not None and sc.max_total else None,
        "raw_total": round(raw_total, 4),
        "critical_error": bool(critical),
        "critical_criteria": critical,
        "applicable_weight": applicable_weight,
        "sections": sections,
        "criteria": rows,
    }
