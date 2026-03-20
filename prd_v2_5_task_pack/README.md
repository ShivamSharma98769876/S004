# PRD v2.5 Task Pack

This folder contains structured task and subtask JSON files generated from:

- `options_trading_platform_complete_PRD_v2_5.pdf`

## Files

- `00_manifest.json`: inventory and execution order
- `task_W*.json`: top-level workstream tasks
- `subtasks_W*.json`: detailed implementation subtasks
- `update_tracker.py`: regenerates tracker summary
- `tracker.json`: roll-up completion tracker

## Status values

- `pending`
- `in_progress`
- `blocked`
- `completed`
- `cancelled`

## How to update progress

1. Edit any `task_W*.json` or `subtasks_W*.json` file and update the `status` fields.
2. Regenerate tracker:

```powershell
cd "c:\Users\SharmaS8\OneDrive - Unisys\Shivam Imp Documents-2024 June\PythonProgram\S004-DynamicOptionBuy\prd_v2_5_task_pack"
python update_tracker.py
```

3. Open `tracker.json` to see updated totals and completion percentages.

