"""Add security and gate fields to access_log.

Revision ID: add_security_fields_20260903
Revises: add_access_evidence_20260903
Create Date: 2026-09-03
"""

from alembic import op
import sqlalchemy as sa


# =========================================================
# ALEMBIC REVISION
# =========================================================

revision = 'add_security_fields_20260903'

down_revision = 'add_access_evidence_20260903'

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

        # -------------------------------------------------
        # ALASAN AKSES
        # -------------------------------------------------

        batch_op.add_column(
            sa.Column(
                'reason_code',
                sa.String(length=50),
                nullable=True
            )
        )

        # -------------------------------------------------
        # LEVEL INSIDEN
        # -------------------------------------------------

        batch_op.add_column(
            sa.Column(
                'incident_level',
                sa.String(length=20),
                nullable=True,
                server_default='NORMAL'
            )
        )

        # -------------------------------------------------
        # STATUS GERBANG
        # -------------------------------------------------

        batch_op.add_column(
            sa.Column(
                'gate_status',
                sa.String(length=20),
                nullable=True,
                server_default='CLOSED'
            )
        )

        # -------------------------------------------------
        # WAKTU GERBANG DIBUKA
        # -------------------------------------------------

        batch_op.add_column(
            sa.Column(
                'gate_opened_at',
                sa.DateTime(),
                nullable=True
            )
        )

        # -------------------------------------------------
        # WAKTU GERBANG DITUTUP
        # -------------------------------------------------

        batch_op.add_column(
            sa.Column(
                'gate_closed_at',
                sa.DateTime(),
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
            'gate_closed_at'
        )

        batch_op.drop_column(
            'gate_opened_at'
        )

        batch_op.drop_column(
            'gate_status'
        )

        batch_op.drop_column(
            'incident_level'
        )

        batch_op.drop_column(
            'reason_code'
        )