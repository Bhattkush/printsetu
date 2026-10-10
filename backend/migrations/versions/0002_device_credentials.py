"""Hash existing device credentials and add a single durable payment intent."""
import hashlib
from alembic import op
import sqlalchemy as sa

revision = '0002_device_credentials'
down_revision = '0001_secure_platform'
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    for row in connection.execute(sa.text('SELECT id, credential FROM shop_devices')).fetchall():
        if len(row.credential) != 64:
            connection.execute(sa.text('UPDATE shop_devices SET credential = :value WHERE id = :id'), {'id': row.id, 'value': hashlib.sha256(row.credential.encode()).hexdigest()})
    indexes = sa.inspect(connection).get_indexes('payments')
    if not any(index['unique'] and index['column_names'] == ['order_id'] for index in indexes):
        op.create_index('uq_payment_intent_order', 'payments', ['order_id'], unique=True)


def downgrade():
    raise RuntimeError('Credential hashes cannot be restored to plaintext')
