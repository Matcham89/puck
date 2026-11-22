---
name: write-skill
description: Record something you learned (a procedure or a gotcha) as a skill under agent/skills/ in the puck repo. Use at the end of a task when a future run would repeat this work or mistake.
---
1. Pick a short kebab-case name. If `agent/skills/<name>/` already exists, you are updating it.
2. Write `agent/skills/<name>/SKILL.md` with frontmatter `name` and `description`. The description is a specific trigger: when to use it. Steps only, under 40 lines. Gotchas are skills too.
3. Write `PR.md` (title, then what you learned and why it will recur) and submit. Only `agent/skills/` and `agent/cards/` are allowed paths for this repo.
4. Do NOT commit, push, create branches or open a PR: the runtime opens a draft PR for human review (never main/master). If you are told a previous attempt failed, fix only what the checks report.
