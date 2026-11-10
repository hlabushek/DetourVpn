"""Allow the Windows beta client as a device platform (I-032, ADR-095).

Revision ID: 0022_device_platform_windows
Revises: 0021_device_link_codes

Only the platform check constraint changes: `android` and `windows` are accepted, anything else
(ios, linux, ...) stays rejected. No data is rewritten. The rollback refuses while any non-Android
device exists, because restoring the old constraint would then fail or hide those devices.
"""

from alembic import op

revision: str = "0022_device_platform_windows"
down_revision: str | None = "0021_device_link_codes"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_constraint("ck_devices_platform", "devices", type_="check")
    op.create_check_constraint(
        "ck_devices_platform", "devices", "platform IN ('android', 'windows')"
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("LOCK TABLE devices IN ACCESS EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM devices WHERE platform <> 'android') THEN
            RAISE EXCEPTION 'Refusing downgrade: windows devices exist';
        END IF; END $$;""")
    op.drop_constraint("ck_devices_platform", "devices", type_="check")
    op.create_check_constraint("ck_devices_platform", "devices", "platform = 'android'")
