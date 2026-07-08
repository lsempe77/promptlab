import sqlite3, json, sys
sys.path.insert(0,'.')
from backend.app import analytics, scoring

conn = sqlite3.connect('promptlab_inspect.db')
conn.row_factory = sqlite3.Row

fields = ['authors','author_country','author_affiliation','sector_name','sub_sector']
models = [r[0] for r in conn.execute('SELECT DISTINCT model_id FROM runs WHERE model_id IS NOT NULL ORDER BY model_id').fetchall()]

THRESHOLD = scoring.GATE_THRESHOLD

# 1. Gate metrics
print("=== GATE METRICS (F1 / accuracy) per field/model ===")
all_results = {}
for field in fields:
    all_results[field] = {}
    for mid in models:
        pv = conn.execute('SELECT id FROM prompt_versions WHERE field_name=? AND model_id=? AND accepted=1 ORDER BY version DESC LIMIT 1',(field,mid)).fetchone()
        if not pv:
            pv = conn.execute('SELECT id FROM prompt_versions WHERE field_name=? AND model_id IS NULL AND accepted=1 ORDER BY version DESC LIMIT 1',(field,)).fetchone()
        if not pv: continue
        rows = conn.execute(
            'SELECT r.parsed_value_json, g.value_json FROM runs r '
            'JOIN ground_truth g ON g.record_id=r.record_id AND g.field_name=r.field_name '
            'WHERE r.model_id=? AND r.field_name=? AND r.prompt_version_id=? AND r.parsed_value_json IS NOT NULL',
            (mid,field,pv[0])
        ).fetchall()
        if len(rows) < 5: continue
        preds = [{'predicted': json.loads(r[0]), 'truth': json.loads(r[1])} for r in rows]
        gm = analytics.gate_metrics(field, preds)
        all_results[field][mid] = gm
        m_short = mid.split('/')[-1][:28]
        metric = gm.get('metric',0)
        passes = 'PASS' if metric >= THRESHOLD else '    '
        print(f"  {field:<22} {m_short:<30} {metric:.3f}  n={gm.get('n',0):>3}  {passes}")

print()
print("=== SUMMARY: models passing gate per field ===")
for field in fields:
    passing = sum(1 for v in all_results[field].values() if v.get('metric',0) >= THRESHOLD)
    total = len(all_results[field])
    metrics = sorted([v.get('metric',0) for v in all_results[field].values()], reverse=True)
    best = metrics[0] if metrics else 0
    worst = metrics[-1] if metrics else 0
    median = metrics[len(metrics)//2] if metrics else 0
    print(f"  {field:<22} {passing:>2}/{total} pass  best={best:.3f} med={median:.3f} worst={worst:.3f}")

print()
print("=== OPTIMIZER IMPROVEMENT: baseline vs best achieved ===")
iter_summary = {}
for r in conn.execute(
    'SELECT field_name, model_id, MIN(mean_score) as min_s, MAX(mean_score) as max_s, COUNT(*) as n_iters, SUM(accepted) as n_accepted '
    'FROM iterations GROUP BY field_name, model_id ORDER BY field_name, model_id'
).fetchall():
    fn, mid, mn, mx, ni, na = r
    delta = mx - mn if mn else 0
    m_short = str(mid).split('/')[-1][:28] if mid else '-'
    print(f"  {fn:<22} {m_short:<30} iters={ni:>2} accepted={na}  baseline_min={mn:.3f} peak={mx:.3f} delta=+{delta:.3f}")

print()
print("=== ERROR ANALYSIS: error rates per field/model ===")
for r in conn.execute(
    'SELECT r.field_name, r.model_id, COUNT(*) as total, SUM(CASE WHEN r.error IS NOT NULL THEN 1 ELSE 0 END) as errors, '
    'SUM(CASE WHEN r.parsed_value_json IS NULL AND r.error IS NULL THEN 1 ELSE 0 END) as null_parse '
    'FROM runs r GROUP BY r.field_name, r.model_id ORDER BY r.field_name, errors DESC'
).fetchall():
    fn, mid, total, errors, null_parse = r
    if total < 10: continue
    m_short = str(mid).split('/')[-1][:25] if mid else '-'
    err_pct = 100*errors/total if total else 0
    if err_pct > 5:
        print(f"  {fn:<22} {m_short:<27} total={total:>4} errors={errors:>3} ({err_pct:.0f}%)")

print()
print("=== COST & CARBON summary ===")
for r in conn.execute(
    'SELECT r.field_name, r.model_id, COUNT(*) as runs, SUM(r.cost_usd) as total_cost, SUM(r.co2e_grams) as total_co2 '
    'FROM runs r WHERE r.cost_usd IS NOT NULL GROUP BY r.field_name, r.model_id '
    'ORDER BY total_cost DESC LIMIT 20'
).fetchall():
    fn, mid, runs, cost, co2 = r
    m_short = str(mid).split('/')[-1][:25] if mid else '-'
    print(f"  {fn:<22} {m_short:<27} runs={runs:>5} cost=${cost:.3f} co2={co2:.0f}g")

print()
print("=== GROUND TRUTH: cross-model agreement (how consistent are models?) ===")
for r in conn.execute(
    'SELECT field_name, COUNT(DISTINCT model_id) as n_models, AVG(is_correct) as mean_correct '
    'FROM runs WHERE parsed_value_json IS NOT NULL GROUP BY field_name'
).fetchall():
    fn, nm, mc = r
    print(f"  {fn:<22} n_models={nm:>2}  mean_is_correct={mc:.3f}")

conn.close()
print("\nDone.")
