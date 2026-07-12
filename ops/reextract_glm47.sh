#!/bin/sh
# Force re-extraction for z-ai/glm-4.7-flash across all fields (2048-token fix).
# Runs sequentially to avoid SQLITE_LOCKED conflicts with the supervisor optimizer.
cd /app
FIELDS="authors author_country author_affiliation sector_name sub_sector"
for field in $FIELDS; do
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) re-extracting $field for glm-4.7-flash ..."
    python -m backend.scripts.run_extraction \
        --project dep-extraction \
        --field "$field" \
        --models z-ai/glm-4.7-flash \
        --n 100 \
        --force
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) done $field"
done
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) all fields done."
