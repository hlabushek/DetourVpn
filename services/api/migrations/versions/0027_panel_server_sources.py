"""Panel (3x-ui / Remnawave) servers imported by a vless:// Reality link (ADR-111).

Revision ID: 0027_panel_server_sources
Revises: 0026_telegram_username

One new table. The link is stored only as Fernet ciphertext. Rollback refuses while any panel
server row exists, because dropping the table would destroy the only copy of the imported link.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0027_panel_server_sources"
down_revision: str | None = "0026_telegram_username"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "panel_server_sources",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("label", sa.String(64), nullable=False),
        sa.Column("panel_kind", sa.String(16), nullable=False),
        sa.Column("protocol", sa.String(16), nullable=False),
        sa.Column("link_ciphertext", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["admin_identities.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("length(label) BETWEEN 1 AND 64", name="ck_panel_server_label"),
        sa.CheckConstraint("panel_kind IN ('3x-ui', 'remnawave')", name="ck_panel_server_kind"),
        sa.CheckConstraint("protocol = 'vless_reality'", name="ck_panel_server_protocol"),
    )
    op.create_index("ix_panel_server_sources_created", "panel_server_sources", ["created_at", "id"])


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("LOCK TABLE panel_server_sources IN ACCESS EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM panel_server_sources) THEN
            RAISE EXCEPTION 'Refusing downgrade: panel servers exist';
        END IF; END $$;""")
    op.drop_index("ix_panel_server_sources_created", table_name="panel_server_sources")
    op.drop_table("panel_server_sources")
