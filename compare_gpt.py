import sqlite3, json, sys
sys.path.insert(0,'.')
from backend.app import analytics

backup = sqlite3.connect(r'..\DEP\backups\promptlab_prod_20260708_214047.db')
backup.row_factory = sqlite3.Row
FIELDS = ['authors','author_country','author_affiliation','sector_name','sub_sector']
GATE = 0.90

for mid in ['~openai/gpt-latest', '~openai/gpt-mini-latest', '~anthropic/claude-sonnet-latest']:
    print(f'--- {mid} ---')
    total_cost = 0
    for field in FIELDS:
        pv = backup.execute('SELECT id FROM prompt_versions WHERE field_name=? AND model_id=? AND accepted=1 ORDER BY version DESC LIMIT 1',(field,mid)).fetchone()
        if not pv:
            pv = backup.execute('SELECT id FROM prompt_versions WHERE field_name=? AND model_id IS NULL AND accepted=1 ORDER BY version DESC LIMIT 1',(field,)).fetchone()
        if not pv: continue
        rows = backup.execute('SELECT r.parsed_value_json, g.value_json FROM runs r JOIN ground_truth g ON g.record_id=r.record_id AND g.field_name=r.field_name WHERE r.model_id=? AND r.field_name=? AND r.prompt_version_id=? AND r.parsed_value_json IS NOT NULL',(mid,field,pv[0])).fetchall()
        if not rows: continue
        preds = [{'predicted': json.loads(r[0]), 'truth': json.loads(r[1])} for r in rows]
        gm = analytics.gate_metrics(field, preds)
        metric = gm.get('metric',0)
        recall = gm.get('recall')
        cost_row = backup.execute('SELECT SUM(cost_usd) FROM runs WHERE model_id=? AND field_name=?',(mid,field)).fetchone()
        cost = cost_row[0] or 0
        total_cost += cost
        tag = 'PASS' if metric >= GATE and (recall is None or recall >= 0.85) else ''
        recall_str = f'  recall={recall:.3f}' if recall else ''
        print(f'  {field:<22} {metric:.3f}{recall_str}  n={gm.get("n",0):>3}  cost=${cost:.3f}  {tag}')
    print(f'  TOTAL cost: ${total_cost:.3f}')
    print()

backup.close()
