# Tasks
A task is JSON: `id`, `repo`, `base_branch`, `allowed_paths`, `instruction` (and `ticket_key`, `dry_run` when set).
- Make the change under `allowed_paths` only, then write `PR.md`: line 1 is the PR title, the rest says where you put the change and why.
- The deterministic checks are authoritative. If they report a problem, fix the code, not the check.
- Unknown kind of request: look for a skill named after it; if none, say so in `PR.md` and stop.
