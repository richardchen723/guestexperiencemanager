#!/usr/bin/env python3
"""
Bookkeeping routes and APIs.
"""

import io
import json
import logging
import mimetypes
import os
import threading
import time
from calendar import monthrange
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from flask import Blueprint, jsonify, render_template, request, send_file
from sqlalchemy.orm import load_only, selectinload

from database.models import Listing, ListingTag, Tag, get_session as get_main_session

from dashboard.auth.decorators import admin_required
from dashboard.auth.models import get_google_drive_credential_for_user
from dashboard.auth.session import get_current_user
from dashboard.bookkeeping.models import (
    CHANGE_PROPOSAL_STATUSES,
    EXPENSE_CATEGORIES,
    EDITABLE_ROW_TYPES,
    REVENUE_SOURCES,
    SPECIAL_UPLOAD_SOURCES,
    BookkeepingAIChangeProposal,
    BookkeepingConversationMessage,
    BookkeepingCorrectionRule,
    BookkeepingExpenseItem,
    BookkeepingListingMapping,
    BookkeepingManualEdit,
    BookkeepingPeriod,
    BookkeepingPortfolio,
    BookkeepingProcessingBatch,
    BookkeepingRevenueItem,
    BookkeepingUpload,
    BookkeepingWorkspaceRevision,
    DEFAULT_REVENUE_CHANNELS,
    UPLOAD_STAGE_CORROBORATION,
    UPLOAD_STAGE_EXPENSE,
    UPLOAD_STAGE_REVENUE,
    get_session,
    init_bookkeeping_database,
    normalize_revenue_channels,
)
from dashboard.bookkeeping.service import (
    _revenue_field_header,
    AUTO_CATEGORIZE_CONFIDENCE_THRESHOLD,
    auto_extract_expense_evidence_from_structured,
    build_expense_item_payloads_from_extraction,
    build_revenue_item_payloads,
    build_property_alias_map,
    build_agent_context,
    build_bookkeeping_workbook,
    build_workspace_revision_snapshot,
    build_workspace_summary,
    decimal_or_none,
    extract_expense_evidence_bundle,
    get_upload_absolute_path,
    infer_reporting_period_start,
    logical_month_label,
    parse_date_or_none,
    parse_revenue_file,
    parse_supporting_file,
    reconcile_named_property_payment_uploads,
    reconcile_reimbursement_receipts,
    save_upload_bytes,
    source_label,
    sync_bookkeeping_uploads_to_google_drive,
)
import dashboard.config as config

logger = logging.getLogger(__name__)

bookkeeping_bp = Blueprint("bookkeeping", __name__, url_prefix="/bookkeeping")
EXPENSE_EXTRACTION_MAX_WORKERS = 4
ACTIVE_PROCESSING_BATCH_STATUSES = {"queued", "processing"}

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - dependency exists in deployed env
    OpenAI = None


IMAGE_PREVIEW_MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".jfif": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
}


def _normalized_upload_content_type(filename: str, mime_type: Optional[str]) -> Optional[str]:
    normalized = (mime_type or "").strip().lower()
    extension = os.path.splitext(filename or "")[1].lower()
    if normalized == "image/jfif":
        return "image/jpeg"
    if normalized and normalized not in {"application/octet-stream", "binary/octet-stream"}:
        return normalized
    guessed = IMAGE_PREVIEW_MIME_TYPES.get(extension) or mimetypes.guess_type(filename or "")[0]
    return guessed or (mime_type or None)


def _analyze_supporting_upload(
    *,
    stage: str,
    stored_path: str,
    filename: str,
    mime_type: Optional[str],
    property_alias_map: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    started_at = time.perf_counter()
    file_bytes = get_upload_absolute_path(stored_path).read_bytes()
    parsed_summary = parse_supporting_file(file_bytes, filename)
    auto_extraction = None
    structured_extraction = None

    if stage == UPLOAD_STAGE_EXPENSE:
        structured_extraction = extract_expense_evidence_bundle(
            file_bytes=file_bytes,
            filename=filename,
            mime_type=mime_type,
            parsed_summary=parsed_summary,
            property_alias_map=property_alias_map,
        )
        auto_extraction = auto_extract_expense_evidence_from_structured(structured_extraction)

    return {
        "parsed_summary": parsed_summary,
        "auto_extraction": auto_extraction,
        "structured_extraction": structured_extraction,
        "analysis_seconds": round(time.perf_counter() - started_at, 3),
    }


def _analyze_supporting_uploads(
    files_to_analyze: List[Dict[str, Any]],
    stage: str,
    property_alias_map: Optional[Dict[str, str]] = None,
) -> Dict[str, Dict[str, Any]]:
    if not files_to_analyze:
        return {}

    if stage != UPLOAD_STAGE_EXPENSE:
        return {
            file_info["stored_path"]: _analyze_supporting_upload(
                stage=stage,
                stored_path=file_info["stored_path"],
                filename=file_info["filename"],
                mime_type=file_info["mime_type"],
                property_alias_map=property_alias_map,
            )
            for file_info in files_to_analyze
        }

    started_at = time.perf_counter()
    max_workers = max(1, min(EXPENSE_EXTRACTION_MAX_WORKERS, len(files_to_analyze)))
    results: Dict[str, Dict[str, Any]] = {}

    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="bk-expense") as executor:
        future_map = {
            executor.submit(
                _analyze_supporting_upload,
                stage=stage,
                stored_path=file_info["stored_path"],
                filename=file_info["filename"],
                mime_type=file_info["mime_type"],
                property_alias_map=property_alias_map,
            ): file_info
            for file_info in files_to_analyze
        }
        for future in as_completed(future_map):
            file_info = future_map[future]
            try:
                results[file_info["stored_path"]] = future.result()
            except Exception as extraction_error:
                logger.warning(
                    "Auto extraction failed for %s: %s",
                    file_info["filename"],
                    extraction_error,
                    exc_info=True,
                )
                results[file_info["stored_path"]] = {
                    "parsed_summary": {"preview_text": None, "page_count": None},
                    "auto_extraction": None,
                    "structured_extraction": None,
                    "analysis_seconds": round(time.perf_counter() - started_at, 3),
                }

    logger.info(
        "Processed %s expense evidence file(s) in %.2fs using %s worker(s)",
        len(files_to_analyze),
        time.perf_counter() - started_at,
        max_workers,
    )
    return results


def _portfolio_query(session):
    return session.query(BookkeepingPortfolio).options(
        selectinload(BookkeepingPortfolio.periods),
        selectinload(BookkeepingPortfolio.listing_mappings),
        selectinload(BookkeepingPortfolio.correction_rules),
    )


def _period_query(session, include_workspace_related: bool = False):
    options = [
        selectinload(BookkeepingPeriod.portfolio).selectinload(BookkeepingPortfolio.listing_mappings),
        selectinload(BookkeepingPeriod.portfolio).selectinload(BookkeepingPortfolio.correction_rules),
    ]
    if include_workspace_related:
        options.extend(
            [
                selectinload(BookkeepingPeriod.uploads),
                selectinload(BookkeepingPeriod.revenue_items),
                selectinload(BookkeepingPeriod.expense_items),
                selectinload(BookkeepingPeriod.conversation_messages),
                selectinload(BookkeepingPeriod.manual_edits),
                selectinload(BookkeepingPeriod.change_proposals),
                selectinload(BookkeepingPeriod.processing_batches),
                selectinload(BookkeepingPeriod.revisions).load_only(
                    BookkeepingWorkspaceRevision.bookkeeping_workspace_revision_id,
                    BookkeepingWorkspaceRevision.period_id,
                    BookkeepingWorkspaceRevision.status,
                    BookkeepingWorkspaceRevision.workbook_filename,
                    BookkeepingWorkspaceRevision.created_by,
                    BookkeepingWorkspaceRevision.created_at,
                ),
            ]
        )
    return session.query(BookkeepingPeriod).options(*options)


def _get_period_or_404(session, period_id: int, include_workspace_related: bool = False):
    period = (
        _period_query(session, include_workspace_related=include_workspace_related)
        .filter(BookkeepingPeriod.bookkeeping_period_id == period_id)
        .first()
    )
    if not period:
        return None, (jsonify({"error": "Bookkeeping period not found"}), 404)
    return period, None


def _parse_boolean(value):
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _last_day_of_month(start_date):
    last_day = monthrange(start_date.year, start_date.month)[1]
    return start_date.replace(day=last_day)


def _expense_item_from_payload(item, payload, created_by):
    item.category = payload.get("category", item.category)
    item.item_name = (payload.get("item_name") or "").strip() or None
    item.vendor = (payload.get("vendor") or "").strip() or None
    item.property_code = (payload.get("property_code") or "").strip() or None
    item.scope = payload.get("scope") or "property"
    item.description = (payload.get("description") or "").strip() or None
    item.amount = decimal_or_none(payload.get("amount"))
    item.service_date = parse_date_or_none(payload.get("service_date"))
    item.payment_date = parse_date_or_none(payload.get("payment_date"))
    item.payment_method = (payload.get("payment_method") or "").strip() or None
    item.account_holder = (payload.get("account_holder") or "").strip() or None
    item.account_number = (payload.get("account_number") or "").strip() or None
    item.purchase_type = (payload.get("purchase_type") or "").strip() or None
    item.store_name = (payload.get("store_name") or "").strip() or None
    item.quantity = decimal_or_none(payload.get("quantity"))
    item.unit_amount = decimal_or_none(payload.get("unit_amount"))
    item.subtotal = decimal_or_none(payload.get("subtotal"))
    item.discount = decimal_or_none(payload.get("discount"))
    item.shipping = decimal_or_none(payload.get("shipping"))
    item.tax = decimal_or_none(payload.get("tax"))
    item.total = decimal_or_none(payload.get("total"))
    item.reimbursement_method = (payload.get("reimbursement_method") or "").strip() or None
    item.reimbursement_date = parse_date_or_none(payload.get("reimbursement_date"))
    item.details = (payload.get("details") or "").strip() or None
    item.needs_review = _parse_boolean(payload.get("needs_review"))
    item.review_reason = (payload.get("review_reason") or "").strip() or None
    if "upload_id" in payload:
        upload_id = payload.get("upload_id")
        if upload_id in ("", None):
            item.upload_id = None
        else:
            item.upload_id = int(upload_id)
    if getattr(item, "created_by", None) is None:
        item.created_by = created_by
    return item


MATERIAL_EXPENSE_FIELDS = {
    "category",
    "item_name",
    "vendor",
    "property_code",
    "scope",
    "description",
    "amount",
    "service_date",
    "payment_date",
    "payment_method",
    "account_holder",
    "account_number",
    "purchase_type",
    "store_name",
    "quantity",
    "unit_amount",
    "subtotal",
    "discount",
    "shipping",
    "tax",
    "total",
    "reimbursement_method",
    "reimbursement_date",
    "details",
    "needs_review",
    "review_reason",
    "upload_id",
}

MATERIAL_REVENUE_FIELDS = {
    "guest_name",
    "property_code",
    "transaction_date",
    "booking_date",
    "start_date",
    "end_date",
    "nights",
    "gross_amount",
    "paid_out_amount",
    "commission_amount",
    "hostaway_fee_amount",
    "stripe_fee_amount",
    "cleaning_fee_amount",
    "tax_amount",
    "refund_amount",
    "details",
    "needs_review",
    "review_reason",
    "listing_mapping_id",
    "reservation_identifier",
    "confirmation_code",
    "currency",
    "transaction_type",
}

CORRECTION_RULE_FIELDS = {
    "expense_item": {"category", "item_name", "vendor", "property_code", "scope", "payment_method"},
    "revenue_item": {"property_code", "listing_mapping_id"},
}


def _json_safe(value):
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _serialize_listing_record(listing):
    return {
        "listing_id": listing.listing_id,
        "name": listing.name,
        "internal_listing_name": listing.internal_listing_name,
        "address": listing.address,
        "status": listing.status,
    }


def _normalize_listing_tag(tag_name):
    raw_value = (tag_name or "").strip()
    if not raw_value:
        return None
    return Tag.normalize_name(raw_value)


