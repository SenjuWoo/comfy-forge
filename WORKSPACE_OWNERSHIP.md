# Workspace ownership

- Project root: `.` (this file's directory; one owner shared by every agent).
- Project name: `comfy-kit` (formerly `uabs-image-stack`).
- Active source: `.` — the repo root is the source. No version-stamped snapshots; Git tags carry releases.
- Versioning: SemVer tags (`v0.x.y`), `CHANGELOG.md` is the record.
- Repository: local Git repo on branch `main`; no remote until the operator asks.
- Publication intent: this repo is intended as a public starter pack for new image-generation users (Anima-focused). Publish only when the operator asks.
- Read-only inputs: the installed frontend (`S:\Misc\SwarmUI` on the build machine) and its Models folder. This repo reads them and writes only into them when explicitly asked (`install`, `merge` output, `mcp -Write`).
- State/output: `out/` and `artifacts/` are local and git-ignored; model weights are never committed.
- Recovery: `git log` plus the last green tag; `TOOLS/image-stack.ps1 status` reports the installed state.

- Explicit GitHub publication: public and MIT for original work by default, subject to rights/platform terms; preserve existing licences. Generic publication uses the appropriate destination (Nexus for Skyrim); do not create duplicate source hosting for mods needing no compilation.
- Existing-repository maintenance: preserve its remote/visibility and follow the requested update, documentation, push and release scope.
- Read-only inputs: installed products, game Data/staging/saves and supplied references.
- Release staging: separate runtime payload from source, ownership notes, STATE/PLAN/validation, changelogs, logs and backups. Keep required licences.
- Recovery: read STATE.md and version/build/run metadata before continuing; keep the last known-good artifact.


Read the existing evidence and update the active-source/version fields before substantive edits. Do not create another owner or move files merely to match a template.
