---
name: playbook-change
description: Add or change a skill or plugin in a playbook-style target repo following that repo's own rules. Use when a task asks to add, move or update playbook content.
---
1. Read the repo's `CLAUDE.md` and `CONTRIBUTING.md` first (the repo brief quotes them): they define layout, manifests, versions and the README table.
2. Decide where it belongs: a new plugin, or an existing one. Look at what plugins exist before choosing. Copy any provided source files, keep license and attribution, and do not edit vendored text beyond what the repo requires.
3. Follow the repo rules: `plugin.json` + `marketplace.json` entry in sync, version bumped in both for any changed plugin, plugin `README.md`, a row in the README plugin table.
4. Write `PR.md`: line 1 is the PR title, the rest explains WHERE you put it and WHY, what you left out, and what a reviewer should check (licenses, references to skills that are not included).
5. Submit. The gate runs the repo's checks; fix what it reports. Do NOT commit, push, create branches or open a PR: the runtime opens a draft PR (never main/master).
