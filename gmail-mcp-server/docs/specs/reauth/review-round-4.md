# Re-authentication: review round 4

## Final verdict: APPROVE
Finding 7 is fixed. Open findings: none.

- scripts/lib/common.sh:201-205 `auth_route_available`: curl call now split with a trailing `\` and an indented continuation; LF endings, zero CR bytes; `bash -n` OK.
- shellcheck (koalaman/shellcheck:stable, `-x lib/common.sh auth.sh`): only SC2034 for `IMAGE` and `AUTH_PORT` (pre-existing constants used by other scripts); nothing new.
- Since round 3 the only file under src/, tests/ or scripts/ modified is scripts/lib/common.sh, so the round-3 Python gate (286 passed, 99.19%) still stands.
