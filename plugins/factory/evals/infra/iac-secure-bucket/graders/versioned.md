---
type: regex
target: { source: file, path: main.tf }
pattern: 'versioning_configuration|status\s*=\s*"Enabled"'
weight: 1
---
