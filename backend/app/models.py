import enum
import uuid
from datetime import datetime, timedelta
from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base


def uid() -> str:
    return str(uuid.uuid4())


class Role(str, enum.Enum):
    admin = "admin"
    owner = "owner"
    customer = "customer"


class PaymentStatus(str, enum.Enum):
    demo_unpaid = "DEMO_UNPAID"
    unpaid = "UNPAID"
    paid = "PAID"


class OrderStatus(str, enum.Enum):
    uploaded = "UPLOADED"
    awaiting_payment = "AWAITING_PAYMENT"
    queued = "QUEUED"
    printing = "PRINTING"
    completed = "COMPLETED"
    failed = "FAILED"
    blocked_payment_required = "BLOCKED_PAYMENT_REQUIRED"


class JobStatus(str, enum.Enum):
    queued = "QUEUED"
    claimed = "CLAIMED"
    printing = "PRINTING"
    submitted_to_spooler = "SUBMITTED_TO_SPOOLER"
    completed = "COMPLETED"
    failed = "FAILED"


class DeviceStatus(str, enum.Enum):
    pending = "PENDING"
    active = "ACTIVE"
    revoked = "REVOKED"


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String)
    role: Mapped[Role] = mapped_column(Enum(Role))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    name: Mapped[str] = mapped_column(String, default="", server_default="")
    verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")


class Shop(Base):
    __tablename__ = "shops"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String)
    location: Mapped[str] = mapped_column(String, default="")
    slug: Mapped[str] = mapped_column(String, unique=True, index=True)
    demo_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    owner = relationship("User")
    retention_hours: Mapped[int] = mapped_column(Integer, default=6, server_default="6")


class PricingRule(Base):
    __tablename__ = "pricing_rules"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    shop_id: Mapped[str] = mapped_column(ForeignKey("shops.id"), index=True)
    bw_page_price: Mapped[float] = mapped_column(Numeric(10, 2), default=2)
    color_page_price: Mapped[float] = mapped_column(Numeric(10, 2), default=10)
    base_price: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    minimum_order: Mapped[float] = mapped_column(Numeric(10, 2), default=0)


class ActivationCode(Base):
    __tablename__ = "activation_codes"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    shop_id: Mapped[str] = mapped_column(ForeignKey("shops.id"), index=True)
    code: Mapped[str] = mapped_column(String, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.utcnow() + timedelta(hours=1))
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ShopDevice(Base):
    __tablename__ = "shop_devices"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    shop_id: Mapped[str] = mapped_column(ForeignKey("shops.id"), index=True)
    name: Mapped[str] = mapped_column(String)
    credential: Mapped[str] = mapped_column(String, unique=True, index=True)
    status: Mapped[DeviceStatus] = mapped_column(Enum(DeviceStatus), default=DeviceStatus.active)
    selected_printer: Mapped[str | None] = mapped_column(String, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Printer(Base):
    __tablename__ = "printers"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    shop_id: Mapped[str] = mapped_column(ForeignKey("shops.id"), index=True)
    device_id: Mapped[str | None] = mapped_column(ForeignKey("shop_devices.id"), nullable=True)
    name: Mapped[str] = mapped_column(String)
    supports_color: Mapped[bool] = mapped_column(Boolean, default=True)
    supports_duplex: Mapped[bool] = mapped_column(Boolean, default=False)
    paper_sizes: Mapped[str] = mapped_column(String, default="A4")


class UploadedDocument(Base):
    __tablename__ = "uploaded_documents"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    shop_id: Mapped[str] = mapped_column(ForeignKey("shops.id"), index=True)
    storage_path: Mapped[str] = mapped_column(String)
    original_name: Mapped[str] = mapped_column(String)
    mime_type: Mapped[str] = mapped_column(String)
    page_count: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.utcnow() + timedelta(hours=6))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    customer_hash: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    cleanup_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class PrintOrder(Base):
    __tablename__ = "print_orders"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    order_number: Mapped[str] = mapped_column(String, unique=True, index=True)
    shop_id: Mapped[str] = mapped_column(ForeignKey("shops.id"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("uploaded_documents.id"))
    color_mode: Mapped[str] = mapped_column(String)
    paper_size: Mapped[str] = mapped_column(String)
    duplex: Mapped[bool] = mapped_column(Boolean, default=False)
    page_range: Mapped[str] = mapped_column(String, default="all")
    copies: Mapped[int] = mapped_column(Integer, default=1)
    page_count_charged: Mapped[int] = mapped_column(Integer)
    total_amount: Mapped[float] = mapped_column(Numeric(10, 2))
    payment_status: Mapped[PaymentStatus] = mapped_column(Enum(PaymentStatus), default=PaymentStatus.demo_unpaid)
    status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus), default=OrderStatus.uploaded)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    document = relationship("UploadedDocument")
    amount_paise: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    currency: Mapped[str] = mapped_column(String, default="INR", server_default="INR")
    payment_mode: Mapped[str] = mapped_column(String, default="demo", server_default="demo")


class PrintJob(Base):
    __tablename__ = "print_jobs"
    __table_args__ = (UniqueConstraint("order_id", name="uq_one_job_per_order"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    order_id: Mapped[str] = mapped_column(ForeignKey("print_orders.id"), index=True)
    shop_id: Mapped[str] = mapped_column(ForeignKey("shops.id"), index=True)
    device_id: Mapped[str | None] = mapped_column(ForeignKey("shop_devices.id"), nullable=True)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.queued)
    idempotency_key: Mapped[str] = mapped_column(String, unique=True, index=True)
    adapter_mode: Mapped[str | None] = mapped_column(String, nullable=True)
    spooler_job_id: Mapped[str | None] = mapped_column(String, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    order = relationship("PrintOrder")
    attempt: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    order_id: Mapped[str] = mapped_column(ForeignKey("print_orders.id"), index=True, unique=True)
    provider: Mapped[str] = mapped_column(String, default="NOT_CONFIGURED")
    status: Mapped[str] = mapped_column(String, default="NOT_ATTEMPTED")
    amount: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    amount_paise: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    currency: Mapped[str] = mapped_column(String, default="INR", server_default="INR")
    provider_order_id: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    provider_payment_id: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    mode: Mapped[str] = mapped_column(String, default="test", server_default="test")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Refund(Base):
    __tablename__ = "refunds"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    payment_id: Mapped[str] = mapped_column(ForeignKey("payments.id"), index=True)
    status: Mapped[str] = mapped_column(String, default="NOT_APPLICABLE")
    provider_refund_id: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    amount_paise: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    actor: Mapped[str] = mapped_column(String)
    action: Mapped[str] = mapped_column(String)
    entity_id: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    csrf: Mapped[str] = mapped_column(String)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class EmailChallenge(Base):
    __tablename__ = "email_challenges"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    purpose: Mapped[str] = mapped_column(String)
    code_hash: Mapped[str] = mapped_column(String)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    event: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RateLimit(Base):
    __tablename__ = "rate_limits"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    window_start: Mapped[datetime] = mapped_column(DateTime)

