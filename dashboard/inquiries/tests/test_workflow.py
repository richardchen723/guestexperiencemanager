from datetime import timedelta

import pytest
from sqlalchemy import func, select

from dashboard.inquiries.models import Activity, Guest, Lead, SourceMessage
from dashboard.inquiries.parser import parse_email
from dashboard.inquiries.service import InquiryError
from .conftest import NOW, email, reply


def create(service, **changes):
    result = service.ingest(email(**changes))
    assert result["processing_status"] == "processed", result
    return service.lead(result["lead_id"])


def test_real_format_extracts_dates_property_and_preserves_original(service):
    lead = create(service)
    assert lead.status == "New" and lead.assigned_user_id is None
    assert lead.guest_name == "Sally Example" and lead.listing_id == 101
    assert str(lead.check_in) == "2026-11-17" and str(lead.check_out) == "2026-11-20"
    assert lead.qualification["guest_count"] == 3
    assert lead.original_notes == email()["body"]


def test_reply_updates_same_opportunity_and_retains_sources_and_audit(service, db):
    lead = create(service)
    result = service.ingest(reply())
    assert result["lead_id"] == lead.id and lead.needs_reply
    assert str(lead.check_in) == "2026-11-22" and lead.qualification["children"] == 2
    detail = service.serialize(lead, True)
    assert len(detail["messages"]) == 2 and len(detail["activities"]) == 2
    assert detail["activities"][0]["actor"] == "Larry"
    assert "check_in" in detail["activities"][0]["changes"]
    assert detail["original_notes"] == email()["body"]


def test_replaying_initial_and_reply_creates_no_leads_messages_or_activity(service, db):
    lead = create(service)
    assert service.ingest(email())["replayed"]
    service.ingest(reply())
    version = lead.version
    assert service.ingest(reply())["replayed"]
    assert lead.version == version
    assert db.scalar(select(func.count()).select_from(Lead)) == 1
    assert db.scalar(select(func.count()).select_from(SourceMessage)) == 2
    assert db.scalar(select(func.count()).select_from(Activity)) == 2


def test_changed_message_id_content_rejected(service):
    create(service)
    with pytest.raises(InquiryError, match="different source content"):
        service.ingest(email(body="Changed original"))


def test_internal_test_reply_replay_and_original_are_isolated(service, db):
    original = email(internal_test=True)
    lead = create(service, internal_test=True)
    assert lead.is_test is True and lead.original_notes == original['body']
    assert service.ingest(reply())['lead_id'] == lead.id
    before = service.serialize(lead, True)
    assert service.ingest(original)['replayed'] is True
    assert service.serialize(lead, True) == before
    lead.status = 'Booked'
    db.flush()
    assert service.listing({})['total'] == 0
    internal = service.listing({'record_scope':'internal-tests'})
    assert internal['total'] == 1 and internal['stats']['total'] == 0 and internal['stats']['booked'] == 0
    assert internal['stats']['conversion_rate'] is None
    other = create(service, message_id='genuine', thread_id='genuine-thread', reference='EH-REAL1234')
    assert other.is_test is False
    assert service.listing({})['stats']['total'] == 1
    assert service.listing({'record_scope':'internal-tests'})['stats']['total'] == 1


def test_internal_classification_cannot_be_extracted_or_merge_into_real(service):
    lead = create(service, extraction={'reference':'EH-ABC12345','is_test':True,'fields':{'email':'sally@example.invalid'}})
    assert lead.is_test is False
    result = service.ingest(reply(internal_test=True))
    assert result['processing_status'] == 'failed'
    assert lead.is_test is False


def test_forwarded_same_reference_does_not_create_another_opportunity(service, db):
    lead = create(service)
    result = service.ingest(email(message_id="forward1", thread_id="forward1", subject="Fwd: New stay inquiry EH-ABC12345 from Sally"))
    assert result["lead_id"] == lead.id
    assert db.scalar(select(func.count()).select_from(Lead)) == 1


def test_separate_trip_same_guest_has_same_profile_separate_opportunity(service, db):
    first = create(service)
    second = create(service, message_id="m3", reference="EH-DIFF1234", thread_id="t2")
    assert first.id != second.id and first.guest_id == second.guest_id
    assert db.scalar(select(func.count()).select_from(Guest)) == 1