def _listings_for_tag(main_session, tag_name):
    normalized_tag = _normalize_listing_tag(tag_name)
    if not normalized_tag:
        return []
    return (
        main_session.query(Listing)
        .join(ListingTag, ListingTag.listing_id == Listing.listing_id)
        .join(Tag, Tag.tag_id == ListingTag.tag_id)
        .filter(Tag.name == normalized_tag)
        .order_by(Listing.internal_listing_name.asc().nullslast(), Listing.name.asc())
        .distinct()
        .all()
    )


def _candidate_listings_for_portfolio(main_session, portfolio):
    if not getattr(portfolio, "listing_tag", None):
        return (
            main_session.query(Listing)
            .order_by(Listing.internal_listing_name.asc().nullslast(), Listing.name.asc())
            .all()
        )

    listings = _listings_for_tag(main_session, portfolio.listing_tag)
    existing_listing_ids = {
        mapping.listing_id
        for mapping in getattr(portfolio, "listing_mappings", []) or []
        if getattr(mapping, "listing_id", None) is not None
    }
    current_listing_ids = {listing.listing_id for listing in listings}
    missing_listing_ids = existing_listing_ids - current_listing_ids
    if missing_listing_ids:
        listings.extend(
            main_session.query(Listing)
            .filter(Listing.listing_id.in_(missing_listing_ids))
            .order_by(Listing.internal_listing_name.asc().nullslast(), Listing.name.asc())
            .all()
        )
    listings.sort(key=lambda listing: ((listing.internal_listing_name or "").lower(), (listing.name or "").lower()))
    return listings


def _seed_portfolio_listing_mappings(session, portfolio, listings, user_id):
    created_mappings = []
    seen_listing_ids = set()
    for listing in listings:
        if listing.listing_id in seen_listing_ids:
            continue
        seen_listing_ids.add(listing.listing_id)
        mapping = BookkeepingListingMapping(
            portfolio_id=portfolio.bookkeeping_portfolio_id,
            listing_id=listing.listing_id,
            listing_name=listing.name,
            internal_listing_name=listing.internal_listing_name,
            official_name=(listing.internal_listing_name or listing.name or f"Listing {listing.listing_id}").strip(),
            aliases=[],
            is_active=True,
            created_by=user_id,
        )
        session.add(mapping)
        created_mappings.append(mapping)
    portfolio.listing_count = len(created_mappings)
    return created_mappings


def _sync_portfolio_listing_mappings_to_listings(session, portfolio, listings, user_id):
    existing_by_listing_id = {
        mapping.listing_id: mapping
        for mapping in getattr(portfolio, "listing_mappings", []) or []
    }
    synced_mappings = []
    seen_listing_ids = set()
    removed_mapping_ids = []

    for listing in listings:
        listing_id = getattr(listing, "listing_id", None)
        if listing_id is None or listing_id in seen_listing_ids:
            continue
        seen_listing_ids.add(listing_id)
        mapping = existing_by_listing_id.get(listing_id)
        if mapping is None:
            mapping = BookkeepingListingMapping(
                portfolio_id=portfolio.bookkeeping_portfolio_id,
                listing_id=listing_id,
                listing_name=listing.name,
                internal_listing_name=listing.internal_listing_name,
                official_name=(listing.internal_listing_name or listing.name or f"Listing {listing_id}").strip(),
                aliases=[],
                is_active=True,
                created_by=user_id,
            )
            session.add(mapping)
        else:
            mapping.listing_name = listing.name
            mapping.internal_listing_name = listing.internal_listing_name
            if not (mapping.official_name or "").strip():
                mapping.official_name = (listing.internal_listing_name or listing.name or f"Listing {listing_id}").strip()
            if mapping.aliases is None:
                mapping.aliases = []
            mapping.is_active = True
        synced_mappings.append(mapping)

    for listing_id, mapping in existing_by_listing_id.items():
        if listing_id in seen_listing_ids:
            continue
        removed_mapping_ids.append(mapping.bookkeeping_listing_mapping_id)
        session.delete(mapping)

    if removed_mapping_ids:
        session.query(BookkeepingRevenueItem).filter(
            BookkeepingRevenueItem.listing_mapping_id.in_(removed_mapping_ids)
        ).update({"listing_mapping_id": None}, synchronize_session=False)

    portfolio.listing_count = len([mapping for mapping in synced_mappings if mapping.is_active])
    return synced_mappings


def _cotton_candy_session():
    return get_main_session(config.MAIN_DATABASE_PATH)


def _listing_mapping_lookup(listing_mappings):
    alias_lookup = {}
    by_listing_id = {}
    by_mapping_id = {}
    for mapping in listing_mappings or []:
        mapping_id = getattr(mapping, "bookkeeping_listing_mapping_id", None)
        if mapping_id is not None:
            by_mapping_id[mapping_id] = mapping
        listing_id = getattr(mapping, "listing_id", None)
        if listing_id is not None:
            by_listing_id[listing_id] = mapping
        if not getattr(mapping, "is_active", True):
            continue
        candidates = [
            getattr(mapping, "official_name", None),
            getattr(mapping, "listing_name", None),
            getattr(mapping, "internal_listing_name", None),
            str(listing_id) if listing_id is not None else None,
        ]
        candidates.extend(getattr(mapping, "aliases", None) or [])
        for candidate in candidates:
            normalized = "".join(ch for ch in str(candidate or "").upper() if ch.isalnum())
            if normalized:
                alias_lookup.setdefault(normalized, mapping)
    return {
        "alias_lookup": alias_lookup,
        "by_listing_id": by_listing_id,
        "by_mapping_id": by_mapping_id,
    }


def _material_fields_for_row_type(row_type):
    if row_type == "expense_item":
        return MATERIAL_EXPENSE_FIELDS
    if row_type == "revenue_item":
        return MATERIAL_REVENUE_FIELDS
    return set()


def _mark_period_dirty(period):
    if not period.status or period.status == "approved":
        period.status = "in_review"
    elif period.status == "exported":
        period.status = "in_review"


def _processing_batch_is_active(batch) -> bool:
    return bool(batch and getattr(batch, "status", None) in ACTIVE_PROCESSING_BATCH_STATUSES)


def _active_processing_batch(session, period_id: int, stage: str):
    return (
        session.query(BookkeepingProcessingBatch)
        .filter(
            BookkeepingProcessingBatch.period_id == period_id,
            BookkeepingProcessingBatch.stage == stage,
            BookkeepingProcessingBatch.status.in_(tuple(ACTIVE_PROCESSING_BATCH_STATUSES)),
        )
        .order_by(BookkeepingProcessingBatch.created_at.desc())
        .first()
    )


def _upload_is_processing(upload) -> bool:
    return getattr(upload, "upload_status", None) in {"queued", "processing"}


def _supporting_upload_source(stage: str, source: str) -> str:
    if source and source != "auto":
        return source
    if stage == UPLOAD_STAGE_EXPENSE:
        return "expense_evidence"
    if stage == UPLOAD_STAGE_CORROBORATION:
        return "bank_statement"
    return source or ""


def _prepared_file_payloads(files, relative_paths, period_id: int, stage: str) -> List[Dict[str, Any]]:
    prepared_files: List[Dict[str, Any]] = []
    for index, file_storage in enumerate(files):
        file_bytes = file_storage.read()
        if not file_bytes:
            continue

        stored_path, file_size = save_upload_bytes(period_id, stage, file_storage.filename, file_bytes)
        relative_path = relative_paths[index] if index < len(relative_paths) else None
        prepared_files.append(
            {
                "filename": file_storage.filename,
                "mime_type": file_storage.mimetype,
                "stored_path": stored_path,
                "file_size": file_size,
                "relative_path": relative_path,
            }
        )
    return prepared_files


def _queue_supporting_upload_batch(session, period, stage: str, source: str, notes: Optional[str], prepared_files, user_id: int):
    source_value = _supporting_upload_source(stage, source)
    batch = BookkeepingProcessingBatch(
        period_id=period.bookkeeping_period_id,
        stage=stage,
        status="queued",
        total_uploads=len(prepared_files),
        processed_uploads=0,
        successful_uploads=0,
        failed_uploads=0,
        created_by=user_id,
    )
    session.add(batch)
    session.flush()

    created_uploads = []
    for file_info in prepared_files:
        upload = BookkeepingUpload(
            period_id=period.bookkeeping_period_id,
            processing_batch_id=batch.bookkeeping_processing_batch_id,
            stage=stage,
            source=source_value,
            detected_source=None,
            sheet_name=None,
            upload_status="queued",
            original_filename=file_info["filename"],
            original_relative_path=file_info["relative_path"],
            stored_path=file_info["stored_path"],
            content_type=_normalized_upload_content_type(file_info["filename"], file_info["mime_type"]),
            file_extension=os.path.splitext(file_info["filename"])[1].lower(),
            file_size=file_info["file_size"],
            row_count=None,
            headers=[],
            preview_rows=[],
            parsed_rows=[],
            summary={"status": "queued"},
            processing_error=None,
            notes=notes,
            uploaded_by=user_id,
        )
        session.add(upload)
        created_uploads.append(upload)

    return batch, created_uploads


def _processing_batch_payload(batch, uploads=None):
    payload = batch.to_dict()
    if uploads is not None:
        payload["uploads"] = [
            {
                "bookkeeping_upload_id": upload.bookkeeping_upload_id,
                "original_filename": upload.original_filename,
                "upload_status": upload.upload_status,
                "processing_error": upload.processing_error,
                "processing_started_at": upload.processing_started_at.isoformat() if upload.processing_started_at else None,
                "processing_completed_at": upload.processing_completed_at.isoformat() if upload.processing_completed_at else None,
            }
            for upload in uploads
        ]
    return payload


def _workspace_payload(period):
    return build_workspace_summary(
        period.portfolio,
        period,
        period.uploads,
        period.expense_items,
        revenue_items=period.revenue_items,
        listing_mappings=period.portfolio.listing_mappings,
        change_proposals=period.change_proposals,
        processing_batches=period.processing_batches,
        revisions=period.revisions,
    )


def _normalize_rule_value(value):
    if value in (None, "", [], {}):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return "".join(ch for ch in str(value).upper() if ch.isalnum())


def _rule_matches_context(match_context, candidate_context):
    for key, expected in (match_context or {}).items():
        normalized_expected = _normalize_rule_value(expected)
        normalized_candidate = _normalize_rule_value((candidate_context or {}).get(key))
        if normalized_expected is None:
            continue
        if normalized_expected != normalized_candidate:
            return False
    return True


def _apply_correction_rules(payload, row_type, correction_rules, context):
    updated_payload = dict(payload)
    matched_rule_ids = []
    for rule in correction_rules or []:
        if rule.row_type != row_type:
            continue
        if not _rule_matches_context(rule.match_context, context):
            continue
        for field_name, value in (rule.target_fields or {}).items():
            updated_payload[field_name] = value
        matched_rule_ids.append(rule.bookkeeping_correction_rule_id)
    if matched_rule_ids:
        updated_payload["_applied_correction_rule_ids"] = matched_rule_ids
    return updated_payload


def _manual_override_fields(session, row_type, row_id):
    edits = (
        session.query(BookkeepingManualEdit)
        .filter(
            BookkeepingManualEdit.row_type == row_type,
            BookkeepingManualEdit.row_id == row_id,
        )
        .all()
    )
    fields = set()
    for edit in edits:
        fields.update(edit.changed_fields or [])
    return fields


def _record_manual_edit(session, period_id, row_type, row_id, before_data, after_data, changed_fields, edit_note, user_id):
    manual_edit = BookkeepingManualEdit(
        period_id=period_id,
        row_type=row_type,
        row_id=row_id,
        changed_fields=sorted(changed_fields),
        before_data={key: _json_safe(value) for key, value in (before_data or {}).items()},
        after_data={key: _json_safe(value) for key, value in (after_data or {}).items()},
        edit_note=edit_note,
        created_by=user_id,
    )
    session.add(manual_edit)
    return manual_edit


def _store_correction_rule(session, portfolio_id, row_type, context, after_data, changed_fields, edit_note, manual_edit_id, user_id):
    target_fields = {
        field_name: _json_safe(after_data.get(field_name))
        for field_name in changed_fields
        if field_name in CORRECTION_RULE_FIELDS.get(row_type, set())
    }
    if not target_fields:
        return None

    normalized_context = {
        key: value
        for key, value in (context or {}).items()
        if value not in (None, "", [], {})
    }
    if not normalized_context:
        return None

    rule = BookkeepingCorrectionRule(
        portfolio_id=portfolio_id,
        row_type=row_type,
        match_context=normalized_context,
        target_fields=target_fields,
        note=edit_note,
        source_manual_edit_id=manual_edit_id,
        created_by=user_id,
    )
    session.add(rule)
    return rule


