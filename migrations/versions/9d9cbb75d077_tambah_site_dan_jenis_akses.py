"""Tambah site dan jenis akses

Revision ID: 9d9cbb75d077
Revises:
Create Date: 2026-09-02
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9d9cbb75d077'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():

    # =====================================================
    # TAMBAH KOLOM site_id
    # =====================================================

    with op.batch_alter_table(
        'access_log',
        schema=None
    ) as batch_op:

        batch_op.add_column(
            sa.Column(
                'site_id',
                sa.String(length=50),
                nullable=True
            )
        )

        batch_op.add_column(
            sa.Column(
                'access_type',
                sa.String(length=20),
                nullable=True
            )
        )


    # =====================================================
    # TAMBAH INDEX site_id
    # =====================================================

    op.create_index(
        'ix_access_log_site_id',
        'access_log',
        ['site_id'],
        unique=False
    )


def downgrade():

    # =====================================================
    # HAPUS INDEX
    # =====================================================

    op.drop_index(
        'ix_access_log_site_id',
        table_name='access_log'
    )


    # =====================================================
    # HAPUS KOLOM
    # =====================================================

    with op.batch_alter_table(
        'access_log',
        schema=None
    ) as batch_op:

        batch_op.drop_column(
            'access_type'
        )

        batch_op.drop_column(
            'site_id'
        )