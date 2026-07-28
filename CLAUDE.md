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

**Параметризуй то, что будут подбирать на глаз.** `/mcp reconnect` требуется на
*любую* правку Python, включая изменение одной константы, — а подбор
визуальных величин (угол створки, длина панели, размер глифа) это всегда
несколько итераций. Каждая итерация = правка + реконект + перевставка.
Если величину явно будут крутить — выноси её в аргумент генератора сразу:
тогда следующая примерка стоит одну вставку и ноль реконектов. Проверено на
`door_open_deg`: переход 30°→50° обошёлся без единой правки кода.

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
- **Не доверяй имени документа в ответе `drawing create`.** Оно возвращает имя
  ПРЕДЫДУЩЕГО документа, а не нового: после `create` payload сообщал
  `polisnab-corridor-block-2x2-….dwg`, хотя реально открылся чистый чертёж.
  Сверяться по надёжному признаку — `entity_count` из `drawing info` (у нового
  документа единицы объектов вместо сотен), а не по `DWGNAME`, который тоже
  отстаёт. Та же категория, что `ok:true`≠успех: ответ формируется раньше, чем
  состояние успевает измениться, и звучит правдоподобно.
- **`ok:true` is not proof of success.** `drawing save` / `save_as_dxf` /
  `plot_pdf` echo the path back without checking the file was written — they
  report success even when the underlying `(command …)` no-ops. Always verify
  the artifact on disk (or `DWGTITLED` / `DWGNAME` for saves) after the call.
  (Known-flaky: `-PLOT` to `DWG To PDF.pc3` is a fragile, version-sensitive
  prompt sequence — if no PDF appears, plot manually from the app.)

- **Новый параметр генератора не доезжает до MCP-диспетчера сам.** Добавил
  аргумент в функцию в `polisnab_standards.py` — обязательно прокинь его в
  `server.py`, иначе вызов вернёт `ok:true`, `verified:true`, ноль warnings и
  **нарисует конфигурацию по умолчанию**. Отличить можно только по **эху
  параметра в payload** (`entrance: "row"` вместо `"end"`, `lead_in: 0`), не по
  картинке и не по `ok`. Случалось дважды: `room_number`, `entrance`.
  Правило: после добавления параметра сверять его значение в ответе, а не факт
  успеха. Та же категория, что `ok:true`≠успех и имя документа в `drawing create`.

## Selection rule (erase / modify)
Programmatic entity selection for erase/modify must use `ssget "_X"` (whole
database) + an explicit geometric intersection test, **never** `"_C"` / `"_W"`
(crossing/window). `_C`/`_W` are evaluated against the *current view*, so the
same call gives different results depending on zoom/pan — unstable and
non-reproducible. This is why `erase-window` filters the full DB by rectangle.

**`erase_window` НЕ универсальный ластик — он прибит к слоям стен.** Фильтр
внутри `mcp-cmd-erase-window` жёстко ограничен `AR-WALL` + `AR-WALL-INSUL`
(это сделано намеренно: чтобы прорезка проёма не съела мебель и текст рядом).
Мебель, текст и прочее им стереть **невозможно** — вызов вернёт `ok:true` и
сотрёт ноль объектов. Для мебели: `entity list <layer>` → `entity erase` по
каждому хэндлу. Массовое стирание = по одному вызову на объект (43 объекта —
43 вызова), другого пути через MCP нет.

**Стирание объектов ≠ потеря логики.** Геометрия в DWG — это вывод
генераторов, а не источник истины. Стёртую мебель всегда можно перегенерить
повторным вызовом `insert_*`; теряются только параметры конкретных вызовов
(какой offset был у локеров), не сам код. Не бояться чистить сцену.

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

- **Между мебелью НЕ ставить `insert_interior_wall` как разделитель.** Проверено
  на живом тесте dormitory: короткая перегородка «в ногах» пары кроватей читается
  как несущая стена (двойная грань + серая заливка) — это ошибка чертежа. Кровати,
  стоящие рядом или друг за другом у стены, разделяются **зазором** (заданное
  расстояние между габаритами), а не стеной. `insert_interior_wall` — только для
  настоящих перегородок между зонами (напр. отделить санузел), не для расстановки
  мебели.

