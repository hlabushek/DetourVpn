"""Android public-key registry and session ownership; no tunnel creation."""

import sqlalchemy as sa
from alembic import op

revision: str = "0003_devices"
down_revision: str | None = "0002_authentication"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "devices",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("key_type", sa.String(16), nullable=False),
        sa.Column("public_key", sa.String(44), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("platform = 'android'", name="ck_devices_platform"),
        sa.CheckConstraint("key_type = 'x25519'", name="ck_devices_key_type"),
        sa.UniqueConstraint("public_key", name="uq_devices_public_key"),
        sa.UniqueConstraint("id", "user_id", name="uq_devices_id_user_id"),
    )
    op.create_index("ix_devices_user_id_revoked_at", "devices", ["user_id", "revoked_at"])
    op.add_column("session_families", sa.Column("device_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_session_families_device_owner",
        "session_families",
        "devices",
        ["device_id", "user_id"],
        ["id", "user_id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_session_families_device_id", "session_families", ["device_id"])


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("LOCK TABLE devices, session_families IN ACCESS EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM devices) THEN
            RAISE EXCEPTION 'Refusing downgrade: devices contain data';
        END IF; END $$;""")
    op.drop_index("ix_session_families_device_id", table_name="session_families")
    op.drop_constraint("fk_session_families_device_owner", "session_families", type_="foreignkey")
    op.drop_column("session_families", "device_id")
    op.drop_table("devices")
