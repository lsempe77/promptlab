#!/bin/sh
cd /app
python -c "
import sys
sys.path.insert(0, '/app')
from backend.app import db
with db.get_conn() as conn:
    project_id = db.get_project_id(conn, 'dep-extraction')
    from backend.app.api import _best_pvids_per_model
    mp = _best_pvids_per_model(conn, project_id, 'authors')
    print('model_pvids count:', len(mp))
    for k,v in list(mp.items())[:3]:
        print('  ', k, '->', v)
    pvids = list(set(mp.values()))
    rows = conn.execute(
        'SELECT id, version FROM prompt_versions WHERE id IN ({})'.format(','.join('?'*len(pvids))),
        pvids
    ).fetchall()
    print('pvid_to_version:')
    for r in rows:
        print(' ', r['id'], '->', r['version'])
"