def _build_expense_rule_context(payload, upload=None):
    extraction_data = payload.get("extraction_data") or {}
    shared_fields = extraction_data.get("shared_fields") or {}
    return {
        "source": getattr(upload, "source", None),
        "category": payload.get("category"),
        "vendor": payload.get("vendor"),
        "item_name": payload.get("item_name"),
        "payment_method": payload.get("payment_method") or shared_fields.get("payment_method"),
        "document_type": extraction_data.get("document_type"),
        "raw_property_code": payload.get("property_code"),
    }


def _build_expense_rule_context_from_item(item):
    extraction_data = getattr(item, "extraction_data", None) or {}
    shared_fields = extraction_data.get("shared_fields") or {}
    upload = getattr(item, "upload", None)
    return {
        "source": getattr(upload, "source", None),
        "category": getattr(item, "category", None),
        "vendor": getattr(item, "vendor", None),
        "item_name": getattr(item, "item_name", None),
        "payment_method": getattr(item, "payment_method", None) or shared_fields.get("payment_method"),
        "document_type": extraction_data.get("document_type"),
        "raw_property_code": getattr(item, "property_code", None),
    }


def _build_revenue_rule_context(payload, source):
    return {
        "source": source,
        "raw_listing_name": payload.get("raw_listing_name"),
        "property_code": payload.get("property_code"),
        "reservation_identifier": payload.get("reservation_identifier"),
    }


def _build_revenue_rule_context_from_item(item):
    return {
        "source": getattr(item, "source", None),
        "raw_listing_name": getattr(item, "raw_listing_name", None),
        "property_code": getattr(item, "property_code", None),
        "reservation_identifier": getattr(item, "reservation_identifier", None),
    }


def _revenue_item_data(item):
    return {
        "guest_name": item.guest_name,
        "property_code": item.property_code,
        "transaction_date": item.transaction_date.isoformat() if item.transaction_date else None,
        "booking_date": item.booking_date.isoformat() if item.booking_date else None,
        "start_date": item.start_date.isoformat() if item.start_date else None,
        "end_date": item.end_date.isoformat() if item.end_date else None,
        "nights": item.nights,
        "gross_amount": float(item.gross_amount) if item.gross_amount is not None else None,
        "paid_out_amount": float(item.paid_out_amount) if item.paid_out_amount is not None else None,
        "commission_amount": float(item.commission_amount) if item.commission_amount is not None else None,
        "hostaway_fee_amount": float(item.hostaway_fee_amount) if item.hostaway_fee_amount is not None else None,
        "stripe_fee_amount": float(item.stripe_fee_amount) if item.stripe_fee_amount is not None else None,
        "cleaning_fee_amount": float(item.cleaning_fee_amount) if item.cleaning_fee_amount is not None else None,
        "tax_amount": float(item.tax_amount) if item.tax_amount is not None else None,
        "refund_amount": float(item.refund_amount) if item.refund_amount is not None else None,
        "details": item.details,
        "needs_review": item.needs_review,
        "review_reason": item.review_reason,
        "listing_mapping_id": item.listing_mapping_id,
        "reservation_identifier": item.reservation_identifier,
        "confirmation_code": item.confirmation_code,
        "currency": item.currency,
        "transaction_type": item.transaction_type,
        "normalized_data": dict(item.normalized_data or {}),
    }


def _parse_iso_payload_date(value):
    return parse_date_or_none(value)


def _revenue_item_from_payload(item, payload, created_by):
    source = (payload.get("source") or getattr(item, "source", None) or "").strip() or getattr(item, "source", None)
    item.source = source
    item.row_index = int(payload.get("row_index") or getattr(item, "row_index", 0) or 0)
    item.reservation_identifier = (payload.get("reservation_identifier") or "").strip() or None
    item.confirmation_code = (payload.get("confirmation_code") or "").strip() or None
    item.guest_name = (payload.get("guest_name") or "").strip() or None
    item.property_code = (payload.get("property_code") or "").strip() or None
    item.raw_listing_name = (payload.get("raw_listing_name") or "").strip() or None
    item.transaction_type = (payload.get("transaction_type") or "").strip() or None
    item.currency = (payload.get("currency") or "").strip() or None
    item.transaction_date = _parse_iso_payload_date(payload.get("transaction_date"))
    item.booking_date = _parse_iso_payload_date(payload.get("booking_date"))
    item.start_date = _parse_iso_payload_date(payload.get("start_date"))
    item.end_date = _parse_iso_payload_date(payload.get("end_date"))
    item.nights = int(payload.get("nights")) if payload.get("nights") not in (None, "") else None
    item.gross_amount = decimal_or_none(payload.get("gross_amount"))
    item.paid_out_amount = decimal_or_none(payload.get("paid_out_amount"))
    item.commission_amount = decimal_or_none(payload.get("commission_amount"))
    item.hostaway_fee_amount = decimal_or_none(payload.get("hostaway_fee_amount"))
    item.stripe_fee_amount = decimal_or_none(payload.get("stripe_fee_amount"))
    item.cleaning_fee_amount = decimal_or_none(payload.get("cleaning_fee_amount"))
    item.tax_amount = decimal_or_none(payload.get("tax_amount"))
    item.refund_amount = decimal_or_none(payload.get("refund_amount"))
    item.details = (payload.get("details") or "").strip() or None
    item.needs_review = _parse_boolean(payload.get("needs_review"))
    item.review_reason = (payload.get("review_reason") or "").strip() or None
    item.listing_mapping_id = int(payload.get("listing_mapping_id")) if payload.get("listing_mapping_id") not in (None, "") else None

    normalized_data = dict(getattr(item, "normalized_data", None) or getattr(item, "raw_data", None) or {})
    normalized_data.update(_json_safe(payload.get("normalized_data") or {}))
    for field_name in (
        "reservation_identifier",
        "confirmation_code",
        "guest_name",
        "property_code",
        "transaction_type",
        "transaction_date",
        "booking_date",
        "start_date",
        "end_date",
        "nights",
        "currency",
        "gross_amount",
        "paid_out_amount",
        "commission_amount",
        "hostaway_fee_amount",
        "stripe_fee_amount",
        "cleaning_fee_amount",
        "tax_amount",
        "refund_amount",
        "details",
    ):
        header = _revenue_field_header(source, field_name)
        if not header:
            continue
        value = getattr(item, field_name)
        if value is None:
            normalized_data.pop(header, None)
        else:
            normalized_data[header] = _json_safe(value)

    item.normalized_data = _json_safe(normalized_data)
    if payload.get("raw_data") is not None or getattr(item, "raw_data", None) is None:
        item.raw_data = _json_safe(payload.get("raw_data") or normalized_data)
    if getattr(item, "created_by", None) is None:
        item.created_by = created_by
    return item


def _queue_change_proposal(session, period_id, row_type, row_id, source_upload_id, current_values, proposed_values, reason):
    proposal = BookkeepingAIChangeProposal(
        period_id=period_id,
        row_type=row_type,
        row_id=row_id,
        source_upload_id=source_upload_id,
        current_values={key: _json_safe(value) for key, value in (current_values or {}).items()},
        proposed_values={key: _json_safe(value) for key, value in (proposed_values or {}).items()},
        reason=reason,
        status="pending",
    )
    session.add(proposal)
    return proposal


def _expense_item_snapshot(item):
    payload = item.to_dict()
    payload.pop("created_at", None)
    payload.pop("updated_at", None)
    return payload


def _create_workspace_revision(session, period, user_id, status, workbook_filename=None):
    snapshot = build_workspace_revision_snapshot(
        period.portfolio,
        period,
        period.uploads,
        period.expense_items,
        revenue_items=period.revenue_items,
        listing_mappings=period.portfolio.listing_mappings,
        change_proposals=period.change_proposals,
        processing_batches=period.processing_batches,
    )
    serialized_snapshot = json.dumps(snapshot, default=str)
    if len(serialized_snapshot) > 250_000:
        snapshot = {
            "snapshot_truncated": True,
            "portfolio": snapshot.get("portfolio", {}),
            "period": snapshot.get("period", {}),
            "revenue_progress": snapshot.get("revenue_progress", {}),
            "summary_cards": snapshot.get("summary_cards", {}),
            "software_totals": snapshot.get("software_totals", {}),
            "upload_counts": snapshot.get("upload_counts", {}),
            "upload_status_counts": snapshot.get("upload_status_counts", {}),
            "serialized_chars": len(serialized_snapshot),
        }
    revision = BookkeepingWorkspaceRevision(
        period_id=period.bookkeeping_period_id,
        status=status,
        summary_snapshot=snapshot,
        workbook_filename=workbook_filename,
        created_by=user_id,
    )
    session.add(revision)
    return revision


def _apply_manual_edit(session, period, portfolio, row_type, row_id, before_data, after_data, changed_fields, edit_note, user_id, context):
    manual_edit = _record_manual_edit(
        session,
        period.bookkeeping_period_id,
        row_type,
        row_id,
        before_data,
        after_data,
        changed_fields,
        edit_note,
        user_id,
    )
    session.flush()
    _store_correction_rule(
        session,
        portfolio.bookkeeping_portfolio_id,
        row_type,
        context,
        after_data,
        changed_fields,
        edit_note,
        manual_edit.bookkeeping_manual_edit_id,
        user_id,
    )


def _build_portfolio_rule_set(portfolio):
    return list(getattr(portfolio, "correction_rules", None) or [])


def _sync_revenue_items_for_uploads(session, period, uploads, created_by):
    property_alias_map = build_property_alias_map(period.portfolio, period.uploads)
    listing_lookup = _listing_mapping_lookup(period.portfolio.listing_mappings)
    correction_rules = _build_portfolio_rule_set(period.portfolio)
    created_items = []
    for upload in uploads:
        if upload.stage != UPLOAD_STAGE_REVENUE:
            continue
        existing_items = session.query(BookkeepingRevenueItem).filter(
            BookkeepingRevenueItem.upload_id == upload.bookkeeping_upload_id
        ).all()
        for item in existing_items:
            session.delete(item)
        session.flush()

        revenue_payloads = build_revenue_item_payloads(
            upload,
            property_alias_map=property_alias_map,
            listing_lookup=listing_lookup,
        )
        for payload in revenue_payloads:
            payload = _apply_correction_rules(
                payload,
                "revenue_item",
                correction_rules,
                _build_revenue_rule_context(payload, upload.source),
            )
            item = BookkeepingRevenueItem(
                period_id=period.bookkeeping_period_id,
                upload_id=upload.bookkeeping_upload_id,
                created_by=created_by,
            )
            _revenue_item_from_payload(item, payload, created_by)
            session.add(item)
            created_items.append(item)
    return created_items


def _expense_payload_key(payload):
    extraction_data = payload.get("extraction_data") or {}
    return (
        extraction_data.get("group_key"),
        extraction_data.get("line_index"),
        payload.get("category"),
    )


def _existing_expense_item_key(item):
    extraction_data = getattr(item, "extraction_data", None) or {}
    return (
        extraction_data.get("group_key"),
        extraction_data.get("line_index"),
        getattr(item, "category", None),
    )


def _create_auto_expense_item(period_id, upload_id, payload, created_by):
    return BookkeepingExpenseItem(
        period_id=period_id,
        upload_id=upload_id,
        category=payload["category"],
        item_name=payload.get("item_name"),
        vendor=payload.get("vendor"),
        property_code=payload.get("property_code"),
        scope=payload.get("scope") or "property",
        amount=decimal_or_none(payload.get("amount")),
        total=decimal_or_none(payload.get("total")),
        service_date=payload.get("service_date"),
        payment_date=payload.get("payment_date"),
        payment_method=payload.get("payment_method"),
        account_holder=payload.get("account_holder"),
        account_number=payload.get("account_number"),
        purchase_type=payload.get("purchase_type"),
        store_name=payload.get("store_name"),
        quantity=decimal_or_none(payload.get("quantity")),
        unit_amount=decimal_or_none(payload.get("unit_amount")),
        subtotal=decimal_or_none(payload.get("subtotal")),
        discount=decimal_or_none(payload.get("discount")),
        shipping=decimal_or_none(payload.get("shipping")),
        tax=decimal_or_none(payload.get("tax")),
        reimbursement_method=payload.get("reimbursement_method"),
        reimbursement_date=payload.get("reimbursement_date"),
        details=payload.get("details"),
        needs_review=bool(payload.get("needs_review")),
        review_reason=payload.get("review_reason"),
        extraction_data=payload.get("extraction_data"),
        created_by=created_by,
    )


