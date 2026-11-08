"""Add one-time device link codes, guest sessions and per-device label/traffic cap.

Revision ID: 0021_device_link_codes
Revises: 0020_vpn_node_country

Rollback drops the new table and columns. It refuses while any guest device or guest session
exists, because dropping `role` would silently promote guests to owners.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0021_device_link_codes"
down_revision: str | None = "0020_vpn_node_country"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "device_link_codes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("code_digest", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(40), nullable=True),
        sa.Column("traffic_cap_bytes", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_device_id", sa.Uuid(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["consumed_device_id"], ["devices.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("code_digest", name="uq_device_link_codes_digest"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_device_link_codes_key"),
        sa.CheckConstraint("length(label) BETWEEN 1 AND 40", name="ck_device_link_codes_label"),
        sa.CheckConstraint("traffic_cap_bytes > 0", name="ck_device_link_codes_cap"),
        sa.CheckConstraint("expires_at > created_at", name="ck_device_link_codes_expiry"),
    )
    op.create_index("ix_device_link_codes_user_id", "device_link_codes", ["user_id"])

    op.add_column(
        "devices",
        sa.Column("role", sa.String(8), nullable=False, server_default=sa.text("'owner'")),
    )
    op.add_column("devices", sa.Column("label", sa.String(40), nullable=True))
    op.add_column("devices", sa.Column("traffic_cap_bytes", sa.BigInteger(), nullable=True))
    op.create_check_constraint("ck_devices_role", "devices", "role IN ('owner', 'guest')")
    op.create_check_constraint("ck_devices_label", "devices", "length(label) BETWEEN 1 AND 40")
    op.create_check_constraint("ck_devices_traffic_cap", "devices", "traffic_cap_bytes > 0")

    op.add_column(
        "session_families",
        sa.Column("role", sa.String(8), nullable=False, server_default=sa.text("'owner'")),
    )
    op.add_column("session_families", sa.Column("link_code_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "session_families_link_code_id_fkey",
        "session_families",
        "device_link_codes",
        ["link_code_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_session_families_role", "session_families", "role IN ('owner', 'guest')"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(
        sa.text(
            "SELECT 1 FROM devices WHERE role = 'guest' "
            "UNION ALL SELECT 1 FROM session_families WHERE role = 'guest' LIMIT 1"
        )
    ).first():
        raise RuntimeError(
            "guest devices or sessions exist; downgrade would promote them to owners"
        )
    op.drop_constraint("ck_session_families_role", "session_families", type_="check")
    op.drop_constraint("session_families_link_code_id_fkey", "session_families", type_="foreignkey")
    op.drop_column("session_families", "link_code_id")
    op.drop_column("session_families", "role")
    op.drop_constraint("ck_devices_traffic_cap", "devices", type_="check")
    op.drop_constraint("ck_devices_label", "devices", type_="check")
    op.drop_constraint("ck_devices_role", "devices", type_="check")
    op.drop_column("devices", "traffic_cap_bytes")
    op.drop_column("devices", "label")
    op.drop_column("devices", "role")
    op.drop_index("ix_device_link_codes_user_id", table_name="device_link_codes")
    op.drop_table("device_link_codes")
