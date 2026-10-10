"""Write-side commands for deals. Views stay thin and call this."""

from django.db import transaction
from django.utils import timezone

from crm.availability import assignment_block_reason
from crm.deals.events import record_deal_event
from crm.deals.exceptions import DealServiceError
from crm.deals.legacy import legacy_stage_for, resolve_legacy_stage
from crm.deals.line_items import strategy_for
from crm.deals.notifications import (
    notify_deal_created,
    notify_deal_lost,
    notify_deal_reassigned,
    notify_deal_won,
)
from crm.deals.pricing import line_total, money, recalculate_deal_value
from crm.models import Deal, DealLineItem

_MISSING = object()

_TRACKED_FIELDS = (
    "title",
    "status",
    "payment_method",
    "value",
    "description",
    "currency",
    "expected_close_date",
    "probability",
    "discount_percentage",
    "discount_amount",
    "sales_commission_percentage",
    "sales_commission_amount",
    "start_date",
    "closed_date",
    "reminder_date",
    "unit_id",
    "project_id",
    "client_id",
)

_FK_FIELDS = {
    "client",
    "employee",
    "unit",
    "project",
    "started_by",
    "closed_by",
    "lost_reason",
}


class DealService:
    def __init__(self, actor):
        self.actor = actor
        self.company = getattr(actor, "company", None)

    def _require_company(self):
        if self.company is None:
            raise DealServiceError("Your account is not attached to a company.")
        return self.company

    def _same_company(self, obj, field):
        if obj is None:
            return
        company_id = getattr(obj, "company_id", None)
        if company_id is not None and company_id != self.company.id:
            raise DealServiceError(
                f"{field.replace('_', ' ').title()} does not belong to this company.",
                field=field,
            )

    def _check_employee(self, employee, previous_id):
        if employee is None:
            return
        self._same_company(employee, "employee")
        if previous_id is not None and previous_id == employee.pk:
            return
        reason = assignment_block_reason(employee, company_for_calendar=self.company)
        if reason:
            from crm.availability import assignment_block_message

            raise DealServiceError(assignment_block_message(reason), field="employee")

    def _lock_quota(self):
        company = self._require_company()
        user = self.actor
        if getattr(user, "is_super_admin", lambda: False)():
            return
        from companies.models import Company
        from subscriptions.entitlements import require_quota

        Company.objects.select_for_update().filter(pk=company.pk).first()
        current = Deal.objects.filter(company=company).count()
        require_quota(
            company,
            "max_deals",
            current_count=current,
            requested_delta=1,
            message="You have reached your plan deals limit. Please upgrade your plan to add more deals.",
            error_key="plan_quota_max_deals_exceeded",
        )

    def _resolve_stage(self, data, *, current=None):
        stage = data.get("pipeline_stage", _MISSING)
        legacy = data.get("stage", _MISSING)
        if stage is not _MISSING and stage is not None:
            if stage.pipeline.company_id != self.company.id:
                raise DealServiceError(
                    "Stage does not belong to this company.", field="pipeline_stage"
                )
            return stage
        if legacy is not _MISSING and legacy:
            return resolve_legacy_stage(self.company, legacy)
        if current is not None:
            return current
        return resolve_legacy_stage(self.company, "in_progress")

    def _apply_stage_fields(self, deal, stage, *, lost_reason=_MISSING, lost_note=_MISSING, require_lost_reason=False):
        previous = deal.pipeline_stage if deal.pipeline_stage_id else None
        if stage is None:
            return previous
        if stage.stage_type == "lost" and require_lost_reason:
            chosen = lost_reason if lost_reason is not _MISSING else None
            if chosen is None and not deal.lost_reason_id:
                raise DealServiceError("A lost reason is required.", field="lost_reason")
        now = timezone.now()
        deal.pipeline = stage.pipeline
        deal.pipeline_stage = stage
        deal.stage = legacy_stage_for(stage)
        deal.stage_changed_at = now
        if stage.stage_type == "won":
            deal.won_at = deal.won_at or now
            deal.lost_at = None
            deal.lost_reason = None
            deal.lost_note = ""
            if not deal.closed_date:
                deal.closed_date = timezone.localdate()
            if not deal.closed_by_id and self.actor is not None:
                deal.closed_by = self.actor
        elif stage.stage_type == "lost":
            deal.lost_at = now
            deal.won_at = None
            if not deal.closed_date:
                deal.closed_date = timezone.localdate()
            if not deal.closed_by_id and self.actor is not None:
                deal.closed_by = self.actor
            if lost_reason is not _MISSING and lost_reason is not None:
                self._same_company(lost_reason, "lost_reason")
                deal.lost_reason = lost_reason
            if lost_note is not _MISSING and lost_note is not None:
                deal.lost_note = lost_note
        else:
            if previous is not None and previous.stage_type in ("won", "lost"):
                deal.won_at = None
                deal.lost_at = None
                deal.closed_date = None
                deal.closed_by = None
                deal.lost_reason = None
                deal.lost_note = ""
        return previous

    def _snapshot(self, deal):
        return {name: getattr(deal, name) for name in _TRACKED_FIELDS} | {
            "employee_id": deal.employee_id,
            "pipeline_stage_id": deal.pipeline_stage_id,
        }

    def _record_changes(self, deal, before):
        changes = {}
        for name in _TRACKED_FIELDS:
            old = before.get(name)
            new = getattr(deal, name)
            if old != new:
                changes[name] = ["" if old is None else str(old), "" if new is None else str(new)]
        if changes:
            record_deal_event(
                deal,
                "field_change",
                metadata={"changes": changes},
                created_by=self.actor,
            )
        if before.get("employee_id") != deal.employee_id:
            record_deal_event(
                deal,
                "assigned",
                old_value=str(before.get("employee_id") or ""),
                new_value=str(deal.employee_id or ""),
                created_by=self.actor,
            )
            notify_deal_reassigned(deal, self.actor)

    def _record_stage_event(self, deal, previous, *, reason=""):
        if previous is not None and previous.id == deal.pipeline_stage_id:
            return
        new_stage = deal.pipeline_stage
        new_type = new_stage.stage_type if new_stage is not None else ""
        old_name = previous.name if previous is not None else ""
        new_name = new_stage.name if new_stage is not None else deal.stage
        if new_type == "won":
            event_type = "won"
        elif new_type == "lost":
            event_type = "lost"
        elif previous is not None and previous.stage_type in ("won", "lost") and new_type == "open":
            event_type = "reopened"
        else:
            event_type = "stage_change"
        record_deal_event(
            deal,
            event_type,
            old_value=old_name,
            new_value=new_name,
            reason=reason or "",
            metadata={
                "from_stage_id": previous.id if previous else None,
                "to_stage_id": new_stage.id if new_stage else None,
                "stage_type": new_type,
            },
            created_by=self.actor,
        )
        if event_type == "won":
            notify_deal_won(deal, self.actor)
        elif event_type == "lost":
            notify_deal_lost(deal, self.actor)

    def _currency_default(self, deal):
        if deal.currency:
            return
        unit = deal.unit
        if unit is not None and getattr(unit, "currency", None):
            deal.currency = unit.currency

    @transaction.atomic
    def create(self, data):
        self._require_company()
        self._lock_quota()
        data = dict(data)
        client = data.get("client")
        if client is None:
            raise DealServiceError("A lead is required.", field="client")
        self._same_company(client, "client")
        employee = data.get("employee")
        self._check_employee(employee, None)
        for field in ("unit", "project", "started_by", "closed_by"):
            self._same_company(data.get(field), field)

        stage = self._resolve_stage(data)
        deal = Deal(
            company=self.company,
            client=client,
            employee=employee,
            title=(data.get("title") or "").strip() or client.name,
            payment_method=data.get("payment_method") or "cash",
            status=data.get("status") or "reservation",
            value=data.get("value"),
            reminder_date=data.get("reminder_date"),
            start_date=data.get("start_date"),
            closed_date=data.get("closed_date"),
            discount_percentage=data.get("discount_percentage") or 0,
            discount_amount=data.get("discount_amount") or 0,
            sales_commission_percentage=data.get("sales_commission_percentage") or 0,
            sales_commission_amount=data.get("sales_commission_amount") or 0,
            description=data.get("description") or "",
            unit=data.get("unit"),
            project=data.get("project"),
            started_by=data.get("started_by") or self.actor,
            closed_by=data.get("closed_by"),
            probability=data.get("probability"),
            expected_close_date=data.get("expected_close_date"),
            currency=(data.get("currency") or "").strip(),
        )
        self._currency_default(deal)
        previous = self._apply_stage_fields(
            deal,
            stage,
            lost_reason=data.get("lost_reason", _MISSING),
            lost_note=data.get("lost_note", _MISSING),
            require_lost_reason=False,
        )
        if stage is None and data.get("stage"):
            deal.stage = data["stage"]
        deal.save()
        record_deal_event(
            deal,
            "created",
            new_value=deal.title,
            created_by=self.actor,
        )
        if previous is None and deal.pipeline_stage_id and deal.pipeline_stage.stage_type == "won":
            record_deal_event(deal, "won", new_value=deal.pipeline_stage.name, created_by=self.actor)
        notify_deal_created(deal, self.actor)
        return deal

    @transaction.atomic
    def update(self, deal, data):
        self._require_company()
        if deal.company_id != self.company.id:
            raise DealServiceError("Deal was not found.", status_code=404)
        data = dict(data)
        before = self._snapshot(deal)
        previous_stage = deal.pipeline_stage if deal.pipeline_stage_id else None

        if "client" in data and data["client"] is not None:
            self._same_company(data["client"], "client")
            deal.client = data["client"]
        if "employee" in data:
            self._check_employee(data["employee"], deal.employee_id)
            deal.employee = data["employee"]
        for field in ("unit", "project", "started_by", "closed_by"):
            if field in data:
                self._same_company(data[field], field)
                setattr(deal, field, data[field])
        if "lost_reason" in data and data["lost_reason"] is not None:
            self._same_company(data["lost_reason"], "lost_reason")

        scalar = (
            "title",
            "payment_method",
            "status",
            "reminder_date",
            "start_date",
            "closed_date",
            "discount_percentage",
            "discount_amount",
            "sales_commission_percentage",
            "sales_commission_amount",
            "description",
            "probability",
            "expected_close_date",
            "currency",
            "lost_note",
        )
        for field in scalar:
            if field in data:
                setattr(deal, field, data[field])
        if "lost_reason" in data:
            deal.lost_reason = data["lost_reason"]

        has_items = deal.line_items.exists()
        if "value" in data and not has_items:
            deal.value = data["value"]

        stage_requested = "pipeline_stage" in data or "stage" in data
        stage = None
        if stage_requested:
            stage = self._resolve_stage(data, current=deal.pipeline_stage)
        if stage is not None and (deal.pipeline_stage_id != stage.id):
            self._apply_stage_fields(
                deal,
                stage,
                lost_reason=data["lost_reason"] if "lost_reason" in data else _MISSING,
                lost_note=data["lost_note"] if "lost_note" in data else _MISSING,
                require_lost_reason=False,
            )
        self._currency_default(deal)
        deal.save()
        if has_items and any(
            key in data for key in ("discount_percentage", "discount_amount")
        ):
            recalculate_deal_value(deal)
            deal.refresh_from_db()
        self._record_changes(deal, before)
        if stage is not None and (previous_stage is None or previous_stage.id != deal.pipeline_stage_id):
            self._record_stage_event(deal, previous_stage)
        return deal

    @transaction.atomic
    def move_to_stage(self, deal, stage, *, lost_reason=None, lost_note=None, reason="", require_lost_reason=True):
        self._require_company()
        if deal.company_id != self.company.id:
            raise DealServiceError("Deal was not found.", status_code=404)
        if stage is None or stage.pipeline.company_id != self.company.id:
            raise DealServiceError("Stage does not belong to this company.", field="pipeline_stage")
        previous = deal.pipeline_stage if deal.pipeline_stage_id else None
        if previous is not None and previous.id == stage.id:
            return deal
        self._apply_stage_fields(
            deal,
            stage,
            lost_reason=lost_reason if lost_reason is not None else _MISSING,
            lost_note=lost_note if lost_note is not None else _MISSING,
            require_lost_reason=require_lost_reason,
        )
        deal.save()
        self._record_stage_event(deal, previous, reason=reason or "")
        return deal

    def _stage_of_type(self, deal, stage_type, preferred=None):
        if preferred is not None:
            if preferred.stage_type != stage_type:
                raise DealServiceError("That stage is not the right outcome.", field="pipeline_stage")
            return preferred
        pipeline = deal.pipeline or resolve_legacy_stage(self.company, "in_progress")
        pipeline = getattr(pipeline, "pipeline", pipeline)
        if pipeline is None:
            raise DealServiceError("This company has no deal pipeline.")
        stage = (
            pipeline.stages.filter(is_active=True, stage_type=stage_type).order_by("order", "id").first()
        )
        if stage_type == "open":
            keyed = pipeline.stages.filter(is_active=True, system_key="in_progress").first()
            stage = keyed or stage
        if stage is None:
            raise DealServiceError("No matching stage exists on this pipeline.", field="pipeline_stage")
        return stage

    @transaction.atomic
    def mark_won(self, deal, *, stage=None):
        target = self._stage_of_type(deal, "won", stage)
        return self.move_to_stage(deal, target, require_lost_reason=False)

    @transaction.atomic
    def mark_lost(self, deal, *, lost_reason, lost_note="", stage=None):
        if lost_reason is None:
            raise DealServiceError("A lost reason is required.", field="lost_reason")
        self._same_company(lost_reason, "lost_reason")
        target = self._stage_of_type(deal, "lost", stage)
        return self.move_to_stage(
            deal,
            target,
            lost_reason=lost_reason,
            lost_note=lost_note,
            require_lost_reason=True,
        )

    @transaction.atomic
    def reopen(self, deal, *, stage=None):
        target = stage or self._stage_of_type(deal, "open")
        if target.stage_type != "open":
            raise DealServiceError("Reopen needs an open stage.", field="pipeline_stage")
        return self.move_to_stage(deal, target, require_lost_reason=False)

    @transaction.atomic
    def assign(self, deal, employee):
        return self.update(deal, {"employee": employee})

    @transaction.atomic
    def add_note(self, deal, text):
        if deal.company_id != self.company.id:
            raise DealServiceError("Deal was not found.", status_code=404)
        body = (text or "").strip()
        if not body:
            raise DealServiceError("Note text is required.", field="text")
        return record_deal_event(deal, "note", new_value=body, created_by=self.actor)

    def _build_line_item(self, deal, data, item=None):
        item_type = data.get("item_type") or (item.item_type if item else None)
        strategy = strategy_for(item_type)
        resolved = dict(data)
        if item is not None:
            for attr in ("product", "service", "service_package", "unit", "name", "unit_price"):
                resolved.setdefault(attr, getattr(item, attr))
        name, price, fks = strategy.resolve(self.company, resolved)
        quantity = data.get("quantity", item.quantity if item else 1)
        discount = data.get("discount_percentage", item.discount_percentage if item else 0)
        payload = {
            "item_type": item_type,
            "name": data.get("name") or name,
            "unit_price": price if "unit_price" in data or item is None else money(data.get("unit_price", price)),
            "quantity": quantity or 1,
            "discount_percentage": discount or 0,
            "product": None,
            "service": None,
            "service_package": None,
            "unit": None,
        }
        payload.update(fks)
        if "unit_price" in data:
            payload["unit_price"] = money(data["unit_price"])
        payload["line_total"] = line_total(
            payload["unit_price"], payload["quantity"], payload["discount_percentage"]
        )
        return payload

    @transaction.atomic
    def add_line_item(self, deal, data):
        if deal.company_id != self.company.id:
            raise DealServiceError("Deal was not found.", status_code=404)
        payload = self._build_line_item(deal, data)
        position = deal.line_items.count()
        item = DealLineItem.objects.create(deal=deal, position=position, **payload)
        recalculate_deal_value(deal)
        record_deal_event(
            deal,
            "line_item_change",
            new_value=item.name,
            metadata={"action": "added", "line_item_id": item.id},
            created_by=self.actor,
        )
        return item

    @transaction.atomic
    def update_line_item(self, deal, item, data):
        if item.deal_id != deal.id:
            raise DealServiceError("Line item was not found.", status_code=404)
        payload = self._build_line_item(deal, data, item=item)
        for key, value in payload.items():
            setattr(item, key, value)
        item.save()
        recalculate_deal_value(deal)
        record_deal_event(
            deal,
            "line_item_change",
            new_value=item.name,
            metadata={"action": "updated", "line_item_id": item.id},
            created_by=self.actor,
        )
        return item

    @transaction.atomic
    def delete_line_item(self, deal, item):
        if item.deal_id != deal.id:
            raise DealServiceError("Line item was not found.", status_code=404)
        name = item.name
        item_id = item.id
        item.delete()
        recalculate_deal_value(deal)
        record_deal_event(
            deal,
            "line_item_change",
            old_value=name,
            metadata={"action": "removed", "line_item_id": item_id},
            created_by=self.actor,
        )

    @transaction.atomic
    def bulk(self, deals, action, *, employee=None, stage=None, lost_reason=None, lost_note=""):
        updated = 0
        failed = []
        for deal in deals:
            try:
                if action == "delete":
                    deal.delete()
                elif action == "assign":
                    self.assign(deal, employee)
                elif action == "move":
                    self.move_to_stage(
                        deal,
                        stage,
                        lost_reason=lost_reason,
                        lost_note=lost_note,
                        require_lost_reason=stage is not None and stage.stage_type == "lost",
                    )
                else:
                    raise DealServiceError("Unknown bulk action.", field="action")
                updated += 1
            except DealServiceError as exc:
                failed.append({"id": deal.id, "error": exc.message})
        return {"updated": updated, "failed": failed}

    @transaction.atomic
    def rehome_stage(self, stage, target):
        """Move every deal off `stage` before it is deleted."""
        if target is None or target.pipeline.company_id != self.company.id:
            raise DealServiceError("Choose a stage to move existing deals to.", field="move_to")
        if target.id == stage.id:
            raise DealServiceError("Pick a different stage.", field="move_to")
        for deal in Deal.objects.filter(pipeline_stage=stage, company=self.company):
            self.move_to_stage(deal, target, require_lost_reason=False)
