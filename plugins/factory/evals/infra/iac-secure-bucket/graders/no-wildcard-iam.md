---
type: regex
target: { source: file, path: main.tf }
pattern: 'actions\s*=\s*\[\s*"\*"|"Action"\s*:\s*"\*"'
match: not_contains
weight: 2
---
