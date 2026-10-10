import os
import re
import secrets
import shutil
import hashlib
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime
from pathlib import Path
from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session
from .config import Settings
from .models import (
    ActivationCode, AuditLog, DeviceStatus, JobStatus, OrderStatus, PaymentStatus,
    PricingRule, PrintJob, PrintOrder, ShopDevice, UploadedDocument, Printer
)


def audit(db: Session, actor: str, action: str, entity_id: str) -> None:
    db.add(AuditLog(actor=actor, action=action, entity_id=entity_id))


def parse_page_range(expr: str, total_pages: int) -> int:
    if expr in ("", "all", None):
        return total_pages
    pages: set[int] = set()
    for part in str(expr).split(","):
        part = part.strip()
        if re.fullmatch(r"\d+", part):
            pages.add(int(part))
        elif re.fullmatch(r"\d+-\d+", part):
            start, end = [int(x) for x in part.split("-")]
            if start > end:
                raise HTTPException(400, "Invalid page range")
            pages.update(range(start, end + 1))
        else:
            raise HTTPException(400, "Invalid page range")
    if not pages or min(pages) < 1 or max(pages) > total_pages:
        raise HTTPException(400, "Page range outside document")
    return len(pages)


def save_pdf_upload(db: Session, settings: Settings, shop_id: str, upload: UploadFile) -> UploadedDocument:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise HTTPException(500, "PDF validation dependency pypdf is not installed") from exc
    if upload.content_type not in {"application/pdf", "application/octet-stream"}:
        raise HTTPException(400, "Only PDF uploads are supported")
    storage = Path(settings.storage_dir) / "documents"
    storage.mkdir(parents=True, exist_ok=True)
    doc_id = secrets.token_hex(16)
    path = storage / f"{doc_id}.pdf"
    size = 0
    try:
        with path.open("wb") as out:
            while chunk := upload.file.read(65536):
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise HTTPException(413, "PDF is larger than 25 MB")
                out.write(chunk)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    try:
        reader = PdfReader(str(path))
        pages = len(reader.pages)
        if reader.is_encrypted or not 1 <= pages <= 2000:
            raise ValueError("Encrypted or oversized document")
    except Exception as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(400, "Invalid or unreadable PDF") from exc
    doc = UploadedDocument(
        id=doc_id,
        shop_id=shop_id,
        storage_path=str(path),
        original_name=Path(upload.filename or "upload.pdf").name,
        mime_type="application/pdf",
        page_count=pages,
    )
    db.add(doc)
    audit(db, "customer", "document.uploaded", doc.id)
    return doc


def quote(db: Session, shop_id: str, document_id: str, opts: dict) -> dict:
    doc = db.get(UploadedDocument, document_id)
    if not doc or doc.shop_id != shop_id or doc.deleted_at or doc.expires_at < datetime.utcnow():
        raise HTTPException(404, "Document not found or expired")
    pricing = db.query(PricingRule).filter(PricingRule.shop_id == shop_id).first()
    if not pricing:
        raise HTTPException(400, "Shop pricing is not configured")
    copies = opts.get("copies", 1)
    if isinstance(copies, bool) or not isinstance(copies, int) or not 1 <= copies <= 100 or opts.get("color_mode", "bw") not in {"bw", "color"} or opts.get("paper_size", "A4") not in {"A4", "A3"} or not isinstance(opts.get("duplex", False), bool):
        raise HTTPException(400, "Invalid print options")
    printers = db.query(Printer).join(ShopDevice, Printer.device_id == ShopDevice.id).filter(Printer.shop_id == shop_id, ShopDevice.status == DeviceStatus.active, ShopDevice.selected_printer == Printer.name).all()
    if printers and not any(opts.get('paper_size', 'A4') in p.paper_sizes.split(',') and (opts.get('color_mode', 'bw') != 'color' or p.supports_color) and (not opts.get('duplex', False) or p.supports_duplex) for p in printers):
        raise HTTPException(400, 'These settings are not supported by the connected shop printers')
    pages = parse_page_range(opts.get("page_range", "all"), doc.page_count)
    rate = Decimal(str(pricing.color_page_price if opts.get("color_mode") == "color" else pricing.bw_page_price))
    subtotal = rate * pages * copies
    total = max(Decimal(str(pricing.minimum_order)), Decimal(str(pricing.base_price)) + subtotal)
    return {"pages": pages, "copies": copies, "rate_paise": int((rate * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)), "subtotal_paise": int(subtotal * 100), "base_paise": int(Decimal(str(pricing.base_price)) * 100), "amount_paise": int((total * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)), "currency": "INR"}


