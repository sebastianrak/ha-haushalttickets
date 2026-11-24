"""Recurring series: calendar anchor, checklist and type fields of the template.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column("series", sa.Column("anchor", sa.Date(), nullable=True))
    op.add_column(
        "series",
        sa.Column("checklist", _JSON, nullable=False, server_default="[]"),
    )
    op.add_column(
        "series",
        sa.Column("fields", _JSON, nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("series", "fields")
    op.drop_column("series", "checklist")
    op.drop_column("series", "anchor")