def _apply_payment_memo_reconciliation(session, period, created_by, created_items=None):
    property_alias_map = build_property_alias_map(period.portfolio, period.uploads)
    payloads_by_upload = reconcile_named_property_payment_uploads(period.uploads, property_alias_map)
    if not payloads_by_upload:
        return

    created_items = created_items if created_items is not None else []
    for upload_id, payloads in payloads_by_upload.items():
        linked_items = session.query(BookkeepingExpenseItem).filter(
            BookkeepingExpenseItem.upload_id == upload_id
        ).all()
        if any(_manual_override_fields(session, "expense_item", item.bookkeeping_expense_item_id) for item in linked_items):
            for item in linked_items:
                item.needs_review = True
                if not item.review_reason:
                    item.review_reason = "Payment memo reconciliation found new information, but this row already has human edits."
            continue
        for item in linked_items:
            if (item.extraction_data or {}).get("source") == "auto_expense_evidence":
                if item in created_items:
                    created_items.remove(item)
                session.delete(item)

        for payload in payloads:
            item = _create_auto_expense_item(
                period_id=period.bookkeeping_period_id,
                upload_id=upload_id,
                payload=payload,
                created_by=created_by,
            )
            session.add(item)
            created_items.append(item)

    session.flush()


def _prepare_upload_deletion(session, upload):
    linked_items = session.query(BookkeepingExpenseItem).filter(
        BookkeepingExpenseItem.upload_id == upload.bookkeeping_upload_id
    ).all()
    linked_revenue_items = session.query(BookkeepingRevenueItem).filter(
        BookkeepingRevenueItem.upload_id == upload.bookkeeping_upload_id
    ).all()

    deleted_auto_items = 0
    unlinked_manual_items = 0
    for item in linked_items:
        extraction_source = (item.extraction_data or {}).get("source")
        if extraction_source == "auto_expense_evidence":
            session.delete(item)
            deleted_auto_items += 1
        else:
            item.upload_id = None
            if not item.review_reason:
                item.review_reason = "Source upload removed. Confirm this expense row still has valid supporting evidence."
            item.needs_review = True
            unlinked_manual_items += 1

    deleted_revenue_items = 0
    for item in linked_revenue_items:
        proposal_query = session.query(BookkeepingAIChangeProposal).filter(
            BookkeepingAIChangeProposal.row_type == "revenue_item",
            BookkeepingAIChangeProposal.row_id == item.bookkeeping_revenue_item_id,
        )
        for proposal in proposal_query.all():
            session.delete(proposal)
        session.delete(item)
        deleted_revenue_items += 1

    upload_related_proposals = session.query(BookkeepingAIChangeProposal).filter(
        BookkeepingAIChangeProposal.source_upload_id == upload.bookkeeping_upload_id
    ).all()
    for proposal in upload_related_proposals:
        session.delete(proposal)

    stored_path = upload.stored_path
    upload_id = upload.bookkeeping_upload_id
    session.delete(upload)
    return {
        "upload_id": upload_id,
        "stored_path": stored_path,
        "deleted_auto_expense_items": deleted_auto_items,
        "unlinked_manual_expense_items": unlinked_manual_items,
        "deleted_revenue_items": deleted_revenue_items,
    }


def _reprocess_expense_uploads(session, period, current_user, upload_ids=None):
    query = session.query(BookkeepingUpload).filter(
        BookkeepingUpload.period_id == period.bookkeeping_period_id,
        BookkeepingUpload.stage == UPLOAD_STAGE_EXPENSE,
    )
    if upload_ids:
        query = query.filter(BookkeepingUpload.bookkeeping_upload_id.in_(upload_ids))

    uploads = query.order_by(BookkeepingUpload.bookkeeping_upload_id.asc()).all()
    property_alias_map = build_property_alias_map(period.portfolio, period.uploads)
    correction_rules = _build_portfolio_rule_set(period.portfolio)
    recreated_items = []

    for upload in uploads:
        linked_items = session.query(BookkeepingExpenseItem).filter(
            BookkeepingExpenseItem.upload_id == upload.bookkeeping_upload_id
        ).all()
        existing_auto_items = [
            item for item in linked_items
            if (item.extraction_data or {}).get("source") == "auto_expense_evidence"
        ]
        existing_by_key = {
            _existing_expense_item_key(item): item
            for item in existing_auto_items
        }
        manual_overrides = {
            item.bookkeeping_expense_item_id: _manual_override_fields(session, "expense_item", item.bookkeeping_expense_item_id)
            for item in existing_auto_items
        }

        file_path = get_upload_absolute_path(upload.stored_path)
        if not file_path.exists():
            continue

        file_bytes = file_path.read_bytes()
        parsed_summary = parse_supporting_file(file_bytes, upload.original_filename)
        structured_extraction = extract_expense_evidence_bundle(
            file_bytes=file_bytes,
            filename=upload.original_filename,
            mime_type=upload.content_type,
            parsed_summary=parsed_summary,
            property_alias_map=property_alias_map,
        )
        auto_extraction = auto_extract_expense_evidence_from_structured(structured_extraction)

        upload.summary = {
            **(upload.summary or {}),
            **parsed_summary,
            "auto_extraction": auto_extraction,
            "structured_extraction": structured_extraction,
        }

        payloads = []
        for payload in build_expense_item_payloads_from_extraction(structured_extraction):
            payloads.append(
                _apply_correction_rules(
                    payload,
                    "expense_item",
                    correction_rules,
                    _build_expense_rule_context(payload, upload),
                )
            )

        for payload in payloads:
            matching_item = existing_by_key.pop(_expense_payload_key(payload), None)
            if matching_item is None:
                item = _create_auto_expense_item(
                    period_id=period.bookkeeping_period_id,
                    upload_id=upload.bookkeeping_upload_id,
                    payload=payload,
                    created_by=current_user.user_id,
                )
                session.add(item)
                recreated_items.append(item)
                continue

            override_fields = manual_overrides.get(matching_item.bookkeeping_expense_item_id) or set()
            proposed_values = {}
            current_snapshot = _expense_item_snapshot(matching_item)
            for field_name in MATERIAL_EXPENSE_FIELDS:
                if field_name not in payload:
                    continue
                incoming_value = _json_safe(payload.get(field_name))
                current_value = current_snapshot.get(field_name)
                if incoming_value != current_value:
                    proposed_values[field_name] = incoming_value

            if override_fields and proposed_values:
                _queue_change_proposal(
                    session,
                    period.bookkeeping_period_id,
                    "expense_item",
                    matching_item.bookkeeping_expense_item_id,
                    upload.bookkeeping_upload_id,
                    {field_name: current_snapshot.get(field_name) for field_name in proposed_values},
                    proposed_values,
                    "Reprocessing found new evidence for a human-edited expense row.",
                )
                matching_item.needs_review = True
                matching_item.review_reason = "Updated extraction conflicts with a human-edited row. Review the AI proposal."
                recreated_items.append(matching_item)
                continue

            _expense_item_from_payload(
                matching_item,
                {
                    **payload,
                    "upload_id": upload.bookkeeping_upload_id,
                },
                current_user.user_id,
            )
            recreated_items.append(matching_item)

        for remaining_item in existing_by_key.values():
            if manual_overrides.get(remaining_item.bookkeeping_expense_item_id):
                remaining_item.needs_review = True
                remaining_item.review_reason = "Updated extraction no longer produced this row. Review before removing it."
                continue
            session.delete(remaining_item)

    session.flush()
    _apply_payment_memo_reconciliation(session, period, current_user.user_id, recreated_items)
    period_items = (
        session.query(BookkeepingExpenseItem)
        .filter(BookkeepingExpenseItem.period_id == period.bookkeeping_period_id)
        .all()
    )
    reconcile_reimbursement_receipts(period.uploads, period_items)
    return uploads, recreated_items


def _process_supporting_upload_batch(batch_id: int) -> None:
    session = get_session()
    try:
        batch = (
            session.query(BookkeepingProcessingBatch)
            .options(
                selectinload(BookkeepingProcessingBatch.uploads),
                selectinload(BookkeepingProcessingBatch.period).selectinload(BookkeepingPeriod.portfolio).selectinload(BookkeepingPortfolio.listing_mappings),
                selectinload(BookkeepingProcessingBatch.period).selectinload(BookkeepingPeriod.portfolio).selectinload(BookkeepingPortfolio.correction_rules),
                selectinload(BookkeepingProcessingBatch.period).selectinload(BookkeepingPeriod.uploads),
            )
            .filter(BookkeepingProcessingBatch.bookkeeping_processing_batch_id == batch_id)
            .first()
        )
        if not batch or batch.status not in ACTIVE_PROCESSING_BATCH_STATUSES:
            return

        period = batch.period
        correction_rules = _build_portfolio_rule_set(period.portfolio)
        property_alias_map = build_property_alias_map(period.portfolio, period.uploads) if batch.stage == UPLOAD_STAGE_EXPENSE else {}
        uploads = sorted(batch.uploads or [], key=lambda upload: upload.bookkeeping_upload_id)

        batch.status = "processing"
        batch.started_at = batch.started_at or datetime.utcnow()
        batch.error_message = None
        for upload in uploads:
            upload.upload_status = "processing"
            upload.processing_started_at = upload.processing_started_at or datetime.utcnow()
            upload.processing_error = None
        session.commit()

        auto_created_upload_ids = set()

        with ThreadPoolExecutor(
            max_workers=max(1, min(EXPENSE_EXTRACTION_MAX_WORKERS, len(uploads) or 1)),
            thread_name_prefix="bk-upload-batch",
        ) as executor:
            future_map = {
                executor.submit(
                    _analyze_supporting_upload,
                    stage=batch.stage,
                    stored_path=upload.stored_path,
                    filename=upload.original_filename,
                    mime_type=upload.content_type,
                    property_alias_map=property_alias_map if batch.stage == UPLOAD_STAGE_EXPENSE else None,
                ): upload.bookkeeping_upload_id
                for upload in uploads
            }

            for future in as_completed(future_map):
                upload_id = future_map[future]
                upload = session.query(BookkeepingUpload).filter(
                    BookkeepingUpload.bookkeeping_upload_id == upload_id
                ).first()
                batch = session.query(BookkeepingProcessingBatch).filter(
                    BookkeepingProcessingBatch.bookkeeping_processing_batch_id == batch_id
                ).first()
                if not upload or not batch:
                    continue

                batch.current_upload_id = upload.bookkeeping_upload_id
                batch.current_filename = upload.original_filename

                try:
                    analysis = future.result()
                    parsed_summary = analysis.get("parsed_summary") or {"preview_text": None, "page_count": None}
                    auto_extraction = analysis.get("auto_extraction")
                    structured_extraction = analysis.get("structured_extraction")
                    upload.summary = {
                        **parsed_summary,
                        "auto_extraction": auto_extraction,
                        "structured_extraction": structured_extraction,
                    }
                    upload.upload_status = "processed"
                    upload.processing_error = None
                    upload.processing_completed_at = datetime.utcnow()

                    if batch.stage == UPLOAD_STAGE_EXPENSE and structured_extraction:
                        for payload in build_expense_item_payloads_from_extraction(structured_extraction):
                            payload = _apply_correction_rules(
                                payload,
                                "expense_item",
                                correction_rules,
                                _build_expense_rule_context(payload, upload),
                            )
                            item = _create_auto_expense_item(
                                period_id=period.bookkeeping_period_id,
                                upload_id=upload.bookkeeping_upload_id,
                                payload=payload,
                                created_by=batch.created_by,
                            )
                            session.add(item)
                            auto_created_upload_ids.add(upload.bookkeeping_upload_id)

                    batch.successful_uploads = int(batch.successful_uploads or 0) + 1
                except Exception as exc:
                    logger.error(
                        "Error processing bookkeeping upload %s in batch %s: %s",
                        upload.bookkeeping_upload_id,
                        batch_id,
                        exc,
                        exc_info=True,
                    )
                    upload.summary = {
                        **(upload.summary or {}),
                        "status": "failed",
                    }
                    upload.upload_status = "failed"
                    upload.processing_error = str(exc)
                    upload.processing_completed_at = datetime.utcnow()
                    batch.failed_uploads = int(batch.failed_uploads or 0) + 1

                batch.processed_uploads = int(batch.successful_uploads or 0) + int(batch.failed_uploads or 0)
                session.commit()

        period = (
            _period_query(session, include_workspace_related=True)
            .filter(BookkeepingPeriod.bookkeeping_period_id == period.bookkeeping_period_id)
            .first()
        )
        batch = session.query(BookkeepingProcessingBatch).filter(
            BookkeepingProcessingBatch.bookkeeping_processing_batch_id == batch_id
        ).first()
        if not period or not batch:
            return

        if batch.stage == UPLOAD_STAGE_EXPENSE and auto_created_upload_ids:
            created_items = session.query(BookkeepingExpenseItem).filter(
                BookkeepingExpenseItem.upload_id.in_(tuple(auto_created_upload_ids))
            ).all()
            _apply_payment_memo_reconciliation(session, period, batch.created_by, created_items)
            period_items = (
                session.query(BookkeepingExpenseItem)
                .filter(BookkeepingExpenseItem.period_id == period.bookkeeping_period_id)
                .all()
            )
            reconcile_reimbursement_receipts(period.uploads, period_items)

        _mark_period_dirty(period)
        batch.status = "completed_with_errors" if int(batch.failed_uploads or 0) else "completed"
        batch.completed_at = datetime.utcnow()
        batch.current_upload_id = None
        batch.current_filename = None
        batch.error_message = None
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.error("Error processing bookkeeping upload batch %s: %s", batch_id, exc, exc_info=True)
        try:
            batch = session.query(BookkeepingProcessingBatch).filter(
                BookkeepingProcessingBatch.bookkeeping_processing_batch_id == batch_id
            ).first()
            if batch:
                batch.status = "failed"
                batch.error_message = str(exc)
                batch.completed_at = datetime.utcnow()
                batch.current_upload_id = None
                batch.current_filename = None
                pending_uploads = session.query(BookkeepingUpload).filter(
                    BookkeepingUpload.processing_batch_id == batch_id,
                    BookkeepingUpload.upload_status.in_(("queued", "processing")),
                ).all()
                for upload in pending_uploads:
                    upload.upload_status = "failed"
                    upload.processing_error = str(exc)
                    upload.processing_completed_at = datetime.utcnow()
                batch.failed_uploads = int(batch.failed_uploads or 0) + len(pending_uploads)
                batch.processed_uploads = int(batch.successful_uploads or 0) + int(batch.failed_uploads or 0)
                session.commit()
        except Exception:
            session.rollback()
            logger.error("Failed to mark bookkeeping upload batch %s as failed", batch_id, exc_info=True)
    finally:
        session.close()


