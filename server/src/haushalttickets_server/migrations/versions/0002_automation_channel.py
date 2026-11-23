"""Channel "automation" and the user "Automation" for Home Assistant automations.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = ("ui", "mcp", "nightly_run", "recurrence", "emergency_ui", "system")
_NEW = (
    "ui",
    "mcp",
    "nightly_run",
    "recurrence",
    "emergency_ui",
    "automation",
    "system",
)

# (table, column) pairs holding a Via value.
_VIA_COLUMNS = (
    ("tickets", "created_via"),
    ("comments", "via"),
    ("history", "via"),
    ("attachments", "uploaded_via"),
    ("events", "via"),
)


def _recreate(values: tuple[str, ...]) -> None:
    allowed = ", ".join(f"'{v}'" for v in values)
    for table, column in _VIA_COLUMNS:
        name = f"ck_{table}_{column}"
        op.drop_constraint(op.f(name), table, type_="check")
        op.create_check_constraint(op.f(name), table, f"{column} IN ({allowed})")


def upgrade() -> None:
    _recreate(_NEW)
    op.execute(
        sa.text(
            "INSERT INTO users (ha_user_id, display_name, role, ha_is_admin, active) "
            "VALUES ('automation', 'Automation', 'adult', false, true) "
            "ON CONFLICT (ha_user_id) DO NOTHING"
        )
    )


def downgrade() -> None:
    for table, column in _VIA_COLUMNS:
        op.execute(
            sa.text(
                f"UPDATE {table} SET {column} = 'system' WHERE {column} = 'automation'"
            )
        )
    _recreate(_OLD)
    # The user row stays: tickets may still reference it as requester or assignee.
