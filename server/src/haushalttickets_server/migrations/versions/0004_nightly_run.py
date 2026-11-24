"""Nightly run: approval bound to a draft version, run results, restart hint.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
_OLD = ("no_work", "quota_low", "done", "failed")
_NEW = (*_OLD, "aborted")


def _outcome_check(values: tuple[str, ...]) -> None:
    op.drop_constraint(op.f("ck_nightly_runs_outcome"), "nightly_runs", type_="check")
    op.create_check_constraint(
        op.f("ck_nightly_runs_outcome"),
        "nightly_runs",
        "outcome IN ({})".format(", ".join(f"'{value}'" for value in values)),
    )


def upgrade() -> None:
    op.add_column(
        "tickets", sa.Column("ai_implement_draft_version", sa.Integer(), nullable=True)
    )
    op.add_column(
        "nightly_runs",
        sa.Column("results", _JSON, nullable=False, server_default="[]"),
    )
    op.add_column(
        "nightly_runs",
        sa.Column("usage", _JSON, nullable=False, server_default="{}"),
    )
    _outcome_check(_NEW)
    op.add_column(
        "change_log",
        sa.Column(
            "needs_restart", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )


def downgrade() -> None:
    op.drop_column("change_log", "needs_restart")
    op.execute("UPDATE nightly_runs SET outcome = 'failed' WHERE outcome = 'aborted'")
    _outcome_check(_OLD)
    op.drop_column("nightly_runs", "usage")
    op.drop_column("nightly_runs", "results")
    op.drop_column("tickets", "ai_implement_draft_version")
