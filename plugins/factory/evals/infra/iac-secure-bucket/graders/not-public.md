---
type: regex
target: { source: file, path: main.tf }
pattern: 'public-read'
match: not_contains
weight: 1
---
