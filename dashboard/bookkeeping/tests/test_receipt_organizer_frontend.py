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
    assert 'id="receiptOrganizerSaveBtn"' in template
    assert 'id="receiptOrganizerNextBtn"' in template
    assert 'id="stepModalFooter"' in template
    assert ">Save</button>" in template
    assert ">Next</button>" in template
    assert "Approve &amp;" not in template
    assert "pendingReceiptUploadIds" in script
    assert "advanceReceiptReview()" in script
    assert "saveReceiptOrganization({ advance: false })" in script
    assert "this.nextReceiptOrganization()" in script
    assert "if (!advance)" in script
    assert "status: 'approved'" in script
    assert "this.elements.stepModalFooter.hidden = true" in script
    assert "this.elements.stepModalFooter.hidden = false" in script


def test_receipt_save_stays_put_while_next_advances_the_queue():
    script = (PROJECT_ROOT / "dashboard/static/js/bookkeeping.js").read_text()

    save_method = script.split("async saveReceiptOrganization({ advance = false } = {})", 1)[1]
    save_method = save_method.split("async clearUploadsByStage", 1)[0]
    stay_branch = save_method.split("if (!advance)", 1)[1].split("const reviewProgress", 1)[0]

    assert "Receipt saved" in stay_branch
    assert "Select Next when you are ready to continue." in stay_branch
    assert "return;" in stay_branch
    assert "const reviewProgress = this.advanceReceiptReview();" in save_method

    advance_method = script.split("advanceReceiptReview()", 1)[1].split("receiptDateLabel", 1)[0]
    assert "if (progress.remaining)" in advance_method
    assert "this.openReceiptOrganizer" in advance_method
    assert "this.closeReceiptOrganizer();" in advance_method


def test_next_saves_complete_receipts_and_skips_incomplete_receipts_without_validation_blocking():
    script = (PROJECT_ROOT / "dashboard/static/js/bookkeeping.js").read_text()

    next_method = script.split("async nextReceiptOrganization()", 1)[1]
    next_method = next_method.split("async saveReceiptOrganization", 1)[0]

    assert "receiptOrganizerForm.checkValidity()" in next_method
    assert "await this.saveReceiptOrganization({ advance: true });" in next_method
    assert "const reviewProgress = this.advanceReceiptReview();" in next_method
    assert "receiptOrganizerForm.reportValidity()" not in next_method
    assert "The skipped receipt was left unchanged." in next_method


def test_opening_an_existing_receipt_queues_the_remaining_processed_receipts():
    script = (PROJECT_ROOT / "dashboard/static/js/bookkeeping.js").read_text()

    queue_method = script.split("setReceiptReviewQueueFrom(uploadId)", 1)[1]
    queue_method = queue_method.split("openReceiptOrganizer(uploadId, options = {})", 1)[0]

    assert "entry.stage === 'expense'" in queue_method
    assert "!['queued', 'processing', 'failed'].includes(entry.upload_status)" in queue_method
    assert "reviewableUploadIds.slice(selectedIndex)" in queue_method
    assert "this.state.receiptReviewTotal = reviewableUploadIds.length" in queue_method
    assert "this.state.receiptReviewCompleted = selectedIndex" in queue_method
    assert "this.setReceiptReviewQueueFrom(uploadId);" in script


def test_receipt_organizer_communicates_read_only_development_boundary():
    template = (PROJECT_ROOT / "dashboard/templates/bookkeeping/index.html").read_text()

    assert "The shared sample Drive remains read-only during development and testing." in template
    assert "Nothing in Drive changes until you choose Upload to Google Drive or run the normal export sync." in template


def test_processed_receipts_expose_a_drive_folder_browser_and_approved_only_upload():
    template = (PROJECT_ROOT / "dashboard/templates/bookkeeping/index.html").read_text()
    script = (PROJECT_ROOT / "dashboard/static/js/bookkeeping.js").read_text()

    assert 'id="uploadReceiptsToDriveBtn"' in template
    assert '>Upload to Google Drive</button>' in template
    assert 'id="driveUploadDialog"' in template
    assert 'id="driveFolderBreadcrumbs"' in template
    assert 'id="driveFolderList"' in template
    assert 'id="driveNewFolderBtn"' in template
    assert 'id="driveFolderCreateForm"' in template
    assert 'id="driveFolderNameInput"' in template
    assert 'id="driveUploadSubmitBtn"' in template
    assert "organization.status === 'approved'" in script
    assert "!['queued', 'processing', 'failed'].includes(upload.upload_status)" in script
    assert "/bookkeeping/api/google-drive/folders?parent_id=" in script
    assert "/receipts/upload-to-google-drive`" in script
    assert "body: JSON.stringify({ folder_id: targetFolder.id })" in script
    assert "async createDriveFolder()" in script
    assert "body: JSON.stringify({ parent_id: parentFolder.id, name: folderName })" in script
    assert "This new folder is selected." in script
    assert "Cotton Candy creates the portfolio, year, month, and receipt-type folders" in template


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
