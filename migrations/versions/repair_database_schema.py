"""Repair database schema for final models.

Revision ID: repair_database_schema_20260903
Revises: add_model_update_fields_20260903
Create Date: 2026-09-03
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'repair_database_schema_20260903'

down_revision = 'add_model_update_fields_20260903'

branch_labels = None

depends_on = None


def column_exists(
    inspector,
    table_name,
    column_name
):
    columns = inspector.get_columns(
        table_name
    )

    return any(
        column['name'] == column_name
        for column in columns
    )


def upgrade():

    bind = op.get_bind()

    inspector = inspect(bind)

    # =====================================================
    # TECHNICIAN
    # =====================================================

    if not column_exists(
        inspector,
        'technician',
        'updated_at'
    ):

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

    # =====================================================
    # BTS SITE
    # =====================================================

    inspector = inspect(bind)

    if not column_exists(
        inspector,
        'bts_site',
        'updated_at'
    ):

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

    # =====================================================
    # ACTIVITY LOG - INCIDENT LEVEL
    # =====================================================

    inspector = inspect(bind)

    if not column_exists(
        inspector,
        'activity_log',
        'incident_level'
    ):

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

    # =====================================================
    # ACTIVITY LOG - DEVICE ID
    # =====================================================

    inspector = inspect(bind)

    if not column_exists(
        inspector,
        'activity_log',
        'device_id'
    ):

        with op.batch_alter_table(
            'activity_log',
            schema=None
        ) as batch_op:

            batch_op.add_column(
                sa.Column(
                    'device_id',
                    sa.String(length=500),
                    nullable=True
                )
            )

    # =====================================================
    # ACCESS LOG - EVIDENCE IMAGE
    # =====================================================

    inspector = inspect(bind)

    if not column_exists(
        inspector,
        'access_log',
        'evidence_image'
    ):

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

    # =====================================================
    # ACCESS LOG - REASON CODE
    # =====================================================

    inspector = inspect(bind)

    if not column_exists(
        inspector,
        'access_log',
        'reason_code'
    ):

        with op.batch_alter_table(
            'access_log',
            schema=None
        ) as batch_op:

            batch_op.add_column(
                sa.Column(
                    'reason_code',
                    sa.String(length=50),
                    nullable=True
                )
            )

    # =====================================================
    # ACCESS LOG - INCIDENT LEVEL
    # =====================================================

    inspector = inspect(bind)

    if not column_exists(
        inspector,
        'access_log',
        'incident_level'
    ):

        with op.batch_alter_table(
            'access_log',
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

    # =====================================================
    # ACCESS LOG - GATE STATUS
    # =====================================================

    inspector = inspect(bind)

    if not column_exists(
        inspector,
        'access_log',
        'gate_status'
    ):

        with op.batch_alter_table(
            'access_log',
            schema=None
        ) as batch_op:

            batch_op.add_column(
                sa.Column(
                    'gate_status',
                    sa.String(length=20),
                    nullable=True,
                    server_default='CLOSED'
                )
            )

    # =====================================================
    # ACCESS LOG - GATE OPENED AT
    # =====================================================

    inspector = inspect(bind)

    if not column_exists(
        inspector,
        'access_log',
        'gate_opened_at'
    ):

        with op.batch_alter_table(
            'access_log',
            schema=None
        ) as batch_op:

            batch_op.add_column(
                sa.Column(
                    'gate_opened_at',
                    sa.DateTime(),
                    nullable=True
                )
            )

    # =====================================================
    # ACCESS LOG - GATE CLOSED AT
    # =====================================================

    inspector = inspect(bind)

    if not column_exists(
        inspector,
        'access_log',
        'gate_closed_at'
    ):

        with op.batch_alter_table(
            'access_log',
            schema=None
        ) as batch_op:

            batch_op.add_column(
                sa.Column(
                    'gate_closed_at',
                    sa.DateTime(),
                    nullable=True
                )
            )


def downgrade():

    bind = op.get_bind()

    inspector = inspect(bind)

    # -----------------------------------------------------
    # ACCESS LOG
    # -----------------------------------------------------

    access_log_columns = [
        'gate_closed_at',
        'gate_opened_at',
        'gate_status',
        'incident_level',
        'reason_code',
        'evidence_image'
    ]

    for column_name in access_log_columns:

        inspector = inspect(bind)

        if column_exists(
            inspector,
            'access_log',
            column_name
        ):

            with op.batch_alter_table(
                'access_log',
                schema=None
            ) as batch_op:

                batch_op.drop_column(
                    column_name
                )

    # -----------------------------------------------------
    # ACTIVITY LOG
    # -----------------------------------------------------

    for column_name in (
        'device_id',
        'incident_level'
    ):

        inspector = inspect(bind)

        if column_exists(
            inspector,
            'activity_log',
            column_name
        ):

            with op.batch_alter_table(
                'activity_log',
                schema=None
            ) as batch_op:

                batch_op.drop_column(
                    column_name
                )

    # -----------------------------------------------------
    # BTS SITE
    # -----------------------------------------------------

    inspector = inspect(bind)

    if column_exists(
        inspector,
        'bts_site',
        'updated_at'
    ):

        with op.batch_alter_table(
            'bts_site',
            schema=None
        ) as batch_op:

            batch_op.drop_column(
                'updated_at'
            )

    # -----------------------------------------------------
    # TECHNICIAN
    # -----------------------------------------------------

    inspector = inspect(bind)

    if column_exists(
        inspector,
        'technician',
        'updated_at'
    ):

        with op.batch_alter_table(
            'technician',
            schema=None
        ) as batch_op:

            batch_op.drop_column(
                'updated_at'
            )