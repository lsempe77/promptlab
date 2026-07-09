"""
Provision a new prompt-lab Fly.io app for a client project.

Uses `flyctl` (must be installed and authenticated) to:
1. Create a new Fly app under the same org
2. Create a /data volume
3. Set required secrets (OPENROUTER_API_KEY, JWT_SECRET, PROMPTLAB_PASSWORD)
4. Deploy the same Docker image as dep-promptlab-api
5. Upload the corpus + ground truth from the manifest
6. Launch the supervisor daemon

Usage:
    python -m backend.scripts.provision_project \\
        --app ge-screening \\
        --password "SuperSecret123!" \\
        --region iad

    # Or with a manifest file from the wizard:
    python -m backend.scripts.provision_project --manifest project_manifest.json

The manifest JSON format (output by the wizard):
{
    "app_name": "dep-promptlab-ge",
    "project_name": "GE Screening 2025",
    "project_slug": "ge-screening",
    "project_type": "screening_ta",
    "password": "...",
    "region": "iad",
    "corpus_dir": "/path/to/corpus/files",
    "ground_truth_csv": "/path/to/gt.csv"
}
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

# Template Fly config for new client apps — mirrors fly.toml but parameterised
_FLY_CONFIG_TEMPLATE = """\
app = '{app_name}'
primary_region = '{region}'

[build]
  dockerfile = 'Dockerfile'

[http_service]
  internal_port = 8080
  force_https = true
  auto_stop_machines = true
  auto_start_machines = true
  min_machines_running = 0

[[vm]]
  size = 'shared-cpu-1x'
  memory = '1024mb'
"""

_SUPERVISOR_SH = """\
#!/bin/sh
cd /app
nohup python -m backend.scripts.supervisor \\
  --project {slug} \\
  --loop \\
  --max-cycles 12 \\
  --interval 10800 \\
  --parallelism 2 \\
  >> /data/supervisor.log 2>&1 &
echo "LAUNCHED pid $!"
"""

_KILL_ALL_SH = """\
#!/bin/sh
FOUND=0
for pid in $(ls /proc | grep -E '^[0-9]+$'); do
    cmd=$(cat /proc/$pid/cmdline 2>/dev/null | tr '\\0' ' ')
    case "$cmd" in
        *supervisor*|*run_extraction*|*llm_judge*|*optimize_prompt*|*run_screening*)
            kill $pid 2>/dev/null && echo "killing $pid: $cmd" && FOUND=$((FOUND+1))
            ;;
    esac
done
echo "killed $FOUND process(es)"
"""

_LIST_SUP_SH = """\
#!/bin/sh
for pid in $(ls /proc | grep -E '^[0-9]+$'); do
    cmd=$(cat /proc/$pid/cmdline 2>/dev/null | tr '\\0' ' ')
    case "$cmd" in
        *supervisor*) echo "$pid: $cmd" ;;
    esac
