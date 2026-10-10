"""Initial platform schema and data-preserving upgrade of the local MVP."""
from alembic import op
import sqlalchemy as sa
from backend.app.db import Base
from backend.app import models

revision = "0001_secure_platform"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    old_tables = set(inspector.get_table_names())
    for table in Base.metadata.sorted_tables:
        if table.name not in old_tables:
            table.create(connection)
            continue
        present = {column['name'] for column in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name not in present:
                added = column._copy()
                added.nullable = True
                added.unique = False
                added.index = False
                op.add_column(table.name, added)
                if column.name == "created_at":
                    connection.execute(sa.text(f'UPDATE "{table.name}" SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL'))
        existing_indexes = {item['name'] for item in inspector.get_indexes(table.name)}
        for index in table.indexes:
            if index.name not in existing_indexes:
                index.create(connection)
        for column in table.columns:
            if column.unique and column.name not in present:
                op.create_index(f"uq_{table.name}_{column.name}", table.name, [column.name], unique=True)
    connection.execute(sa.text("UPDATE print_orders SET amount_paise = CAST(total_amount * 100 AS INTEGER) WHERE amount_paise = 0"))


def downgrade():
    raise RuntimeError("Use a database backup to restore; automatic downgrade would destroy documents and payment history.")
