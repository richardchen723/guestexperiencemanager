"""Additive procurement schema. No existing ticket or finance tables are modified."""

from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, Column, Date, DateTime, ForeignKey, Integer, JSON, LargeBinary,
    MetaData, Numeric, String, Text, UniqueConstraint, text,
)
from sqlalchemy.orm import declarative_base, deferred, relationship

from dashboard.auth.models import get_session


Base = declarative_base(metadata=MetaData(schema="procurement"))


class Staff(Base):
    __tablename__ = "staff"
    id = Column(Integer, primary_key=True)
    version = Column(Integer, nullable=False, default=1)
    name = Column(String(160), nullable=False)
    phone = Column(String(80))
    user_id = Column(Integer, unique=True)
    listing_ids = Column(JSON, nullable=False, default=list)
    active = Column(Boolean, nullable=False, default=True)


class Location(Base):
    __tablename__ = "locations"
    id = Column(Integer, primary_key=True)
    label = Column(String(160), nullable=False)
    kind = Column(String(24), nullable=False)
    address = Column(Text, nullable=False)
    instructions = Column(Text)
    staff_id = Column(Integer, ForeignKey("procurement.staff.id"))
    listing_id = Column(Integer)
    active = Column(Boolean, nullable=False, default=True)


class CatalogItem(Base):
    __tablename__ = "catalog"
    id = Column(Integer, primary_key=True)
    name = Column(String(180), nullable=False, unique=True)
    category = Column(String(60), nullable=False, default="supplies")
    unit = Column(String(40), nullable=False)
    pack_description = Column(String(160))
    active = Column(Boolean, nullable=False, default=True)


class Ticket(Base):
    __tablename__ = "requests"
    id = Column(Integer, primary_key=True)
    version = Column(Integer, nullable=False, default=1)
    title = Column(String(240), nullable=False)
    listing_id = Column(Integer, index=True)
    property_snapshot = Column(JSON, nullable=False, default=dict)
    property_snapshots = Column(JSON, nullable=False, default=list)
    storage_location_id = Column(Integer, ForeignKey("procurement.locations.id"))
    requester_staff_id = Column(Integer, ForeignKey("procurement.staff.id"))
    requester_name = Column(String(160), nullable=False)
    owner_user_id = Column(Integer, index=True)
    priority = Column(String(20), nullable=False, default="normal")
    required_by = Column(DateTime, index=True)
    response_due_at = Column(DateTime)
    reviewed_at = Column(DateTime)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_by = Column(String(180), nullable=False)
    cancelled_reason = Column(Text)
    items = relationship("RequestItem", back_populates="ticket", order_by="RequestItem.id")
    messages = relationship("SourceMessage", back_populates="ticket", order_by="SourceMessage.id")


class RequestItem(Base):
    __tablename__ = "request_items"
    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, ForeignKey("procurement.requests.id"), nullable=False, index=True)
    listing_id = Column(Integer, index=True)
    catalog_id = Column(Integer, ForeignKey("procurement.catalog.id"))
    name = Column(String(180), nullable=False)
    category = Column(String(60), nullable=False, default="supplies")
    unit = Column(String(40), nullable=False)
    quantity = Column(Numeric(12, 3))
    cancelled_quantity = Column(Numeric(12, 3), nullable=False, default=0)
    cancellation_reason = Column(Text)
    ticket = relationship("Ticket", back_populates="items")
    allocations = relationship("Allocation", back_populates="item")


class SourceMessage(Base):
    __tablename__ = "source_messages"
    __table_args__ = (UniqueConstraint("source", "message_id", "ticket_id", name="uq_procurement_source"),)
    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, ForeignKey("procurement.requests.id"), nullable=False)
    source = Column(String(80), nullable=False)
    message_id = Column(String(240))
    body = Column(Text, nullable=False)
    source_url = Column(Text)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    ticket = relationship("Ticket", back_populates="messages")


