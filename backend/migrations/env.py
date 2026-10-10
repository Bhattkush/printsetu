from alembic import context
from sqlalchemy import create_engine
from backend.app.config import get_settings
from backend.app.db import Base
from backend.app import models

engine = create_engine(get_settings().database_url)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=engine.dialect.name == "sqlite")
    with context.begin_transaction():
        context.run_migrations()
