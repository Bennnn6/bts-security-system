"""Add update and activity audit fields.

Revision ID: add_model_update_fields_20260903
Revises: add_security_fields_20260903
Create Date: 2026-09-03
"""

from alembic import op
import sqlalchemy as sa


# =========================================================
# ALEMBIC REVISION
# =========================================================

revision = 'add_model_update_fields_20260903'

down_revision = 'add_security_fields_20260903'

branch_labels = None

depends_on = None


# =========================================================
# UPGRADE
# =========================================================

def upgrade():

    # -----------------------------------------------------
    # TECHNICIAN
    # -----------------------------------------------------

    with op.batch_alter_table(
        'technician',
        schema=None
    ) as batch_op:

        batch_op.add_column(
            sa.Column(
                'updated_at',
                sa.DateTime(),
                nullable=True
            )
        )

    # -----------------------------------------------------
    # BTS SITE
    # -----------------------------------------------------

    with op.batch_alter_table(
        'bts_site',
        schema=None
    ) as batch_op:

        batch_op.add_column(
            sa.Column(
                'updated_at',
                sa.DateTime(),
                nullable=True
            )
        )

    # -----------------------------------------------------
    # ACTIVITY LOG
    # -----------------------------------------------------

    with op.batch_alter_table(
        'activity_log',
        schema=None
    ) as batch_op:

        batch_op.add_column(
            sa.Column(
                'incident_level',
                sa.String(length=20),
                nullable=True,
                server_default='NORMAL'
            )
        )

        batch_op.add_column(
            sa.Column(
                'device_id',
                sa.String(length=500),
                nullable=True
            )
        )


# =========================================================
# DOWNGRADE
# =========================================================

def downgrade():

    with op.batch_alter_table(
        'activity_log',
        schema=None
    ) as batch_op:

        batch_op.drop_column(
            'device_id'
        )

        batch_op.drop_column(
            'incident_level'
        )

    with op.batch_alter_table(
        'bts_site',
        schema=None
    ) as batch_op:

        batch_op.drop_column(
            'updated_at'
        )

    with op.batch_alter_table(
        'technician',
        schema=None
    ) as batch_op:

        batch_op.drop_column(
            'updated_at'
        )