def _start_supporting_upload_batch(batch_id: int) -> None:
    worker = threading.Thread(
        target=_process_supporting_upload_batch,
        args=(batch_id,),
        name=f"bookkeeping-upload-batch-{batch_id}",
        daemon=True,
    )
    worker.start()


@bookkeeping_bp.route("/")
@admin_required
def bookkeeping_page():
    return render_template("bookkeeping/index.html", current_user=get_current_user())


@bookkeeping_bp.route("/api/reference-data")
@admin_required
def bookkeeping_reference_data():
    return jsonify(
        {
            "revenue_sources": [{"value": source, "label": source_label(source)} for source in REVENUE_SOURCES],
            "default_revenue_channels": list(DEFAULT_REVENUE_CHANNELS),
            "special_upload_sources": [{"value": source, "label": source_label(source)} for source in SPECIAL_UPLOAD_SOURCES],
            "expense_categories": [{"value": category, "label": category.replace("_", " ").title()} for category in EXPENSE_CATEGORIES],
            "editable_row_types": EDITABLE_ROW_TYPES,
        }
    )


@bookkeeping_bp.route("/api/listings/catalog", methods=["GET"])
@admin_required
def api_list_cotton_candy_listings():
    main_session = _cotton_candy_session()
    try:
        listings = (
            main_session.query(Listing)
            .order_by(Listing.internal_listing_name.asc().nullslast(), Listing.name.asc())
            .all()
        )
        return jsonify({"listings": [_serialize_listing_record(listing) for listing in listings]})
    finally:
        main_session.close()


@bookkeeping_bp.route("/api/listing-tags", methods=["GET"])
@admin_required
def api_list_cotton_candy_listing_tags():
    main_session = _cotton_candy_session()
    try:
        tags = main_session.query(Tag).order_by(Tag.name.asc()).all()
        return jsonify(
            {
                "tags": [
                    {
                        "tag_id": tag.tag_id,
                        "name": tag.name,
                        "color": tag.color,
                        "usage_count": len(getattr(tag, "listing_tags", []) or []),
                    }
                    for tag in tags
                ]
            }
        )
    finally:
        main_session.close()


@bookkeeping_bp.route("/api/portfolios", methods=["GET"])
@admin_required
def api_list_portfolios():
    session = get_session()
    try:
        portfolios = _portfolio_query(session).order_by(BookkeepingPortfolio.updated_at.desc()).all()
        return jsonify(
            {
                "portfolios": [
                    {
                        **portfolio.to_dict(),
                        "period_count": len(portfolio.periods),
                        "listing_mapping_count": len(portfolio.listing_mappings),
                        "latest_period_id": portfolio.periods[0].bookkeeping_period_id if portfolio.periods else None,
                    }
                    for portfolio in portfolios
                ]
            }
        )
    finally:
        session.close()


