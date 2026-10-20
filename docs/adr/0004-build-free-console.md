# ADR 0004: A local, build-free console

Status: accepted (2026-10)

## Context
The original board was a 600-line HTML page polling directory listings. The new console has many views, live
updates and a chat with agents, and must run from a fresh clone with nothing but Python.

## Decision
`factoryctl serve`: a standard-library HTTP server on 127.0.0.1 with a small JSON API, and a UI of vanilla ES
modules, a tiny DOM helper and CSS tokens. No framework, no bundler, no CDN, no runtime dependency.

## Consequences
- Works offline, starts instantly, nothing to install; easy to audit (CSP `default-src 'self'`).
- More hand-written DOM code than a framework would need; mitigated by small modules, JS unit tests and Playwright
  tests (journeys, axe, widths, themes).
