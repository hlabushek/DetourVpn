"""Add optional public country and city metadata to VPN nodes.

Revision ID: 0020_vpn_node_country
Revises: 0019_account_deletion

Both columns are nullable display metadata (ISO 3166-1 alpha-2 country, free-text city); they carry
no secrets and are not used for routing. Rollback drops them; no other data depends on them.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0020_vpn_node_country"
down_revision: str | None = "0019_account_deletion"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("vpn_nodes", sa.Column("country_code", sa.String(2), nullable=True))
    op.add_column("vpn_nodes", sa.Column("city", sa.String(64), nullable=True))
    op.create_check_constraint(
        "ck_vpn_nodes_country_code", "vpn_nodes", "country_code ~ '^[A-Z]{2}$'"
    )
    op.create_check_constraint("ck_vpn_nodes_city", "vpn_nodes", "length(city) BETWEEN 1 AND 64")


def downgrade() -> None:
    op.drop_constraint("ck_vpn_nodes_city", "vpn_nodes", type_="check")
    op.drop_constraint("ck_vpn_nodes_country_code", "vpn_nodes", type_="check")
    op.drop_column("vpn_nodes", "city")
    op.drop_column("vpn_nodes", "country_code")