def create_order(db: Session, settings: Settings, shop_id: str, document_id: str, opts: dict) -> PrintOrder:
    doc = db.get(UploadedDocument, document_id)
    if not doc or doc.shop_id != shop_id or doc.deleted_at:
        raise HTTPException(404, "Document not found for this shop")
    pricing = db.query(PricingRule).filter(PricingRule.shop_id == shop_id).first()
    if not pricing:
        raise HTTPException(400, "Shop pricing is not configured")
    color_mode = opts.get("color_mode", "bw")
    copies = int(opts.get("copies", 1))
    if color_mode not in {"bw", "color"} or copies < 1 or copies > 100:
        raise HTTPException(400, "Invalid print options")
    calculated = quote(db, shop_id, document_id, opts)
    charged_pages = calculated["pages"]
    order = PrintOrder(
        order_number=f"PS-{secrets.token_hex(3).upper()}",
        shop_id=shop_id,
        document_id=document_id,
        color_mode=color_mode,
        paper_size=opts.get("paper_size", "A4"),
        duplex=bool(opts.get("duplex", False)),
        page_range=opts.get("page_range", "all"),
        copies=copies,
        page_count_charged=charged_pages,
        total_amount=Decimal(calculated["amount_paise"]) / 100,
        amount_paise=calculated["amount_paise"],
        payment_mode=settings.razorpay_mode if settings.razorpay_mode != "disabled" else "demo",
        payment_status=PaymentStatus.unpaid if settings.razorpay_mode != "disabled" else PaymentStatus.demo_unpaid,
        status=OrderStatus.awaiting_payment,
    )
    db.add(order)
    db.flush()
    if settings.razorpay_mode == "disabled":
        queue_order_if_allowed(db, settings, order)
    audit(db, "customer", "order.created", order.id)
    return order


def queue_order_if_allowed(db: Session, settings: Settings, order: PrintOrder) -> PrintJob | None:
    if order.payment_status != PaymentStatus.paid and (not settings.unpaid_printing_allowed or order.payment_mode != "demo"):
        order.status = OrderStatus.blocked_payment_required
        audit(db, "system", "order.blocked_payment_required", order.id)
        return None
    existing = db.query(PrintJob).filter(PrintJob.order_id == order.id).first()
    if existing:
        return existing
    job = PrintJob(
        order_id=order.id,
        shop_id=order.shop_id,
        status=JobStatus.queued,
        idempotency_key=f"print:{order.id}",
    )
    order.status = OrderStatus.queued
    db.add(job)
    audit(db, "system", "print_job.queued", order.id)
    return job


def activate_device(db: Session, code: str, name: str) -> ShopDevice:
    activation = db.query(ActivationCode).filter(ActivationCode.code == code).with_for_update().first()
    if not activation or activation.used_at or activation.expires_at < datetime.utcnow():
        raise HTTPException(400, "Activation code is invalid, expired, or already used")
    activation.used_at = datetime.utcnow()
    raw = secrets.token_urlsafe(32)
    device = ShopDevice(shop_id=activation.shop_id, name=name, credential=hashlib.sha256(raw.encode()).hexdigest())
    device.raw_credential = raw
    db.add(device)
    audit(db, "agent", "device.activated", activation.shop_id)
    return device


def authenticate_device(db: Session, credential: str) -> ShopDevice:
    device = db.query(ShopDevice).filter(ShopDevice.credential == hashlib.sha256(credential.encode()).hexdigest()).first()
    if not device or device.status != DeviceStatus.active:
        raise HTTPException(401, "Invalid or revoked device credential")
    device.last_seen_at = datetime.utcnow()
    return device


def cleanup_expired(db: Session) -> dict:
    from .models import AuthSession, EmailChallenge, RateLimit
    deleted = 0
    failures = 0
    now = datetime.utcnow()
    db.query(AuthSession).filter(AuthSession.expires_at < now).delete()
    db.query(EmailChallenge).filter(EmailChallenge.expires_at < now).delete()
    from datetime import timedelta
    db.query(RateLimit).filter(RateLimit.window_start < now - timedelta(days=1)).delete()
    docs = db.query(UploadedDocument).filter(UploadedDocument.deleted_at.is_(None), UploadedDocument.expires_at < now).all()
    for doc in docs:
        active = db.query(PrintJob).join(PrintOrder, PrintJob.order_id == PrintOrder.id).filter(PrintOrder.document_id == doc.id, PrintJob.status.in_([JobStatus.claimed, JobStatus.printing])).first()
        if active:
            continue
        for order in db.query(PrintOrder).filter_by(document_id=doc.id).all():
            if order.status in {OrderStatus.awaiting_payment, OrderStatus.blocked_payment_required, OrderStatus.queued}:
                order.status = OrderStatus.failed
                job = db.query(PrintJob).filter_by(order_id=order.id).first()
                if job:
                    job.status = JobStatus.failed
                    job.error = 'Document expired before submission'
        try:
            if os.path.exists(doc.storage_path):
                os.remove(doc.storage_path)
            doc.deleted_at = now
            doc.cleanup_error = None
            deleted += 1
        except OSError as exc:
            doc.cleanup_error = str(exc)
            failures += 1
    audit(db, "admin", "cleanup.expired_documents", f"deleted={deleted};failures={failures}")
    return {"deleted": deleted, "failures": failures}