def test_new_trip_in_existing_thread_requires_explicit_review(service):
    first = create(service)
    data = reply(message_id="m3", extraction={"reference":"EH-NEW99999", "fields":{"email":"sally@example.invalid"}})
    result = service.ingest(data)
    assert result["processing_status"] == "failed"
    data["extraction"]["new_inquiry"] = True
    result = service.ingest(data)
    assert result["processing_status"] == "processed" and result["lead_id"] != first.id


def test_reply_without_reference_matches_thread(service):
    lead = create(service)
    data = reply(extraction={"fields":{"guest_count":5}})
    assert service.ingest(data)["lead_id"] == lead.id
    assert lead.qualification["guest_count"] == 5


def test_ambiguous_thread_without_reference_fails_for_review(service):
    create(service)
    create(service, message_id="m3", reference="EH-SECOND12")
    result = service.ingest(reply(message_id="m4", extraction={"fields":{"guest_count":5}}))
    assert result["processing_status"] == "failed" and "multiple inquiries" in result["error"]


def test_staff_corrections_internal_notes_and_schedule_survive_guest_reply(service):
    lead = create(service)
    service.patch(lead.id,{"version":1,"check_in":"2026-12-01","check_out":"2026-12-04", "internal_notes":"Negotiated by phone", "assigned_user_id":1,
                           "status":"Follow-up Required","next_action":"Call on Thursday","follow_up_at":"2026-10-22T10:00:00-07:00"})
    service.ingest(reply())
    assert str(lead.check_in) == "2026-12-01" and lead.internal_notes == "Negotiated by phone"
    assert lead.next_action == "Call on Thursday" and lead.needs_reply
    assert any(f.get("field") == "check_in" and f["proposed"] == "2026-11-22" for f in lead.review_flags)
    assert lead.qualification["guest_count"] == 6


def test_stale_staff_version_rejected(service):
    lead = create(service)
    service.ingest(reply())
    with pytest.raises(InquiryError, match="changed") as exc:
        service.patch(lead.id,{"version":1,"internal_notes":"Would overwrite"})
    assert exc.value.status == 409


def test_followup_requires_owner_action_due_and_offset(service):
    lead = create(service)
    with pytest.raises(InquiryError,match="responsible agent"):
        service.patch(lead.id,{"version":1,"status":"Follow-up Required"})


def test_naive_timestamp_rejected(service):
    lead = create(service)
    with pytest.raises(InquiryError, match="UTC offset"):
        service.patch(lead.id,{"version":1,"follow_up_at":"2026-10-21T12:00:00"})


def test_overdue_and_configurable_reminder_escalation_and_unassigned(service):
    lead = create(service)
    assert service.attention(lead)["unassigned"] and not service.attention(lead)["unassigned_alert"]
    service.update_settings({"version":1,"reminder_minutes":30,"escalation_minutes":60,"unassigned_minutes":30})
    assert service.attention(lead)["unassigned_alert"]
    service.patch(lead.id,{"version":1,"assigned_user_id":1,"status":"Follow-up Required","next_action":"Call","follow_up_at":(NOW-timedelta(hours=2)).isoformat()})
    assert service.attention(lead)["overdue"] and service.attention(lead)["escalated"]
    service.patch(lead.id,{"version":lead.version,"status":"Booked"})
    assert not service.attention(lead)["overdue"] and not service.attention(lead)["escalated"]


def test_qualification_unknowns_stay_empty_and_supplied_currency_preserved(service):
    lead = create(service, body="Name: Sally\nDates: Sometime next month\nBudget: EUR 600 total")
    assert lead.check_in is None and lead.check_out is None and lead.email is None
    assert lead.qualification["budget"] == "EUR 600 total"
    assert lead.review_flags


def test_unrelated_or_unapproved_mail_is_ignored(service, db):
    result = service.ingest(email(subject="Invoice for cleaning",body="Email: wrong@example.invalid"))
    assert result["processing_status"] == "ignored"
    result = service.ingest(email(message_id="m2",sender="stranger@example.invalid"))
    assert result["processing_status"] == "ignored"
    assert db.scalar(select(func.count()).select_from(Lead)) == 0


def test_internal_participant_does_not_overwrite_guest_fields(service):
    lead = create(service)
    result = service.ingest(reply(sender="Lydia <lydia@enchantedhavens.com>",extraction={"reference":"EH-ABC12345","fields":{"guest_name":"Wrong Person","guest_count":77}}))
    assert result["processing_status"] == "processed"
    assert lead.guest_name == "Sally Example" and lead.qualification["guest_count"] == 3
    assert any("Non-guest" in f["reason"] for f in lead.review_flags)


