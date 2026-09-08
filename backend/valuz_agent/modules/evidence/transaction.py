"""Keep SQLite savepoints inside the caller's actual transaction."""

from sqlalchemy import Connection


def ensure_outer_transaction(connection: Connection) -> None:
    # Python sqlite's legacy transaction mode does not BEGIN for SELECT or
    # SAVEPOINT. Releasing such a first savepoint would otherwise commit its
    # writes even when the caller subsequently rolls back the SQLAlchemy UOW.
    if connection.dialect.name == "sqlite":
        driver = connection.connection.driver_connection
        if not driver.in_transaction:
            connection.exec_driver_sql("BEGIN")
