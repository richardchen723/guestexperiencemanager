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
