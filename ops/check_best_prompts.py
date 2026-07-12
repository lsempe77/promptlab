import sqlite3

backup = sqlite3.connect(r'..\DEP\backups\promptlab_prod_20260708_214047.db')
backup.row_factory = sqlite3.Row

print("=== Best accepted SHARED prompts (model_id IS NULL) ===")
for field in ['authors','author_country','author_affiliation','sector_name','sub_sector']:
    row = backup.execute(
        'SELECT id, version, template FROM prompt_versions '
        'WHERE field_name=? AND model_id IS NULL AND accepted=1 ORDER BY version DESC LIMIT 1',
        (field,)
    ).fetchone()
    if row:
        pv_id = row['id']
        v = row['version']
        t = row['template']
        print(f"\n--- {field} (pv={pv_id} v={v} len={len(t)}) ---")
        print(t[:800])
        if len(t) > 800: print("...")

print()
print("=== Best val-score accepted per-model prompts per field ===")
for field in ['sector_name','sub_sector','authors','author_affiliation']:
    row = backup.execute(
        'SELECT pv.model_id, pv.version, pv.id, i.mean_score, pv.template '
        'FROM iterations i JOIN prompt_versions pv ON i.prompt_version_id = pv.id '
        'WHERE i.field_name=? AND i.accepted=1 ORDER BY i.mean_score DESC LIMIT 1',
        (field,)
    ).fetchone()
    if row:
        mid = str(row['model_id']).split('/')[-1] if row['model_id'] else '-'
        print(f"\n--- {field} best accepted: model={mid} v={row['version']} val={row['mean_score']:.3f} ---")
        print(row['template'][:600])
        if len(row['template']) > 600: print("...")

backup.close()
print("\nDone.")
