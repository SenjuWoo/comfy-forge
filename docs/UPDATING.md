# Updating comfy-kit's external dependencies

Every external node pack is pinned by commit in `config/dependencies.lock.json`.
`install` checks out those exact commits; `status` reports drift. Nothing is
promoted automatically — a newer upstream commit is only pinned after it has been
installed and exercised locally.

## Check what is pinned vs installed

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File TOOLS\image-stack.ps1 status
```

`packs hades-anima-suite locked` means the checkout matches the lock.
`drift` means it does not — the local checkout is left alone, never reset.

## Review an upstream change before pinning it

```powershell
cd "S:\Misc\SwarmUI\dlbackend\comfy\ComfyUI\custom_nodes\hades-anima-suite"
git fetch --all
git log --oneline HEAD..origin/main
git diff HEAD origin/main -- pyproject.toml
```

Read the diff. Check for new Python requirements and renamed node classes.

## Try it out

```powershell
git checkout <candidate-sha>
```

Restart the backend, then confirm the nodes still register and that a real render
still works:

```powershell
& "S:\Misc\SwarmUI\dlbackend\comfy\python_embeded\python.exe" TESTS\integration_live.py --url http://127.0.0.1:7822
```

## Pin it only after that passes

1. Put the new SHA in `config/dependencies.lock.json`, with the version, the node
   classes it provides and a note about what was verified.
2. Update `CHANGELOG.md` and `VERSION`.
3. Run the fast layer from `docs/TESTING.md`.
4. Land it through a pull request; `main` does not accept direct pushes.

## Rolling back

```powershell
git -C "<custom_nodes>\<pack>" checkout <previous-sha>
```

and restore the previous lock entry. Models are not affected: their SHA-256
contract lives in `presets/anima-manifest.json` and is checked on every install.

## ComfyUI's own upgrade

Upgrading ComfyUI can change the node API. After an upgrade, rerun the fast layer
and the live test. `.github/workflows/ci.yml` cannot cover this: it has no GPU and
no models, so those checks report UNVERIFIED there by design.
