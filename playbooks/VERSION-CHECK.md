# Version Check — Update Walk-Through
*Loaded by the Architect only when the fetched `latest` differs from `version_notified`.
The cheap pre-check (fetch latest.json, compare, skip silently on match or network failure)
lives in the role file's Session Start — do not repeat it here.*

---

## Determine the current version

Read `manifest.md` in the project root and find the `version` field. If `manifest.md` does
not exist, read the `VERSION` file instead. If neither exists, treat the current version
as pre-v1.2.3.

## Walk the updates

1. Fetch `https://raw.githubusercontent.com/cloudaipro/three-man-team/main/releases/latest.json`
   (already fetched in Session Start — reuse it, do not re-fetch).
2. Parse `versions[]` to find all versions between the current version and `latest`
   (exclusive of current, inclusive of latest).
3. Separate into two groups: **critical** versions (`critical: true`) and non-critical.
4. Process critical versions first, in ascending order — for each:
   - Fetch `https://raw.githubusercontent.com/cloudaipro/three-man-team/main/releases/{version}.json`
   - Open with `arch_opening` verbatim.
   - Walk each change using `how_to_assess` + `user_decision`.
   - Do not skip a critical version — they are mandatory checkpoints.
5. After all critical versions are handled, present non-critical updates as optional —
   ask the Project Owner if they want to walk through them.

## Record the outcome

When the conversation concludes: write `version_notified: {latest}` to
`handoff/SESSION-CHECKPOINT.md` under the Version Check section.
