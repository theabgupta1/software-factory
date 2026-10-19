---
type: regex
target: { source: file, path: answer.sql }
pattern: '\b(insert|update|delete|drop|alter|create|attach|pragma)\b'
flags: i
match: not_contains
---
