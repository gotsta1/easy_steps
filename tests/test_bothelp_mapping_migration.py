from importlib import import_module
from io import StringIO

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import UniqueConstraint

from app.db.models import User


def test_mapping_migration_enforces_uniqueness_without_touching_access_or_payments():
    migration = import_module("migrations.versions.0022_harden_bothelp_mapping")
    out = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": out}
    )
    with Operations.context(context):
        migration.upgrade()
    sql = out.getvalue()
    assert "UNIQUE (bothelp_subscriber_id)" in sql
    assert "bothelp_mapping_verified_at" in sql
    assert "bothelp_status_next_retry_at" in sql
    assert "UPDATE entitlements" not in sql
    assert "UPDATE pending_invoices" not in sql
    assert "DELETE" not in sql


def test_orm_has_matching_subscriber_uniqueness_constraint():
    constraints = [c for c in User.__table__.constraints if isinstance(c, UniqueConstraint)]
    assert any(c.name == "uq_users_bothelp_subscriber_id" for c in constraints)
