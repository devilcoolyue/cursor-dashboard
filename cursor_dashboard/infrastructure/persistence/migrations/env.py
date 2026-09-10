from alembic import context
from cursor_dashboard.infrastructure.persistence.models import Base

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("Use cursor-core upgrade so the runtime lock and transaction are held")
context.configure(connection=connection, target_metadata=Base.metadata,
                  transactional_ddl=True, render_as_batch=True)
with context.begin_transaction():
    context.run_migrations()
