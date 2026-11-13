# SpriteGrid Editor Help

A tool for **copying, mirroring, pasting and clearing pixel-art sprite sheets cell by cell**.
Every edit, zoom and save uses **nearest-neighbor** only, so your pixels never get blurred.

## Quick start

1. Press `Ctrl+O` to open a PNG sheet, or drop a file onto the window.
2. A **sheet spec** matching the image size (H1 Walk 3 cols × 4 rows, H2 Combat 6 cols × 5 rows) is selected automatically. If nothing matches, the `Custom` dialog opens.
3. Click a cell to choose the **Source**, then hold `Shift` or `Ctrl` and click to set the **Target**.
4. Edit with `Ctrl+M` (Mirror & Apply) or `Ctrl+C` → `Ctrl+V`, etc.
5. Press `Ctrl+S` to save. The first time, a dialog lets you pick the folder and file name.

## Canvas controls

| Action | Result |
|---|---|
| Click / drag | Select the **Source** cell (drag to select a rectangular range of cells) |
| `Shift`/`Ctrl` + click (drag) | Set the **Target** (solid yellow = Source, dashed orange = Target) |
| Mouse wheel | Zoom 100–800% (integer scales) |
| Middle-drag / `Space` + drag | Pan |
| `G` | Toggle the grid |
| `B` | Cycle the background (checkerboard → Base Body → white → black → dot grid) |

Column headers read `C1 C2 C3` across the top and row headers `R1 R2 R3 R4` down the side. Cells are written like `R4C1` (row 4, column 1).

## Edit menu / shortcuts

| Function | Shortcut | Description |
|---|---|---|
| Copy / Cut | `Ctrl+C` / `Ctrl+X` | Put the Source area on the internal clipboard |
| Paste | `Ctrl+V` | Overwrite at the **Target** (or the top-left of the Source if no Target) |
| Paste Mirrored | `M` | Paste flipped horizontally |
| Paste Flipped Vertically | (menu) | Paste flipped vertically |
| **Mirror & Apply** | `Ctrl+M` | Flip the Source and write it straight onto the Target |
| Clear Cells | `Delete` | Reset the selected cells' RGBA to (0,0,0,0) |
| Undo / Redo | `Ctrl+Z` / `Ctrl+Y` | Up to 100 steps |

- Selecting **several cells** (e.g. a whole row) copies, pastes, mirrors and clears them in one go.
- Mirroring flips **each cell separately**: the cell order (frame order) stays the same and only each frame's picture is flipped.
- If you set the Target as a **range**, its shape (rows × cols) must equal the copied area. Clicking a single Target cell just sets the start position.
- **Asymmetric details** such as a hair parting or a weapon hand can be off after mirroring. Check with the Base Body overlay.

## Common tasks

**Make a walking frame face the other way (R4C1 → R4C3)**
1. Click `R4C1` (Source) → `Ctrl`+click `R4C3` (Target) → `Ctrl+M`.

**Mirror a whole row (3 frames) onto the opposite-direction row**
1. Drag-select the first row (`R1C1`–`R1C3`) → `Ctrl+C`.
2. `Ctrl`+click the first cell of the destination row → `M`.

**Check that hair/outfit positions line up with the Body**
1. In the right panel under `Background`, choose `Base Body overlay` and adjust the opacity.

## Sheet specs (columns × rows)

- Pick from the toolbar's `Sheet spec ▼`: `H1 Walk — 3 cols × 4 rows (cell 100×135)`, `H2 Combat — 6 cols × 5 rows (cell 256×256)` or `Custom...`.
- **Custom**: ① sheet size (px) + columns/rows → cell size is calculated, ② cell size (px) + columns/rows → sheet size is calculated. If the sheet size is not divisible by the column/row counts you cannot confirm.
- Turn on `Save as preset` to add it to the list. Manage presets in `Settings → Preferences → Sheet` (the built-in H1/H2 cannot be deleted; duplicate them to edit).
- If the sheet size does not match `columns × cell width` / `rows × cell height`, a warning is shown and **saving is blocked**.

## Body type (Female F / Male M / Common) and Base Body

- A file name ending in `…F` / `…M` sets the body type automatically; otherwise it is **Common** and you choose it in the toolbar.
- The **Base Body** is a **read-only reference image** for checking that parts such as outfits line up (it is not included in the export).
- The tool finds `Body{F|M}[_T2~5]_{H1WK|H2}.png` from the body type + skin tone (T1–T5) + sheet spec. (There is currently no male H2 Body → a warning appears; use `Choose...`.)
- Change the Body folder in `Settings → Preferences → Reference`.
- **Pasting across body types** (female cells → male sheet) shifts frame positions, so a warning is shown.

## Saving

- Only **PNG (RGBA)** is supported.
- **Alpha threshold** (default 128): alpha below the threshold becomes 0, otherwise 255, which removes translucent fringes. Turn it off to keep the original alpha. Transparent pixels get RGB (0,0,0) by default.
- **File name helper**: builds names like `H1WK_P001F` from `kind (U/P/G/N/S) + 3-digit number + body type`. Names that break the rule only produce a warning; saving is still allowed.
- If the name already exists you are asked to confirm, and the previous file is kept as `<name>_backup.png`.
- Without a configured save folder, files go to `output/` next to the program. Use `Preferences → Save` for any folder, the recent-folder list, and `Ask for the folder every time I save`.
- A file you only opened is **never overwritten automatically**; the save dialog always opens on the first save.

## Autosave / recovery

- On by default · every 10 minutes · keeps 2 snapshots (change in `Preferences → Autosave`).
- After an unexpected exit, a `Recover / Ignore` prompt appears on the next launch. Snapshots are deleted after a normal save or a normal exit.
- Autosaves never overwrite your original PNG.

## Preferences

`Settings → Preferences`: language (한국어 / English), theme (dark / light), grid color and thickness, save folder, alpha threshold, transparent pixel RGB, sheet spec presets, Base Body folder, autosave.
Every value is saved to `config.json` as soon as you change it.

## Coming next (roadmap)

- v0.2.0: color → gray + index mask conversion, in-cell nudge, preset color preview, mask region statistics
- v0.3.0: mask boundary brushes, F/M pair mode, multiple reference layers
