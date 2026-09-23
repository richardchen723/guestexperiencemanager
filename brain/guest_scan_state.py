"""Stable issue updates shared by first scans, rescans, and review ingestion."""

from datetime import datetime

from brain.models import ComprehensiveStayAnalysis, GuestReviewIssueAnalysis, PropertyGuestIssue
from brain.guest_issue_identity import normalize_complaint_key


RESCAN_INSTRUCTIONS = (
    "Analyze the complete conversation, including new_message_ids, against existing_issues. "
    "For every previously identified complaint return existing_issue_key exactly as supplied "
    "and preserve its complaint_key. Update its evidence and resolution assessment; do not "
    "create another issue for repeated wording, follow-ups, or resolution messages. Return "
    "all genuine complaints, including earlier ones. New issues on a rescan must cite at "
    "least one new guest complaint message. Reviews repeating a complaint from the same "
    "reservation must reuse that complaint's existing_issue_key and complaint_key. "
    "Never change operator workflow, reopen resolved issues, or create tickets."
)


def scan_version(row):
    return int((row.source_metadata or {}).get("scan_version", 1)) if row else 0


def existing_issues(session, listing_id, reservation_id, review_id=None):
    query = session.query(PropertyGuestIssue).filter(PropertyGuestIssue.listing_id == listing_id)
    if reservation_id:
        query = query.filter(PropertyGuestIssue.reservation_id == reservation_id)
    else:
        query = query.filter(PropertyGuestIssue.review_id == review_id)
    return query.order_by(PropertyGuestIssue.issue_id).all()


def issue_context(rows):
    return [{
        "existing_issue_key": row.source_issue_key,
        "complaint_key": row.complaint_key,
        "summary": row.summary,
        "details": row.details,
        "resolution_state": row.resolution_state,
        "source_references": row.source_references,
    } for row in rows]


def merge_issue(service, *, run_id, source_kind, listing_id, reservation_id,
                review_id, analysis_id, source_date, issue, previous_message_ids=None):
    session = service.brain_session
    key = normalize_complaint_key(issue.get("complaint_key"))
    if not key:
        raise ValueError("Every issue requires complaint_key")
    rows = existing_issues(session, listing_id, reservation_id, review_id)
    explicit = issue.get("existing_issue_key")
    matches = [row for row in rows if row.source_issue_key == explicit] if explicit else [
        row for row in rows if row.complaint_key == key
    ]
    if explicit and not matches:
        raise ValueError("existing_issue_key does not belong to this stay or review")
    # Preserve a linked ticket and stable legacy IDs, including already closed issues.
    row = min(matches, key=lambda row: (not bool(row.linked_ticket_id), row.issue_id)) if matches else None
    if row and row.complaint_key and row.complaint_key != key:
        raise ValueError("An existing issue's complaint_key cannot change on rescan")
    if not row and previous_message_ids is not None:
        new_complaints = [ref for ref in issue["source_references"]
                          if ref.get("role") == "complaint" and ref["source_id"] not in previous_message_ids]
        if not new_complaints:
            raise ValueError("A new issue on a rescan must cite a new guest complaint message")

    from brain.guest_experience_replication import GuestExperienceReplicationService
    replication = GuestExperienceReplicationService(session)
    scope = f"reservation:{reservation_id}" if reservation_id else f"review:{review_id}"
    identity = f"listing:{listing_id}:{scope}:{key}"
    if row:
        for model, identifier in ((ComprehensiveStayAnalysis, row.stay_analysis_id),
                                  (GuestReviewIssueAnalysis, row.review_analysis_id)):
            prior = session.get(model, identifier) if identifier else None
            if prior and prior.run_id and prior.run_id != run_id:
                replication.seal_run(prior.run_id)
    else:
        row = PropertyGuestIssue(
            source_kind=source_kind, source_issue_key=identity, dedupe_key=identity,
            listing_id=listing_id, reservation_id=reservation_id, source_date=source_date,
            source_references=[],
        )
        session.add(row)

    for field in ("issue_category", "summary", "details", "suggested_improvement", "severity"):
        setattr(row, field, issue.get(field))
    row.complaint_key = key
    row.dedupe_key = row.dedupe_key or identity
    if source_kind == "stay":
        row.stay_analysis_id = analysis_id
        row.resolution_state = issue["resolution_state"]
    else:
        row.review_analysis_id = analysis_id
        row.review_id = review_id
    refs = list(row.source_references or [])
    for ref in issue["source_references"]:
        if ref not in refs:
            refs.append(ref)
    row.source_references = refs
    row.analysis_updated_at = datetime.utcnow()
    # Keep a newly posted review visible even when the associated stay is old.
    row.source_date = max(row.source_date, source_date)
    session.flush()
    service._touched_issue_keys.add(row.source_issue_key)
    return row
