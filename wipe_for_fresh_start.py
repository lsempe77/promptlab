"""
Fresh-start DB wipe for DEP prompt lab.
Keeps: projects, records, ground_truth, prompt_versions WHERE model_id IS NULL AND version=1
Wipes: runs, iterations, llm_judgments, jobs, self_consistency_runs, prompt_versions (all others)
"""
import sqlite3

DB = '/data/promptlab.db'
conn = sqlite3.connect(DB)

print("Counting before wipe...")
for t in ['runs','iterations','llm_judgments','jobs','prompt_versions']:
    n = conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
    print(f"  {t}: {n}")

print("\nWiping...")

# Keep only the shared v1 baseline prompts (model_id IS NULL, version=1)
conn.execute("DELETE FROM prompt_versions WHERE NOT (model_id IS NULL AND version = 1)")
conn.execute("DELETE FROM runs")
conn.execute("DELETE FROM iterations")
conn.execute("DELETE FROM llm_judgments")
conn.execute("DELETE FROM jobs")

# self_consistency_runs if it exists
try:
    conn.execute("DELETE FROM self_consistency_runs")
except Exception:
    pass

conn.commit()

print("\nCounting after wipe...")
for t in ['runs','iterations','llm_judgments','jobs','prompt_versions']:
    n = conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
    print(f"  {t}: {n}")

# Verify GT is intact
gt = conn.execute('SELECT field_name, COUNT(*) as n FROM ground_truth GROUP BY field_name').fetchall()
print("\nGround truth preserved:")
for fn, n in gt:
    print(f"  {fn}: {n} records")

proj = conn.execute('SELECT COUNT(*) FROM projects').fetchone()[0]
rec = conn.execute('SELECT COUNT(*) FROM records').fetchone()[0]
print(f"\nProjects: {proj}  |  Records: {rec}")

conn.close()
print("\nDone. Fresh start ready.")
