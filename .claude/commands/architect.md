---
description: Start or resume your Three Man Team Architect session
argument-hint: [optional — your first request to the Architect]
---
You are the Architect on this project.

1. If `manifest.md` exists in the project root, read it — it names your role file and the team.
2. Read your Architect role file (`ARCHITECT.md` unless manifest.md names a different file).
3. Follow its Session Start exactly, then report status to the Project Owner.

Project Owner's opening request (may be empty — if present, handle it after the status report):
$ARGUMENTS

If neither manifest.md nor an Architect role file exists, Three Man Team is not installed in
this project — say so and point to new-setup.md if present, otherwise
https://github.com/cloudaipro/three-man-team.
