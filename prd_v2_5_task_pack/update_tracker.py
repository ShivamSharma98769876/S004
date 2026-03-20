from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


VALID_STATUSES = {"pending", "in_progress", "blocked", "completed", "cancelled"}


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def pct(completed: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round((completed / total) * 100.0, 2)


def normalize_status(value: str) -> str:
    if value in VALID_STATUSES:
        return value
    return "pending"


def main() -> None:
    base_dir = Path(__file__).resolve().parent

    task_files = sorted(base_dir.glob("task_W*.json"))
    subtask_files = sorted(base_dir.glob("subtasks_W*.json"))

    subtask_by_task: Dict[str, List[Dict[str, Any]]] = {}
    for sf in subtask_files:
        data = load_json(sf)
        task_id = data.get("task_id", "")
        subtasks = data.get("subtasks", [])
        if not isinstance(subtasks, list):
            subtasks = []
        subtask_by_task[task_id] = subtasks

    task_items: List[Dict[str, Any]] = []
    task_status_counter: Counter[str] = Counter()
    subtask_status_counter: Counter[str] = Counter()

    completed_tasks = 0
    total_subtasks = 0
    completed_subtasks = 0

    for tf in task_files:
        task = load_json(tf)
        task_id = task.get("task_id", "")
        title = task.get("title", tf.name)
        task_status = normalize_status(str(task.get("status", "pending")))
        task_status_counter[task_status] += 1
        if task_status == "completed":
            completed_tasks += 1

        raw_subtasks = subtask_by_task.get(task_id, [])
        processed_subtasks: List[Dict[str, Any]] = []
        local_completed = 0
        for st in raw_subtasks:
            st_status = normalize_status(str(st.get("status", "pending")))
            subtask_status_counter[st_status] += 1
            if st_status == "completed":
                local_completed += 1
            processed_subtasks.append(
                {
                    "subtask_id": st.get("subtask_id", ""),
                    "title": st.get("title", ""),
                    "status": st_status,
                }
            )

        total_local = len(processed_subtasks)
        total_subtasks += total_local
        completed_subtasks += local_completed

        task_items.append(
            {
                "task_id": task_id,
                "title": title,
                "status": task_status,
                "task_file": tf.name,
                "subtask_file": f"subtasks_{tf.name[5:]}",
                "subtasks_total": total_local,
                "subtasks_completed": local_completed,
                "subtasks_completion_pct": pct(local_completed, total_local),
                "subtasks": processed_subtasks,
            }
        )

    total_tasks = len(task_items)
    total_units = total_tasks + total_subtasks
    completed_units = completed_tasks + completed_subtasks

    tracker = {
        "tracker_name": "PRD v2.5 Execution Tracker",
        "source_pack": "prd_v2_5_task_pack",
        "last_updated_utc": datetime.now(timezone.utc).isoformat(),
        "status_values": sorted(VALID_STATUSES),
        "summary": {
            "tasks_total": total_tasks,
            "tasks_completed": completed_tasks,
            "tasks_completion_pct": pct(completed_tasks, total_tasks),
            "subtasks_total": total_subtasks,
            "subtasks_completed": completed_subtasks,
            "subtasks_completion_pct": pct(completed_subtasks, total_subtasks),
            "overall_units_total": total_units,
            "overall_units_completed": completed_units,
            "overall_completion_pct": pct(completed_units, total_units),
        },
        "status_distribution": {
            "tasks": {k: task_status_counter.get(k, 0) for k in sorted(VALID_STATUSES)},
            "subtasks": {k: subtask_status_counter.get(k, 0) for k in sorted(VALID_STATUSES)},
        },
        "tasks": task_items,
    }

    output_path = base_dir / "tracker.json"
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(tracker, f, indent=2)
        f.write("\n")

    print(f"Tracker updated: {output_path}")


if __name__ == "__main__":
    main()