def test_failed_parsing_is_durable_and_retry_works_without_partial_lead(service, db):
    data = email(extraction={"reference":"EH-ABC12345","fields":{"check_in":"not-a-date"}})
    result = service.ingest(data)
    assert result["processing_status"] == "failed"
    db.commit()
    assert db.scalar(select(func.count()).select_from(Lead)) == 0
    assert db.scalar(select(SourceMessage)).body == data["body"]
    result = service.ingest(email())
    assert result["processing_status"] == "processed"
    assert db.scalar(select(func.count()).select_from(SourceMessage)) == 1


def test_old_reply_never_rolls_back_newer_guest_information(service):
    lead = create(service)
    service.ingest(reply())
    service.ingest(reply(message_id="old", received_at="2026-10-20T17:15:00Z", extraction={"reference":"EH-ABC12345","fields":{"guest_count":2}}))
    assert lead.qualification["guest_count"] == 6


def test_explicit_guest_email_change_keeps_both_addresses_in_the_reply_loop(service):
    lead = create(service)
    service.ingest(reply(extraction={"reference":"EH-ABC12345","fields":{"email":"sally.new@example.invalid"}}))
    assert lead.email == "sally.new@example.invalid"
    service.ingest(reply(message_id="m3",received_at="2026-10-20T17:40:00Z",extraction={"reference":"EH-ABC12345","fields":{"guest_count":7}}))
    assert lead.qualification["guest_count"] == 7
    service.ingest(reply(message_id="m4",sender="sally.new@example.invalid",received_at="2026-10-20T17:50:00Z",
                         extraction={"reference":"EH-ABC12345","fields":{"guest_count":8}}))
    assert lead.qualification["guest_count"] == 8


def test_new_guest_information_clears_obsolete_missing_field_flags(service):
    lead = create(service,body="Name: Sally\nEmail: sally@example.invalid\nDates: Flexible")
    assert any(f.get("field") == "check_in" for f in lead.review_flags)
    service.ingest(reply())
    assert not any(f.get("field") in ("check_in","check_out") and f["reason"] == "Missing or unclear information." for f in lead.review_flags)


def test_exact_property_alias_only_never_fuzzy_guesses(service):
    lead = create(service, body="Property: Fair Haven-ish\nName: Sally")
    assert lead.listing_id is None and lead.tag_id is None
    assert any(f.get("field") == "property_text" for f in lead.review_flags)


def test_tag_group_matches_existing_directory(service):
    lead = create(service, body="Property: Enchanted Havens\nName: Sally")
    assert lead.listing_id is None and lead.tag_id == 1


def test_conversion_denominator_excludes_open_leads_and_filters_apply(service):
    first = create(service)
    second = create(service, message_id="m3", thread_id="t3", reference="EH-SECOND12")
    third = create(service, message_id="m4", thread_id="t4", reference="EH-THIRD123")
    service.patch(first.id,{"version":1,"status":"Booked"})
    service.patch(second.id,{"version":1,"status":"Closed — Not Booked"})
    assert service.listing({})["stats"]["conversion_rate"] == 50
    stats = service.listing({"status":"Booked"})["stats"]
    assert stats["total"] == 1 and stats["conversion_rate"] == 100
    assert service.listing({"search":"Fair Haven"})["total"] == 3
    assert service.listing({"tag_id":"1"})["total"] == 3
    assert service.listing({"search":"%"})["total"] == 0


def test_local_date_filter_uses_pacific_midnight(service):
    create(service, received_at="2026-10-20T05:00:00Z") # Oct 19 Pacific
    assert service.listing({"from":"2026-10-20","to":"2026-10-20"})["total"] == 0
    assert service.listing({"from":"2026-10-19","to":"2026-10-19"})["total"] == 1


def test_review_attention_and_pagination(service):
    create(service, body="Name: Sally\nProperty: Unknown")
    assert service.listing({"attention":"review"})["total"] == 1
    assert service.listing({"page":"2","per_page":"1"})["leads"] == []


def test_parser_never_reads_quoted_old_fields():
    parsed = parse_email("Re: EH-ABC12345", "Thanks, four please.\n\nOn Tue, Sally wrote:\nGuests: 3\nEmail: stale@example.invalid")
    assert "email" not in parsed["fields"] and "guest_count" not in parsed["fields"]
