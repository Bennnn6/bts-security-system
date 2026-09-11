"""Add work order evidence and verification fields.

Revision ID: add_work_order_completion_fields_20260905
Revises: repair_database_schema_20260903
Create Date: 2026-09-05
"""

from alembic import op
import sqlalchemy as sa


revision = 'add_work_order_completion_fields_20260905'
down_revision = 'repair_database_schema_20260903'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('work_order', sa.Column('completion_note', sa.Text(), nullable=True))
    op.add_column('work_order', sa.Column('evidence_image', sa.Text(), nullable=True))
    op.add_column('work_order', sa.Column('completed_at', sa.DateTime(), nullable=True))
    op.add_column('work_order', sa.Column('verified_at', sa.DateTime(), nullable=True))
    op.add_column('work_order', sa.Column('verified_by', sa.String(length=64), nullable=True))


def downgrade():
    op.drop_column('work_order', 'verified_by')
    op.drop_column('work_order', 'verified_at')
    op.drop_column('work_order', 'completed_at')
    op.drop_column('work_order', 'evidence_image')
    op.drop_column('work_order', 'completion_note')
