import asyncio
import base64
import hashlib
import io
import json
import secrets
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from qrcode import make as make_qr
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from . import auth, payments
from .config import get_settings
from .db import SessionLocal, get_db
from .models import *
from .services import activate_device, authenticate_device, audit, cleanup_expired, create_order, quote, save_pdf_upload

settings = get_settings()


def run_cleanup():
    with SessionLocal() as db:
        result = cleanup_expired(db)
        db.commit()
        return result


@asynccontextmanager
async def lifespan(app):
    settings.validate_production()
    async def cleaner():
        while True:
            await asyncio.sleep(settings.cleanup_interval_seconds)
            try:
                await asyncio.to_thread(run_cleanup)
            except Exception:
                # File failures are recorded per document; a DB outage is retried.
                logging.getLogger(__name__).exception('Scheduled document cleanup failed')
    task = asyncio.create_task(cleaner())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="PrintSetu", lifespan=lifespan)
app.include_router(auth.router)
app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins.split(','), allow_credentials=True, allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["Content-Type", "Authorization", "X-CSRF-Token"])


@app.middleware("http")
async def security_headers(request, call_next):
    origin = request.headers.get("origin")
    if request.method not in {"GET", "HEAD", "OPTIONS"} and origin and origin != f"{request.url.scheme}://{request.url.netloc}" and origin not in settings.allowed_origins.split(',') and not (settings.public_url and origin == settings.public_url):
        return Response(status_code=403, content='{"detail":"Origin not allowed"}', media_type="application/json")
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Cache-Control"] = "no-store"
    if settings.env == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


def public_order(order, db):
    job = db.query(PrintJob).filter_by(order_id=order.id).first()
    return {"id": order.id, "order_number": order.order_number, "shop_id": order.shop_id, "status": order.status, "payment_status": order.payment_status, "payment_mode": order.payment_mode, "demo_label": "DEMO / UNPAID" if order.payment_mode == "demo" else "RAZORPAY TEST" if order.payment_mode == "test" else "LIVE", "total_amount": float(order.total_amount), "amount_paise": order.amount_paise, "options": {"color_mode": order.color_mode, "paper_size": order.paper_size, "duplex": order.duplex, "page_range": order.page_range, "copies": order.copies, "page_count_charged": order.page_count_charged}, "print_job": {"status": job.status, "adapter_mode": job.adapter_mode, "error": job.error, "spooler_job_id": job.spooler_job_id} if job else None}


def public_shop(shop):
    return {"id": shop.id, "owner_id": shop.owner_id, "name": shop.name, "location": shop.location, "slug": shop.slug, "retention_hours": shop.retention_hours}


def owned_document(db, document_id, customer_hash):
    doc = db.get(UploadedDocument, document_id)
    if not doc or doc.customer_hash != customer_hash or doc.deleted_at or doc.expires_at < datetime.utcnow():
        raise HTTPException(404, "Document not found or expired")
    return doc


def customer_order(db, order_id, customer_hash):
    order = db.get(PrintOrder, order_id)
    if not order or order.document.customer_hash != customer_hash:
        raise HTTPException(404, "Order not found")
    return order


class Options(BaseModel):
    document_id: str
    color_mode: str = Field(default="bw", pattern="^(bw|color)$")
    paper_size: str = Field(default="A4", pattern="^(A4|A3)$")
    copies: int = Field(default=1, ge=1, le=100, strict=True)
    page_range: str = Field(default="all", max_length=1000)
    duplex: bool = Field(default=False, strict=True)


@app.get('/api/health')
def health():
    ready = settings.checkout_ready
    return {"product_name": "PrintSetu", "env": settings.env, "demo_mode": settings.demo_mode, "payment_required": settings.payment_required, "unpaid_printing_allowed": settings.unpaid_printing_allowed, "payment_mode": settings.razorpay_mode, "checkout_ready": ready, "email_mode": settings.otp_mode}


