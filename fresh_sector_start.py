import sys, json
sys.path.insert(0, "/app")
from backend.app import db, prompt_store, prompts, config

FIELDS = ["sector_name", "sub_sector"]
NOTES = "human-baseline-v2: WB definitions + hierarchical structure (2026-07-07)"

roster = config.load_models()
model_ids = [m["id"] for m in roster]

with db.get_conn() as conn:
    project_id = db.get_project_id(conn, "dep-extraction")

    for field in FIELDS:
        instruction = prompts.BASELINE_INSTRUCTIONS[field]
        print(f"\n=== {field} ({len(instruction)} chars) ===")

        # 1. Delete judgments for runs in this field (FK: llm_judgments -> runs)
        n_j = conn.execute(
            "DELETE FROM llm_judgments WHERE run_id IN "
            "(SELECT id FROM runs WHERE project_id=? AND field_name=?)",
            (project_id, field)
        ).rowcount
        print(f"  Deleted {n_j} llm_judgments")

        # 2. Delete jobs for this field
        conn.execute(
            "DELETE FROM jobs WHERE project_id=? AND field_name=?",
            (project_id, field)
        )

        # 3. Delete runs (FK: runs -> prompt_versions)
        n_runs = conn.execute(
            "DELETE FROM runs WHERE project_id=? AND field_name=?",
            (project_id, field)
        ).rowcount
        print(f"  Deleted {n_runs} runs")

        # 4. Delete iterations (FK: iterations -> prompt_versions)
        n_iters = conn.execute(
            "DELETE FROM iterations WHERE project_id=? AND field_name=?",
            (project_id, field)
        ).rowcount
        print(f"  Deleted {n_iters} iterations")

        # 5. Delete prompt_versions
        n_pv = conn.execute(
            "DELETE FROM prompt_versions WHERE project_id=? AND field_name=?",
            (project_id, field)
        ).rowcount
        print(f"  Deleted {n_pv} prompt versions")

        # 6. Create fresh per-model baselines using the improved instruction
        for model_id in model_ids:
            pv = prompt_store.get_or_create_baseline(
                conn, project_id, field, model_id=model_id
            )
            if pv["template"] != instruction:
                conn.execute(
                    "UPDATE prompt_versions SET template=?, notes=? WHERE id=?",
                    (instruction, NOTES, pv["id"])
                )
        print(f"  Created fresh baselines for {len(model_ids)} models")

print("\nFresh start complete. Supervisor will extract and evaluate the new prompts.")
