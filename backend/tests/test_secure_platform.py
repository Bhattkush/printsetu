import hashlib
import hmac
import io
import json
from datetime import datetime, timedelta
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from backend.app import application, auth, payments
from backend.app.config import Settings
from backend.app.db import Base, get_db
from backend.app.models import User, Shop, PricingRule, PrintOrder, PrintJob, Payment, UploadedDocument, EmailChallenge, ShopDevice


@pytest.fixture
def platform(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, env='local', jwt_secret='test-secret-only-not-a-production-key', storage_dir=str(tmp_path), razorpay_mode='disabled', otp_mode='development', demo_mode=True, payment_required=False, allow_unpaid_local_printing=True)
    monkeypatch.setattr(application, 'settings', settings)
    monkeypatch.setattr(auth, 'settings', settings)
    engine = create_engine('sqlite://', connect_args={'check_same_thread':False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    def dependency():
        with factory() as db:
            yield db
    application.app.dependency_overrides[get_db] = dependency
    with factory() as db:
        user = User(email='one@example.com', name='One', role='owner', verified=True, password_hash=auth.passwords.hash('TestingPass123!'))
        other = User(email='two@example.com', name='Two', role='owner', verified=True, password_hash=auth.passwords.hash('TestingPass123!'))
        db.add_all([user,other]); db.flush()
        a = Shop(owner_id=user.id, name='One', slug='one'); b = Shop(owner_id=other.id, name='Two', slug='two')
        db.add_all([a,b]); db.flush()
        db.add_all([PricingRule(shop_id=a.id,bw_page_price=2.5,color_page_price=10),PricingRule(shop_id=b.id,bw_page_price=2,color_page_price=10)]); db.commit()
        ids = a.id,b.id
    client = TestClient(application.app)
    yield client,factory,settings,ids
    application.app.dependency_overrides.clear()
    client.close(); engine.dispose()


def upload(client, shop):
    client.get('/api/shops/one')
    pdf = io.BytesIO(); writer = PdfWriter(); writer.add_blank_page(width=595,height=842); writer.write(pdf)
    result = client.post(f'/api/shops/{shop}/documents',files={'file':('test.pdf',pdf.getvalue(),'application/pdf')})
    assert result.status_code == 200, result.text
    return result.json()['id']


def login(client):
    result = client.post('/api/auth/login',json={'email':'one@example.com','password':'TestingPass123!'})
    assert result.status_code == 200, result.text
    return {'x-csrf-token':result.json()['csrf']}


def test_tenant_and_csrf(platform):
    client,_,_,(shop,other)=platform
    assert client.get('/api/owner/summary').status_code == 401
    headers=login(client)
    assert len(client.get('/api/owner/summary').json()['shops']) == 1
    assert client.post(f'/api/owner/shops/{shop}/activation-codes').status_code == 403
    assert client.post(f'/api/owner/shops/{other}/activation-codes',headers=headers).status_code == 404
    assert client.get('/api/admin/overview').status_code == 403
    assert client.patch(f'/api/owner/shops/{shop}/pricing',headers={**headers,'origin':'https://evil.example'},json={'bw_page_price':0}).status_code == 403


def test_email_verification_attempts_and_replay(platform):
    client,factory,_,_=platform
    form={'email':'new@example.com','password':'TestingPass123!','name':'New','shop_name':'New shop'}
    result=client.post('/api/auth/register',json=form)
    assert result.status_code == 200
    code=result.json()['development_code']
    assert client.post('/api/auth/login',json=form).status_code == 403
    assert client.post('/api/auth/resend',json={'email':form['email']}).status_code == 429
    with factory() as db:
        row=db.query(EmailChallenge).one(); assert code not in row.code_hash
    assert client.post('/api/auth/verify',json={'email':form['email'],'code':code}).status_code == 200
    assert client.post('/api/auth/verify',json={'email':form['email'],'code':code}).status_code == 400


def test_private_documents_and_server_price(platform):
    client,_,_,(shop,other)=platform
    doc=upload(client,shop)
    body={'document_id':doc,'copies':3,'color_mode':'bw'}
    result=client.post(f'/api/shops/{shop}/quote',json=body)
    assert result.json()['amount_paise'] == 750
    assert client.post(f'/api/shops/{other}/orders',json=body).status_code == 404
    assert client.post(f'/api/shops/{shop}/quote',json={**body,'copies':1.5}).status_code == 422
    saved=client.cookies.get('ps_customer'); client.cookies.clear()
    assert client.get(f'/api/customer/documents/{doc}/file').status_code == 404
    client.cookies.set('ps_customer',saved)
    assert client.get(f'/api/customer/documents/{doc}/preview').status_code == 200
    assert client.post(f'/api/shops/{shop}/documents',files={'file':('bad.pdf',b'not a pdf','application/pdf')}).status_code == 400


def test_capture_is_required_and_webhook_is_idempotent(platform,monkeypatch):
    client,factory,settings,(shop,_)=platform
    settings.razorpay_mode='test'; settings.razorpay_key_id='rzp_test_fixture'; settings.razorpay_key_secret='sandbox-secret'; settings.razorpay_webhook_secret='webhook-secret'
    doc=upload(client,shop)
    order=client.post(f'/api/shops/{shop}/orders',json={'document_id':doc,'copies':2,'amount_paise':1,'payment_status':'PAID'}).json()
    assert order['amount_paise'] == 500 and order['payment_status'] == 'UNPAID'
    with factory() as db: assert db.query(PrintJob).count() == 0
    monkeypatch.setattr(payments,'gateway',lambda *args,**kwargs:{'id':'order_test','amount':500,'currency':'INR'})
    created=client.post('/api/payments/create-order',json={'order_id':order['id']}); assert created.status_code == 200
    signature=hmac.new(b'sandbox-secret',b'order_test|pay_test',hashlib.sha256).hexdigest()
    entity={'id':'pay_test','order_id':'order_test','amount':500,'currency':'INR','captured':False,'status':'authorized'}
    monkeypatch.setattr(payments,'gateway',lambda *args,**kwargs:entity)
    body={'order_id':order['id'],'razorpay_order_id':'order_test','razorpay_payment_id':'pay_test','razorpay_signature':signature}
    assert client.post('/api/payments/verify',json={**body,'razorpay_signature':'wrong'}).status_code == 400
    assert client.post('/api/payments/verify',json=body).json()['confirmed'] is False
    with factory() as db: assert db.query(PrintJob).count() == 0
    entity.update(status='captured',captured=True)
    raw=json.dumps({'event':'payment.captured','payload':{'payment':{'entity':entity}}}).encode()
    signed=hmac.new(b'webhook-secret',raw,hashlib.sha256).hexdigest()
    headers={'x-razorpay-signature':signed,'x-razorpay-event-id':'evt_once','content-type':'application/json'}
    assert client.post('/api/payments/webhook',content=raw,headers={**headers,'x-razorpay-signature':'bad'}).status_code == 400
    assert client.post('/api/payments/webhook',content=raw,headers=headers).status_code == 200
    assert client.post('/api/payments/webhook',content=raw,headers=headers).json()['duplicate']
    assert client.post('/api/payments/verify',json=body).status_code == 200
    with factory() as db:
        assert db.query(PrintJob).count() == 1
        assert db.get(PrintOrder,order['id']).payment_status == 'PAID'


def test_expired_document_does_not_release_paid_job(platform,monkeypatch):
    client,factory,settings,(shop,_)=platform
    settings.razorpay_mode='test'
    doc=upload(client,shop)
    order=client.post(f'/api/shops/{shop}/orders',json={'document_id':doc}).json()
    with factory() as db:
        document=db.get(UploadedDocument,doc); document.expires_at=datetime.utcnow()-timedelta(seconds=1)
        payment=Payment(order_id=order['id'],provider_order_id='order_expired',amount_paise=250,currency='INR',mode='test')
        db.add(payment);db.commit()
        payments.confirm_payment(db,settings,payment,{'id':'pay_expired','order_id':'order_expired','amount':250,'currency':'INR','captured':True,'status':'captured'});db.commit()
        assert db.query(PrintJob).count()==0 and db.get(PrintOrder,order['id']).status=='FAILED'


def enable_test_payments(settings):
    settings.razorpay_mode = 'test'
    settings.razorpay_key_id = 'rzp_test_fixture'
    settings.razorpay_key_secret = 'fixture-secret'
    settings.demo_mode = False
    settings.payment_required = True
    settings.allow_unpaid_local_printing = False


def test_unconfigured_checkout_does_not_create_payment_intent(platform):
    client, factory, settings, (shop, _) = platform
    enable_test_payments(settings)
    settings.razorpay_key_id = 'rzp_live_wrong_environment'
    assert client.get('/api/health').json()['checkout_ready'] is False
    doc = upload(client, shop)
    order = client.post(f'/api/shops/{shop}/orders', json={'document_id': doc}).json()
    assert client.post('/api/payments/create-order', json={'order_id': order['id']}).status_code == 503
    with factory() as db:
        assert db.query(Payment).count() == 0
        assert db.query(PrintJob).count() == 0


def test_reconcile_capture_releases_paid_job_past_old_demo_job(platform, monkeypatch):
    client, factory, settings, (shop, _) = platform
    doc = upload(client, shop)
    demo = client.post(f'/api/shops/{shop}/orders', json={'document_id': doc}).json()
    enable_test_payments(settings)
    order = client.post(f'/api/shops/{shop}/orders', json={'document_id': doc}).json()
    monkeypatch.setattr(payments, 'gateway', lambda *args, **kwargs: {'id': 'order_capture', 'amount': 250, 'currency': 'INR'})
    assert client.post('/api/payments/create-order', json={'order_id': order['id']}).status_code == 200
    credential = 'fixture-device'
    with factory() as db:
        db.add(ShopDevice(shop_id=shop, name='Printer PC', credential=hashlib.sha256(credential.encode()).hexdigest()))
        db.commit()
    headers = {'authorization': 'Bearer ' + credential}
    assert client.get('/api/agent/jobs/next', headers=headers).json()['job'] is None
    entity = {'id': 'pay_capture', 'order_id': 'order_capture', 'amount': 250, 'currency': 'INR', 'captured': False, 'status': 'authorized'}
    monkeypatch.setattr(payments, 'gateway', lambda *args, **kwargs: {'items': [entity]})
    body = {'order_id': order['id']}
    assert client.post('/api/payments/reconcile', json=body).json()['confirmed'] is False
    assert client.get('/api/agent/jobs/next', headers=headers).json()['job'] is None
    entity.update(status='captured', captured=True)
    assert client.post('/api/payments/reconcile', json=body).json()['confirmed'] is True
    assert client.post('/api/payments/reconcile', json=body).json()['confirmed'] is True
    with factory() as db:
        paid_job = db.query(PrintJob).filter_by(order_id=order['id']).one()
        job_id = paid_job.id
        assert db.query(PrintJob).filter_by(order_id=demo['id']).one().status == 'QUEUED'
    assert client.get('/api/agent/jobs/next', headers=headers).json()['job']['id'] == job_id
    client.cookies.clear()
    assert client.post('/api/payments/reconcile', json=body).status_code == 404


def test_reconcile_rejects_provider_amount_mismatch(platform, monkeypatch):
    client, factory, settings, (shop, _) = platform
    enable_test_payments(settings)
    doc = upload(client, shop)
    order = client.post(f'/api/shops/{shop}/orders', json={'document_id': doc}).json()
    monkeypatch.setattr(payments, 'gateway', lambda *args, **kwargs: {'id': 'order_wrong', 'amount': 250, 'currency': 'INR'})
    assert client.post('/api/payments/create-order', json={'order_id': order['id']}).status_code == 200
    entity = {'id': 'pay_wrong', 'order_id': 'order_wrong', 'amount': 1, 'currency': 'INR', 'captured': True, 'status': 'captured'}
    monkeypatch.setattr(payments, 'gateway', lambda *args, **kwargs: {'items': [entity]})
    assert client.post('/api/payments/reconcile', json={'order_id': order['id']}).status_code == 400
    with factory() as db:
        assert db.query(PrintJob).count() == 0
        assert db.get(PrintOrder, order['id']).payment_status == 'UNPAID'