- **`offset` вдоль стены отсчитывается от ВНЕШНЕГО угла, а не от внутренней
  грани.** (`_side_offset_geom`: «Offset 0 is the outer corner».) Ряд у стены с
  `offset=0` и суммарной длиной, равной полной наружной длине стены
  (`count*cell == L` или `W`), утапливает по 1/4 крайних ячеек в торцевые стены
  (150 мм из 600 = четверть — ровно это и всплыло на живом тесте локеров).
  Чистый пролёт между внутренними гранями = `L - 2t` (или `W - 2t`), он короче
  наружного на 2t. Правило: старт ряда от внутренней грани (`offset = t`), а
  ширину ячейки при переполнении ужимать под чистый пролёт
  (`cell = min(запрошенная, чистый_пролёт/count)`), иначе крайние ячейки сидят в
  стенах. Тот же трюк — для любого `wall_side`-ряда (кровати, мебель), не только
  локеров.

- **Пристенные элементы — не декорация, а физика.** Следующие приборы в реальном
  модуле ставятся ТОЛЬКО у стены, посреди помещения они не встречаются:
  - **`insert_convector`** — крепится под окном / к стене (подводка отопления);
  - **`insert_split_system`** — внутренний блок вешается на стену (трасса,
    дренаж);
  - **`insert_electrical_panel`** — щиток навешивается на стену (ввод кабеля);
  - `insert_shower` — угловой прибор: слив и вентиль должны лечь на реальные
    стены (стены по локальным -X и -Y);
  - `insert_nightstand` — спинкой к стене.

  Первые три — жёстко: конвектор/сплит/щиток посреди пола это ошибка чертежа, а
  не вопрос вкуса. Для них **обязательно** проверять, что тыльная грань лежит на
  внутренней грани стены, а `rotation_deg` развёрнут к этой стене.

  Центр ставится на `depth_mm/2` от внутренней грани стены, иначе прибор либо
  утонет в стене, либо повиснет с зазором.

  **`rotation_deg=0` НЕ означает одну и ту же стену у разных генераторов** —
  библиотека здесь исторически несогласована, проверять по докстрингу каждого:
  - стена **+Y (север)** при rot=0: `insert_toilet`, `insert_sink`;
  - стена **−Y (юг)** при rot=0: `insert_convector`, `insert_split_system`,
    `insert_electrical_panel`, `insert_nightstand`, `insert_wardrobe`;
  - `insert_shower` — угловой: стены −X и −Y (при rot=0 запад + юг).

  Практическое следствие: раковина и конвектор на ОДНОЙ стене требуют
  `rotation_deg`, отличающихся на 180°. Поставить их с одинаковым углом — тихая
  ошибка: `ok:true`, габариты верные, приборы смотрят в разные стороны.

  **Реалистичная расстановка (проверено на живом тесте студии).** Несколько
  правил «как в жизни», всплывших при доработке `generate_studio_module`:
  - **Санузел — это отдельная комната, а не ниша.** Отделять его глухой
    перегородкой на всю высоту с реальной дверью (`insert_interior_wall` двумя
    сегментами + `_draw_door_symbol` в проёме), а не оставлять открытый проход
    «как на кухню». Заодно санузел делается компактнее, а жилая зона больше.
  - **Тумба — у изголовья, а не в ногах.** Ставить у той стороны кровати, где
    голова/лицо, чтобы дотянуться рукой (кровать головой к стене → тумба сбоку
    у изголовья, спинкой к той же стене).
  - **Сплит-система — не над кроватью** (сквозняк на спящего). Вешать на стену
    в стороне, над жилой/рабочей зоной.
  - **Щиток — у входа**, а не в санузле (ввод кабеля идёт от входной группы).
  - **Стул — вплотную к столу** (передняя грань сиденья к кромке столешницы),
    а не на расстоянии.

  **Ловушка:** сигнатуры этих генераторов принимают абсолютные `x_mm, y_mm` +
  `rotation_deg` — то есть НЕ мешают поставить прибор в центр комнаты, и ошибка
  ничем не проявится (`ok:true`, габарит верный). В отличие от них
  `insert_locker_row` / двери / окна берут `wall_side` + `offset_mm` и посадку на
  стену гарантируют самой сигнатурой. Если пристенные элементы начнут ставиться
  часто — их стоит перевести на тот же `wall_side`-идиом, тогда правило будет
  выполняться механически, а не держаться на внимательности.

## Reference
Full plan, phase status, and standards (layers / dimstyle / node nomenclature):
see `PROJECT-BRIEF-autocad-mcp-polisnab.md`.