@bookkeeping_bp.route("/api/portfolios", methods=["POST"])
@admin_required
def api_create_portfolio():
    session = get_session()
    main_session = _cotton_candy_session()
    current_user = get_current_user()
    payload = request.get_json() or {}

    try:
        name = (payload.get("name") or "").strip()
        code = (payload.get("code") or name).strip()
        property_name = (payload.get("property_name") or name).strip()
        try:
            listing_tag = _normalize_listing_tag(payload.get("listing_tag"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        if not name or not listing_tag:
            return jsonify({"error": "Portfolio name and portfolio tag are required"}), 400

        existing = session.query(BookkeepingPortfolio).filter(BookkeepingPortfolio.code == code).first()
        if existing:
            return jsonify({"error": "A portfolio with this name already exists"}), 409
        tagged_listings = _listings_for_tag(main_session, listing_tag)
        if not tagged_listings:
            return jsonify({"error": f"No Cotton Candy listings were found for portfolio tag '{listing_tag}'"}), 400

        portfolio = BookkeepingPortfolio(
            name=name,
            code=code,
            listing_tag=listing_tag,
            property_name=property_name or name,
            property_address=(payload.get("property_address") or "").strip() or None,
            notes=(payload.get("notes") or "").strip() or None,
            default_currency=(payload.get("default_currency") or "USD").strip() or "USD",
            owner_share_percentage=decimal_or_none("100"),
            management_fee_percentage=decimal_or_none(payload.get("management_fee_percentage")) or decimal_or_none("20"),
            listing_count=0,
            revenue_channels=normalize_revenue_channels(payload.get("revenue_channels")),
            hostaway_price_per_listing=decimal_or_none(payload.get("hostaway_price_per_listing")),
            pricelabs_price_per_listing=decimal_or_none(payload.get("pricelabs_price_per_listing")),
            created_by=current_user.user_id,
        )
        session.add(portfolio)
        session.flush()
        _seed_portfolio_listing_mappings(session, portfolio, tagged_listings, current_user.user_id)
        session.commit()
        session.refresh(portfolio)
        return jsonify({"portfolio": portfolio.to_dict()}), 201
    except Exception as exc:
        session.rollback()
        logger.error("Error creating bookkeeping portfolio: %s", exc, exc_info=True)
        return jsonify({"error": "Failed to create portfolio"}), 500
    finally:
        main_session.close()
        session.close()


@bookkeeping_bp.route("/api/portfolios/<int:portfolio_id>", methods=["PUT"])
@admin_required
def api_update_portfolio(portfolio_id):
    session = get_session()
    main_session = _cotton_candy_session()
    payload = request.get_json() or {}

    try:
        portfolio = session.query(BookkeepingPortfolio).filter(
            BookkeepingPortfolio.bookkeeping_portfolio_id == portfolio_id
        ).first()
        if not portfolio:
            return jsonify({"error": "Portfolio not found"}), 404

        updated_name = (payload.get("name") or "").strip() or portfolio.name
        updated_code = (payload.get("code") or updated_name or portfolio.code).strip()
        existing = session.query(BookkeepingPortfolio).filter(
            BookkeepingPortfolio.code == updated_code,
            BookkeepingPortfolio.bookkeeping_portfolio_id != portfolio_id,
        ).first()
        if existing:
            return jsonify({"error": "A portfolio with this name already exists"}), 409

        previous_tag = portfolio.listing_tag
        portfolio.name = updated_name
        portfolio.code = updated_code
        incoming_tag = payload.get("listing_tag")
        requested_tag = previous_tag
        if incoming_tag is not None:
            requested_tag = _normalize_listing_tag(incoming_tag)
            portfolio.listing_tag = requested_tag
        portfolio.property_name = (payload.get("property_name") or updated_name).strip() or portfolio.property_name or portfolio.name
        portfolio.property_address = (payload.get("property_address") or "").strip() or None
        portfolio.notes = (payload.get("notes") or "").strip() or None
        portfolio.management_fee_percentage = decimal_or_none(payload.get("management_fee_percentage")) or decimal_or_none("20")
        portfolio.hostaway_price_per_listing = decimal_or_none(payload.get("hostaway_price_per_listing"))
        portfolio.pricelabs_price_per_listing = decimal_or_none(payload.get("pricelabs_price_per_listing"))
        if "revenue_channels" in payload:
            portfolio.revenue_channels = normalize_revenue_channels(payload.get("revenue_channels"))

        if requested_tag and requested_tag != previous_tag:
            tagged_listings = _listings_for_tag(main_session, requested_tag)
            if not tagged_listings:
                return jsonify({"error": f"No Cotton Candy listings were found for portfolio tag '{requested_tag}'"}), 400
            _sync_portfolio_listing_mappings_to_listings(session, portfolio, tagged_listings, get_current_user().user_id)
        elif not portfolio.listing_mappings and portfolio.listing_tag:
            tagged_listings = _listings_for_tag(main_session, portfolio.listing_tag)
            if not tagged_listings:
                return jsonify({"error": f"No Cotton Candy listings were found for portfolio tag '{portfolio.listing_tag}'"}), 400
            _seed_portfolio_listing_mappings(session, portfolio, tagged_listings, get_current_user().user_id)
        else:
            portfolio.listing_count = len([mapping for mapping in portfolio.listing_mappings if mapping.is_active])
        session.commit()
        session.refresh(portfolio)
        return jsonify({"portfolio": portfolio.to_dict()})
    except Exception as exc:
        session.rollback()
        logger.error("Error updating bookkeeping portfolio: %s", exc, exc_info=True)
        return jsonify({"error": "Failed to update portfolio"}), 500
    finally:
        main_session.close()
        session.close()


@bookkeeping_bp.route("/api/portfolios/<int:portfolio_id>", methods=["DELETE"])
@admin_required
def api_delete_portfolio(portfolio_id):
    session = get_session()
    try:
        portfolio = _portfolio_query(session).filter(
            BookkeepingPortfolio.bookkeeping_portfolio_id == portfolio_id
        ).first()
        if not portfolio:
            return jsonify({"error": "Portfolio not found"}), 404

        stored_paths = [
            stored_path
            for (stored_path,) in session.query(BookkeepingUpload.stored_path)
            .join(BookkeepingPeriod, BookkeepingPeriod.bookkeeping_period_id == BookkeepingUpload.period_id)
            .filter(BookkeepingPeriod.portfolio_id == portfolio_id)
            .all()
            if stored_path
        ]
        stored_paths = list(dict.fromkeys(stored_paths))
        portfolio_name = portfolio.name

        session.delete(portfolio)
        session.commit()

        for stored_path in stored_paths:
            remaining_references = session.query(BookkeepingUpload).filter(
                BookkeepingUpload.stored_path == stored_path
            ).count()
            if remaining_references:
                continue
            file_path = get_upload_absolute_path(stored_path)
            if file_path.exists():
                file_path.unlink()

        return jsonify(
            {
                "deleted_portfolio_id": portfolio_id,
                "deleted_portfolio_name": portfolio_name,
                "deleted_upload_file_count": len(stored_paths),
            }
        )
    except Exception as exc:
        session.rollback()
        logger.error("Error deleting bookkeeping portfolio: %s", exc, exc_info=True)
        return jsonify({"error": "Failed to delete portfolio"}), 500
    finally:
        session.close()


@bookkeeping_bp.route("/api/portfolios/<int:portfolio_id>/revenue-channels", methods=["PUT"])
@admin_required
def api_update_portfolio_revenue_channels(portfolio_id):
    session = get_session()
    payload = request.get_json() or {}
    try:
        portfolio = session.query(BookkeepingPortfolio).filter(
            BookkeepingPortfolio.bookkeeping_portfolio_id == portfolio_id
        ).first()
        if not portfolio:
            return jsonify({"error": "Portfolio not found"}), 404

        revenue_channels = normalize_revenue_channels(payload.get("channels") or payload.get("revenue_channels"))
        if not revenue_channels:
            return jsonify({"error": "At least one revenue channel is required"}), 400

        portfolio.revenue_channels = revenue_channels
        session.commit()
        session.refresh(portfolio)
        return jsonify({"portfolio": portfolio.to_dict(), "revenue_channels": revenue_channels})
    except Exception as exc:
        session.rollback()
        logger.error("Error updating revenue channels: %s", exc, exc_info=True)
        return jsonify({"error": "Failed to update revenue channels"}), 500
    finally:
        session.close()


@bookkeeping_bp.route("/api/portfolios/<int:portfolio_id>/listing-mappings", methods=["GET"])
@admin_required
def api_get_listing_mappings(portfolio_id):
    session = get_session()
    main_session = _cotton_candy_session()
    try:
        portfolio = _portfolio_query(session).filter(
            BookkeepingPortfolio.bookkeeping_portfolio_id == portfolio_id
        ).first()
        if not portfolio:
            return jsonify({"error": "Portfolio not found"}), 404

        listings = (
            _candidate_listings_for_portfolio(main_session, portfolio)
        )
        return jsonify(
            {
                "portfolio": portfolio.to_dict(),
                "listings": [_serialize_listing_record(listing) for listing in listings],
                "listing_mappings": [mapping.to_dict() for mapping in portfolio.listing_mappings],
            }
        )
    finally:
        main_session.close()
        session.close()


@bookkeeping_bp.route("/api/portfolios/<int:portfolio_id>/listing-mappings", methods=["PUT"])
@admin_required
def api_save_listing_mappings(portfolio_id):
    session = get_session()
    current_user = get_current_user()
    payload = request.get_json() or {}
    mappings_payload = payload.get("mappings") or []
    main_session = _cotton_candy_session()

    try:
        portfolio = _portfolio_query(session).filter(
            BookkeepingPortfolio.bookkeeping_portfolio_id == portfolio_id
        ).first()
        if not portfolio:
            return jsonify({"error": "Portfolio not found"}), 404

        requested_listing_ids = []
        for row in mappings_payload:
            try:
                requested_listing_ids.append(int(row.get("listing_id")))
            except (TypeError, ValueError):
                continue
        requested_listing_ids = list(dict.fromkeys(requested_listing_ids))
        listing_records = {}
        if requested_listing_ids:
            listing_records = {
                listing.listing_id: listing
                for listing in main_session.query(Listing).filter(Listing.listing_id.in_(requested_listing_ids)).all()
            }

        existing_by_listing_id = {
            mapping.listing_id: mapping
            for mapping in portfolio.listing_mappings
        }
        seen_listing_ids = set()
        saved_mappings = []

        for row in mappings_payload:
            try:
                listing_id = int(row.get("listing_id"))
            except (TypeError, ValueError):
                continue
            listing = listing_records.get(listing_id)
            if not listing:
                continue
            seen_listing_ids.add(listing_id)
            mapping = existing_by_listing_id.get(listing_id)
            if mapping is None:
                mapping = BookkeepingListingMapping(
                    portfolio_id=portfolio_id,
                    listing_id=listing_id,
                    created_by=current_user.user_id,
                )
                session.add(mapping)

            aliases = row.get("aliases") or []
            if isinstance(aliases, str):
                aliases = [alias.strip() for alias in aliases.split(",") if alias.strip()]

            mapping.listing_name = listing.name
            mapping.internal_listing_name = listing.internal_listing_name
            mapping.official_name = (row.get("official_name") or listing.internal_listing_name or listing.name or f"Listing {listing_id}").strip()
            mapping.aliases = aliases
            mapping.is_active = _parse_boolean(row.get("is_active", True))
            mapping.notes = (row.get("notes") or "").strip() or None
            saved_mappings.append(mapping)

        for listing_id, mapping in existing_by_listing_id.items():
            if listing_id not in seen_listing_ids:
                session.delete(mapping)

        session.flush()
        portfolio.listing_count = len([mapping for mapping in saved_mappings if mapping.is_active])
        if not portfolio.property_name and saved_mappings:
            portfolio.property_name = saved_mappings[0].official_name
        session.commit()
        session.refresh(portfolio)
        return jsonify(
            {
                "portfolio": portfolio.to_dict(),
                "listing_mappings": [mapping.to_dict() for mapping in portfolio.listing_mappings],
            }
        )
    except Exception as exc:
        session.rollback()
        logger.error("Error saving listing mappings: %s", exc, exc_info=True)
        return jsonify({"error": "Failed to save listing mappings"}), 500
    finally:
        main_session.close()
        session.close()


@bookkeeping_bp.route("/api/portfolios/<int:portfolio_id>/periods", methods=["GET"])
@admin_required
def api_list_periods(portfolio_id):
    session = get_session()
    try:
        periods = (
            session.query(BookkeepingPeriod)
            .filter(BookkeepingPeriod.portfolio_id == portfolio_id)
            .order_by(BookkeepingPeriod.period_start.desc())
            .all()
        )
        return jsonify({"periods": [period.to_dict() for period in periods]})
    finally:
        session.close()


@bookkeeping_bp.route("/api/periods", methods=["POST"])
@admin_required
def api_create_period():
    session = get_session()
    current_user = get_current_user()
    payload = request.get_json() or {}

    try:
        portfolio_id = payload.get("portfolio_id")
        period_start = parse_date_or_none(payload.get("period_start"))
        if not portfolio_id or not period_start:
            return jsonify({"error": "Portfolio and period start date are required"}), 400

        portfolio = session.query(BookkeepingPortfolio).filter(
            BookkeepingPortfolio.bookkeeping_portfolio_id == portfolio_id
        ).first()
        if not portfolio:
            return jsonify({"error": "Portfolio not found"}), 404

        existing = session.query(BookkeepingPeriod).filter(
            BookkeepingPeriod.portfolio_id == portfolio_id,
            BookkeepingPeriod.period_start == period_start,
        ).first()
        if existing:
            return jsonify({"period": existing.to_dict(), "message": "Existing period loaded"}), 200

        period = BookkeepingPeriod(
            portfolio_id=portfolio_id,
            name=(payload.get("name") or "").strip() or logical_month_label(period_start),
            period_start=period_start,
            period_end=parse_date_or_none(payload.get("period_end")) or _last_day_of_month(period_start),
            status=(payload.get("status") or "draft").strip() or "draft",
            notes=(payload.get("notes") or "").strip() or None,
            created_by=current_user.user_id,
        )
        session.add(period)
        session.commit()
        session.refresh(period)
        return jsonify({"period": period.to_dict()}), 201
    except Exception as exc:
        session.rollback()
        logger.error("Error creating bookkeeping period: %s", exc, exc_info=True)
        return jsonify({"error": "Failed to create bookkeeping period"}), 500
    finally:
        session.close()


@bookkeeping_bp.route("/api/periods/<int:period_id>/workspace", methods=["GET"])
@admin_required
def api_period_workspace(period_id):
    session = get_session()
    try:
        period, error_response = _get_period_or_404(session, period_id, include_workspace_related=True)
        if error_response:
            return error_response

        summary = _workspace_payload(period)
        summary["messages"] = [message.to_dict() for message in period.conversation_messages]
        return jsonify(summary)
    finally:
        session.close()


@bookkeeping_bp.route("/api/processing-batches/<int:batch_id>", methods=["GET"])
@admin_required
def api_processing_batch(batch_id):
    session = get_session()
    try:
        batch = session.query(BookkeepingProcessingBatch).filter(
            BookkeepingProcessingBatch.bookkeeping_processing_batch_id == batch_id
        ).first()
        if not batch:
            return jsonify({"error": "Processing batch not found"}), 404

        uploads = session.query(BookkeepingUpload).filter(
            BookkeepingUpload.processing_batch_id == batch_id
        ).order_by(BookkeepingUpload.bookkeeping_upload_id.asc()).all()
        return jsonify({"processing_batch": _processing_batch_payload(batch, uploads)})
    finally:
        session.close()


@bookkeeping_bp.route("/api/periods/<int:period_id>/uploads", methods=["POST"])
@admin_required
def api_upload_files(period_id):
    session = get_session()
    current_user = get_current_user()

    try:
        period, error_response = _get_period_or_404(session, period_id)
        if error_response:
            return error_response

        stage = (request.form.get("stage") or "").strip()
        source = (request.form.get("source") or "auto").strip()
        notes = (request.form.get("notes") or "").strip() or None
        files = request.files.getlist("files")
        relative_paths = request.form.getlist("relative_paths")

        if stage not in {UPLOAD_STAGE_REVENUE, UPLOAD_STAGE_EXPENSE, UPLOAD_STAGE_CORROBORATION}:
            return jsonify({"error": "Invalid upload stage"}), 400
        if not files:
            return jsonify({"error": "No files uploaded"}), 400

        if stage != UPLOAD_STAGE_REVENUE:
            existing_batch = _active_processing_batch(session, period.bookkeeping_period_id, stage)
            if existing_batch:
                existing_uploads = session.query(BookkeepingUpload).filter(
                    BookkeepingUpload.processing_batch_id == existing_batch.bookkeeping_processing_batch_id
                ).order_by(BookkeepingUpload.bookkeeping_upload_id.asc()).all()
                return jsonify(
                    {
                        "error": "A processing batch is already running for this step. Wait for it to finish before uploading more files.",
                        "processing_batch": _processing_batch_payload(existing_batch, existing_uploads),
                    }
                ), 409

        created_uploads = []
        created_revenue_items = []
        auto_created_expense_items = []
        prepared_files = _prepared_file_payloads(files, relative_paths, period_id, stage)
        if not prepared_files:
            return jsonify({"error": "No readable files were uploaded"}), 400

        if stage != UPLOAD_STAGE_REVENUE:
            batch, created_uploads = _queue_supporting_upload_batch(
                session,
                period,
                stage,
                source,
                notes,
                prepared_files,
                current_user.user_id,
            )
            _mark_period_dirty(period)
            session.commit()
            batch_payload = _processing_batch_payload(batch, created_uploads)
            _start_supporting_upload_batch(batch.bookkeeping_processing_batch_id)
            return jsonify(
                {
                    "processing_batch": batch_payload,
                    "uploads": [upload.to_dict() for upload in created_uploads],
                    "message": "Files uploaded. Background bookkeeping processing has started.",
                }
            ), 202

        for file_info in prepared_files:
            relative_path = file_info["relative_path"]
            if stage == UPLOAD_STAGE_REVENUE:
                file_bytes = get_upload_absolute_path(file_info["stored_path"]).read_bytes()
                logical_uploads = parse_revenue_file(file_bytes, file_info["filename"], source)
                for logical_upload in logical_uploads:
                    upload = BookkeepingUpload(
                        period_id=period.bookkeeping_period_id,
                        stage=stage,
                        source=logical_upload["source"],
                        detected_source=logical_upload.get("detected_source"),
                        sheet_name=logical_upload.get("sheet_name"),
                        upload_status="processed",
                        original_filename=file_info["filename"],
                        original_relative_path=relative_path,
                        stored_path=file_info["stored_path"],
                        content_type=_normalized_upload_content_type(file_info["filename"], file_info["mime_type"]),
                        file_extension=os.path.splitext(file_info["filename"])[1].lower(),
                        file_size=file_info["file_size"],
                        row_count=logical_upload.get("row_count"),
                        headers=logical_upload.get("headers"),
                        preview_rows=logical_upload.get("preview_rows"),
                        parsed_rows=logical_upload.get("rows"),
                        summary=logical_upload.get("summary"),
                        notes=notes,
                        uploaded_by=current_user.user_id,
                    )
                    session.add(upload)
                    created_uploads.append(upload)

        session.flush()
        if stage == UPLOAD_STAGE_REVENUE:
            created_revenue_items = _sync_revenue_items_for_uploads(session, period, created_uploads, current_user.user_id)

        _mark_period_dirty(period)
        session.commit()
        return jsonify(
            {
                "uploads": [upload.to_dict() for upload in created_uploads],
                "revenue_items": [item.to_dict() for item in created_revenue_items],
                "auto_created_expense_items": [item.to_dict() for item in auto_created_expense_items],
                "auto_categorize_threshold": AUTO_CATEGORIZE_CONFIDENCE_THRESHOLD,
            }
        ), 201
    except ValueError as exc:
        session.rollback()
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        session.rollback()
        logger.error("Error uploading bookkeeping files: %s", exc, exc_info=True)
        return jsonify({"error": "Failed to upload files"}), 500
    finally:
        session.close()


@bookkeeping_bp.route("/api/periods/<int:period_id>/reprocess-expenses", methods=["POST"])
@admin_required
def api_reprocess_expense_uploads(period_id):
    session = get_session()
    current_user = get_current_user()
    try:
        period, error_response = _get_period_or_404(session, period_id)
        if error_response:
            return error_response
        if _active_processing_batch(session, period.bookkeeping_period_id, UPLOAD_STAGE_EXPENSE):
            return jsonify({"error": "Expense evidence is already being processed for this month."}), 409

        uploads, recreated_items = _reprocess_expense_uploads(session, period, current_user)
        _mark_period_dirty(period)
        session.commit()
        return jsonify(
            {
                "reprocessed_upload_count": len(uploads),
                "auto_created_expense_items": [item.to_dict() for item in recreated_items],
            }
        )
    except Exception as exc:
        session.rollback()
        logger.error("Error reprocessing expense uploads: %s", exc, exc_info=True)
        return jsonify({"error": "Failed to reprocess expense evidence"}), 500
    finally:
        session.close()


@bookkeeping_bp.route("/api/uploads/<int:upload_id>/file", methods=["GET"])
@admin_required
def api_download_upload(upload_id):
    session = get_session()
    try:
        upload = session.query(BookkeepingUpload).filter(BookkeepingUpload.bookkeeping_upload_id == upload_id).first()
        if not upload:
            return jsonify({"error": "Upload not found"}), 404

        file_path = get_upload_absolute_path(upload.stored_path)
        if not file_path.exists():
            return jsonify({"error": "Stored file not found"}), 404

        return send_file(
            file_path,
            as_attachment=_parse_boolean(request.args.get("download")),
            download_name=upload.original_filename,
            mimetype=_normalized_upload_content_type(upload.original_filename, upload.content_type) or "application/octet-stream",
        )
    finally:
        session.close()


@bookkeeping_bp.route("/api/uploads/<int:upload_id>", methods=["DELETE"])
@admin_required
def api_delete_upload(upload_id):
    session = get_session()
    try:
        upload = session.query(BookkeepingUpload).filter(BookkeepingUpload.bookkeeping_upload_id == upload_id).first()
        if not upload:
            return jsonify({"error": "Upload not found"}), 404
        if _upload_is_processing(upload):
            return jsonify({"error": "This upload is still being processed. Wait for the batch to finish before removing it."}), 409

        period = upload.period
        deletion_result = _prepare_upload_deletion(session, upload)
        if period:
            _mark_period_dirty(period)
        session.flush()
        other_references = session.query(BookkeepingUpload).filter(
            BookkeepingUpload.stored_path == deletion_result["stored_path"]
        ).count()

        session.commit()

        if other_references == 0:
            file_path = get_upload_absolute_path(deletion_result["stored_path"])
            if file_path.exists():
                file_path.unlink()

        return jsonify(
            {
                "deleted_upload_id": upload_id,
                "deleted_auto_expense_items": deletion_result["deleted_auto_expense_items"],
                "unlinked_manual_expense_items": deletion_result["unlinked_manual_expense_items"],
                "deleted_revenue_items": deletion_result["deleted_revenue_items"],
            }
        )
    except Exception as exc:
        session.rollback()
        logger.error("Error deleting upload: %s", exc, exc_info=True)
        return jsonify({"error": "Failed to delete upload"}), 500
    finally:
        session.close()


@bookkeeping_bp.route("/api/uploads/bulk-delete", methods=["POST"])
@admin_required
def api_bulk_delete_uploads():
    session = get_session()
    payload = request.get_json() or {}
    upload_ids = payload.get("upload_ids") or []

    try:
        normalized_ids = []
        for upload_id in upload_ids:
            try:
                normalized_ids.append(int(upload_id))
            except (TypeError, ValueError):
                continue

        normalized_ids = list(dict.fromkeys(normalized_ids))
        if not normalized_ids:
            return jsonify({"error": "No uploads selected"}), 400

        uploads = session.query(BookkeepingUpload).filter(
            BookkeepingUpload.bookkeeping_upload_id.in_(normalized_ids)
        ).all()
        uploads_by_id = {upload.bookkeeping_upload_id: upload for upload in uploads}

        missing_ids = [upload_id for upload_id in normalized_ids if upload_id not in uploads_by_id]
        if missing_ids:
            return jsonify({"error": f"Uploads not found: {missing_ids}"}), 404
        busy_uploads = [upload.original_filename for upload in uploads if _upload_is_processing(upload)]
        if busy_uploads:
            return jsonify(
                {
                    "error": "One or more selected uploads are still being processed. Wait for the batch to finish before removing them.",
                    "busy_uploads": busy_uploads,
                }
            ), 409

        deletion_results = []
        impacted_periods = set()
        for upload_id in normalized_ids:
            if uploads_by_id[upload_id].period_id:
                impacted_periods.add(uploads_by_id[upload_id].period_id)
            deletion_results.append(_prepare_upload_deletion(session, uploads_by_id[upload_id]))

        if impacted_periods:
            periods = session.query(BookkeepingPeriod).filter(
                BookkeepingPeriod.bookkeeping_period_id.in_(impacted_periods)
            ).all()
            for period in periods:
                _mark_period_dirty(period)

        session.flush()

        removable_paths = set()
        for result in deletion_results:
            remaining_references = session.query(BookkeepingUpload).filter(
                BookkeepingUpload.stored_path == result["stored_path"]
            ).count()
            if remaining_references == 0:
                removable_paths.add(result["stored_path"])

        session.commit()

        removed_files = 0
        for stored_path in removable_paths:
            file_path = get_upload_absolute_path(stored_path)
            if file_path.exists():
                file_path.unlink()
                removed_files += 1

        return jsonify(
            {
                "deleted_upload_ids": [result["upload_id"] for result in deletion_results],
                "deleted_upload_count": len(deletion_results),
                "deleted_auto_expense_items": sum(result["deleted_auto_expense_items"] for result in deletion_results),
                "unlinked_manual_expense_items": sum(result["unlinked_manual_expense_items"] for result in deletion_results),
                "deleted_revenue_items": sum(result["deleted_revenue_items"] for result in deletion_results),
                "removed_file_count": removed_files,
            }
        )
    except Exception as exc:
        session.rollback()
        logger.error("Error bulk deleting uploads: %s", exc, exc_info=True)
        return jsonify({"error": "Failed to bulk delete uploads"}), 500
    finally:
        session.close()


@bookkeeping_bp.route("/api/periods/<int:period_id>/expense-items", methods=["POST"])
@admin_required
def api_create_expense_item(period_id):
    session = get_session()
    current_user = get_current_user()
    payload = request.get_json() or {}

    try:
        period, error_response = _get_period_or_404(session, period_id)
        if error_response:
            return error_response

        category = payload.get("category")
        if category not in EXPENSE_CATEGORIES:
            return jsonify({"error": "Invalid expense category"}), 400

        upload_id = payload.get("upload_id")
        if upload_id:
            upload = session.query(BookkeepingUpload).filter(
                BookkeepingUpload.bookkeeping_upload_id == upload_id,
                BookkeepingUpload.period_id == period_id,
            ).first()
            if not upload:
                return jsonify({"error": "Linked upload not found in this period"}), 404

        item = BookkeepingExpenseItem(period_id=period_id, created_by=current_user.user_id)
        _expense_item_from_payload(item, payload, current_user.user_id)
        session.add(item)
        _mark_period_dirty(period)
        session.commit()
        session.refresh(item)
        return jsonify({"expense_item": item.to_dict()}), 201
    except Exception as exc:
        session.rollback()
        logger.error("Error creating expense item: %s", exc, exc_info=True)
        return jsonify({"error": "Failed to create expense item"}), 500
    finally:
        session.close()


@bookkeeping_bp.route("/api/expense-items/<int:item_id>", methods=["PUT"])
@admin_required
def api_update_expense_item(item_id):
    session = get_session()
    current_user = get_current_user()
    payload = request.get_json() or {}

    try:
        item = session.query(BookkeepingExpenseItem).filter(
            BookkeepingExpenseItem.bookkeeping_expense_item_id == item_id
        ).first()
        if not item:
            return jsonify({"error": "Expense item not found"}), 404

        if payload.get("category") and payload["category"] not in EXPENSE_CATEGORIES:
            return jsonify({"error": "Invalid expense category"}), 400
        if payload.get("updated_at") and item.updated_at and payload.get("updated_at") != item.updated_at.isoformat():
            return jsonify({"error": "This expense row has changed. Refresh the workspace and try again."}), 409

        before_data = _expense_item_snapshot(item)
        _expense_item_from_payload(item, payload, current_user.user_id)
        after_data = _expense_item_snapshot(item)
        changed_fields = {
            field_name
            for field_name in MATERIAL_EXPENSE_FIELDS
            if before_data.get(field_name) != after_data.get(field_name)
        }
        edit_note = (payload.get("edit_note") or "").strip()
        if changed_fields and not edit_note:
            return jsonify({"error": "A note is required for manual bookkeeping changes"}), 400

        period = _period_query(session).filter(BookkeepingPeriod.bookkeeping_period_id == item.period_id).first()
        if changed_fields:
            _apply_manual_edit(
                session,
                period,
                period.portfolio,
                "expense_item",
                item.bookkeeping_expense_item_id,
                before_data,
                after_data,
                changed_fields,
                edit_note,
                current_user.user_id,
                _build_expense_rule_context_from_item(item),
            )
        _mark_period_dirty(period)
        session.commit()
        session.refresh(item)
        return jsonify({"expense_item": item.to_dict()})
    except Exception as exc:
        session.rollback()
        logger.error("Error updating expense item: %s", exc, exc_info=True)
        return jsonify({"error": "Failed to update expense item"}), 500
    finally:
        session.close()


@bookkeeping_bp.route("/api/periods/<int:period_id>/revenue-items", methods=["POST"])
@admin_required
def api_create_revenue_item(period_id):
    session = get_session()
    current_user = get_current_user()
    payload = request.get_json() or {}

    try:
        period, error_response = _get_period_or_404(session, period_id)
        if error_response:
            return error_response

        source = (payload.get("source") or "").strip()
        if source not in REVENUE_SOURCES:
            return jsonify({"error": "Invalid revenue source"}), 400

        item = BookkeepingRevenueItem(period_id=period_id, created_by=current_user.user_id)
        _revenue_item_from_payload(item, payload, current_user.user_id)
        session.add(item)
        _mark_period_dirty(period)
        session.commit()
        session.refresh(item)
        return jsonify({"revenue_item": item.to_dict()}), 201
    except Exception as exc:
        session.rollback()
        logger.error("Error creating revenue item: %s", exc, exc_info=True)
        return jsonify({"error": "Failed to create revenue item"}), 500
    finally:
        session.close()


@bookkeeping_bp.route("/api/revenue-items/<int:item_id>", methods=["PUT"])
@admin_required
def api_update_revenue_item(item_id):
    session = get_session()
    current_user = get_current_user()
    payload = request.get_json() or {}

    try:
        item = session.query(BookkeepingRevenueItem).filter(
            BookkeepingRevenueItem.bookkeeping_revenue_item_id == item_id
        ).first()
        if not item:
            return jsonify({"error": "Revenue item not found"}), 404

        if payload.get("updated_at") and item.updated_at and payload.get("updated_at") != item.updated_at.isoformat():
            return jsonify({"error": "This revenue row has changed. Refresh the workspace and try again."}), 409

        before_data = _revenue_item_data(item)
        _revenue_item_from_payload(item, payload, current_user.user_id)
        after_data = _revenue_item_data(item)
        changed_fields = {
            field_name
            for field_name in MATERIAL_REVENUE_FIELDS
            if before_data.get(field_name) != after_data.get(field_name)
        }
        edit_note = (payload.get("edit_note") or "").strip()
        if changed_fields and not edit_note:
            return jsonify({"error": "A note is required for manual bookkeeping changes"}), 400

        period = _period_query(session).filter(BookkeepingPeriod.bookkeeping_period_id == item.period_id).first()
        if changed_fields:
            _apply_manual_edit(
                session,
                period,
                period.portfolio,
                "revenue_item",
                item.bookkeeping_revenue_item_id,
                before_data,
                after_data,
                changed_fields,
                edit_note,
                current_user.user_id,
                _build_revenue_rule_context_from_item(item),
            )
        _mark_period_dirty(period)
        session.commit()
        session.refresh(item)
        return jsonify({"revenue_item": item.to_dict()})
    except Exception as exc:
        session.rollback()
        logger.error("Error updating revenue item: %s", exc, exc_info=True)
        return jsonify({"error": "Failed to update revenue item"}), 500
    finally:
        session.close()


@bookkeeping_bp.route("/api/change-proposals/<int:proposal_id>/resolve", methods=["POST"])
@admin_required
def api_resolve_change_proposal(proposal_id):
    session = get_session()
    current_user = get_current_user()
    payload = request.get_json() or {}

    try:
        proposal = session.query(BookkeepingAIChangeProposal).filter(
            BookkeepingAIChangeProposal.bookkeeping_ai_change_proposal_id == proposal_id
        ).first()
        if not proposal:
            return jsonify({"error": "Change proposal not found"}), 404
        if proposal.status != "pending":
            return jsonify({"error": "Change proposal is already resolved"}), 409

        action = (payload.get("action") or "").strip().lower()
        if action not in {"accept", "reject"}:
            return jsonify({"error": "Action must be accept or reject"}), 400

        period = _period_query(session).filter(
            BookkeepingPeriod.bookkeeping_period_id == proposal.period_id
        ).first()
        if not period:
            return jsonify({"error": "Workspace not found"}), 404

        if action == "accept":
            if proposal.row_type == "expense_item":
                item = session.query(BookkeepingExpenseItem).filter(
                    BookkeepingExpenseItem.bookkeeping_expense_item_id == proposal.row_id
                ).first()
                if not item:
                    return jsonify({"error": "Expense row not found"}), 404
                before_data = _expense_item_snapshot(item)
                _expense_item_from_payload(item, proposal.proposed_values or {}, current_user.user_id)
                after_data = _expense_item_snapshot(item)
                changed_fields = {
                    field_name
                    for field_name in MATERIAL_EXPENSE_FIELDS
                    if before_data.get(field_name) != after_data.get(field_name)
                }
                if changed_fields:
                    _apply_manual_edit(
                        session,
                        period,
                        period.portfolio,
                        "expense_item",
                        item.bookkeeping_expense_item_id,
                        before_data,
                        after_data,
                        changed_fields,
                        (payload.get("note") or "Accepted AI change proposal").strip(),
                        current_user.user_id,
                        _build_expense_rule_context_from_item(item),
                    )
            elif proposal.row_type == "revenue_item":
                item = session.query(BookkeepingRevenueItem).filter(
                    BookkeepingRevenueItem.bookkeeping_revenue_item_id == proposal.row_id
                ).first()
                if not item:
                    return jsonify({"error": "Revenue row not found"}), 404
                before_data = _revenue_item_data(item)
                _revenue_item_from_payload(item, {**before_data, **(proposal.proposed_values or {})}, current_user.user_id)
                after_data = _revenue_item_data(item)
                changed_fields = {
                    field_name
                    for field_name in MATERIAL_REVENUE_FIELDS
                    if before_data.get(field_name) != after_data.get(field_name)
                }
                if changed_fields:
                    _apply_manual_edit(
                        session,
                        period,
                        period.portfolio,
                        "revenue_item",
                        item.bookkeeping_revenue_item_id,
                        before_data,
                        after_data,
                        changed_fields,
                        (payload.get("note") or "Accepted AI change proposal").strip(),
                        current_user.user_id,
                        _build_revenue_rule_context_from_item(item),
                    )
            else:
                return jsonify({"error": "Unsupported proposal row type"}), 400

        proposal.status = "accepted" if action == "accept" else "rejected"
        proposal.resolved_by = current_user.user_id
        proposal.resolved_at = datetime.utcnow()
        _mark_period_dirty(period)
        session.commit()
        return jsonify({"change_proposal": proposal.to_dict()})
    except Exception as exc:
        session.rollback()
        logger.error("Error resolving change proposal: %s", exc, exc_info=True)
        return jsonify({"error": "Failed to resolve change proposal"}), 500
    finally:
        session.close()


@bookkeeping_bp.route("/api/periods/<int:period_id>/approve", methods=["POST"])
@admin_required
def api_approve_workspace(period_id):
    session = get_session()
    current_user = get_current_user()
    payload = request.get_json() or {}
    force = _parse_boolean(payload.get("force"))

    try:
        period, error_response = _get_period_or_404(session, period_id)
        if error_response:
            return error_response

        summary = _workspace_payload(period)
        blocking_issues = []
        if summary["summary_cards"].get("expense_items_needing_review"):
            blocking_issues.append("expense review items")
        if summary["summary_cards"].get("revenue_items_needing_review"):
            blocking_issues.append("revenue review items")
        if summary["summary_cards"].get("pending_change_proposals"):
            blocking_issues.append("pending AI change proposals")
        if blocking_issues and not force:
            return jsonify({"error": f"Resolve {', '.join(blocking_issues)} before approval or use force approval."}), 409

        period.status = "approved"
        revision = _create_workspace_revision(session, period, current_user.user_id, "approved")
        session.commit()
        session.refresh(revision)
        return jsonify({"revision": revision.to_dict(), "period": period.to_dict()})
    except Exception as exc:
        session.rollback()
        logger.error("Error approving bookkeeping workspace: %s", exc, exc_info=True)
        return jsonify({"error": "Failed to approve bookkeeping workspace"}), 500
    finally:
        session.close()


@bookkeeping_bp.route("/api/periods/<int:period_id>/assistant/messages", methods=["GET"])
@admin_required
def api_list_assistant_messages(period_id):
    session = get_session()
    try:
        period, error_response = _get_period_or_404(session, period_id)
        if error_response:
            return error_response
        return jsonify({"messages": [message.to_dict() for message in period.conversation_messages]})
    finally:
        session.close()


@bookkeeping_bp.route("/api/periods/<int:period_id>/assistant/messages", methods=["POST"])
@admin_required
def api_post_assistant_message(period_id):
    session = get_session()
    current_user = get_current_user()
    payload = request.get_json() or {}

    try:
        period, error_response = _get_period_or_404(session, period_id)
        if error_response:
            return error_response

        message_text = (payload.get("message") or "").strip()
        if not message_text:
            return jsonify({"error": "Message text is required"}), 400
        if OpenAI is None:
            return jsonify({"error": "OpenAI client is not installed"}), 500

        user_message = BookkeepingConversationMessage(
            period_id=period_id,
            role="user",
            message_text=message_text,
            message_metadata=None,
            created_by=current_user.user_id,
        )
        session.add(user_message)
        session.flush()

        recent_messages = (
            session.query(BookkeepingConversationMessage)
            .filter(BookkeepingConversationMessage.period_id == period_id)
            .order_by(BookkeepingConversationMessage.created_at.asc())
            .all()
        )
        agent_context = build_agent_context(
            period.portfolio,
            period,
            period.uploads,
            period.expense_items,
            revenue_items=period.revenue_items,
            listing_mappings=period.portfolio.listing_mappings,
            change_proposals=period.change_proposals,
        )

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a bookkeeping copilot for a short-term rental business. "
                    "Be conservative, never invent facts, and ask targeted follow-up questions when the workspace context is not enough. "
                    "Supported expense categories are cleaning, maintenance, supplies, misc, software_fee, and direct_refund. "
                    "Owner statements credit the owner with 100% of revenue less expenses. "
                    "High-confidence evidence can be auto-categorized, but direct_refund requires explicit guest refund wording in the evidence. "
                    "Use the portfolio/month context and uploaded evidence summaries below."
                ),
            },
            {
                "role": "user",
                "content": f"Workspace context:\n{agent_context}",
            },
        ]

        for message in recent_messages[-10:]:
            if message.role not in {"user", "assistant"}:
                continue
            messages.append({"role": message.role, "content": message.message_text})

        client = OpenAI(api_key=config.OPENAI_API_KEY)
        response = client.chat.completions.create(
            model=config.OPENAI_MODEL,
            temperature=0.2,
            messages=messages,
        )
        assistant_text = (response.choices[0].message.content or "").strip()
        if not assistant_text:
            assistant_text = "I could not produce a useful answer from the current workspace context."

        assistant_message = BookkeepingConversationMessage(
            period_id=period_id,
            role="assistant",
            message_text=assistant_text,
            message_metadata={
                "model": config.OPENAI_MODEL,
                "usage": getattr(response, "usage", None).model_dump() if getattr(response, "usage", None) else None,
            },
            created_by=None,
        )
        session.add(assistant_message)
        session.commit()
        session.refresh(user_message)
        session.refresh(assistant_message)
        return jsonify(
            {
                "user_message": user_message.to_dict(),
                "assistant_message": assistant_message.to_dict(),
            }
        )
    except Exception as exc:
        session.rollback()
        logger.error("Error posting assistant message: %s", exc, exc_info=True)
        return jsonify({"error": "Failed to generate assistant response"}), 500
    finally:
        session.close()


