---
type: regex
target: { source: file, path: main.tf }
pattern: 'AKIA[0-9A-Z]{16}|secret_key\s*='
match: not_contains
weight: 2
---
