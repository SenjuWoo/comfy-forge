# STATE

**Goal** — a beginner-usable Anima stack for SwarmUI/ComfyUI: install models and
node packs, render from presets, and merge checkpoints into real, loadable files.

**Version** — see `VERSION` (0.5.0-rc.1). Tags remain v0.4.2 and earlier; no
release has been cut for these changes yet.

**Last known good** — v0.4.2.

**Done**

- Four-way merge keeps every donor's weight when an anchor donor substitutes for
  a missing ancestor; shares are 31.48/27.78/22.22/18.52 at scale 1.
- Mirror verified by Windows file identity, not by file name; a merge whose
  output already sits in the ComfyUI tree is left alone instead of deleted.
- Installer reads node-pack `requirements.txt` and installs missing
  distributions into the frontend's own interpreter.
- Shipped workflow no longer pairs a 40-block base with 28-block donors;
  `TESTS/validate_workflows.py` fails the build if it ever does again.

**Next**

- Run the HaDeS save node enabled to prove a UI-driven save reloads (CLI path is
  already proven).
- MCP: prove one real backend operation, not just a handshake.

**Log** — merge fixes, mirror rewrite, installer deps, workflow validator.
**Build/run** — `TOOLS/image-stack.ps1 <status|install|presets|generate|merge|mcp>`.