done
echo "----"
"""


def _run(cmd: list[str], check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(
        cmd, check=check,
        capture_output=capture, text=True,
    )


def _fly(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return _run(["fly", *args], check=check)


def provision(
    app_name: str,
    project_slug: str,
    project_name: str,
    project_type: str,
    password: str,
    region: str = "iad",
    openrouter_key: str | None = None,
    corpus_dir: str | None = None,
    ground_truth_csv: str | None = None,
    dry_run: bool = False,
) -> str:
    """Provision a new Fly app and return its URL."""

    print(f"\n=== Provisioning {app_name} ===\n")

    if dry_run:
        print("[DRY RUN] Would provision:", app_name)
        return f"https://{app_name}.fly.dev"

    # 1. Create the app
    print("1. Creating Fly app...")
    _fly("apps", "create", app_name, "--org", "personal", check=False)

    # 2. Create a 5GB volume for DB + corpus
    print("2. Creating /data volume...")
    _fly("volumes", "create", "dep_data",
         "--app", app_name, "--size", "5", "--region", region, check=False)

    # 3. Set secrets
    print("3. Setting secrets...")
    key = openrouter_key or os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        print("  WARNING: OPENROUTER_API_KEY not set — set it manually with:")
        print(f"  fly secrets set OPENROUTER_API_KEY=sk-... --app {app_name}")
    else:
        _fly("secrets", "set",
             f"OPENROUTER_API_KEY={key}",
             f"JWT_SECRET={secrets.token_urlsafe(48)}",
             f"PROMPTLAB_PASSWORD={password}",
             f"DEP_DB_PATH=/data/promptlab.db",
             f"DEP_MD_DIR=/data/corpus",
             "--app", app_name)

    # 4. Deploy using the same image as the main app
    print("4. Deploying image...")
    # Write a temporary fly.toml for this app
    cfg_path = Path(f"/tmp/{app_name}_fly.toml")
    cfg_path.write_text(_FLY_CONFIG_TEMPLATE.format(app_name=app_name, region=region))
    _fly("deploy",
         "--app", app_name,
         "--config", str(cfg_path),
         "--ha=false")
    cfg_path.unlink(missing_ok=True)

    # 5. Upload helper scripts
    print("5. Uploading supervisor scripts...")
    for script_name, content in [
        ("launch_supervisor.sh", _SUPERVISOR_SH.format(slug=project_slug)),
        ("kill_all.sh", _KILL_ALL_SH),
        ("list_sup.sh", _LIST_SUP_SH),
    ]:
        tmp = Path(f"/tmp/{script_name}")
        tmp.write_text(content)
        _fly("ssh", "sftp", "put", str(tmp), f"/data/{script_name}", "--app", app_name, check=False)
        tmp.unlink(missing_ok=True)

    # 6. Wait for machine to be healthy
    print("6. Waiting for machine health...")
    for _ in range(12):
        r = subprocess.run(
            ["fly", "ssh", "console", "-C", "python3 -c \"import backend; print('ok')\"",
             "--app", app_name],
            capture_output=True, text=True, timeout=30
        )
        if "ok" in r.stdout:
            break
        print("  ... waiting")
        time.sleep(10)

    # 7. Upload corpus if provided
    if corpus_dir and Path(corpus_dir).exists():
        print(f"7. Uploading corpus from {corpus_dir}...")
        _fly("ssh", "console", "-C", "mkdir -p /data/corpus", "--app", app_name)
        for md_file in Path(corpus_dir).glob("*.md"):
            _fly("ssh", "sftp", "put", str(md_file), f"/data/corpus/{md_file.name}",
                 "--app", app_name, check=False)
        print(f"  Uploaded {len(list(Path(corpus_dir).glob('*.md')))} markdown files")

    # 8. Launch supervisor
    print("8. Launching supervisor...")
    _fly("ssh", "console", "-C", "sh /data/launch_supervisor.sh", "--app", app_name)

    url = f"https://{app_name}.fly.dev"
    print(f"\n✓ Done! App available at: {url}")
    print(f"  Dashboard: https://lsempe77.github.io/promptlab/  (set API URL to {url})")
    print(f"  Password:  {password}")
    return url


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", help="JSON manifest file downloaded from the wizard")
    ap.add_argument("--app", help="Fly app name (e.g. dep-promptlab-ge)")
    ap.add_argument("--slug", help="Project slug (e.g. ge-screening)")
    ap.add_argument("--name", default="New Project", help="Project display name")
    ap.add_argument("--type", default="extraction", choices=["extraction","screening_ta","screening_ft"])
    ap.add_argument("--password", help="Shared access password")
    ap.add_argument("--region", default="iad")
    ap.add_argument("--corpus-dir", help="Local directory of .md corpus files (extraction)")
    ap.add_argument("--gt-csv", help="Ground truth CSV file")
    ap.add_argument("--eppi-file", help="EPPI Excel file for screening projects")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.manifest:
        with open(args.manifest) as f:
            m = json.load(f)
        app_name = m["app_name"]
        slug = m["project_slug"]
        name = m["project_name"]
        ptype = m["project_type"]
        pw = m.get("password") or args.password or ""
        if not pw or pw == "(set before provisioning)":
            ap.error("Password not set in manifest. Pass --password <value>.")
        region = m.get("region", "iad")
        corpus = args.corpus_dir or m.get("corpus_dir")
        gt = args.gt_csv or m.get("ground_truth_csv")
        eppi = args.eppi_file
        print(f"Manifest loaded: {name} ({slug}) → {app_name}")
        if m.get("exclusion_criteria"):
            print(f"  {len(m['exclusion_criteria'])} exclusion criteria, strategy={m.get('maybe_strategy','?')}")
        if m.get("fields"):
            print(f"  {len(m['fields'])} extraction fields")
        print(f"  {len(m.get('selected_models',[]))} models: {', '.join(x.split('/')[-1] for x in m.get('selected_models',[]))}")
    else:
        if not args.app or not args.slug or not args.password:
            ap.error("--app, --slug, and --password are required (or use --manifest)")
        app_name = args.app
        slug = args.slug
        name = args.name
        ptype = args.type
        pw = args.password
        region = args.region
        corpus = args.corpus_dir
        gt = args.gt_csv
        eppi = args.eppi_file

    if ptype != "extraction" and not eppi and not corpus:
        print(f"WARNING: screening project but no --eppi-file provided.")
        print(f"  Run again with: --eppi-file path/to/eppi_export.xlsx")

    provision(
        app_name=app_name,
        project_slug=slug,
        project_name=name,
        project_type=ptype,
        password=pw,
        region=region,
        corpus_dir=corpus,
        ground_truth_csv=gt,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
