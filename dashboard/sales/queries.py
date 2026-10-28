"""Shared list/board/report filters. All totals use the full matching cohort."""
from datetime import datetime, time, timedelta, timezone
from collections import defaultdict
from decimal import Decimal
from statistics import median
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select

from . import validation as v
from .models import (Account, Activity, Agreement, Contact, Deal, DealContact, DealProperty,
                     Handoff, Intake, Notice, Permission, Property, Proposal, Source,
                     StageHistory, Task)


def record(row, names):
    return {key: getattr(row, key) for key in names.split()}


class QueryMixin:
    def settings_dict(self):
        return v.jsonable(record(self.settings(), "version timezone reminder_minutes escalation_minutes unassigned_minutes qualification_guidance assignment_guidance commercial_guidance onboarding_guidance"))

    def task_dict(self, task):
        return v.jsonable({**record(task, "id version deal_id title assigned_user_id due_at status is_primary kind outcome completed_at"),
                           "assigned_name": self.users.get(task.assigned_user_id, {}).get("name", "Former team member"),
                           "overdue": task.status == "open" and task.due_at < self.now})

    def summary(self, deal):
        cached = getattr(self, "_summary_cache", {}).get(deal.id)
        primary = cached["primary"] if cached else self.primary(deal)
        props = cached["props"] if cached else self.db.execute(select(Property, DealProperty).join(DealProperty, Property.id == DealProperty.property_id).where(DealProperty.deal_id == deal.id)).all()
        account = cached["account"] if cached else (self.db.get(Account, deal.account_id) if deal.account_id else None)
        source = cached["source"] if cached else self.db.get(Source, deal.source_id)
        agreement = cached["agreement"] if cached else self.db.scalar(select(Agreement).where(Agreement.deal_id == deal.id))
        handoff = cached["handoff"] if cached else (self.db.get(Handoff, agreement.id) if agreement else None)
        active_props = [link for prop, link in props if link.disposition != "not_signed"]
        known = [link.management_revenue for link in active_props if link.management_revenue is not None]
        board_stage = (7 if handoff and handoff.status != "pending" else 6) if deal.outcome == "won" else deal.stage
        last_stage = cached["last_stage"] if cached else self.db.scalar(select(StageHistory.entered_at).where(StageHistory.deal_id == deal.id).order_by(StageHistory.id.desc()).limit(1))
        return v.jsonable({**record(deal, "id version title account_id source_id source_reference assigned_user_id stage outcome outcome_reason revisit_at priority summary discovery_notes expected_close_date last_inbound_at last_outbound_at needs_reply is_test created_at updated_at closed_at acquisition_cost"),
            "code": f"EH-{deal.id:04d}", "account_name": account.name if account else None,
            "assigned_name": self.users.get(deal.assigned_user_id, {}).get("name", "Unassigned" if not deal.assigned_user_id else "Former team member"),
            "source_name": source.name, "source_kind": source.kind,
            "properties": [{**record(prop, "id version name address market property_type bedrooms bathrooms amenities rental_status listing_id"),
                             **record(link, "rental_revenue management_revenue disposition")} for prop, link in props],
            "primary_task": self.task_dict(primary) if primary else None,
            "overdue": bool(primary and primary.due_at < self.now and deal.outcome == "open"),
            "missing_action": deal.outcome == "open" and not primary,
            "annual_management_revenue": sum(known, Decimal(0)) if len(known) == len(active_props) and known else None,
            "assessed_management_revenue": sum(known, Decimal(0)) if known else None,
            "unassessed_property_count": len(active_props)-len(known),
            "property_count": len(active_props), "board_stage": board_stage,
            "stage_label": v.STAGES[board_stage], "handoff_status": handoff.status if handoff else None,
            "days_in_stage": max(0, int((self.now-(last_stage or deal.created_at)).total_seconds()/86400))})

    def summaries(self, deals):
        """Bounded eager reads for table/board/export; never one query per cell."""
        deals = list(deals)
        if not deals: return []
        ids = [d.id for d in deals]
        properties = defaultdict(list)
        for prop, scope in self.db.execute(select(Property, DealProperty).join(DealProperty, Property.id == DealProperty.property_id).where(DealProperty.deal_id.in_(ids))):
            properties[scope.deal_id].append((prop, scope))
        tasks = {t.deal_id: t for t in self.db.scalars(select(Task).where(Task.deal_id.in_(ids), Task.status == "open", Task.is_primary.is_(True)))}
        accounts = {a.id: a for a in self.db.scalars(select(Account).where(Account.id.in_({d.account_id for d in deals if d.account_id})))}
        sources = {s.id: s for s in self.db.scalars(select(Source).where(Source.id.in_({d.source_id for d in deals})))}
        agreements = {a.deal_id: a for a in self.db.scalars(select(Agreement).where(Agreement.deal_id.in_(ids)))}
        handoffs = {h.agreement_id: h for h in self.db.scalars(select(Handoff).where(Handoff.agreement_id.in_([a.id for a in agreements.values()])))}
        latest = select(func.max(StageHistory.id)).where(StageHistory.deal_id.in_(ids)).group_by(StageHistory.deal_id)
        stages = {h.deal_id: h.entered_at for h in self.db.scalars(select(StageHistory).where(StageHistory.id.in_(latest)))}
        self._summary_cache = {d.id: {"props": properties[d.id], "primary": tasks.get(d.id), "account": accounts.get(d.account_id),
            "source": sources[d.source_id], "agreement": agreements.get(d.id), "handoff": handoffs.get(agreements[d.id].id) if d.id in agreements else None,
            "last_stage": stages.get(d.id)} for d in deals}
        try: return [self.summary(d) for d in deals]
        finally: self._summary_cache = {}

    def detail(self, identity):
        deal = self.deal(identity)
        result = self.summary(deal)
        contacts = self.db.execute(select(Contact, DealContact).join(DealContact, Contact.id == DealContact.contact_id).where(DealContact.deal_id == deal.id)).all()
        result["contacts"] = [{**record(c, "id version name email phone preferred_channel"), "role": link.role,
                               "permissions": [self.permission_state(c.id, channel) for channel in v.CHANNELS]} for c, link in contacts]
        result["qualification"] = deal.qualification
        result["assessment"] = deal.assessment
        result["tasks"] = [self.task_dict(task) for task in self.db.scalars(select(Task).where(Task.deal_id == deal.id).order_by(Task.status, Task.due_at, Task.id))]
        result["activities"] = [record(a, "id kind actor actor_name body changes direction outcome channel contact_id source_url occurred_at created_at") for a in self.db.scalars(select(Activity).where(Activity.deal_id == deal.id).order_by(Activity.id.desc()).limit(200))]
        result["activity_has_more"] = (self.db.scalar(select(func.count()).select_from(Activity).where(Activity.deal_id == deal.id)) or 0) > 200
        result["stage_history"] = [record(h, "stage outcome reason actor entered_at") for h in self.db.scalars(select(StageHistory).where(StageHistory.deal_id == deal.id).order_by(StageHistory.id))]
        result["proposals"] = [{**record(p, "id number terms document_url created_by created_at"), **self.proposal_state(p)} for p in self.db.scalars(select(Proposal).where(Proposal.deal_id == deal.id).order_by(Proposal.number.desc()))]
        agreement = self.db.scalar(select(Agreement).where(Agreement.deal_id == deal.id))
        result["agreement"] = record(agreement, "id proposal_id document_url signed_at effective_date signatories scope recorded_by") if agreement else None
        handoff = self.db.get(Handoff, agreement.id) if agreement else None
        result["handoff"] = record(handoff, "agreement_id version status assigned_user_id target_date commitments checklist operational_links accepted_by accepted_at completed_at") if handoff else None
        result["notice_status"] = [{"status": status, "count": count} for status, count in self.db.execute(select(Notice.status, func.count()).where(Notice.deal_id == deal.id).group_by(Notice.status))]
        return v.jsonable(result)

    def filtered(self, filters):
        statement = select(Deal)
        scope = filters.get("record_scope", "genuine")
        v.choice(scope, ("genuine", "tests"), "record scope")
        if scope == "tests": self.require_manager()
        statement = statement.where(Deal.is_test.is_(scope == "tests"))
        search = v.string(filters.get("search"), "Search", 240)
        if search:
            pattern = "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            contacts = select(DealContact.deal_id).join(Contact).where(or_(Contact.name.ilike(pattern, escape="\\"), Contact.email.ilike(pattern, escape="\\"), Contact.phone.ilike(pattern, escape="\\")))
            properties = select(DealProperty.deal_id).join(Property).where(or_(Property.name.ilike(pattern, escape="\\"), Property.address.ilike(pattern, escape="\\"), Property.market.ilike(pattern, escape="\\")))
            statement = statement.where(or_(Deal.title.ilike(pattern, escape="\\"), Deal.id.in_(contacts), Deal.id.in_(properties), Deal.account_id.in_(select(Account.id).where(Account.name.ilike(pattern, escape="\\")))))
        for key, column in (("assigned_user_id", Deal.assigned_user_id), ("source_id", Deal.source_id)):
            if filters.get(key):
                if filters[key] == "unassigned" and key == "assigned_user_id": statement = statement.where(column.is_(None))
                else:
                    try: number = int(filters[key])
                    except (ValueError, TypeError): raise v.SalesError(f"Invalid {key}.") from None
                    statement = statement.where(column == number)
        if filters.get("market"):
            statement = statement.where(Deal.id.in_(select(DealProperty.deal_id).join(Property).where(Property.market == filters["market"])))
        if filters.get("outcome"):
            statement = statement.where(Deal.outcome == v.choice(filters["outcome"], v.OUTCOMES, "outcome"))
        if filters.get("priority"):
            statement = statement.where(Deal.priority == v.choice(filters["priority"], ("low", "normal", "high"), "priority"))
        pending_handoffs = select(Agreement.deal_id).join(Handoff, Agreement.id == Handoff.agreement_id).where(Handoff.status == "pending")
        if filters.get("stage") not in (None, ""):
            try: stage = int(filters["stage"])
            except (ValueError, TypeError): raise v.SalesError("Invalid stage.") from None
            v.integer(stage, "Stage", 0, 7)
            statement = statement.where(and_(Deal.outcome == "won", Deal.id.in_(pending_handoffs)) if stage == 6 else and_(Deal.outcome == "won", ~Deal.id.in_(pending_handoffs)) if stage == 7 else and_(Deal.stage == stage, Deal.outcome == "open"))
        localzone = ZoneInfo(self.settings().timezone)
        for key, end in (("created_from", False), ("created_to", True)):
            if filters.get(key):
                value = v.day(filters[key], key, True) + (timedelta(days=1) if end else timedelta())
                boundary = datetime.combine(value, time.min, localzone).astimezone(timezone.utc)
                statement = statement.where(Deal.created_at < boundary if end else Deal.created_at >= boundary)
        queue = filters.get("queue", "all")
        active_tasks = select(Task.deal_id).where(Task.status == "open", Task.is_primary.is_(True))
        overdue = Deal.id.in_(active_tasks.where(Task.due_at < self.now))
        today = self.now.astimezone(localzone).date()
        start = datetime.combine(today, time.min, localzone).astimezone(timezone.utc)
        stop = datetime.combine(today+timedelta(days=1), time.min, localzone).astimezone(timezone.utc)
        if queue == "attention": statement = statement.where(Deal.outcome == "open", or_(Deal.needs_reply.is_(True), overdue, and_(Deal.stage > 0, ~Deal.id.in_(active_tasks))))
        elif queue == "overdue": statement = statement.where(Deal.outcome == "open", overdue)
        elif queue == "today": statement = statement.where(Deal.outcome == "open", Deal.id.in_(active_tasks.where(Task.due_at >= start, Task.due_at < stop)))
        elif queue == "unassigned": statement = statement.where(Deal.outcome == "open", Deal.assigned_user_id.is_(None))
        elif queue == "missing_action": statement = statement.where(Deal.outcome == "open", ~Deal.id.in_(active_tasks))
        elif queue == "replies": statement = statement.where(Deal.needs_reply.is_(True))
        elif queue == "handoffs": statement = statement.where(Deal.id.in_(pending_handoffs))
        elif queue == "nurture_due": statement = statement.where(Deal.outcome == "nurture", Deal.revisit_at <= self.now)
        elif queue != "all": raise v.SalesError("Unknown attention queue.")
        return statement

    def listing(self, filters):
        try: page, size = int(filters.get("page", 1)), int(filters.get("page_size", 30))
        except (ValueError, TypeError): raise v.SalesError("Page and page_size must be integers.") from None
        v.integer(page, "Page", 1, 100000)
        v.integer(size, "Page size", 1, 100)
        statement = self.filtered(filters)
        total = self.db.scalar(select(func.count()).select_from(statement.subquery()))
        sort = filters.get("sort", "updated")
        options = {"updated": Deal.updated_at, "created": Deal.created_at, "title": Deal.title,
                   "stage": Deal.stage, "assigned": Deal.assigned_user_id}
        if sort not in options: raise v.SalesError("Unknown sort column.")
        direction = v.choice(filters.get("direction", "desc"), ("asc", "desc"), "sort direction")
        order = options[sort].asc().nulls_last() if direction == "asc" else options[sort].desc().nulls_last()
        rows = self.db.scalars(statement.order_by(order, Deal.id).limit(size).offset((page-1)*size))
        return {"deals": self.summaries(rows), "total": total, "page": page, "page_size": size}

    def tasks_listing(self, filters):
        try: page = int(filters.get("page", 1))
        except (ValueError, TypeError): raise v.SalesError("Invalid task page.") from None
        v.integer(page, "Page", 1, 100000)
        base = {k: val for k, val in filters.items() if k not in ("queue", "assigned_user_id")}
        ids = self.filtered(base).with_only_columns(Deal.id)
        statement = select(Task, Deal.title).join(Deal, Deal.id == Task.deal_id).where(Task.deal_id.in_(ids), Task.status == "open")
        if filters.get("assigned_user_id"):
            try: assignee = int(filters["assigned_user_id"])
            except (ValueError, TypeError): assignee = -1
            statement = statement.where(Task.assigned_user_id == assignee)
        queue = filters.get("queue", "all")
        if queue in ("overdue", "attention"): statement = statement.where(Task.due_at < self.now)
        elif queue == "today":
            zone = ZoneInfo(self.settings().timezone)
            today = self.now.astimezone(zone).date()
            statement = statement.where(Task.due_at >= datetime.combine(today, time.min, zone), Task.due_at < datetime.combine(today+timedelta(days=1), time.min, zone))
        elif queue != "all": statement = statement.where(Deal.id.in_(self.filtered({**base, "queue": queue}).with_only_columns(Deal.id)))
        total = self.db.scalar(select(func.count()).select_from(statement.subquery()))
        rows = self.db.execute(statement.order_by(Task.due_at, Task.id).limit(50).offset((page-1)*50))
        return {"tasks": [{**self.task_dict(t), "deal_title": title} for t, title in rows], "total": total, "page": page, "page_size": 50}

    def pipeline(self, filters):
        # Every column has its own bounded page; no silent truncation of a large board.
        base = {k: val for k, val in filters.items() if k not in ("stage", "outcome", "page")}
        result = []
        for stage, name in enumerate(v.STAGES):
            page = filters.get(f"page_{stage}", "1")
            listing = self.listing({**base, "stage": str(stage), "page": page, "page_size": "15"})
            if filters.get("outcome") and filters["outcome"] != ("won" if stage >= 6 else "open"):
                listing.update(deals=[], total=0)
            result.append({"stage": stage, "label": name, **listing})
        dispositions = {outcome: self.db.scalar(select(func.count()).select_from(self.filtered({**base, "outcome": outcome}).subquery())) for outcome in ("nurture", "lost", "disqualified")}
        return {"columns": result, "dispositions": dispositions}

    def reports(self, filters):
        # The report date range is a CREATED cohort, shared by every denominator.
        base = {k: val for k, val in filters.items() if k not in ("page", "page_size", "sort", "direction")}
        base["record_scope"] = "genuine"
        ids = self.filtered(base).with_only_columns(Deal.id).subquery()
        scoped = select(Deal).where(Deal.id.in_(select(ids.c.id)))
        outcomes = dict(self.db.execute(select(Deal.outcome, func.count()).where(Deal.id.in_(select(ids.c.id))).group_by(Deal.outcome)).all())
        property_count = self.db.scalar(select(func.count(func.distinct(DealProperty.property_id))).where(DealProperty.deal_id.in_(select(ids.c.id)), DealProperty.disposition != "not_signed"))
        account_count = self.db.scalar(select(func.count(func.distinct(Deal.account_id))).where(Deal.id.in_(select(ids.c.id))))
        estimates = self.db.execute(select(func.sum(DealProperty.management_revenue), func.count()).where(DealProperty.deal_id.in_(select(ids.c.id)), DealProperty.disposition != "not_signed", DealProperty.management_revenue.is_not(None))).one()
        won_revenue = Decimal(0)
        signed_props = set()
        unknown_won = 0
        agreements = self.db.scalars(select(Agreement).where(Agreement.deal_id.in_(select(ids.c.id)))).all()
        for agreement in agreements:
            for prop in agreement.scope["properties"]:
                signed_props.add(prop["property_id"])
                if prop.get("management_revenue") is None: unknown_won += 1
                else: won_revenue += Decimal(prop["management_revenue"])
        created = dict(self.db.execute(select(Deal.id, Deal.created_at).where(Deal.id.in_(select(ids.c.id)))).all())
        responses = self.db.execute(select(Activity.deal_id, func.min(Activity.occurred_at)).where(Activity.deal_id.in_(select(ids.c.id)), Activity.direction == "outbound", Activity.actor.like("user:%")).group_by(Activity.deal_id)).all()
        response_hours = [(at-created[did]).total_seconds()/3600 for did, at in responses if at >= created[did]]
        stage_ever = dict(self.db.execute(select(StageHistory.stage, func.count(func.distinct(StageHistory.deal_id))).where(StageHistory.deal_id.in_(select(ids.c.id)), StageHistory.outcome == "open").group_by(StageHistory.stage)).all())
        stage_times = {i: [] for i in range(6)}
        histories = self.db.scalars(select(StageHistory).where(StageHistory.deal_id.in_(select(ids.c.id))).order_by(StageHistory.deal_id, StageHistory.id)).all()
        for i, event in enumerate(histories):
            if event.outcome != "open": continue
            stop = histories[i+1].entered_at if i+1 < len(histories) and histories[i+1].deal_id == event.deal_id else self.now
            stage_times[event.stage].append(max(0, (stop-event.entered_at).total_seconds()/86400))
        sources = []
        for source_id, name, count, known_cost, cost_count in self.db.execute(select(Source.id, Source.name, func.count(Deal.id), func.sum(Deal.acquisition_cost), func.count(Deal.acquisition_cost)).join(Deal, Deal.source_id == Source.id).where(Deal.id.in_(select(ids.c.id))).group_by(Source.id, Source.name)):
            wins = self.db.scalar(select(func.count()).select_from(Deal).where(Deal.id.in_(select(ids.c.id)), Deal.source_id == source_id, Deal.outcome == "won"))
            sources.append({"source_id": source_id, "name": name, "leads": count, "won": wins, "recorded_cost": known_cost,
                            "cost_records": cost_count, "acquisition_cost_per_win": known_cost / wins if known_cost is not None and cost_count == count and wins else None})
        resolved = outcomes.get("won", 0) + outcomes.get("lost", 0)
        activities = dict(self.db.execute(select(Activity.kind, func.count()).where(Activity.deal_id.in_(select(ids.c.id))).group_by(Activity.kind)).all())
        qualification_count = self.db.scalar(select(func.count()).select_from(scoped.where(Deal.qualification["decision"].as_string() == "approved").subquery()))
        overdue_count = self.db.scalar(select(func.count(func.distinct(Task.deal_id))).join(Deal, Deal.id == Task.deal_id).where(Deal.id.in_(select(ids.c.id)), Deal.outcome == "open", Task.status == "open", Task.due_at < self.now))
        handoff_pending = self.db.scalar(select(func.count()).select_from(Handoff).join(Agreement, Handoff.agreement_id == Agreement.id).where(Agreement.deal_id.in_(select(ids.c.id)), Handoff.status != "complete"))
        return v.jsonable({"date_basis": "Opportunity created date (cohort)", "currency": "USD", "test_records_excluded": True,
            "opportunities": sum(outcomes.values()), "accounts": account_count, "properties": property_count,
            "outcomes": outcomes, "qualified": qualification_count, "overdue_opportunities": overdue_count,
            "win_rate": round(outcomes.get("won",0)/resolved*100,1) if resolved else None,
            "win_rate_denominator": "Won + Lost; nurture, open and disqualified excluded",
            "assessed_management_revenue": estimates[0], "assessed_properties": estimates[1],
            "signed_agreements": len(agreements), "signed_properties": len(signed_props),
            "won_management_revenue": won_revenue if agreements and not unknown_won else None,
            "known_won_management_revenue": won_revenue if agreements else None, "unknown_won_properties": unknown_won,
            "awaiting_launch": handoff_pending, "first_human_response_median_hours": round(median(response_hours),2) if response_hours else None,
            "response_sample_size": len(response_hours), "response_time_basis": "Opportunity capture to first recorded human outbound contact",
            "stages": [{"stage": i, "label": label, "ever_entered": stage_ever.get(i,0),
                        "median_visit_days": round(median(stage_times[i]),2) if stage_times[i] else None} for i,label in enumerate(v.STAGES[:6])],
            "activity_counts": activities, "sources": sources})

    def directory(self, kind, query=""):
        cls = {"accounts": Account, "contacts": Contact, "properties": Property}.get(kind)
        if cls is None: raise v.SalesError("Unknown directory.")
        statement = select(cls).where(cls.merged_into_id.is_(None))
        if query:
            query = v.string(query,"Search",240)
            statement = statement.where(cls.name.ilike("%"+query.replace("%","\\%").replace("_","\\_")+"%",escape="\\"))
        rows = self.db.scalars(statement.order_by(cls.name,cls.id).limit(50)).all()
        return {"records": [v.jsonable({c.name:getattr(row,c.name) for c in cls.__table__.columns}) for row in rows], "limit":50}
