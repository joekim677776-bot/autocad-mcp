# CLAUDE.md

Fork of **autocad-mcp**, customized for drafting Polisnab module cabins (бытовки).

## Environment
- Windows, Python 3.14.2, `uv` for env/deps.
- AutoCAD 2027 (trial). Active backend: **file_ipc** (talks to AutoCAD over COM).
- Repo: `C:\Projects\cadagent\autocad-mcp`.

## Deploy / reload rule
The LISP dispatcher (`lisp-code/mcp_dispatch.lsp`) lives in the namespace of a
**specific AutoCAD document**. Opening a **new drawing** drops it — reload with
`APPLOAD` or `(load "…/mcp_dispatch.lsp")` in that document before using the tools.
- Changed **LISP** → reload the dispatcher (per above).
- Changed **only Python** → just `/mcp reconnect`; do not touch LISP.

## Known traps (rules, not history)
- **FILEDIA guard.** Any LISP command that can pop a file dialog (e.g.
  `-LINETYPE _LOAD`) must be wrapped `(setvar "FILEDIA" 0) … (setvar "FILEDIA" 1)`.
  Otherwise it silently hangs and corrupts everything later in the same
  `(command …)` chain.
- **Coordinate serialization.** Every float coordinate crossing the file_ipc
  boundary must go through `_fmt_coord` / `_round_coord` (in
  `backends/file_ipc.py`). Raw Python `repr(float)` emits scientific notation and
  FP noise that AutoLISP `atof` cannot parse — geometry collapses / distorts.
- **execute_lisp is disabled by default** (`AUTOCAD_MCP_ALLOW_EXECUTE_LISP=false`).
  Deliberate security choice; do not enable without an explicit reason.
- **File paths must use forward slashes** (`C:/Projects/…`), not backslashes.
  A path with `\\` reaches the LISP JSON parser un-unescaped and `_.SAVEAS` /
  `-PLOT` silently fail on the malformed path. Symptom: the tool returns
  `ok:true` but nothing is written and `DWGTITLED` stays `0`. Forward slashes
  are accepted everywhere by AutoCAD and sidestep all escaping.
- **`ok:true` is not proof of success.** `drawing save` / `save_as_dxf` /
  `plot_pdf` echo the path back without checking the file was written — they
  report success even when the underlying `(command …)` no-ops. Always verify
  the artifact on disk (or `DWGTITLED` / `DWGNAME` for saves) after the call.
  (Known-flaky: `-PLOT` to `DWG To PDF.pc3` is a fragile, version-sensitive
  prompt sequence — if no PDF appears, plot manually from the app.)

## Selection rule (erase / modify)
Programmatic entity selection for erase/modify must use `ssget "_X"` (whole
database) + an explicit geometric intersection test, **never** `"_C"` / `"_W"`
(crossing/window). `_C`/`_W` are evaluated against the *current view*, so the
same call gives different results depending on zoom/pan — unstable and
non-reproducible. This is why `erase-window` filters the full DB by rectangle.

## Screenshots / visual feedback
- `get_screenshot` returns a **black frame** if the AutoCAD window is minimized,
  covered, or not in focus. Keep the AutoCAD window **visible and maximized**
  for the whole session when relying on screenshots.
- If the screenshot is still black despite a visible window, run **`REGENALL`**
  manually in the AutoCAD command line to force a redraw.
- Screenshots are large; capturing repeatedly burns a lot of context. Prefer
  querying state (`drawing info`, `get_variables`) and only screenshot when you
  genuinely need to *see* the geometry.

## Small decorative icons (door glyphs, furniture symbols)
Tiny decorative details don't converge well through several rounds of
text/coordinate description. If a glyph isn't right after 2–3 attempts, stop
re-describing it in prose — either (a) hand the agent the design freedom within
stated bounds, or (b) give it a **reference image** to analyse pixel-by-pixel
directly (Python + PIL). Both beat another round of "move it 3 mm left".

## Drafting rules
- **Внутренние перегородки (`insert_interior_wall`) НЕ предполагаются по
  умолчанию сплошными на весь пролёт помещения.** Планировка реальных модулей
  часто использует:
  - (а) неполные перегородки, не доходящие до противоположной стены — например,
    короткая стенка гардероба с открытым проходом рядом;
  - (б) проходы без двери — реализуются просто как разрыв между двумя отдельными
    вызовами `insert_interior_wall` на одной оси, а не как отдельный инструмент;
  - (в) Г-образные и многосегментные перегородки вокруг ниш — несколько
    последовательных прямых сегментов, состыкованных по углам.

  При построении планировки по ТЗ всегда явно продумывать, где нужен полный
  пролёт, а где частичный/с проходом — не считать «от стены до стены» дефолтом.
  См. референсы в `reference/` для примеров.

## Reference
Full plan, phase status, and standards (layers / dimstyle / node nomenclature):
see `PROJECT-BRIEF-autocad-mcp-polisnab.md`.
