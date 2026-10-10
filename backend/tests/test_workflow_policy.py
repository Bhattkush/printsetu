import os
import tempfile

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.config import Settings
from backend.app.db import Base
from backend.app.models import PricingRule, PrintJob, Shop, UploadedDocument, User
from backend.app.services import create_order, parse_page_range


def session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def seed(db):
    owner = User(email="owner@test.local", password_hash="x", role="owner")
    db.add(owner)
    db.flush()
    shop = Shop(owner_id=owner.id, name="A", slug="a")
    other = Shop(owner_id=owner.id, name="B", slug="b")
    db.add_all([shop, other])
    db.flush()
    db.add(PricingRule(shop_id=shop.id, bw_page_price=2, color_page_price=10))
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    tmp.write(b"%PDF-1.4\n%%EOF")
    tmp.close()
    doc = UploadedDocument(shop_id=shop.id, storage_path=tmp.name, original_name="x.pdf", mime_type="application/pdf", page_count=3)
    db.add(doc)
    db.commit()
    return shop, other, doc


def test_page_range_counts_unique_pages():
    assert parse_page_range("1-2,2,3", 5) == 3


def test_demo_unpaid_order_queues_when_local_policy_allows():
    db = session()
    shop, _, doc = seed(db)
    settings = Settings(_env_file=None, env="local", demo_mode=True, payment_required=False, allow_unpaid_local_printing=True)
    order = create_order(db, settings, shop.id, doc.id, {"color_mode": "bw", "copies": 2, "page_range": "1-2"})
    db.commit()
    assert order.payment_status == "DEMO_UNPAID"
    assert order.status == "QUEUED"
    assert db.query(PrintJob).count() == 1
    create_order(db, settings, shop.id, doc.id, {"color_mode": "color", "copies": 1, "page_range": "all"})
    assert db.query(PrintJob).count() == 2
    os.remove(doc.storage_path)


def test_unpaid_order_blocked_when_payment_required():
    db = session()
    shop, _, doc = seed(db)
    settings = Settings(_env_file=None, env="production", demo_mode=False, payment_required=True, allow_unpaid_local_printing=False)
    order = create_order(db, settings, shop.id, doc.id, {"color_mode": "bw", "copies": 1, "page_range": "all"})
    db.commit()
    assert order.status == "BLOCKED_PAYMENT_REQUIRED"
    assert db.query(PrintJob).count() == 0
    os.remove(doc.storage_path)


def test_other_shop_cannot_order_document():
    db = session()
    _, other, doc = seed(db)
    settings = Settings(_env_file=None)
    try:
        create_order(db, settings, other.id, doc.id, {"color_mode": "bw", "copies": 1, "page_range": "all"})
    except HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("Expected shop isolation failure")
    os.remove(doc.storage_path)

