# SpriteGrid Editor Help

A tool for **copying, mirroring, pasting and clearing pixel-art sprite sheets cell by cell**.
Every edit, zoom and save uses **nearest-neighbor** only, so your pixels never get blurred.

## Quick start

1. Press `Ctrl+O` to open an image (PNG, JPG, ...), or drop a file onto the window.
2. A **sheet spec** matching the image size (H1 Walk 3 cols × 4 rows, H2 Combat 6 cols × 5 rows) is selected automatically. If the size differs or the background is not transparent, the **Import Image** dialog opens (see below).
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
| `B` | Cycle the background (checkerboard → white → black → dot grid) |
| `Shift+B` | Show/hide the Base Body reference layer |
| `Shift+E` | Show/hide the edit layer (to compare against the references) |
| `Ctrl+Shift+R` | Add a reference layer (PNG) |

Column headers read `C1 C2 C3` across the top and row headers `R1 R2 R3 R4` down the side. Cells are written like `R4C1` (row 4, column 1).

## Edit menu / shortcuts

| Function | Shortcut | Description |
|---|---|---|
| Copy / Cut | `Ctrl+C` / `Ctrl+X` | Put the Source area on the internal clipboard |
| Paste | `Ctrl+V` | Overwrite at the **Target** (or the top-left of the Source if no Target) |
| Paste Mirrored | `M` | Paste flipped horizontally |
| Paste Flipped Vertically | `Shift+M` | Paste flipped vertically |
| **Mirror & Apply** | `Ctrl+M` | Flip the Source and write it straight onto the Target |
| Clear Cells | `Delete` | Reset the selected cells' RGBA to (0,0,0,0) |
| Undo / Redo | `Ctrl+Z` / `Ctrl+Y` | Up to 100 steps |

- Selecting **several cells** (e.g. a whole row) copies, pastes, mirrors and clears them in one go.
- Mirroring flips **each cell separately**: the cell order (frame order) stays the same and only each frame's picture is flipped.
- If you set the Target as a **range**, its shape (rows × cols) must equal the copied area. Clicking a single Target cell just sets the start position.
- **Asymmetric details** such as a hair parting or a weapon hand can be off after mirroring. Check with the Base Body overlay and fix them by **double-clicking the cell to open the cell editor** (there is no option to exclude areas from mirroring).

## Common tasks

**Make a walking frame face the other way (R4C1 → R4C3)**
1. Click `R4C1` (Source) → `Ctrl`+click `R4C3` (Target) → `Ctrl+M`.

**Mirror a whole row (3 frames) onto the opposite-direction row**
1. Drag-select the first row (`R1C1`–`R1C3`) → `Ctrl+C`.
2. `Ctrl`+click the first cell of the destination row → `M`.

