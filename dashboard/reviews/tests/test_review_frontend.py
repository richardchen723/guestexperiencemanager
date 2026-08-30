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


def test_manual_severity_editor_is_auditable_and_updates_queue_state_immediately():
    project_root = Path(__file__).resolve().parents[3]
    template = (project_root / "dashboard/templates/reviews/index.html").read_text()
    script = (project_root / "dashboard/static/js/reviews-page.js").read_text()

    for element_id in (
        'reviewSeverityModal',
        'reviewConversationThread',
        'reviewConversationSummary',
        'reviewAiAssessment',
        'reviewAiConfidence',
        'reviewSeverityOptions',
        'reviewSeverityAudit',
        'reviewSeverityRestore',
    ):
        assert f'id="{element_id}"' in template
    assert 'data-action="edit-severity"' in script
    assert '/severity`' in script
    assert "await fetchJson(`/reviews/api/queue/${reservationId}/severity`)" in script
    assert 'renderReviewConversation(context.conversation || {}, activeReviewSeverity);' in script
    assert 'review-message--${direction}' in script
    assert "body: JSON.stringify({ restore_ai: true })" in script
    assert 'applyReviewSeverityResult(result);' in script
    assert 'populateQueueRiskFilter();' in script
    assert 'updateQueueSummary(queueSummaryFromReviews());' in script
    assert "AI was ${escapeHtml(aiRisk.short_label" in script


def test_severity_modal_prioritizes_the_complete_thread_and_compact_tags():
    project_root = Path(__file__).resolve().parents[3]
    template = (project_root / "dashboard/templates/reviews/index.html").read_text()
    stylesheet = (project_root / "dashboard/static/css/review-operations.css").read_text()

    conversation_position = template.index('class="review-conversation-panel"')
    decision_position = template.index('class="review-severity-decision"')
    assert conversation_position < decision_position
    assert 'Complete conversation' in template
    assert 'grid-template-columns: minmax(0, 1.55fr) minmax(340px, .8fr);' in stylesheet
    assert 'min-height: 30px;' in stylesheet
    assert '.review-conversation-thread' in stylesheet
    assert 'overflow-y: auto;' in stylesheet


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


def test_published_review_report_exposes_all_required_filters_and_details():
    project_root = Path(__file__).resolve().parents[3]
    template = (project_root / "dashboard/templates/reviews/published.html").read_text()
    script = (project_root / "dashboard/static/js/published-reviews.js").read_text()

    for element_id in (
        'publishedStartDate',
        'publishedEndDate',
        'publishedPortfolio',
        'publishedSort',
        'publishedRatingAll',
        'publishedResetFilters',
        'publishedReviews',
        'publishedReviewDetail',
        'publishedReviewDetailBody',
    ):
        assert f'id="{element_id}"' in template
    assert '{% for rating in [5, 4, 3, 2, 1] %}' in template
    assert 'value="{{ rating }}"' in template
    assert '/reviews/api/published' in script
    assert 'review.publication_date' in script
    assert 'review.listing_name' in script
    assert 'review.guest_name' in script
    assert 'review.review_text' in script
    assert 'review.rating_bucket' in script
    assert 'data-action="open-review"' in script
    assert 'openPublishedReviewDetail(review, button)' in script
    assert 'data-action="close-review-detail"' in template
