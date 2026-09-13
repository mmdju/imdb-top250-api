# Security Policy

## Reporting a Vulnerability

Please do NOT open a public issue. Report privately via the
[Security tab](../../security/advisories/new)
(Advisories → Report a vulnerability).

Notes:
- `/refresh` is admin-only (needs `ADMIN_KEY`; unavailable until it is configured).
- Error responses are `no-store`; success responses are public-cacheable for an hour.
