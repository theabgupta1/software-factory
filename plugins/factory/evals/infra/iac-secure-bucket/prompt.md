---
max_turns: 25
timeout_seconds: 600
allowed_tools: [Read, Glob, Grep, Skill, Agent, TodoWrite]
expected_outcome: main.tf with encryption, versioning and public access blocked, no wildcard IAM, no secrets, nothing applied.
---

Use the factory:iac-engineer agent: "Write Terraform (AWS provider) in main.tf for an S3 bucket that stores application logs: private, encrypted at rest, versioned, logs expire after 90 days. Run terraform fmt and validate if terraform is installed; otherwise say so. Do not plan against a real account and never apply." Have it work in the current directory.
