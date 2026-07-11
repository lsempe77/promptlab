import sys
sys.path.insert(0, '/app')
from backend.app import db_pg
print('pg_enabled:', db_pg.pg_enabled())
with db_pg.get_pg_conn() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name")
        tables = [r['table_name'] for r in cur.fetchall()]
print('Neon tables:', tables)
with db_pg.get_pg_conn() as conn:
    print('Pending tasks:', db_pg.pending_task_count(conn))
print('ALL OK - Postgres is live!')
