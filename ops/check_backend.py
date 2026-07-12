import requests

base = 'https://dep-promptlab-api.fly.dev'

print('=== Health ===')
print(requests.get(f'{base}/api/health').json())

print()
print('=== Stage status ===')
fields = ['authors','author_country','author_affiliation','sector_name','sub_sector']
all_jobs = []

for f in fields:
    d = requests.get(f'{base}/api/projects/dep-extraction/fields/{f}/stage-status').json()
    jobs = requests.get(f'{base}/api/projects/dep-extraction/fields/{f}/jobs').json()
    all_jobs.extend(jobs[:4])
    running = [j for j in jobs if j.get('status') == 'running']
    job_str = (running[0]['kind'] + ':' + (running[0].get('model_id') or '').split('/')[-1][:16]) if running else 'idle'
    refs = d.get('references', 0)
    passing = d.get('n_models_passing', 0)
    evaluated = d.get('n_models_evaluated', 0)
    print(f"  {f[:22]:<24} refs={refs:>3}  eval={evaluated}/12  pass={passing}  [{job_str}]")

print()
print('=== Recent jobs ===')
all_jobs.sort(key=lambda j: j.get('started_at', ''), reverse=True)
for j in all_jobs[:10]:
    fn = j.get('field_name', '')[:18]
    mid = (j.get('model_id') or '').split('/')[-1][:20]
    kind = j.get('kind', '')
    status = j.get('status', '')
    started = str(j.get('started_at', ''))[:16]
    print(f"  {fn:<20} {mid:<22} {kind:<12} {status:<10} {started}")
