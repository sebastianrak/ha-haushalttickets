"""Free categories for tickets and series; users hidden from the board.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("color", sa.String(length=7), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_categories")),
        sa.UniqueConstraint("name", name=op.f("uq_categories_name")),
    )
    for table in ("tickets", "series"):
        op.add_column(table, sa.Column("category_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            op.f(f"fk_{table}_category_id_categories"),
            table,
            "categories",
            ["category_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index(op.f("ix_tickets_category_id"), "tickets", ["category_id"])
    op.add_column(
        "users",
        sa.Column(
            "board_visible", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "board_visible")
    op.drop_index(op.f("ix_tickets_category_id"), table_name="tickets")
    for table in ("series", "tickets"):
        op.drop_constraint(
            op.f(f"fk_{table}_category_id_categories"), table, type_="foreignkey"
        )
        op.drop_column(table, "category_id")
    op.drop_table("categories")
