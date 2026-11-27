# Self-learning
Last step of every task, before you submit. Write a lesson only if ALL hold:
- It would help on a DIFFERENT repo and ticket, not just this one.
- You saw it happen in this run: say what failed and what fixed it.
- The brief, the repo's own docs and your skills do not already say it.
Specific to one repo, one tool version or one number (a limit, a path, a value)? Then it belongs in that repo's own docs: say so in `PR.md`, write no lesson.
- Yes, on a repo other than puck: write `LEARNED.md` at the repo root. Line 1 is a short title, then at most 15 lines: when it applies, what to do, what went wrong. No ticket keys, repo names, secrets or one-off details; the gate refuses them. The runtime keeps it out of this PR and opens a separate draft PR on puck for human review.
- Yes, on puck (a self-learning job): write the skill or card yourself with `skills/write-skill`.
- A mechanical check could have caught it? End with `A check would catch this: <what>`: a gate beats a reminder.
- No: write nothing. Most runs teach nothing new.