@app.get('/api/shops/{slug}')
def get_shop(slug: str, customer_hash=Depends(auth.customer), db: Session = Depends(get_db)):
    shop = db.query(Shop).filter_by(slug=slug).first()
    if not shop:
        raise HTTPException(404, "Shop not found")
    pricing = db.query(PricingRule).filter_by(shop_id=shop.id).first()
    return {**public_shop(shop), "pricing": {k: float(getattr(pricing, k)) for k in ['bw_page_price', 'color_page_price', 'base_price', 'minimum_order']} if pricing else None, "policy": health(), "printers": [{"name": p.name, "supports_color": p.supports_color, "supports_duplex": p.supports_duplex, "paper_sizes": p.paper_sizes.split(',')} for p in db.query(Printer).filter_by(shop_id=shop.id).all()]}


@app.post('/api/shops/{shop_id}/documents')
def upload(shop_id: str, request: Request, file: UploadFile = File(...), customer_hash=Depends(auth.customer), db: Session = Depends(get_db)):
    if not db.get(Shop, shop_id):
        raise HTTPException(404, "Shop not found")
    auth.throttle(db, 'upload:' + customer_hash, 20)
    auth.throttle(db, 'upload-ip:' + request.client.host, 60)
    doc = save_pdf_upload(db, settings, shop_id, file)
    doc.customer_hash = customer_hash
    doc.expires_at = datetime.utcnow() + timedelta(hours=db.get(Shop, shop_id).retention_hours)
    db.commit()
    return {"id": doc.id, "page_count": doc.page_count, "original_name": doc.original_name}


@app.get('/api/customer/documents/{document_id}/file')
def customer_file(document_id: str, customer_hash=Depends(auth.customer), db: Session = Depends(get_db)):
    doc = owned_document(db, document_id, customer_hash)
    return FileResponse(doc.storage_path, media_type='application/pdf', content_disposition_type='inline')


@app.get('/api/customer/documents/{document_id}/preview')
def preview(document_id: str, page: int = 1, customer_hash=Depends(auth.customer), db: Session = Depends(get_db)):
    import pymupdf
    doc = owned_document(db, document_id, customer_hash)
    if not 1 <= page <= doc.page_count:
        raise HTTPException(400, 'Page not found')
    with pymupdf.open(doc.storage_path) as pdf:
        pdf_page = pdf[page - 1]
        scale = min(96 / 72, 1600 / max(pdf_page.rect.width, pdf_page.rect.height, 1))
        pixmap = pdf_page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        return Response(pixmap.tobytes('png'), media_type='image/png')


@app.post('/api/shops/{shop_id}/quote')
def calculate(shop_id: str, body: Options, customer_hash=Depends(auth.customer), db: Session = Depends(get_db)):
    owned_document(db, body.document_id, customer_hash)
    return quote(db, shop_id, body.document_id, body.model_dump())


@app.post('/api/shops/{shop_id}/orders')
def submit(shop_id: str, body: Options, customer_hash=Depends(auth.customer), db: Session = Depends(get_db)):
    owned_document(db, body.document_id, customer_hash)
    order = create_order(db, settings, shop_id, body.document_id, body.model_dump())
    db.commit()
    return public_order(order, db)


@app.get('/api/orders/{order_id}')
def get_order(order_id: str, customer_hash=Depends(auth.customer), db: Session = Depends(get_db)):
    return public_order(customer_order(db, order_id, customer_hash), db)


@app.get('/api/orders/{order_id}/receipt')
def receipt(order_id: str, customer_hash=Depends(auth.customer), db: Session = Depends(get_db)):
    import pymupdf
    order = customer_order(db, order_id, customer_hash)
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((50, 65), 'PrintSetu receipt', fontsize=22)
        notice = 'Sandbox payments are test transactions, not live revenue.' if order.payment_mode == 'test' else 'Demo order: unpaid. No payment was collected.' if order.payment_mode == 'demo' else 'Live payment status is recorded above.'
        page.insert_text((50, 110), f'Order: {order.order_number}\nAmount: INR {order.amount_paise / 100:.2f}\nPayment status: {order.payment_status}\nPayment mode: {order.payment_mode}\nPrint status: {order.status}\n\n{notice}', fontsize=12)
        return Response(pdf.tobytes(), media_type='application/pdf', headers={'Content-Disposition': f'attachment; filename="{order.order_number}.pdf"'})


