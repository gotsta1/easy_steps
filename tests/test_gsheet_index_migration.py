from importlib import import_module
from io import StringIO

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.schema import CreateIndex
from sqlalchemy.dialects import postgresql

from app.db.models import PendingInvoice


def migration_sql(direction):
    migration = import_module("migrations.versions.0021_update_gsheet_pending_index")
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    with Operations.context(context):
        getattr(migration, direction)()
    return output.getvalue()


def test_upgrade_uses_referral_and_retry_order_without_changing_payment_rows():
    sql = migration_sql("upgrade")
    assert "DROP INDEX ix_pending_invoices_gsheet_pending" in sql
    assert "(ref, gsheet_attempts, id)" in sql
    assert "WHERE paid IS TRUE AND gsheet_recorded_at IS NULL" in sql
    assert "tanya" not in sql
    assert "ref_ref" not in sql
    assert "UPDATE " not in sql and "DELETE " not in sql


def test_downgrade_restores_previous_index():
    sql = migration_sql("downgrade")
    assert "DROP INDEX ix_pending_invoices_gsheet_pending" in sql
    assert "(id)" in sql
    assert "ref = 'tanya'" in sql


def test_orm_index_matches_new_migration():
    index = next(
        i for i in PendingInvoice.__table__.indexes
        if i.name == "ix_pending_invoices_gsheet_pending"
    )
    sql = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    assert sql in migration_sql("upgrade")
