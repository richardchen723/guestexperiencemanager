from pathlib import Path


def test_severity_filter_displays_total_for_every_risk_tier():
    project_root = Path(__file__).resolve().parents[3]
    script = (project_root / "dashboard/static/js/reviews-page.js").read_text()

    for key in ("bad_high", "bad_elevated", "mixed", "good_likely", "good_high"):
        assert f"value: '{key}'" in script
    assert "All severities (${reviewQueueState.reviews.length})" in script
    assert "label: `${option.label} (${riskCounts.get(option.value) || 0})`" in script
    assert "reviewQueueState.risk = populateQueueSelect(" in script
