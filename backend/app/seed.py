from .db import Base, engine, SessionLocal
from .models import ActivationCode, PricingRule, Shop, User
import secrets


def main():
    from .config import get_settings
    from .auth import passwords
    if get_settings().env != "local":
        raise RuntimeError("Demo seed is only available in local development")
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        owner = db.query(User).filter(User.email.in_(["owner@printsetu.local", "owner@example.com"])).first()
        if not owner:
            owner = User(email="owner@example.com", password_hash=passwords.hash("PrintSetuDemo123!"), role="owner", name="Demo Owner", verified=True)
            db.add(owner)
            db.flush()
        if owner.email == "owner@printsetu.local" or owner.password_hash == "demo-only":
            owner.email = "owner@example.com"
            owner.password_hash = passwords.hash("PrintSetuDemo123!")
            owner.verified = True
        shop = db.query(Shop).filter(Shop.slug == "demo-shop").first()
        if not shop:
            shop = Shop(owner_id=owner.id, name="PrintSetu Demo Xerox", location="Local Demo", slug="demo-shop")
            db.add(shop)
            db.flush()
            db.add(PricingRule(shop_id=shop.id, bw_page_price=2, color_page_price=10, base_price=0, minimum_order=0))
        code = ActivationCode(shop_id=shop.id, code=secrets.token_urlsafe(9).replace("-", "").replace("_", "")[:12].upper())
        db.add(code)
        db.commit()
        print(f"Seeded demo shop: http://127.0.0.1:5173/shop/demo-shop")
        print("Local demo login: owner@example.com / PrintSetuDemo123!")
    finally:
        db.close()


if __name__ == "__main__":
    main()

