import getpass
from .auth import passwords
from .db import SessionLocal
from .models import User


def main():
    email = input('Verified administrator email: ').strip().lower()
    password = getpass.getpass('Administrator password (10+ characters): ')
    if '@' not in email or len(password) < 10:
        raise ValueError('Provide an email and a password of at least 10 characters')
    with SessionLocal() as db:
        if db.query(User).filter_by(email=email).first():
            raise ValueError('Account already exists; do not silently elevate an existing owner')
        db.add(User(name='Administrator', email=email, password_hash=passwords.hash(password), verified=True, role='admin'))
        db.commit()
    print('Administrator created. Sign in through /login.')


if __name__ == '__main__':
    main()