class PurchaseOrder(Base):
    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("vendor_key", "reference", name="uq_procurement_vendor_order"),)
    id = Column(Integer, primary_key=True)
    version = Column(Integer, nullable=False, default=1)
    purchase_revision = Column(Integer, nullable=False, default=1)
    vendor = Column(String(160), nullable=False)
    vendor_key = Column(String(160), nullable=False)
    reference = Column(String(180))
    currency = Column(String(3), nullable=False, default="USD")
    destination = Column(JSON, nullable=False)
    state = Column(String(20), nullable=False, default="draft")
    tax_cents = Column(BigInteger, nullable=False, default=0)
    shipping_cents = Column(BigInteger, nullable=False, default=0)
    discount_cents = Column(BigInteger, nullable=False, default=0)
    responsibility = Column(String(30), nullable=False, default="unassigned")
    approval = Column(JSON)
    purchased_on = Column(Date, index=True)
    placed_at = Column(DateTime)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    reconciled_at = Column(DateTime)
    actual_charged_cents = Column(BigInteger)
    notes = Column(Text)
    lines = relationship("OrderLine", back_populates="order", cascade="all, delete-orphan", order_by="OrderLine.id")
    shipments = relationship("Shipment", back_populates="order", order_by="Shipment.id")
    attachments = relationship("Attachment", back_populates="order", order_by="Attachment.id")


class OrderLine(Base):
    __tablename__ = "order_lines"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("procurement.orders.id"), nullable=False, index=True)
    catalog_id = Column(Integer, ForeignKey("procurement.catalog.id"))
    name = Column(String(180), nullable=False)
    unit = Column(String(40), nullable=False)
    quantity = Column(Numeric(12, 3), nullable=False)
    pack_size = Column(Numeric(12, 3), nullable=False, default=1)
    unit_price_cents = Column(BigInteger, nullable=False)
    substitution_reason = Column(Text)
    destination = Column(JSON)
    order = relationship("PurchaseOrder", back_populates="lines")
    allocations = relationship("Allocation", back_populates="line", cascade="all, delete-orphan")
    refunds = relationship("Refund", back_populates="line")


class Allocation(Base):
    __tablename__ = "allocations"
    id = Column(Integer, primary_key=True)
    line_id = Column(Integer, ForeignKey("procurement.order_lines.id"), nullable=False, index=True)
    item_id = Column(Integer, ForeignKey("procurement.request_items.id"), nullable=False, index=True)
    quantity = Column(Numeric(12, 3), nullable=False)
    problem_quantity = Column(Numeric(12, 3), nullable=False, default=0)
    excluded_delivered_quantity = Column(Numeric(12, 3), nullable=False, default=0)
    line = relationship("OrderLine", back_populates="allocations")
    item = relationship("RequestItem", back_populates="allocations")
    confirmations = relationship("Confirmation", back_populates="allocation", order_by="Confirmation.id")


class Shipment(Base):
    __tablename__ = "shipments"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("procurement.orders.id"), nullable=False)
    carrier = Column(String(120))
    tracking = Column(String(240))
    expected_at = Column(DateTime)
    delivered_at = Column(DateTime)
    cancelled_reason = Column(Text)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    order = relationship("PurchaseOrder", back_populates="shipments")
    lines = relationship("ShipmentLine", cascade="all, delete-orphan")


class ShipmentLine(Base):
    __tablename__ = "shipment_lines"
    id = Column(Integer, primary_key=True)
    shipment_id = Column(Integer, ForeignKey("procurement.shipments.id"), nullable=False)
    line_id = Column(Integer, ForeignKey("procurement.order_lines.id"), nullable=False)
    quantity = Column(Numeric(12, 3), nullable=False)


class Confirmation(Base):
    __tablename__ = "confirmations"
    id = Column(Integer, primary_key=True)
    allocation_id = Column(Integer, ForeignKey("procurement.allocations.id"), nullable=False)
    listing_id = Column(Integer, nullable=False, index=True)
    property_name = Column(String(240), nullable=False)
    staff_id = Column(Integer, ForeignKey("procurement.staff.id"), nullable=False)
    quantity = Column(Numeric(12, 3), nullable=False)  # Negative for an explicitly recorded return.
    note = Column(Text, nullable=False)
    evidence = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    allocation = relationship("Allocation", back_populates="confirmations")


class Refund(Base):
    __tablename__ = "refunds"
    id = Column(Integer, primary_key=True)
    line_id = Column(Integer, ForeignKey("procurement.order_lines.id"), nullable=False)
    amount_cents = Column(BigInteger, nullable=False)
    reason = Column(Text, nullable=False)
    refunded_on = Column(Date, nullable=False, index=True)
    line = relationship("OrderLine", back_populates="refunds")


