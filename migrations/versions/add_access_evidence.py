"""Add evidence_image to access_log.

Revision ID: add_access_evidence_20260903
Revises: 9d9cbb75d077
Create Date: 2026-09-03
"""

from alembic import op
import sqlalchemy as sa


# =========================================================
# ALEMBIC REVISION
# =========================================================

revision = 'add_access_evidence_20260903'

down_revision = '9d9cbb75d077'

branch_labels = None

depends_on = None


# =========================================================
# UPGRADE
# =========================================================

def upgrade():
    with op.batch_alter_table(
        'access_log',
        schema=None
    ) as batch_op:

        batch_op.add_column(
            sa.Column(
                'evidence_image',
                sa.Text(),
                nullable=True
            )
        )


# =========================================================
# DOWNGRADE
# =========================================================

def downgrade():
    with op.batch_alter_table(
        'access_log',
        schema=None
    ) as batch_op:

        batch_op.drop_column(
            'evidence_image'
        )