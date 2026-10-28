"""Sales transitions, immutable commercial versions, and operations handoff."""
from sqlalchemy import func, select

from . import validation as v
from .models import (Activity, Agreement, Deal, DealContact, DealProperty, Handoff,
                     Proposal, ProposalAction, Property, Task)


class WorkflowMixin:
    def qualify(self, identity, data):
        v.fields(data, {"version", "fit", "decision", "readiness", "owner_needs", "operational_fit", "owner_expectations", "sources"})
        deal = self.edit(identity, data)
        if deal.outcome != "open": raise v.SalesError("Reopen the opportunity before changing qualification.")
        result = {"fit": v.choice(data.get("fit"), ("strong", "needs_review", "poor"), "property fit"),
                  "decision": v.choice(data.get("decision"), ("approved", "needs_review", "rejected"), "qualification decision")}
        for key in ("readiness", "owner_needs", "operational_fit", "owner_expectations"):
            result[key] = v.string(data.get(key), key.replace("_", " "), 4000, result["decision"] == "approved")
        sources = data.get("sources", [])
        if not isinstance(sources, list) or len(sources) > 30: raise v.SalesError("Provide up to 30 evidence references.")
        result["sources"] = [v.url(url, "Evidence URL", True) for url in sources]
        if result["decision"] == "approved" and not self.db.scalar(select(DealProperty.property_id).where(DealProperty.deal_id == deal.id)):
            raise v.SalesError("Link at least one prospect property before approving qualification.")
        if result["decision"] == "approved" and result["fit"] == "poor":
            raise v.SalesError("A poor-fit prospect cannot be approved without revising the fit assessment.")
        result.update(reviewed_by=self.actor, reviewed_at=self.now.isoformat())
        if deal.stage >= 2 and result["decision"] != "approved":
            raise v.SalesError("Move this opportunity back to qualification before removing its approval.")
        deal.qualification = result
        self.changed(deal)
        self.log(deal, "qualification", "Qualification reviewed", result)
        return self.detail(deal.id)

    def assess(self, identity, data):
        v.fields(data, {"version", "assumptions", "service_strategy", "sources", "properties"})
        deal = self.edit(identity, data)
        if deal.outcome != "open": raise v.SalesError("Assessments require an open opportunity.")
        sources = data.get("sources", [])
        if not isinstance(sources, list) or len(sources) > 30: raise v.SalesError("Provide up to 30 source URLs.")
        result = {"assumptions": v.string(data.get("assumptions"), "Revenue assumptions", 10000, True),
                  "service_strategy": v.string(data.get("service_strategy"), "Service strategy", 10000, True),
                  "sources": [v.url(s, "Source URL", True) for s in sources],
                  "reviewed_by": self.actor, "reviewed_at": self.now.isoformat()}
        estimates = data.get("properties", [])
        if not isinstance(estimates, list) or not estimates or len(estimates) > 100:
            raise v.SalesError("Provide estimates for the assessed properties.")
        seen = set()
        for estimate in estimates:
            v.fields(estimate, {"property_id", "rental_revenue", "management_revenue"})
            pid = v.integer(estimate.get("property_id"), "Property ID")
            link = self.db.get(DealProperty, (deal.id, pid))
            if not link or pid in seen: raise v.SalesError("Choose each linked property once.")
            seen.add(pid)
            link.rental_revenue = v.money(estimate.get("rental_revenue"), "Rental revenue")
            link.management_revenue = v.money(estimate.get("management_revenue"), "Management revenue")
        result["properties"] = v.jsonable(estimates)
        deal.assessment = result
        self.changed(deal)
        self.log(deal, "assessment", "Property assessment reviewed", result)
        return self.detail(deal.id)

    def transition(self, identity, data):
        v.fields(data, {"version", "stage", "outcome", "reason", "revisit_at", "revisit_local", "fold", "next_action"})
        deal = self.edit(identity, data)
        if deal.outcome == "won":
            raise v.SalesError("Signed agreements remain won. Manage their operational progress in onboarding.")
        outcome = v.choice(data.get("outcome", deal.outcome), v.OUTCOMES[:-1], "outcome")
        stage = v.integer(data.get("stage", deal.stage), "Sales stage", 0, 5)
        reason = v.string(data.get("reason"), "Transition reason", 3000)
        if (outcome != "open" or stage < deal.stage or deal.outcome != "open") and not reason:
            raise v.SalesError("Record a reason for this disposition, reopening, or backward transition.")
        if data.get("next_action"):
            self.set_primary(deal, data["next_action"])
        old_reason = deal.outcome_reason
        previous = {"stage": deal.stage, "outcome": deal.outcome}
        if outcome == "open":
            if stage >= 2 and deal.qualification.get("decision") != "approved":
                raise v.SalesError("Approve qualification before outreach.")
            if stage >= 2 and not self.db.scalar(select(Activity.id).where(Activity.deal_id == deal.id, Activity.direction == "outbound")):
                raise v.SalesError("Record an actual permitted outreach attempt first. Drafts do not count.")
            if stage >= 3 and not deal.discovery_notes:
                raise v.SalesError("Record the owner's needs in discovery notes before advancing.")
            if stage >= 4 and not deal.assessment.get("reviewed_at"):
                raise v.SalesError("Review the property assessment and service strategy first.")
            if stage >= 5:
                proposal = self.latest_proposal(deal.id)
                if not proposal or self.proposal_state(proposal)["status"] != "sent":
                    raise v.SalesError("Record an approved proposal as sent before entering negotiation.")
            deal.closed_at, deal.revisit_at = None, None
        else:
            if outcome == "nurture":
                deal.revisit_at = v.local_instant(data["revisit_local"], self.settings().timezone, fold=data.get("fold")) if data.get("revisit_local") else v.instant(data.get("revisit_at"), "Nurture revisit time", True)
                if deal.revisit_at <= self.now: raise v.SalesError("Choose a future nurture revisit time.")
            else:
                deal.revisit_at = None
            deal.closed_at = self.now if outcome in ("lost", "disqualified") else None
            self.cancel_tasks(deal, "Opportunity moved to " + outcome)
        deal.stage, deal.outcome, deal.outcome_reason = stage, outcome, reason
        self.validate_active(deal)
        if previous != {"stage": stage, "outcome": outcome} or data.get("next_action") or data.get("revisit_at") or data.get("revisit_local") or reason != old_reason:
            self.changed(deal)
            self.log(deal, "transition", reason, {"before": previous, "after": {"stage": stage, "outcome": outcome}})
            self.history(deal, reason)
            self.notice(deal, "stage_changed")
        return self.detail(deal.id)

    def cancel_tasks(self, deal, reason):
        for task in self.db.scalars(select(Task).where(Task.deal_id == deal.id, Task.status == "open")):
            task.status, task.outcome, task.completed_at = "cancelled", reason, self.now
            task.updated_at = self.now
            task.version += 1
        self.db.flush()

    def latest_proposal(self, identity):
        return self.db.scalar(select(Proposal).where(Proposal.deal_id == identity).order_by(Proposal.number.desc()).limit(1))

    def proposal_state(self, proposal):
        actions = self.db.scalars(select(ProposalAction).where(ProposalAction.proposal_id == proposal.id).order_by(ProposalAction.id)).all()
        status = actions[-1].action if actions else "draft"
        return {"status": status, "actions": [{"action": a.action, "actor": a.actor, "note": a.note, "occurred_at": a.occurred_at} for a in actions]}

    def create_proposal(self, identity, data):
        v.fields(data, {"version", "document_url", "terms"})
        deal = self.edit(identity, data)
        if deal.outcome != "open" or not deal.assessment.get("reviewed_at"):
            raise v.SalesError("An open opportunity with a reviewed assessment is required.")
        terms = data.get("terms")
        v.fields(terms, {"currency", "fee_percent", "fee_basis", "one_time_fee", "services", "responsibilities", "proposed_start", "open_questions", "exceptions", "properties"})
        if terms.get("currency", "USD") != "USD": raise v.SalesError("Sales proposals must use USD.")
        percentage = v.money(terms.get("fee_percent"), "Management fee percentage", maximum=100)
        if percentage is None: raise v.SalesError("Record the proposed management fee percentage.")
        scope = terms.get("properties")
        if not isinstance(scope, list) or not scope or len(scope) > 100: raise v.SalesError("Choose the proposal's property scope.")
        properties = []
        seen = set()
        for item in scope:
            v.fields(item, {"property_id", "rental_revenue", "management_revenue"})
            pid = v.integer(item.get("property_id"), "Property ID")
            link = self.db.get(DealProperty, (deal.id, pid))
            if not link or pid in seen: raise v.SalesError("Choose each linked property once.")
            seen.add(pid)
            prop = self.row(Property, pid)
            properties.append({"property_id": pid, "name": prop.name, "address": prop.address,
                               "rental_revenue": v.money(item.get("rental_revenue"), "Rental revenue"),
                               "management_revenue": v.money(item.get("management_revenue"), "Management revenue")})
        snapshot = {"currency": "USD", "fee_percent": percentage, "properties": properties,
                    "one_time_fee": v.money(terms.get("one_time_fee"), "One-time fee"),
                    "proposed_start": v.day(terms.get("proposed_start"), "Proposed start")}
        for key in ("fee_basis", "services", "responsibilities", "open_questions", "exceptions"):
            snapshot[key] = v.string(terms.get(key), key.replace("_", " "), 10000, key in ("fee_basis", "services", "responsibilities"))
        latest = self.latest_proposal(deal.id)
        proposal = Proposal(deal_id=deal.id, number=latest.number + 1 if latest else 1,
                            terms=v.jsonable(snapshot), document_url=v.url(data.get("document_url"), required=True),
                            created_by=self.actor, created_at=self.now)
        self.db.add(proposal)
        self.changed(deal)
        # A new draft makes the current proposal unsent; return to assessment.
        if deal.stage == 5:
            deal.stage = 4
            self.history(deal, "New proposal revision prepared")
        self.db.flush()
        self.log(deal, "proposal", f"Proposal v{proposal.number} prepared", {"proposal_id": proposal.id})
        return self.detail(deal.id)

    def proposal_action(self, identity, data):
        v.fields(data, {"version", "proposal_id", "action", "note", "contact_id", "channel"})
        deal = self.edit(identity, data)
        if deal.outcome != "open": raise v.SalesError("This opportunity is closed.")
        proposal = self.row(Proposal, data.get("proposal_id"))
        if proposal.deal_id != deal.id or self.latest_proposal(deal.id).id != proposal.id:
            raise v.SalesError("Use the current proposal version.")
        action = v.choice(data.get("action"), ("approved", "sent", "withdrawn"), "proposal action")
        status = self.proposal_state(proposal)["status"]
        if action == "approved":
            self.require_manager()
            if status not in ("draft", "withdrawn"): raise v.SalesError("This proposal has already been reviewed.")
        elif action == "sent":
            if status != "approved": raise v.SalesError("A sales manager must approve this version before it is sent.")
            contact_id = v.integer(data.get("contact_id"), "Recipient contact")
            if not self.db.get(DealContact, (deal.id, contact_id)): raise v.SalesError("Choose a linked recipient.")
            channel = v.choice(data.get("channel", "email"), v.CHANNELS, "channel")
            self.assert_contact_allowed(contact_id, channel)
            self.db.add(Activity(deal_id=deal.id, contact_id=contact_id, kind="proposal_sent", actor=self.actor,
                                 actor_name=self.actor_name, body=f"Proposal v{proposal.number} sent", direction="outbound",
                                 channel=channel, source_url=proposal.document_url, occurred_at=self.now))
            deal.last_outbound_at = self.now
        else:
            if deal.stage == 5:
                deal.stage = 4
                self.history(deal, "Proposal withdrawn")
        note = v.string(data.get("note"), "Review / delivery evidence", 5000, True)
        self.db.add(ProposalAction(proposal_id=proposal.id, action=action, actor=self.actor, note=note, occurred_at=self.now))
        self.changed(deal)
        self.log(deal, "proposal", f"Proposal v{proposal.number} {action}", {"proposal_id": proposal.id, "note": note})
        return self.detail(deal.id)

    def sign(self, identity, data):
        v.fields(data, {"version", "proposal_id", "document_url", "signed_at", "signed_local", "fold", "effective_date", "signatory_ids", "property_ids", "remaining_action", "remaining_reason", "operations_user_id", "target_date", "commitments"})
        deal = self.edit(identity, data)
        if deal.outcome != "open": raise v.SalesError("Only an open opportunity can become a signed agreement.")
        proposal = self.row(Proposal, data.get("proposal_id"))
        if proposal.deal_id != deal.id or self.latest_proposal(deal.id).id != proposal.id or self.proposal_state(proposal)["status"] != "sent":
            raise v.SalesError("Record a signature against the current approved and sent proposal.")
        ids = data.get("property_ids")
        signers = data.get("signatory_ids")
        if not isinstance(ids, list) or not ids or any(type(x) is not int for x in ids) or len(set(ids)) != len(ids):
            raise v.SalesError("Select the properties actually included in the signed agreement.")
        scope = [p for p in proposal.terms["properties"] if p["property_id"] in ids]
        if len(scope) != len(ids): raise v.SalesError("Signed properties must belong to this proposal.")
        if not isinstance(signers, list) or not signers or any(type(x) is not int for x in signers):
            raise v.SalesError("Record the actual signatories.")
        from .models import Contact
        contacts = []
        for cid in set(signers):
            if not self.db.get(DealContact, (deal.id, cid)): raise v.SalesError("Signatories must be linked decision-makers.")
            contact = self.row(Contact, cid)
            contacts.append({"id": cid, "name": contact.name, "email": contact.email})
        signed_at = v.local_instant(data["signed_local"], self.settings().timezone, fold=data.get("fold")) if data.get("signed_local") else v.instant(data.get("signed_at"), "Signature time", True)
        if signed_at > self.now: raise v.SalesError("An agreement cannot be recorded as signed in the future.")
        links = self.db.scalars(select(DealProperty).where(DealProperty.deal_id == deal.id)).all()
        remaining = [link for link in links if link.property_id not in ids]
        remainder = None
        if remaining:
            choice = v.choice(data.get("remaining_action"), ("split", "exclude"), "remaining property treatment")
            reason = v.string(data.get("remaining_reason"), "Remaining property reason", 3000, True)
            if choice == "split":
                remainder = Deal(title=deal.title[:210] + " — remaining properties", account_id=deal.account_id,
                                 source_id=deal.source_id, source_reference=deal.source_reference,
                                 assigned_user_id=deal.assigned_user_id, summary=reason, is_test=deal.is_test,
                                 parent_deal_id=deal.id, created_at=self.now, updated_at=self.now)
                self.db.add(remainder)
                self.db.flush()
                for link in remaining:
                    self.db.add(DealProperty(deal_id=remainder.id, property_id=link.property_id,
                                            rental_revenue=link.rental_revenue, management_revenue=link.management_revenue))
                for link in self.db.scalars(select(DealContact).where(DealContact.deal_id == deal.id)):
                    self.db.add(DealContact(deal_id=remainder.id, contact_id=link.contact_id, role=link.role))
                self.log(remainder, "created", reason, {"split_from": deal.id})
                self.history(remainder, reason)
            for link in remaining: link.disposition = "not_signed"
        for link in links:
            if link.property_id in ids: link.disposition = "signed"
        agreement = Agreement(deal_id=deal.id, proposal_id=proposal.id,
                              document_url=v.url(data.get("document_url"), "Signed agreement URL", True),
                              signed_at=signed_at, effective_date=v.day(data.get("effective_date"), "Effective date", True),
                              signatories=contacts, scope={**proposal.terms, "properties": scope}, recorded_by=self.actor)
        self.db.add(agreement)
        self.db.flush()
        handoff = Handoff(agreement_id=agreement.id, assigned_user_id=self.user(data.get("operations_user_id"), True),
                          target_date=v.day(data.get("target_date"), "Target launch date"),
                          commitments=v.string(data.get("commitments"), "Sales commitments and handoff notes", 10000, True))
        self.db.add(handoff)
        deal.outcome, deal.closed_at, deal.revisit_at = "won", self.now, None
        self.cancel_tasks(deal, "Agreement signed; continue in onboarding")
        self.changed(deal)
        self.history(deal, "Agreement signed", stage=6)
        self.log(deal, "agreement", "Signed agreement recorded", {"agreement_id": agreement.id, "property_ids": ids, "remaining_deal_id": remainder.id if remainder else None})
        self.notice(deal, "handoff", payload={"agreement_id": agreement.id, "assigned_user_id": handoff.assigned_user_id})
        self.db.flush()
        return {**self.detail(deal.id), "remaining_deal_id": remainder.id if remainder else None}

    def onboarding(self, identity, data):
        v.fields(data, {"version", "action", "assigned_user_id", "target_date", "commitments", "checklist", "operational_links", "listing_links"})
        deal = self.edit(identity, data)
        agreement = self.db.scalar(select(Agreement).where(Agreement.deal_id == deal.id))
        if not agreement: raise v.SalesError("Record a signed agreement first.")
        handoff = self.db.get(Handoff, agreement.id)
        if handoff.status == "complete":
            raise v.SalesError("Completed onboarding is retained as history; create an operational ticket for subsequent changes.")
        action = v.choice(data.get("action", "update"), ("update", "accept", "complete"), "onboarding action")
        if not self.manager and self.actor != f"user:{handoff.assigned_user_id}":
            raise v.SalesError("Only the assigned operations owner or a sales manager can update this handoff.",403)
        if "assigned_user_id" in data:
            self.require_manager()
            new_owner = self.user(data["assigned_user_id"], True)
            if new_owner != handoff.assigned_user_id:
                handoff.status, handoff.accepted_at, handoff.accepted_by = "pending", None, None
                handoff.assigned_user_id = new_owner
        if "target_date" in data: handoff.target_date = v.day(data["target_date"], "Target date")
        if "commitments" in data: handoff.commitments = v.string(data["commitments"], "Commitments", 10000, True)
        if "checklist" in data:
            checklist = data["checklist"]
            if not isinstance(checklist, list) or len(checklist) > 100: raise v.SalesError("Provide at most 100 onboarding tasks.")
            handoff.checklist = [{"title": v.string(c.get("title"), "Checklist item", 500, True), "done": v.boolean(c.get("done", False), "Completed")} for c in checklist if isinstance(c, dict)]
            if len(handoff.checklist) != len(checklist): raise v.SalesError("Each checklist item must be an object.")
        if "operational_links" in data:
            links = data["operational_links"]
            if not isinstance(links, list) or len(links) > 100: raise v.SalesError("Provide at most 100 task references.")
            handoff.operational_links = [v.url(link, "Operational task URL", True) for link in links]
        if "listing_links" in data:
            links = data["listing_links"]
            if not isinstance(links,list) or len(links)>100: raise v.SalesError("Provide at most 100 listing links.")
            signed = {p["property_id"] for p in agreement.scope["properties"]}
            for link in links:
                v.fields(link, {"property_id", "listing_id"})
                if link.get("property_id") not in signed or link.get("listing_id") not in self.listings:
                    raise v.SalesError("Link a signed prospect property to an existing Hostaway listing.")
                prop = self.row(Property, link["property_id"], lock=True)
                prop.listing_id = link["listing_id"]
                prop.version += 1
        if action == "accept":
            if handoff.status != "pending": raise v.SalesError("This handoff has already been accepted.")
            handoff.status, handoff.accepted_by, handoff.accepted_at = "in_progress", self.actor, self.now
            self.history(deal, "Operations accepted handoff", stage=7)
        elif action == "complete":
            if handoff.status != "in_progress": raise v.SalesError("Accept the handoff before completing it.")
            if not handoff.checklist or any(not item["done"] for item in handoff.checklist):
                raise v.SalesError("Complete the onboarding checklist first.")
            handoff.status, handoff.completed_at = "complete", self.now
        handoff.version += 1
        handoff.updated_at = self.now
        self.changed(deal)
        self.log(deal, "onboarding", "Operations handoff " + action, {"status": handoff.status, "checklist": handoff.checklist, "assigned_user_id": handoff.assigned_user_id})
        self.notice(deal, "handoff_updated", payload={"agreement_id": agreement.id})
        return self.detail(deal.id)