class Attachment(Base):
    __tablename__ = "attachments"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("procurement.orders.id"), nullable=False)
    name = Column(String(240), nullable=False)
    mime_type = Column(String(80), nullable=False)
    content = deferred(Column(LargeBinary, nullable=False))
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    order = relationship("PurchaseOrder", back_populates="attachments")


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, index=True)
    order_id = Column(Integer, index=True)
    actor = Column(String(180), nullable=False)
    action = Column(String(80), nullable=False)
    detail = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class Command(Base):
    __tablename__ = "commands"
    key = Column(String(180), primary_key=True)
    fingerprint = Column(String(64), nullable=False)
    result = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


def init_procurement_database(engine=None):
    if engine is None:
        from dashboard.auth.models import get_engine
        from dashboard.config import USERS_DATABASE_PATH
        engine = get_engine(USERS_DATABASE_PATH)
    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            connection.execute(text("CREATE SCHEMA IF NOT EXISTS procurement"))
    Base.metadata.create_all(engine)
    from sqlalchemy import inspect
    schema = "procurement" if engine.dialect.name == "postgresql" else None
    for table_name, name, sql_type in (("requests", "property_snapshots", "JSON NOT NULL DEFAULT '[]'"),
                                        ("request_items", "listing_id", "INTEGER"),
                                        ("order_lines", "destination", "JSON")):
        columns = {c["name"] for c in inspect(engine).get_columns(table_name, schema=schema)}
        if name not in columns:
            table = f"procurement.{table_name}" if schema else table_name
            with engine.begin() as connection:
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"))
    # Old allocations retain their item IDs and all receipt/cost history. The
    # parent property remains a fallback while older workers finish requests.
    prefix = "procurement." if schema else ""
    with engine.begin() as connection:
        connection.execute(text(f"UPDATE {prefix}request_items SET listing_id = "
                                f"(SELECT listing_id FROM {prefix}requests WHERE id = {prefix}request_items.ticket_id) "
                                f"WHERE listing_id IS NULL AND EXISTS (SELECT 1 FROM {prefix}requests "
                                f"WHERE id = {prefix}request_items.ticket_id AND listing_id IS NOT NULL)"))
    columns = {c["name"] for c in inspect(engine).get_columns("staff", schema=schema)}
    if "version" not in columns:
        table = "procurement.staff" if schema else "staff"
        with engine.begin() as connection:
            connection.execute(text(f"ALTER TABLE {table} ADD COLUMN version INTEGER NOT NULL DEFAULT 1"))
    columns = {c["name"] for c in inspect(engine).get_columns("orders", schema=schema)}
    if "purchase_revision" not in columns:
        table = "procurement.orders" if schema else "orders"
        with engine.begin() as connection:
            connection.execute(text(f"ALTER TABLE {table} ADD COLUMN purchase_revision INTEGER NOT NULL DEFAULT 1"))
    columns = {c["name"] for c in inspect(engine).get_columns("allocations", schema=schema)}
    for name in ("problem_quantity", "excluded_delivered_quantity"):
        if name not in columns:
            table = "procurement.allocations" if schema else "allocations"
            with engine.begin() as connection:
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} NUMERIC(12,3) NOT NULL DEFAULT 0"))
    columns = {c["name"] for c in inspect(engine).get_columns("shipments", schema=schema)}
    if "cancelled_reason" not in columns:
        table = "procurement.shipments" if schema else "shipments"
        with engine.begin() as connection:
            connection.execute(text(f"ALTER TABLE {table} ADD COLUMN cancelled_reason TEXT"))
    columns = {c["name"] for c in inspect(engine).get_columns("order_lines", schema=schema)}
    if "pack_size" not in columns:
        table = "procurement.order_lines" if schema else "order_lines"
        with engine.begin() as connection:
            connection.execute(text(f"ALTER TABLE {table} ADD COLUMN pack_size NUMERIC(12,3) NOT NULL DEFAULT 1"))
    if engine.dialect.name == "postgresql":
        for table_name, names in (("orders", ("tax_cents", "shipping_cents", "discount_cents", "actual_charged_cents")),
                                  ("order_lines", ("unit_price_cents",)), ("refunds", ("amount_cents",))):
            column_types = {c["name"]: c["type"] for c in inspect(engine).get_columns(table_name, schema="procurement")}
            for name in names:
                if not isinstance(column_types[name], BigInteger):
                    with engine.begin() as connection:
                        connection.execute(text(f"ALTER TABLE procurement.{table_name} ALTER COLUMN {name} TYPE BIGINT"))
    for table in Base.metadata.tables.values():
        for index in table.indexes:
            index.create(engine, checkfirst=True)
