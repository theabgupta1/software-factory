---
type: regex
target: { source: file, path: etl.py }
pattern: 'ON\s+CONFLICT|INSERT\s+OR\s+REPLACE|REPLACE\s+INTO|DELETE\s+FROM|PRIMARY\s+KEY'
flags: i
---
