import hashlib
import hmac
import json
from datetime import datetime
import requests
from fastapi import HTTPException
from .models import Payment, PaymentStatus, PrintOrder, Refund, WebhookEvent
from .services import audit, queue_order_if_allowed


def gateway(settings, method, path, body=None):
    if not settings.checkout_ready:
        raise HTTPException(503, "Razorpay credentials do not match the configured payment mode")
    try:
        response = requests.request(method, "https://api.razorpay.com/v1" + path, auth=(settings.razorpay_key_id, settings.razorpay_key_secret), json=body, timeout=20)
        response.raise_for_status()
        return response.json()
    except requests.RequestException:
        raise HTTPException(502, "Payment provider unavailable. Check payment status before retrying.")


def create_payment(db, settings, order):
    order = db.query(PrintOrder).filter_by(id=order.id).with_for_update().one()
    if not settings.checkout_ready:
        raise HTTPException(503, "Payment checkout is not configured")
    if order.payment_mode != settings.razorpay_mode:
        raise HTTPException(409, "Payment mode changed. Upload your document and create a new order.")
    if order.payment_mode == "demo" or order.payment_status == PaymentStatus.paid:
        raise HTTPException(409, "This order does not need checkout")
    if order.document.deleted_at or order.document.expires_at < datetime.utcnow():
        raise HTTPException(410, "Document expired. Upload it again before paying.")
    existing = db.query(Payment).filter_by(order_id=order.id).first()
    if existing:
        if not existing.provider_order_id:
            raise HTTPException(409, "Payment creation needs reconciliation before another attempt")
        return existing
    payment = Payment(order_id=order.id, provider="RAZORPAY", status="CREATING", amount_paise=order.amount_paise, amount=order.total_amount, currency="INR", mode=settings.razorpay_mode)
    db.add(payment); db.flush()
    # Keep a durable intent if a provider timeout leaves its outcome unknown.
    db.commit()
    try:
        result = gateway(settings, "POST", "/orders", {"amount": order.amount_paise, "currency": "INR", "receipt": order.id, "notes": {"printsetu_order": order.id, "payment_record": payment.id}})
        if result.get("amount") != order.amount_paise or result.get("currency") != "INR":
            raise HTTPException(502, "Payment order amount mismatch")
        payment.provider_order_id = result["id"]
        payment.status = "CREATED"
        db.commit()
        return payment
    except HTTPException:
        payment.status = "RECONCILIATION_REQUIRED"
        db.commit()
        raise


def confirm_payment(db, settings, payment, entity):
    order = db.query(PrintOrder).filter_by(id=payment.order_id).with_for_update().one()
    if entity.get("order_id") != payment.provider_order_id or entity.get("amount") != payment.amount_paise or entity.get("currency") != "INR":
        raise HTTPException(400, "Payment does not match this print order")
    if entity.get("status") != "captured" or entity.get("captured") is not True:
        return {"confirmed": False, "status": entity.get("status", "pending")}
    if payment.provider_payment_id and payment.provider_payment_id != entity["id"]:
        raise HTTPException(409, "Another payment is already associated with this order")
    if payment.mode != order.payment_mode or payment.amount_paise != order.amount_paise:
        raise HTTPException(400, "Payment environment or amount mismatch")
    payment.provider_payment_id = entity["id"]
    payment.status = "CAPTURED"
    order.payment_status = PaymentStatus.paid
    if order.document.deleted_at or order.document.expires_at < datetime.utcnow():
        order.status = "FAILED"
        audit(db, "payment", "payment.captured_after_document_expiry.refund_required", order.id)
    else:
        queue_order_if_allowed(db, settings, order)
    audit(db, "payment", "payment.captured", order.id)
    return {"confirmed": True, "status": "captured"}


def verify_checkout(db, settings, order, body):
    payment = db.query(Payment).filter_by(order_id=order.id, provider_order_id=body.get("razorpay_order_id")).first()
    if not payment:
        raise HTTPException(404, "Payment order not found")
    payment_id = body.get("razorpay_payment_id", "")
    signature = hmac.new(settings.razorpay_key_secret.encode(), f"{payment.provider_order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, body.get("razorpay_signature", "")):
        raise HTTPException(400, "Invalid payment signature")
    entity = gateway(settings, "GET", "/payments/" + payment_id)
    result = confirm_payment(db, settings, payment, entity)
    db.commit()
    return result


def reconcile_payment(db, settings, order):
    payment = db.query(Payment).filter_by(order_id=order.id).first()
    if not payment or not payment.provider_order_id:
        raise HTTPException(409, "No provider order is available to check")
    if payment.mode != settings.razorpay_mode:
        raise HTTPException(409, "Payment mode changed. Contact the shop to check this payment.")
    if order.payment_status == PaymentStatus.paid:
        return {"confirmed": True, "status": "captured"}
    result = gateway(settings, "GET", "/orders/" + payment.provider_order_id + "/payments")
    for entity in result.get("items", []):
        if entity.get("status") == "captured":
            confirmed = confirm_payment(db, settings, payment, entity)
            db.commit()
            return confirmed
    return {"confirmed": False, "status": "pending"}


def process_webhook(db, settings, raw, signature, event_id):
    if not settings.razorpay_webhook_secret:
        raise HTTPException(503, "Webhook verification is not configured")
    expected = hmac.new(settings.razorpay_webhook_secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(400, "Invalid webhook signature")
    identifier = event_id or hashlib.sha256(raw).hexdigest()
    if db.get(WebhookEvent, identifier):
        return {"ok": True, "duplicate": True}
    try:
        data = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "Invalid event payload")
    event = data.get("event", "")
    payload = data.get("payload", {})
    if event in {"payment.captured", "order.paid", "payment.failed"}:
        entity = payload.get("payment", {}).get("entity", {})
        if entity.get("id"):
            payment = db.query(Payment).filter_by(provider_order_id=entity.get("order_id")).first()
            if not payment:
                raise HTTPException(503, "Payment mapping not ready; retry delivery")
            if event != "payment.failed":
                entity = gateway(settings, "GET", "/payments/" + entity["id"])
                confirm_payment(db, settings, payment, entity)
            elif payment.status != "CAPTURED":
                payment.status = "FAILED"
    if event.startswith("refund."):
        entity = payload.get("refund", {}).get("entity", {})
        payment = db.query(Payment).filter_by(provider_payment_id=entity.get("payment_id")).first()
        if payment:
            refund = db.query(Refund).filter_by(provider_refund_id=entity.get("id")).first()
            if not refund:
                refund = db.query(Refund).filter_by(payment_id=payment.id, provider_refund_id=None).first()
                if refund:
                    refund.provider_refund_id = entity['id']
            if not refund:
                refund = Refund(payment_id=payment.id, provider_refund_id=entity["id"], amount_paise=entity.get("amount", 0))
                db.add(refund)
            if refund.status != "processed":
                refund.status = entity.get("status", "pending")
    db.add(WebhookEvent(id=identifier, event=event)); db.commit()
    return {"ok": True}
