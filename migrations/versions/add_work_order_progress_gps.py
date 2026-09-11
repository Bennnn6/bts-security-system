"""Add work order progress and GPS fields.

Revision ID: add_work_order_progress_gps_20260905
Revises: add_work_order_completion_fields_20260905
Create Date: 2026-09-05
"""

from alembic import op
import sqlalchemy as sa


revision = 'add_work_order_progress_gps_20260905'
down_revision = 'add_work_order_completion_fields_20260905'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('work_order', sa.Column('started_at', sa.DateTime(), nullable=True))
    op.add_column('work_order', sa.Column('start_latitude', sa.String(length=40), nullable=True))
    op.add_column('work_order', sa.Column('start_longitude', sa.String(length=40), nullable=True))
    op.add_column('work_order', sa.Column('completion_latitude', sa.String(length=40), nullable=True))
    op.add_column('work_order', sa.Column('completion_longitude', sa.String(length=40), nullable=True))


def downgrade():
    op.drop_column('work_order', 'completion_longitude')
    op.drop_column('work_order', 'completion_latitude')
    op.drop_column('work_order', 'start_longitude')
    op.drop_column('work_order', 'start_latitude')
    op.drop_column('work_order', 'started_at')
