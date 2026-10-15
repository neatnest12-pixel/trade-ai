"""Add immutable frozen-V1 identity fields while preserving pre-freeze records."""
import sqlalchemy as sa
from alembic import op

revision="0003"; down_revision="0002"; branch_labels=None; depends_on=None

def upgrade():
    op.add_column("predictions", sa.Column("v1_snapshot_id", sa.String(80), nullable=True))
    op.add_column("predictions", sa.Column("v1_config_hash", sa.String(64), nullable=True))
    op.create_index("ix_predictions_v1_snapshot_id", "predictions", ["v1_snapshot_id"])
    op.create_index("ix_predictions_v1_config_hash", "predictions", ["v1_config_hash"])

def downgrade():
    op.drop_index("ix_predictions_v1_config_hash", table_name="predictions")
    op.drop_index("ix_predictions_v1_snapshot_id", table_name="predictions")
    op.drop_column("predictions", "v1_config_hash")
    op.drop_column("predictions", "v1_snapshot_id")
