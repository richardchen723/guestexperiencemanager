import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_bookkeeping_ui_exposes_reviewable_receipt_organizer():
    template = (PROJECT_ROOT / "dashboard/templates/bookkeeping/index.html").read_text()
    script = (PROJECT_ROOT / "dashboard/static/js/bookkeeping.js").read_text()

    assert 'id="receiptOrganizerWorkspace"' in template
    assert 'id="receiptOrganizerModal"' not in template
    assert 'id="receiptOrganizerDate"' in template
    assert 'id="receiptOrganizerType"' in template
    assert 'id="receiptOrganizerFilename"' in template
    assert 'data-review-receipt-id="${upload.bookkeeping_upload_id}"' in script
    assert "openReceiptOrganizer(uploadId, options = {})" in script
    assert "/receipt-organization`" in script


def test_expense_ingestion_accepts_a_batch_and_reviews_receipts_sequentially():
    template = (PROJECT_ROOT / "dashboard/templates/bookkeeping/index.html").read_text()
    script = (PROJECT_ROOT / "dashboard/static/js/bookkeeping.js").read_text()

    expense_input = template.split('id="expenseFilesInput"', 1)[1].split(">", 1)[0]
    assert "multiple" in expense_input
    assert "1 · Choose receipt batch" in template
    assert 'id="receiptOrganizerProgress"' in template
    assert "3 · Confirm filing details" in template
    assert "Approve &amp; review next" in template
    assert "pendingReceiptUploadIds" in script
    assert "advanceReceiptReview()" in script


def test_receipt_organizer_communicates_read_only_development_boundary():
    template = (PROJECT_ROOT / "dashboard/templates/bookkeeping/index.html").read_text()

    assert "The shared sample Drive remains read-only during development and testing." in template
    assert "Nothing in Drive changes until the normal export sync runs." in template


def test_task_pane_follows_long_workbook_scroll_and_remains_usable():
    template = (PROJECT_ROOT / "dashboard/templates/bookkeeping/index.html").read_text()

    taskpane_rule = re.search(r"\.bk-taskpane\s*\{(?P<body>.*?)\}", template, re.DOTALL)
    assert taskpane_rule is not None
    taskpane_css = taskpane_rule.group("body")
    assert "position: sticky" in taskpane_css
    assert "top: calc(var(--product-topbar-height) + 0.9rem)" in taskpane_css
    assert "max-height: calc(100dvh - var(--product-topbar-height) - 4.5rem)" in taskpane_css

    active_pane_rule = re.search(r"\.bk-pane\s*\{(?P<body>.*?)\}", template, re.DOTALL)
    assert active_pane_rule is not None
    assert "overflow: auto" in active_pane_rule.group("body")
    assert "overscroll-behavior: contain" in active_pane_rule.group("body")

    compact_layout = template.split("@media (max-width: 1120px)", 1)[1]
    compact_taskpane_rule = re.search(r"\.bk-taskpane\s*\{(?P<body>.*?)\}", compact_layout, re.DOTALL)
    assert compact_taskpane_rule is not None
    assert "position: static" in compact_taskpane_rule.group("body")
    assert "max-height: none" in compact_taskpane_rule.group("body")
