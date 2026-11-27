from contextlib import contextmanager
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from app.config import settings

# 1. Create a reusable pool of open connections to Neon Postgres
pool = ConnectionPool(
    conninfo=settings.database_url,
    min_size=1,
    max_size=5,
    kwargs={"row_factory": dict_row},   #Tells Postgres to return every row as a readable dictionary
    open=False,
)


# 2. Helper functions to open/close the pool cleanly when FastAPI starts/stops
def init_pool() -> None:
    pool.open()
    

def close_pool() -> None:
    pool.close()


# 3. Safe context manager to borrow a connection and automatically return it
@contextmanager
def get_db_cursor():
    with pool.connection() as conn:
        with conn.cursor() as cur:
            yield cur