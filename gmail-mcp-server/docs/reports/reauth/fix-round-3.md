# Fix round 3

Finding 7 fixed for real using the Edit tool: the curl call in `auth_route_available` (`scripts/lib/common.sh`) is now split with a trailing `\` and an indented continuation line. Verified with `cat -A` (no `^M`, LF endings; zero CR characters) and `bash -n`. fix-round-2.md corrected. No Python changed, so no gate re-run.
