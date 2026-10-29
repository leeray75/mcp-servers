# External review triage (DISCORD-MCP-ISSUE-REPORT.md, F1-F11)

- **Source:** `C:\Users\hoyen\Documents\discord-mcp-server\DISCORD-MCP-ISSUE-REPORT.md` (goose / Qwen, read-only audit). Untrusted
  input: each claim was verified by the orchestrator against the code on disk and docs.discord.com on 2026-10-28.
- **Outcome:** decisions recorded in `../../SPEC.md` section 16 (amendment A1); code fixes are recorded in `fix-round-1.md`.

| # | Claim | Verdict | Evidence | Action |
|---|---|---|---|---|
| F1 | `obfuscated` from flag `1<<17` is never set over HTTP | **Confirmed** | Channel Flags table: "the HTTP API never sets this flag"; `project.py:47,257`, `models.py:88` | A1-9: remove the field |
| F2 | Intent gating has no remedy if `flags` is missing | **Confirmed (risk)** | Application object lists `flags?` ("App's legacy public flags", optional) and `flags_new?`; `capabilities.py:77`, `discord_client.py:411-412` | A1-7: `flags_new` fallback, `flags_missing`, force overrides |
| F3 | `search_members` may need GUILD_MEMBERS | **Partly.** Only List Guild Members carries the intent warning; Search Guild Members has none, so the code matches the docs. Live behaviour is unproven | Guild resource, both sections | A1-11: softened description, `50001` hint; live check stays with AC5 |
| F4 | No output-size budget, undocumented departure | **Confirmed as a documentation gap.** D11 was an explicit human decision but was not recorded as a departure from the siblings | SPEC D11; `sports-mcp-server/CLAUDE.md` size budgets | A1-8: record the departure, size-measurement test |
| F5 | 1 MiB upstream cap can reject legitimate large pages | **Plausible** (cannot be measured offline) | `config.py:72` | A1-8: default 4 MiB, hint to lower `limit` |
| F6 | Search results show `reactions: []` though Discord omits the key | **Confirmed** | Search Guild Messages returns messages "without the `reactions` key"; `project.py:356` | A1-10: `null` |
| F7 | Gate self-reported; shellcheck not run | **Gate: rebutted.** The reviewer re-ran `make check` and `make audit` in round 1 (282 passed, 96.22%, no vulnerabilities). **shellcheck: confirmed** not run | `review-round-1.md` gate table | Orchestrator re-runs the gate before the summary; shellcheck is an open item |
| F8 | Root docs half-updated | **Confirmed** (also review m11) | root `CLAUDE.md:15` says "four"; root `README.md` diagram lacks 8769; root `CHANGELOG.md` untouched | A1 docs changes |
| F9 | Nits: `tools/**` ignore, placeholder README, `.dockerignore` | **Rebutted.** `tools/try_live.py` exists; the README is the full 215-line document; the Dockerfile copies nothing that `.dockerignore` excludes, and the image builds | `discord-mcp-server/tools/`, `README.md`, `.dockerignore` | none |
| F10 | Approval claim; leaked credentials | Approval: the human typed "Approve" in the session on 2026-10-28. Credentials: **the human's action** | session transcript | Open items in the summary |
| F11 | Audit action names from recall | **Confirmed, and worse than reported.** 146 is misnamed `CREATOR_MONETIZATION_REQUEST_CREATED` (docs: `AUTO_MODERATION_QUARANTINE_USER`); 147 does not exist (the monetization events are 150/151); 130-132, 163-167 and 190-193 are missing. Channel types match the docs. `has` negation with `-` is documented but rejected by the code. The `action_type` cap of 255 is arbitrary | Audit Log Events table; Search Guild Messages `has` note; `project.py:48-105`, `service.py:512,739` | A1-12: transcribe, test each entry, allow negation, drop the cap |
