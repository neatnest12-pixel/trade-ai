"""Dataset and experiment audit records."""
from alembic import op
import sqlalchemy as sa
revision="0002";down_revision="0001";branch_labels=None;depends_on=None
def upgrade():
    op.create_table("dataset_versions",sa.Column("version",sa.String(80),primary_key=True),sa.Column("symbol",sa.String(20),nullable=False),sa.Column("timeframe",sa.String(10),nullable=False),sa.Column("source",sa.String(50),nullable=False),sa.Column("broker",sa.String(100)),sa.Column("timezone",sa.String(30),nullable=False),sa.Column("start_date",sa.DateTime(timezone=True),nullable=False),sa.Column("end_date",sa.DateTime(timezone=True),nullable=False),sa.Column("rows",sa.Integer(),nullable=False),sa.Column("retrieved_at",sa.DateTime(timezone=True),nullable=False),sa.Column("sha256",sa.String(64),unique=True,nullable=False),sa.Column("quality_report",sa.JSON(),nullable=False))
    op.create_table("experiments",sa.Column("id",sa.String(80),primary_key=True),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),sa.Column("dataset_version",sa.String(80),sa.ForeignKey("dataset_versions.version"),nullable=False),sa.Column("strategy_version",sa.String(80),nullable=False),sa.Column("parameters",sa.JSON(),nullable=False),sa.Column("training_dates",sa.JSON(),nullable=False),sa.Column("validation_dates",sa.JSON(),nullable=False),sa.Column("test_dates",sa.JSON(),nullable=False),sa.Column("results",sa.JSON(),nullable=False))
def downgrade():
    op.drop_table("experiments");op.drop_table("dataset_versions")