@bookkeeping_bp.route("/api/periods/<int:period_id>/export", methods=["GET"])
@admin_required
def api_export_period_workbook(period_id):
    session = get_session()
    current_user = get_current_user()
    try:
        period, error_response = _get_period_or_404(session, period_id)
        if error_response:
            return error_response

        report_period_start = infer_reporting_period_start(period, period.uploads, period.expense_items)
        has_user_drive_credential = bool(
            current_user and getattr(current_user, "user_id", None) and get_google_drive_credential_for_user(current_user.user_id)
        )
        try:
            sync_bookkeeping_uploads_to_google_drive(
                period.portfolio,
                period,
                period.uploads,
                month_label=logical_month_label(report_period_start),
                user=current_user,
            )
        except RuntimeError as sync_error:
            if has_user_drive_credential:
                return jsonify(
                    {
                        "error": str(sync_error),
                    }
                ), 400
            logger.warning(
                "Skipping bookkeeping Drive sync during export for period %s: %s",
                period_id,
                sync_error,
            )
        workbook_bytes = build_bookkeeping_workbook(
            period.portfolio,
            period,
            period.uploads,
            period.expense_items,
            revenue_items=period.revenue_items,
        )
        filename = f"{period.portfolio.name or period.portfolio.code} - Bookkeeping - {logical_month_label(report_period_start)}.xlsx"
        _create_workspace_revision(session, period, current_user.user_id, "exported", workbook_filename=filename)
        session.commit()
        return send_file(
            io.BytesIO(workbook_bytes),
            as_attachment=True,
            download_name=filename,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except Exception as exc:
        session.rollback()
        logger.error("Error exporting bookkeeping workbook: %s", exc, exc_info=True)
        return jsonify({"error": str(exc) or "Failed to export bookkeeping workbook"}), 500
    finally:
        session.close()


def register_bookkeeping_routes(app):
    init_bookkeeping_database()
    app.register_blueprint(bookkeeping_bp)
