---
type: regex
target: { source: file, path: review.md }
pattern: 'IDOR|owner(ship|_id)?[^\n]{0,80}(check|compar|verif)|authori[sz]ation|any (signed[- ]in |logged[- ]in )?user can'
flags: i
weight: 2
---
