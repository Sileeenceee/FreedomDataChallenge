def calculate_score(criteria):
    weights = {
        1: 10,
        2: 15,
        3: 20,
        4: 20,
        5: 15,
        6: 15,
        7: 5
    }

    max_scores = {
        4: 20,
        6: 10
    }

    allowed_scores = {
        1: {"Да", "Нет"},
        2: {"Да", "Нет", "N/A"},
        3: {"Да", "Нет", "N/A"},
        4: {0, 5, 10, 15, 20, "N/A"},
        5: {"Да", "Нет"},
        6: {0, 4, 7, 10},
        7: {"Да", "Нет", "N/A"},
        8: {"Да", "Нет"}
    }

    if len(criteria) != 8:
        raise ValueError("Expected exactly 8 criteria")

    ids = [item["id"] for item in criteria]

    if set(ids) != set(range(1, 9)):
        raise ValueError("Missing or duplicate criterion IDs")

    earned = 0
    applicable_weight = 0
    severe_violation = False

    for item in criteria:
        criterion_id = item["id"]
        score = item["score"]

        if score not in allowed_scores[criterion_id]:
            raise ValueError(
                f"Invalid score {score} for criterion {criterion_id}"
            )

        if criterion_id == 8:
            severe_violation = score == "Да"
            continue

        if score == "N/A":
            continue

        weight = weights[criterion_id]
        applicable_weight += weight

        if criterion_id in max_scores:
            normalized_score = score / max_scores[criterion_id]
        else:
            normalized_score = 1 if score == "Да" else 0

        earned += weight * normalized_score

    total_score = round(
        earned / applicable_weight * 100, 2
    ) if applicable_weight > 0 else None

    return {
        "total_score": total_score,
        "severe_violation": severe_violation
    }