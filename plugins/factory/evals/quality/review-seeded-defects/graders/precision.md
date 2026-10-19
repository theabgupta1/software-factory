---
type: llm
focus: { source: file, path: review.md }
---

PASS if every finding in this review is a real problem in the reviewed change (SQL injection via f-string, hard-coded secret, missing ownership/authorization check, debug mode) or a clearly labelled low-severity note, and none of the findings is invented or about code that is not in the change.
FAIL if any finding describes a defect that is not there, or if the review is mostly generic advice.
