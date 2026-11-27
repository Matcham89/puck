---
name: write-skill
description: Record something you learned (a procedure or a gotcha) as a skill or reference card under agent/ in the puck repo. Use at the end of a task when a future run would repeat this work or mistake.
---
1. Pick a short kebab-case name; the folder name and `name:` match. List `agent/skills/` first: if a skill covers it, update that one instead of adding a duplicate.
1b. Skill or card? A skill is a procedure for one kind of task (`agent/skills/<name>/SKILL.md`). A card is a short situational rule that applies to any task, like "if X happens, do Y" (`agent/cards/<name>.md`: `# Title`, at most 15 lines). If unsure, write a skill.
2. Write `agent/skills/<name>/SKILL.md`, exactly that filename. Line 1 is `---`, then valid YAML with only `name` and `description` (unknown fields are silently ignored), then `---`.
3. `description` is the trigger: what it does and when to use it, key use case first, specific enough to match one kind of task.
4. Body: standing rules or numbered steps, not a diary or the ticket's story. Under 40 lines and under 8000 characters, because the gate cuts anything longer when it loads skills. One file only: sibling files are never loaded. Use MUST sparingly.
5. Record what the repo's own CLAUDE.md or README does not already say (the brief quotes those). Never put secrets, ticket text, customer names or account IDs in a skill.
6. Only write the skill if this job's repo is puck (allowed paths `agent/skills/` and `agent/cards/`). On any other repo, do NOT add files under `agent/`: write the lesson in `LEARNED.md` at the repo root instead, as `cards/self-learning.md` says.
6b. Write only a lesson that is general: no ticket keys, job ids or target-repo names (the gate refuses them), and no numbers or values that belong to one repo. Cards are at most 15 lines, skills 40, and the whole `agent/` folder has a size budget: if the gate says it is full, merge or shorten existing cards instead of adding. If the lesson turns out to be about one repo only, make no file change and say why in `PR.md`.
7. Write `PR.md` (title, then what you learned and why it will recur) and submit. Do NOT commit, push, create branches or open a PR: the runtime opens a draft PR for human review (never main/master). If you are told a previous attempt failed, fix only what the checks report.
