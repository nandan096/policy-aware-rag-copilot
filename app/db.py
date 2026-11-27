from contextlib import contextmanager
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from app.config import settings

# Reusable pool of open connections to Neon Postgres with health checks for idle timeouts
pool = ConnectionPool(
    conninfo=settings.database_url,
    min_size=1,
    max_size=5,
    check=ConnectionPool.check_connection,
    kwargs={"row_factory": dict_row},
    open=False,
)

def init_pool() -> None:
    pool.open()

def close_pool() -> None:
    pool.close()

@contextmanager
def get_db_cursor():
    with pool.connection() as conn:
        with conn.cursor() as cur:
            yield cur