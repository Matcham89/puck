---
name: document-backup-posture
description: Use when asked to document the backup, recovery or DR posture of a system in a repository (e.g. KAN-19). Keeps the write-up verified, and separates what exists from what is not configured.
---
1. Find the evidence first: `rg -n -i 'backup|snapshot|restore|retention|replica|pitr|velero|cronjob|persistentvolume|storageclass' <paths>`. Check manifests, Helm values, Terraform, CronJobs, scripts and docs.
2. For every claim, note the file and line that proves it (schedule, retention, target, storage location). No file, no claim.
3. Write two explicit sections: "Configured" (with file references) and "Not configured / not found" (e.g. no backup job, no off-cluster copy, no restore test, no retention policy, no RPO/RTO defined).
4. Add a "Could not verify" section for anything that depends on things outside the repo (cloud console settings, managed-service defaults, runtime state, secrets). Say why it was not verifiable.
5. Do not infer a backup from a replica, a PVC or a default; replication is not backup. Do not state RPO/RTO unless defined in the repo.
6. In PR.md, list what you checked, what you left out, and ask the reviewer to confirm the "Could not verify" items.
