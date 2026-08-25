from pathlib import Path


def test_severity_filter_displays_total_for_every_risk_tier():
    project_root = Path(__file__).resolve().parents[3]
    script = (project_root / "dashboard/static/js/reviews-page.js").read_text()

    for key in ("bad_high", "bad_elevated", "mixed", "good_likely", "good_high"):
        assert f"value: '{key}'" in script
    assert "All severities (${portfolioReviews.length})" in script
    assert "label: `${option.label} (${riskCounts.get(option.value) || 0})`" in script
    assert "reviewQueueState.risk = populateQueueSelect(" in script


def test_portfolio_selection_recalculates_severity_totals():
    project_root = Path(__file__).resolve().parents[3]
    script = (project_root / "dashboard/static/js/reviews-page.js").read_text()

    portfolio_change = script.index("reviewQueueState.portfolio = event.target.value;")
    risk_refresh = script.index("populateQueueRiskFilter();", portfolio_change)
    queue_render = script.index("renderReviewQueue();", portfolio_change)

    assert portfolio_change < risk_refresh < queue_render
    assert "(review.portfolio || 'Unmapped') === reviewQueueState.portfolio" in script
