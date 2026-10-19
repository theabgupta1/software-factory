STATUS: READY

# Sessions: SQLite (WAL) or Redis

## Findings
- **Fact** (SQLite docs, https://www.sqlite.org/wal.html): WAL lets readers proceed while one writer writes; there is still a single writer.
- **Fact** (Redis docs, https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/): durability depends on AOF/RDB settings.
- **Assumption**: session writes are small and infrequent relative to reads.

## Recommendation
SQLite with WAL for a single-host app; Redis once several app hosts share sessions. Confidence: medium.

## Sources
- https://www.sqlite.org/wal.html
- https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/
