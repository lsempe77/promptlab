import sqlite3
conn = sqlite3.connect('/data/promptlab.db')
cur = conn.execute(
    'SELECT id, version, accepted, model_id FROM prompt_versions WHERE id=222'
)
row = cur.fetchone()
print('before:', row)
conn.execute(
    'UPDATE prompt_versions SET accepted=0 WHERE id=222'
)
conn.commit()
row = conn.execute(
    'SELECT id, version, accepted, model_id FROM prompt_versions WHERE id=222'
).fetchone()
print('after:', row)
# Confirm fallback is now the shared baseline
best = conn.execute(
    'SELECT id, version, model_id FROM prompt_versions '
    'WHERE field_name="authors" AND model_id="deepseek/deepseek-v4-flash" AND accepted=1 '
    'ORDER BY version DESC LIMIT 1'
).fetchone()
print('remaining deepseek-specific accepted:', best)
shared = conn.execute(
    'SELECT id, version FROM prompt_versions '
    'WHERE field_name="authors" AND model_id IS NULL AND accepted=1 '
    'ORDER BY version DESC LIMIT 1'
).fetchone()
print('shared baseline fallback:', shared)
conn.close()