**Check that hair/outfit positions line up with the Body**
1. Press `Shift+B` (or tick the Base Body row in the right panel's `Layers` list) and adjust its opacity there.

## Layers: edit layer vs reference layers

- The **edit layer** is the sheet you opened. Clicks, pastes and saves only ever affect it. It has a **thick outline** and appears as `[EDIT·saved]` in the list. The bar above the canvas always names it.
- **Reference layers** (`[REF·above/below]`) are read-only pictures for checking alignment — the Base Body and any PNGs you add. They are tinted (turn off with `Tint references`) and are **never saved**.
- Each layer has an eye checkbox; references also have an opacity slider and an above/below-the-sheet position.
- **Limit: up to 6 reference layers** (Base Body 1 + 5 added PNGs). The edit layer is always exactly 1. Added references are not remembered after you quit; Base Body visibility/opacity/position are.

## Import image (size & background)

When you open an image whose **size differs from the sheet specs or whose background is not transparent** (e.g. output from A1111 or Gemini), this dialog appears.
For an already open sheet use `Tools → Adjust Image Size / Background...` (`Ctrl+Shift+I`).

- The **original size** and **background** (has transparency / solid #RRGGBB / not solid) are detected automatically.
- **Sheet spec**: choose what the sheet will be used for. Width and height are calculated to fit it.
- **Auto (keep ratio)** is the default: type only the width and the height follows (and vice versa). Choose **Custom** to enter width and height separately.
- Quick buttons: `Fit to Sheet Spec` / `Original` / `50%`.
- **Resampling**: `Nearest` for pixel art; `Box average` gives smoother results when shrinking large AI images.
- **Fit to sheet**: pads with transparency or crops the overflow so the result matches the spec size exactly, so the grid and **Base Body** line up.
- **Background**: `Auto` removes the border color. If the background is not solid, choose `Make chosen color transparent` and **click the original preview** or use the color button.
  - **Tolerance**: higher values also remove similar colors (AI noise and soft edges).
  - **Scope**: `Only areas connected to the edge` keeps the same color inside the character (eye whites, etc.). To also clear enclosed gaps (between arm and body), choose `Same color everywhere`.
- `Apply and Open` opens the result as **modified (*)**; the original file is untouched. `Open Original` opens it as before.

## Gray + mask (outfit sheets)

Turns a color outfit sheet into a **gray sheet + index mask (`_M`)** for Unity's runtime tint (CHR-ART.5 §3.3).

1. Open a color outfit sheet and choose `Tools → Convert Color → Gray + Mask...` (`Ctrl+Shift+G`).
2. **Gray**: keep `Normalize brightness` on for new outfits (0.42–1.0, median 0.72). Turn it off for finished sheets (e.g. base underwear).
3. **Region split**
   - `Color based`: enter the region count (1–4) and press [Split]. Choose an index per chip (1 Primary / 2 Secondary / 3 Accent / 4 Patch / 0 None).
     If the split is wrong, press a chip's color button and **click the original preview** to change its representative color (re-splits immediately).
     If light and dark sides of the same garment are separated, lower `Lightness weight`.
   - `Horizontal split`: single-color outfits. Set indices above/below a line y in each cell.
   - `No mask`: fixed-color outfits (gray only).
4. [Apply] → check borders with the mask overlay. It can be undone.
5. `Ctrl+S` saves `name.png` (gray) and `name_M.png` (8-bit mask) together, after a mask check.

### Mask view · color preview (left panel `Outfit Color · Mask`)
- **Mask view**: Off / Overlay / Mask only (`Alt+M`). 1 red · 2 blue · 3 green · 4 yellow.
- **Highlight missing pixels**: opaque pixels with region 0 are shown in magenta.
- **Tint preview on canvas** (`Alt+T`): multiplies the 4 colors with the gray (same formula as the Unity shader). `Brightness gain` is 1.00 for new outfits, 1.33 for the base underwear.
- Enter colors as `#RRGGBB`, click a swatch, or use `Paste colors` (4 `#RRGGBB` values from the clipboard — a copied sheet row works).
- `Compare 4 Presets...` (`Ctrl+Shift+P`): shows the Source cell (or the whole sheet) with 4 presets + a squad color side by side. ⚠ means the color is too dark (V below 0.30) or the shading disappears.
- `Mask Region Stats...` (`Ctrl+Shift+T`): region pixel counts per cell, region 0 (possibly missing), regions much smaller than in other cells (orange).
- Confirm exact final colors in Unity.

### Nudge
- Select Source cell(s) and press `Alt+Arrow` (1px) / `Alt+Shift+Arrow` (5px). Content moves inside each cell; pixels pushed out of the cell are dropped. The mask moves too.

### Mask files
- Opening `X.png` also loads `X_M.png` from the same folder as the mask, if present.
- Copy, paste, mirror and clear also handle the mask.

## Mask brush · pair work · part references (stage 3)

### Mask brush (left panel `Mask Brush`, menu `Tools → Mask Brush`)
- Tools: **Select (V)** · **Pencil (P)** · **Fill (F)** · **Rectangle (R)** · **Eraser (E)**. Pick the region index with keys `1`-`4`. Brush size 1-15 px (square, for pixel art).
- **Alpha protection**: indexes 1-4 can't be painted where alpha is 0. The eraser writes 0, so it works anywhere.
- **Scope**: `Current cell` (where the stroke began) / `Selected cells (Source)` / `Whole sheet`. Pixels outside the scope are never touched.
- While a tool is on, click/drag paints. **Alt+click = select Source**; Shift/Ctrl+click = Target as usual. The mask overlay turns on automatically with a tool.
- One stroke / fill / rectangle = **one undo step**.
- **Replace index**: change index A to B inside the scope at once (e.g. 2→3, missing 0→1).

### Cell-to-cell mask copy
- `Copy mask only to Target` (Ctrl+Alt+M): copies only the mask of the Source cell(s) to the Target position. **Artwork stays**, and pixels with no alpha become 0.
- `Copy mask to whole row`: copies the first Source cell's mask to the other cells of the same row (frames of the same motion).

### F/M pair work (`Tools → Start F/M pair work`)
- For building one outfit for both female (F) and male (M) bodies. The current sheet + mask is duplicated as a **new document for the other body type** (`…F` → `…M`), and the original stays as a **read-only reference**. It is not an automatic conversion: you re-fit positions on the other body type's Base Body.
- View: `Side by side` (reference canvas on the left, zoom/scroll synced) / `Overlay` (semi-transparent reference layer above the edit sheet).
- Cross-gender paste warnings are skipped during pair work. If the other body's Base Body isn't found you're asked before starting: `Pick Body / Continue without Body / Cancel`. The mask overlay turns on at start so you can check edges that shifted with the new body type and fix them with the brush. `End pair work` closes the reference.
- Sheets with body type Common (C) can't start pair work.

### Part reference layers
- Choose the **Editing part** (Outfit / Hair / …) in the right `Layers` panel; reference parts are placed above/below the edit sheet by the CHR-ART.5 §7 compositing order (Body → Eyes → Outfit → Hair → Helmet → Weapon). The part is guessed from the file name, defaulting to Outfit.
- `Auto-load part references` loads, from `Allies/<part>/`, the files matching body type and motion, following the real naming: **Hair loads `HairC01` (F) / `HairC17` (M)** (body type is by number: C01-16 F, C17-24 M), **Eyes loads `EyesC01_E1_H1WK_Open`**, other parts only **when there is a single candidate** (use `Add` for any other file). Max 6 reference layers including Body; eye, opacity and position are per layer.

## Cell editor · Move area · Batch convert (stage 4)

### Cell editor — erase dots and paint (`Edit → Cell Editor…`, Ctrl+E, double-click a cell)
- Opens the selected cell (or **the whole selected range**) in a pop-up, magnified (800% by default, wheel to zoom). Base Body and reference layers for the same area are drawn below (or above) so you can paint against them.
- **Only the area inside the thick border is edited**; the neighbor cells around it are shown semi-transparent, view-only (`Neighbors` checkbox / turn off or set opacity 10-90% in Preferences).
- Tools: **B paint · E erase · I eyedropper**. While painting or erasing, **hold Alt for the eyedropper** (release to go back); Alt+click also picks. `[` `]` brush size, `X` swaps primary ↔ secondary color, click a color swatch to choose a color.
- The cursor follows the tool — paint = brush-size square, erase = dashed square, eyedropper = pipette. The top line shows tool, size and color; the right side shows the coordinates and color under the mouse.
- Paint is always opaque (alpha 255); erased pixels become transparent with mask 0. On sheets with a mask, **the eyedropper takes the mask index together with the color** and paints it too (or set `Mask index` directly).
- Ctrl+Z / Ctrl+Y undo inside the window. **Apply (Ctrl+Enter)** closes it and writes to the sheet — in the main window **one undo** reverts everything done in the editor. `Cancel` / Esc discards (asks if anything changed).

### Move area — move across cell borders (`Edit → Move Area`, S)
- Drag on the canvas to select a **pixel rectangle**, then drag inside it to move. While dragging, the original spot is dimmed and the content is previewed at its new place.
- Arrow keys 1px / Shift+arrows 5px, Esc clears the selection. Each move is one undo step.
- The original spot is cleared to transparent and **only opaque pixels** are placed (transparent parts of the selection don't erase a neighboring frame). The mask moves too; anything pushed off the sheet is dropped. No resizing or rotation.

### Batch convert a folder — many outfits at once (`Tools → Batch Convert Folder…`, Ctrl+Shift+B)
- Converts the color sheets in an input folder **to gray + mask (`_M.png`) in one go** and saves them to an output folder. Same rules as the single conversion (color-based / horizontal split / gray only, despeckle, brightness normalize auto/on/off).
- Pick files with the checkboxes. `_M.png` and `_backup.png` files are left out, and files whose size matches no sheet spec are skipped.
- Color-based split assigns **regions automatically, most pixels first: 1 (main), 2 (secondary), 3, 4** — fix anything split differently with the mask brush.
- The result column shows saved / warnings (file-name rule, missing regions, …) / skipped; hover for all notes. **Double-click a result row to open that file in the main window.**
- Using the input folder as the output folder is blocked (it would overwrite the color originals). Save settings (alpha threshold, transparent RGB) come from Preferences.

### Next / previous file in the folder (`File → Next File in Folder`, Ctrl+PgDn / Ctrl+PgUp)
- Opens the next or previous PNG in the same folder as the current file (by name, masks and backups excluded) — handy for stepping through batch results one by one. Asks first if there are unsaved changes.

### Unity `.meta` copy (`Preferences → Save`, on by default)
- When saving **inside a Unity project's `Assets`** and the PNG has no `.meta` yet, the `.meta` of a **same-size sheet in the same folder is copied** — import settings such as Point filter, no compression and sprite slicing carry over.
- guid follows the project rule `md5('S_unit_' + file name)`, sprites are renamed `newname_0…`, sprite IDs are rebuilt from the names (same values every time).
- An existing `.meta` is never touched (overwriting keeps the guid). Without a same-size template nothing is copied and Unity creates defaults. Masks (`_M.png`) only use mask `.meta` files as templates.

### Keyboard shortcuts (`Help → Keyboard Shortcuts`, Ctrl+/)
- Lists the menu shortcuts **read live from the running menus**, plus canvas and cell-editor keys. The cell editor's B and E only work inside that window, so they don't clash with the main B (background cycle) and E (mask eraser).

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
- **File name checks** (warnings only): rule format, lower case, old kind letters (`C`, `TC`, `BU`, …), number 000, a trailing `_M` (looks like a mask), a prefix that differs from the sheet spec motion (`H1WK`, `H2`), common and body-specific files with the same ID in the save folder, and an old `_M.png` left behind when saving without a mask.
- If the name already exists you are asked to confirm, and the previous file is kept as `<name>_backup.png`.
- Without a configured save folder, files go to `output/` next to the program. Use `Preferences → Save` for any folder, the recent-folder list, and `Ask for the folder every time I save`.
- A file you only opened is **never overwritten automatically**; the save dialog always opens on the first save.

## Autosave / recovery

- On by default · every 10 minutes · keeps 2 snapshots (change in `Preferences → Autosave`).
- After an unexpected exit, a `Recover / Ignore` prompt appears on the next launch. Snapshots are deleted after a normal save or a normal exit.
- Autosaves never overwrite your original PNG.

## Preferences

`Settings → Preferences`: language (한국어 / English), theme (dark / light / orange / midnight blue / forest green / high contrast), grid color and thickness, save folder, alpha threshold, transparent pixel RGB, sheet spec presets, Base Body folder, Allies folder (part references), pair-work overlay opacity, cell editor (neighbor display, opacity, default zoom), Unity `.meta` copy, autosave.
Every value is saved to `config.json` as soon as you change it.

## Coming next (roadmap)

- All four stages of the plan's roadmap are implemented. Next: tune values and defaults while making real outfits.