@app.post('/api/payments/create-order')
def payment_create(body: dict, customer_hash=Depends(auth.customer), db: Session = Depends(get_db)):
    order = customer_order(db, body.get('order_id'), customer_hash)
    payment = payments.create_payment(db, settings, order)
    return {'key_id': settings.razorpay_key_id, 'razorpay_order_id': payment.provider_order_id, 'amount_paise': payment.amount_paise, 'currency': 'INR', 'mode': payment.mode}


@app.post('/api/payments/verify')
def payment_verify(body: dict, customer_hash=Depends(auth.customer), db: Session = Depends(get_db)):
    return payments.verify_checkout(db, settings, customer_order(db, body.get('order_id'), customer_hash), body)


@app.post('/api/payments/reconcile')
def payment_reconcile(body: dict, customer_hash=Depends(auth.customer), db: Session = Depends(get_db)):
    return payments.reconcile_payment(db, settings, customer_order(db, body.get('order_id'), customer_hash))


@app.post('/api/payments/webhook')
async def webhook(request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    if len(raw) > 1024 * 1024:
        raise HTTPException(413, 'Event too large')
    try:
        return payments.process_webhook(db, settings, raw, request.headers.get('x-razorpay-signature', ''), request.headers.get('x-razorpay-event-id', ''))
    except IntegrityError:
        db.rollback()
        event_id = request.headers.get('x-razorpay-event-id') or hashlib.sha256(raw).hexdigest()
        if db.get(WebhookEvent, event_id):
            return {'ok': True, 'duplicate': True}
        raise


@app.get('/api/payments/status/{order_id}')
def payment_status(order_id: str, customer_hash=Depends(auth.customer), db: Session = Depends(get_db)):
    order = customer_order(db, order_id, customer_hash)
    payment = db.query(Payment).filter_by(order_id=order.id).first()
    return {'payment_status': order.payment_status, 'provider_status': payment.status if payment else 'NOT_ATTEMPTED', 'mode': order.payment_mode}


@app.get('/api/owner/summary')
def owner_summary(user=Depends(auth.owner), db: Session = Depends(get_db)):
    query = db.query(Shop)
    shops = query.all() if user.role == 'admin' else query.filter_by(owner_id=user.id).all()
    ids = [s.id for s in shops]
    orders = db.query(PrintOrder).filter(PrintOrder.shop_id.in_(ids)).order_by(PrintOrder.created_at.desc()).limit(100).all()
    devices = db.query(ShopDevice).filter(ShopDevice.shop_id.in_(ids)).all()
    return {'shops': [public_shop(s) for s in shops], 'orders': [public_order(o, db) for o in orders], 'devices': [{'id': d.id, 'shop_id': d.shop_id, 'name': d.name, 'status': d.status, 'selected_printer': d.selected_printer, 'last_seen_at': d.last_seen_at} for d in devices], 'server_url': settings.server_url}


@app.get('/api/owner/events')
async def owner_events(request: Request, user=Depends(auth.owner), db: Session = Depends(get_db)):
    session_id = auth.digest(request.cookies.get('ps_session', ''))
    user_id = user.id
    db.rollback()
    async def events():
        while not await request.is_disconnected():
            with SessionLocal() as session:
                login = session.get(AuthSession, session_id)
                if not login or login.expires_at < datetime.utcnow(): break
                current = session.get(User, user_id)
                payload = owner_summary(user=current, db=session)
                yield 'data: ' + json.dumps(payload, default=str) + '\n\n'
            await asyncio.sleep(3)
    return StreamingResponse(events(), media_type='text/event-stream', headers={'X-Accel-Buffering':'no'})


@app.get('/api/orders/{order_id}/events')
async def order_events(order_id: str, request: Request, customer_hash=Depends(auth.customer), db: Session = Depends(get_db)):
    customer_order(db, order_id, customer_hash)
    db.rollback()
    async def events():
        while not await request.is_disconnected():
            with SessionLocal() as session:
                order = customer_order(session, order_id, customer_hash)
                payload = public_order(order, session)
                yield 'data: ' + json.dumps(payload, default=str) + '\n\n'
                if order.status in {OrderStatus.completed, OrderStatus.failed}: break
            await asyncio.sleep(2)
    return StreamingResponse(events(), media_type='text/event-stream', headers={'X-Accel-Buffering':'no'})


@app.patch('/api/owner/shops/{shop_id}')
def update_shop(shop_id: str, body: dict, user=Depends(auth.owner), db: Session = Depends(get_db)):
    shop = auth.require_shop(db, user, shop_id)
    for key in ['name', 'location']:
        if key in body:
            if not isinstance(body[key], str) or not 1 <= len(body[key]) <= 150:
                raise HTTPException(400, 'Invalid shop details')
            setattr(shop, key, body[key])
    if 'retention_hours' in body:
        if not isinstance(body['retention_hours'], int) or not 1 <= body['retention_hours'] <= 24:
            raise HTTPException(400, 'Retention must be between 1 and 24 hours')
        shop.retention_hours = body['retention_hours']
    audit(db, user.id, 'shop.updated', shop.id); db.commit()
    return public_shop(shop)


@app.get('/api/owner/shops/{shop_id}/pricing')
def pricing_get(shop_id: str, user=Depends(auth.owner), db: Session = Depends(get_db)):
    auth.require_shop(db, user, shop_id)
    rule = db.query(PricingRule).filter_by(shop_id=shop_id).first()
    return {key: float(getattr(rule, key)) for key in ['bw_page_price', 'color_page_price', 'base_price', 'minimum_order']}


@app.patch('/api/owner/shops/{shop_id}/pricing')
def pricing_update(shop_id: str, body: dict, user=Depends(auth.owner), db: Session = Depends(get_db)):
    auth.require_shop(db, user, shop_id)
    rule = db.query(PricingRule).filter_by(shop_id=shop_id).first()
    from decimal import Decimal, InvalidOperation
    for key in ['bw_page_price', 'color_page_price', 'base_price', 'minimum_order']:
        if key in body:
            try:
                amount = Decimal(str(body[key]))
                if not amount.is_finite() or amount < 0 or amount > 10000 or amount.as_tuple().exponent < -2:
                    raise InvalidOperation()
            except InvalidOperation:
                raise HTTPException(400, 'Prices must be nonnegative INR amounts with at most two decimals')
            setattr(rule, key, amount)
    db.commit(); return {'ok': True}


@app.post('/api/owner/shops/{shop_id}/activation-codes')
def activation_code(shop_id: str, request: Request, user=Depends(auth.owner), db: Session = Depends(get_db)):
    auth.require_shop(db, user, shop_id)
    code = secrets.token_hex(6).upper()
    row = ActivationCode(shop_id=shop_id, code=code, expires_at=datetime.utcnow() + timedelta(minutes=10))
    db.add(row); db.commit()
    host = request.url.hostname
    address = settings.public_url or f'{request.url.scheme}://{host}:8000'
    return {'code': code, 'expires_at': row.expires_at, 'server_url': address}


@app.get('/api/owner/shops/{shop_id}/qr')
def qr(shop_id: str, request: Request, user=Depends(auth.owner), db: Session = Depends(get_db)):
    shop = auth.require_shop(db, user, shop_id)
    base = settings.public_url.rstrip('/') or f'http://{request.url.hostname}:5173'
    url = base + '/shop/' + shop.slug
    buf = io.BytesIO(); make_qr(url).save(buf, format='PNG')
    return {'url': url, 'png_base64': base64.b64encode(buf.getvalue()).decode()}


@app.delete('/api/owner/devices/{device_id}')
def revoke(device_id: str, user=Depends(auth.owner), db: Session = Depends(get_db)):
    device = db.get(ShopDevice, device_id)
    if not device:
        raise HTTPException(404, 'Device not found')
    auth.require_shop(db, user, device.shop_id)
    device.status = DeviceStatus.revoked
    audit(db, user.id, 'device.revoked', device.id); db.commit()
    return {'ok': True}


@app.get('/api/owner/payments')
def payment_records(user=Depends(auth.owner), db: Session = Depends(get_db)):
    query = db.query(Payment).join(PrintOrder, Payment.order_id == PrintOrder.id).join(Shop, PrintOrder.shop_id == Shop.id)
    if user.role != 'admin': query = query.filter(Shop.owner_id == user.id)
    return [{'id': p.id, 'order_id': p.order_id, 'status': p.status, 'mode': p.mode, 'amount_paise': p.amount_paise, 'refunds': [{'status': r.status, 'amount_paise': r.amount_paise} for r in db.query(Refund).filter_by(payment_id=p.id).all()]} for p in query.all()]


@app.post('/api/owner/payments/{payment_id}/refund')
def refund_request(payment_id: str, user=Depends(auth.owner), db: Session = Depends(get_db)):
    payment = db.get(Payment, payment_id)
    if not payment: raise HTTPException(404, 'Payment not found')
    auth.require_shop(db, user, db.get(PrintOrder, payment.order_id).shop_id)
    payment = db.query(Payment).filter_by(id=payment_id).with_for_update().one()
    if payment.status != 'CAPTURED' or db.query(Refund).filter_by(payment_id=payment_id).first():
        raise HTTPException(409, 'Payment cannot be refunded or refund already requested')
    job = db.query(PrintJob).filter_by(order_id=payment.order_id).first()
    if job and job.status not in {JobStatus.failed}:
        raise HTTPException(409, 'Stop or resolve the print job before refunding')
    row = Refund(payment_id=payment.id, status='REQUESTING', amount_paise=payment.amount_paise)
    db.add(row); db.commit()
    result = payments.gateway(settings, 'POST', '/payments/' + payment.provider_payment_id + '/refund', {'amount': row.amount_paise, 'receipt': row.id})
    row.provider_refund_id = result['id']; row.status = result.get('status', 'pending'); db.commit()
    return {'status': row.status}


@app.get('/api/agent/download')
def download_agent():
    for name in ['PrintSetu-Setup.exe', 'PrintSetu-Connector.exe']:
        path = Path(__file__).resolve().parents[2] / 'dist' / name
        if path.exists(): return FileResponse(path, media_type='application/octet-stream', filename=name)
    raise HTTPException(503, 'Windows installer has not been built yet')


@app.post('/api/agent/activate')
def activate(body: dict, request: Request, db: Session = Depends(get_db)):
    auth.throttle(db, 'pairing:' + request.client.host, 10)
    device = activate_device(db, body.get('code', ''), str(body.get('name', 'Shop PC'))[:100])
    db.commit()
    return {'device_id': device.id, 'shop_id': device.shop_id, 'credential': device.raw_credential}


def device_auth(authorization: str | None, db):
    if not authorization or not authorization.startswith('Bearer '): raise HTTPException(401, 'Device authentication required')
    return authenticate_device(db, authorization[7:])


@app.post('/api/agent/connection')
def connection(body: dict, authorization: str | None = Header(None), db: Session = Depends(get_db)):
    device = device_auth(authorization, db)
    if body.get('mode') not in {'mock','windows'}: raise HTTPException(400, 'Invalid adapter')
    if settings.env == 'production' and body['mode'] != 'windows': raise HTTPException(403, 'Mock printing is development only')
    device.selected_printer = 'MOCK - no physical printing' if body['mode'] == 'mock' else body.get('printer')
    db.query(Printer).filter_by(device_id=device.id).delete()
    for p in body.get('printers', [])[:100]:
        db.add(Printer(shop_id=device.shop_id, device_id=device.id, name=p['name'], supports_color=p.get('supports_color', False), supports_duplex=p.get('supports_duplex', False), paper_sizes=','.join(p.get('paper_sizes', ['A4']))))
    db.commit(); return {'ok': True}


@app.get('/api/agent/jobs/next')
def next_job(authorization: str | None = Header(None), db: Session = Depends(get_db)):
    device = device_auth(authorization, db)
    query = db.query(PrintJob).filter_by(shop_id=device.shop_id, status=JobStatus.queued)
    if not settings.unpaid_printing_allowed:
        query = query.join(PrintOrder, PrintJob.order_id == PrintOrder.id).filter(PrintOrder.payment_status == PaymentStatus.paid)
    job = query.order_by(PrintJob.created_at).with_for_update(skip_locked=True).first()
    if not job: db.commit(); return {'job': None}
    order = job.order
    if order.payment_status != PaymentStatus.paid and (order.payment_mode != 'demo' or not settings.unpaid_printing_allowed):
        raise HTTPException(403, 'Unpaid printing is blocked')
    claimed = db.execute(update(PrintJob).where(PrintJob.id == job.id, PrintJob.status == JobStatus.queued).values(status=JobStatus.claimed, device_id=device.id, attempt=PrintJob.attempt + 1))
    if claimed.rowcount != 1: db.rollback(); return {'job': None}
    db.commit()
    return {'job': {'id': job.id, 'idempotency_key': job.idempotency_key, 'document_id': order.document_id, 'download_url': '/api/agent/documents/' + order.document_id, 'options': public_order(order, db)['options']}}


@app.get('/api/agent/documents/{document_id}')
def agent_document(document_id: str, authorization: str | None = Header(None), db: Session = Depends(get_db)):
    device = device_auth(authorization, db)
    doc = db.get(UploadedDocument, document_id)
    active = db.query(PrintJob).join(PrintOrder, PrintJob.order_id == PrintOrder.id).filter(PrintOrder.document_id == document_id, PrintJob.device_id == device.id, PrintJob.status.in_([JobStatus.claimed, JobStatus.printing])).first()
    if not doc or doc.shop_id != device.shop_id or doc.deleted_at or not active: raise HTTPException(404, 'Document not authorized for this job')
    db.commit(); return FileResponse(doc.storage_path, media_type='application/pdf')


@app.post('/api/agent/jobs/{job_id}/status')
def job_status(job_id: str, body: dict, authorization: str | None = Header(None), db: Session = Depends(get_db)):
    device = device_auth(authorization, db)
    job = db.query(PrintJob).filter_by(id=job_id, device_id=device.id, shop_id=device.shop_id).with_for_update().first()
    if settings.env == 'production' and body.get('adapter_mode') != 'windows':
        raise HTTPException(403, 'Production requires the Windows printer adapter')
    if not job: raise HTTPException(404, 'Job not found')
    try: target = JobStatus(body['status'])
    except (KeyError, ValueError): raise HTTPException(400, 'Invalid job status')
    transitions = {JobStatus.claimed: {JobStatus.printing, JobStatus.failed}, JobStatus.printing: {JobStatus.submitted_to_spooler, JobStatus.completed, JobStatus.failed}, JobStatus.submitted_to_spooler: {JobStatus.completed, JobStatus.failed}}
    if target != job.status and target not in transitions.get(job.status, set()): raise HTTPException(409, 'Invalid status transition')
    if target == JobStatus.completed and body.get('adapter_mode') != 'mock': raise HTTPException(400, 'Physical completion cannot be confirmed by this adapter')
    job.status = target; job.adapter_mode = body.get('adapter_mode'); job.spooler_job_id = body.get('spooler_job_id'); job.error = str(body.get('error') or '')[:1000] or None; job.updated_at = datetime.utcnow()
    job.order.status = OrderStatus.completed if target == JobStatus.completed else OrderStatus.failed if target == JobStatus.failed else OrderStatus.printing
    if target in {JobStatus.completed, JobStatus.failed, JobStatus.submitted_to_spooler}:
        job.order.document.expires_at = datetime.utcnow() + timedelta(hours=db.get(Shop, device.shop_id).retention_hours)
    db.commit(); return {'ok': True}


@app.post('/api/admin/cleanup')
def cleanup(user=Depends(auth.admin)):
    return run_cleanup()


@app.get('/api/admin/overview')
def overview(user=Depends(auth.admin), db: Session = Depends(get_db)):
    return {'shops': db.query(Shop).count(), 'users': db.query(User).count(), 'orders': db.query(PrintOrder).count(), 'jobs': db.query(PrintJob).count(), 'devices': db.query(ShopDevice).count(), 'policy': health(), 'audit_logs': [{'action': a.action, 'entity_id': a.entity_id, 'created_at': a.created_at} for a in db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(100)], 'cleanup_failures': [{'id': d.id, 'error': d.cleanup_error} for d in db.query(UploadedDocument).filter(UploadedDocument.cleanup_error.is_not(None)).all()], 'failed_jobs': [{'id': j.id, 'error': j.error} for j in db.query(PrintJob).filter_by(status=JobStatus.failed).limit(100).all()]}
