import sys
sys.path.insert(0, '/app')
from backend.app import db_pg
print('pg_enabled:', db_pg.pg_enabled())
db_pg.init_pg()
print('Schema created OK')
print('Pending tasks:', db_pg.pending_task_count(db_pg._get_pool().getconn()))
