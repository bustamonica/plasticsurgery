# Uploading this repo to GitHub (no local git needed)

Everything happens in your browser. ~5 minutes.

## 1. Open a Codespace on your repo

1. Go to `https://github.com/bustamonica/plasticsurgery`
2. Click the green **Code** button → **Codespaces** tab → **Create codespace on main**
   (if the repo is empty and GitHub offers no codespace, add any file first via
   **Add file → Create new file**, e.g. a blank `README.md`, then retry)
3. Wait for the VS Code window to load in the browser tab.

## 2. Upload the bundle

1. In the codespace's left **file explorer**, right-click the empty area →
   **Upload...** and select `morphengine.bundle` (use the one downloaded WITH
   this session — older copies are outdated).
2. It lands in `/workspaces/plasticsurgery/morphengine.bundle`.

## 3. Paste these 4 lines into the terminal

Open a terminal in the codespace (menu **Terminal → New Terminal**, or Ctrl+`)
and paste:

```bash
git clone /workspaces/plasticsurgery/morphengine.bundle /tmp/mp
cd /tmp/mp
git remote set-url origin https://github.com/bustamonica/plasticsurgery.git
git push -f origin main
```

> `-f` (force) replaces whatever history is currently on GitHub `main` with
> this repo's history. That is intended: this bundle IS the project.
> Push works out of the box — the codespace authenticates to its own repo.

## 4. Verify

```bash
git log --oneline -3
```

The top line must be the skirted-dome commit (`8bc3e4e feat: skirted dome
(SPEC rev.8) ...`). Then refresh the repo page on GitHub — all files should
be there.

## 5. Clean up (optional but recommended)

Delete the codespace at `https://github.com/codespaces` (••• → Delete) so it
doesn't consume the free monthly quota.
