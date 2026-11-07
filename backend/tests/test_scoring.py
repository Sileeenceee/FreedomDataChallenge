import pytest
from pydantic import ValidationError

from app.checklist import ChecklistConfig, compute_scores, round_score

CFG = ChecklistConfig.model_validate(
    {
        "name": "t",
        "sections": [{"id": "a", "title": "A"}, {"id": "b", "title": "B"}],
        "criteria": [
            {"id": "c1", "section_id": "a", "title": "bin", "type": "binary", "weight": 50},
            {
                "id": "c2",
                "section_id": "a",
                "title": "scale",
                "type": "scale",
                "scale": {"min": 0, "max": 2},
                "weight": 30,
                "na_allowed": True,
            },
            {
                "id": "c3",
                "section_id": "b",
                "title": "crit",
                "type": "binary",
                "weight": 20,
                "critical": True,
            },
        ],
        "scoring": {"max_total": 100, "decimals": 0, "rounding": "half_up"},
    }
)


def item(cid, value, applicable=True):
    return {"criterion_id": cid, "value": value, "applicable": applicable}


def test_full_marks():
    r = compute_scores(CFG, [item("c1", 1), item("c2", 2), item("c3", 1)])
    assert r["total"] == 100 and not r["critical_error"]


def test_scale_is_weighted():
    r = compute_scores(CFG, [item("c1", 1), item("c2", 1), item("c3", 1)])
    assert r["total"] == 85  # 50 + 30*0.5 + 20


def test_na_redistributes_weight():
    r = compute_scores(CFG, [item("c1", 1), item("c2", None, applicable=False), item("c3", 0)])
    # c2 out: weights 50/70 and 20/70 of 100 -> c1 gives 71.43, c3 fails
    assert r["total"] == 71
    row = next(x for x in r["criteria"] if x["criterion_id"] == "c1")
    assert row["effective_weight"] == pytest.approx(71.4286, abs=1e-3)
    assert r["critical_error"] and r["critical_criteria"] == ["c3"]


def test_sections():
    r = compute_scores(CFG, [item("c1", 0), item("c2", 2), item("c3", 1)])
    a = next(s for s in r["sections"] if s["id"] == "a")
    assert a["percent"] == round_score(30 / 80 * 100, CFG.scoring)
    assert next(s for s in r["sections"] if s["id"] == "b")["percent"] == 100


def test_zero_total_policy():
    cfg = CFG.model_copy(deep=True)
    cfg.scoring.critical_policy = "zero_total"
    r = compute_scores(cfg, [item("c1", 1), item("c2", 2), item("c3", 0)])
    assert r["total"] == 0 and r["critical_error"]


def test_rounding_modes():
    sc = CFG.scoring.model_copy()
    assert round_score(84.5, sc) == 85
    sc.rounding = "half_even"
    assert round_score(84.5, sc) == 84
    sc.decimals, sc.rounding = 1, "floor"
    assert round_score(84.59, sc) == 84.5


def test_validation():
    with pytest.raises(ValidationError):
        ChecklistConfig.model_validate({"name": "x", "criteria": [{"id": "a b", "title": "t", "weight": 1}]})
    with pytest.raises(ValidationError):
        ChecklistConfig.model_validate(
            {"name": "x", "criteria": [{"id": "a", "title": "t", "weight": 1, "section_id": "nope"}]}
        )
