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


def test_resolution_custom_date_range_validates_applies_and_clears():
    project_root = Path(__file__).resolve().parents[3]
    template = (project_root / "dashboard/templates/reviews/resolutions.html").read_text()
    script = (project_root / "dashboard/static/js/review-resolutions.js").read_text()

    assert 'id="resolutionStartDate"' in template
    assert 'id="resolutionEndDate"' in template
    assert 'id="resolutionDateApply"' in template
    assert 'id="resolutionDateClear"' in template
    assert 'From and To dates are inclusive.' in template
    assert "params.set('start_date', resolutionState.startDate);" in script
    assert "params.set('end_date', resolutionState.endDate);" in script
    assert "To date cannot be earlier than From date." in script
    assert "resolutionState.startDate = '';" in script
    assert "resolutionState.endDate = '';" in script


def test_resolution_card_displays_a_readable_review_channel():
    project_root = Path(__file__).resolve().parents[3]
    script = (project_root / "dashboard/static/js/review-resolutions.js").read_text()

    assert "const channelLabel = resolutionChannelLabel(review.channel_name);" in script
    assert "${resolutionEscape(channelLabel)}</p>" in script
    assert "airbnbofficial: 'Airbnb'" in script
    assert "bookingcom: 'Booking.com'" in script
