# Archivum issues and todo

## Claude Rubric

For each row in the table add a new row below it for your input, set item to ">>CC".

* Enter low, medium or high effort
* Enter None, ... as impact on "speed" = user feel for speed of application. App MUST remain sprightly. This is assessment of whether the feature would have any impact on baseline performance of the app in its core functions.
* Under description add a **human-understandable** **short one- or two-line** summary and diagnosis (existing examples were too detailed and too complicated for me to understand!). Flag any issues.
* **BELOW** the table and with title the Item number, add your developer issues and implementation plan - this is "notes for Claude". Here lies comments more like the ones you have been producing.

***

Key

* ⚪ in progress
* ✅ completed by Claude
* 🟥 completed but issues
* 💚 completed and correct

## Batch A: 2026-10-24

| Item | Effort HML | Status/Impact | Description |
|--:|:---:|:---:|:-------------------------|
| **A.01** | ✅| | PDF viewer opens in a mode where the page does not quite fit. Mouse wheel jiggles page up and down vs desired behavior = page up/down |
| >>CC | Low | None | PDF.js wraps each page in an invisible 9px border, so "page fit" ends up 14px taller than the window: you get a scrollbar and the wheel nudges the page instead of turning it. Fix: drop the border, make the wheel turn pages. Both checked in a test browser. |
| **A.02** | ⚪|✅ | Opened files have "Not Secure..." in header - can that be removed  |
| >>CC | None | None | Closed, no work (Steve, 2026-10-24): call page from  http://127.0.0.1:9124 on DOOB to avoid. |
| **A.03** | ✅| | Add paper tag and title in windows bar (currently says archivum, want PAPERTAG \| title of displayed pdf. Make the title bar a distinctive color (blue) would help distinguish the app. |
| >>CC | Low | None | Easy: the title becomes `TAG \| title`, and the brand blue goes into the manifest and pages. ⚠ The blue shows only in an installed-app window (or on a phone), never in a normal browser tab. The app reinstalled from 127.0.0.1 qualifies. |
| **A.04** | ✅| | Text input boxes (eg query box, ripgrep box) do not store their history nicely; sometimes you see large and sometimes small fonts; no up/down to cycle through  |
| >>CC | Medium | None | Today only Enter saves, and the app has no Enter, hence about 6 entries after months. Fix: the server already sees every search, so it keeps the last one per box and files it once you stop (10 s quiet, box cleared, or ↑). One history shared by local and VPN use. Also switch off Chrome's own autocomplete list, the small-font popup that grabs the arrows. Decided: 10 s; one history per library. |
| **A.05** | | |   |
| >>CC | | |
| **A.06** | | |   |
| >>CC | | |

### Claude additional considerations

#### Executing batch A

Approved items and order: **A.01 → A.03 → A.04**. A.02 is closed with no work. Implement and
commit each item without asking again (the house rule: the plan is the approval gate). Steve
kicks the tires afterwards.

- **Repo**: `V:\dev\archivum`. Read the repo `CLAUDE.md` first ("Read this first — you are
  operating on live data"). The root `TODO.md` has no open items touching these areas.
- **This file stays at `dev/TODO.md`.** It is a running batch list, not a single plan, so it does
  not move to `dev/done/`. It was untracked when the batch was planned. Commit it with A.01, and
  update it in each item's commit: set that item's status cell (the ⚪ in column 2) to ✅ and add
  a short **Execution notes** list under the item's section (divergences, surprises, test
  results).
- **One version bump per item, one commit per bump, pushed at once.** The version is
  single-sourced in `pyproject.toml` `project.version` (2.13.1 when planned). After editing it,
  run `uv lock` so the `archivum` entry in `uv.lock` follows (earlier bumps committed both).
  Versions: A.01 **2.13.2**, A.03 **2.13.3**, A.04 **2.14.0** (a feature). Commit subject is
  one line with no body, ending with the Co-Authored-By trailer; subjects are given under each
  item. Check the staged file list for data before committing: no CSV, feather or
  `search-history.csv` from a library may be staged.
- **CHANGELOG.md**: newest first, matching the existing heading form `## 2.13.1 (2026-10-15)`
  with `### Added` / `### Changed` / `### Fixed`. Three to eight one-line bullets; no
  narratives.
- **`help.html` is a hard rule**: every web-visible change updates
  `src/archivum/web/templates/help.html` in the same commit. The sections touched are named
  under each item.
- **Tests.** Default suite: `uv run --extra test pytest -q`. Browser tests (Playwright,
  Chromium already installed):
  `uv run --extra test pytest -m "web and browser" --run-browser --run-slow-web`. Or use the
  runner `.\scripts\Test-ArchivumWeb.ps1 -Mode All`. New tests use a **temp library** (pattern:
  the `lib` and `app` fixtures in `tests/test_reader.py`, which build on `tmp_library` /
  `make_tmp_library` in `tests/conftest.py`), never the production library.

#### A.01 Reader page fit and mouse-wheel paging

**Goal.** In the in-app PDF reader, Page Fit shows the whole page with no scrollbar, and the
mouse wheel turns pages: one notch, one page.

**Current behavior.** `/view/<tag>` renders `src/archivum/web/templates/reader.html`, a page
whose `<iframe id="reader">` loads the vendored PDF.js 6.4.299 viewer
(`static/vendor/pdfjs/web/viewer.html`). A `webviewerloaded` listener in `reader.html` sets
`disablePreferences`, `scrollModeOnLoad` 3 (PAGE, one page at a time), `spreadModeOnLoad` 0 and
`defaultZoomValue` `page-fit`. Measured with a scratch Playwright probe (temp library, letter
pages):

- At 1280×800 the viewer container (`#viewerContainer`) has `clientHeight` 768 but
  `scrollHeight` 782, opening at `scrollTop` 10. The wheel moves the page between `scrollTop`
  0 and 14 and never changes page. Same at 1920×1080 (1048 against 1062).
- At 900×1200, width-limited, the page fits and the wheel does nothing.

**Cause.** `PDFViewer.#setScale("page-fit")` in `viewer.mjs` sizes the page to
`container.clientHeight - 5` (`VERTICAL_PADDING`). But `viewer.css` gives every page
`--page-border: 9px solid transparent` and `--page-margin: 1px auto -8px` (on `:root`), so the
page box is 14px taller than the container. Continuous scrolling hides that slack; one-page
view does not. Separately, PDF.js turns pages on the wheel only in fullscreen presentation mode
(`PDFPresentationMode.#mouseWheel`); in `ScrollMode.PAGE` the wheel only scrolls. The keyboard
needs nothing: PgDn, PgUp and the arrows already turn pages at page fit (`turnOnlyIfPageFit` in
the key handler).

**Change.** Only `reader.html`; the vendored files stay unmodified. Extend the existing
`webviewerloaded` listener as follows. The probe ran exactly this logic, but injected after
load rather than from the listener, so the browser test must confirm the listener version.

```js
document.addEventListener('webviewerloaded', (event) => {
    const win = event.detail.source;
    const options = win.PDFViewerApplicationOptions;
    // stored viewer preferences would otherwise override what follows
    options.set('disablePreferences', true);
    options.set('scrollModeOnLoad', 3);       // ScrollMode.PAGE: one page at a time
    options.set('spreadModeOnLoad', 0);       // SpreadMode.NONE
    options.set('defaultZoomValue', 'page-fit');

    // Page Fit allows 5px of padding, but each page carries a 9px transparent border and a
    // 1px / -8px margin: 14px of scroll slack in one-page view. The viewer's CSP
    // (style-src 'self') blocks a <style> element; variables set through the CSSOM are allowed.
    const viewer = win.document.getElementById('viewer');
    viewer.style.setProperty('--page-margin', '2px auto 0');
    viewer.style.setProperty('--page-border', '0 solid transparent');

    // The wheel turns pages while the page fits; zoomed in, it scrolls within the page.
    // Thresholds follow PDF.js presentation mode: delta in pages (pixels / 900, lines / 30),
    // turn at 0.1, 50 ms cooldown after a turn.
    const container = win.document.getElementById('viewerContainer');
    let accumulated = 0, lastTurn = 0;
    container.addEventListener('wheel', (e) => {
        const pdfViewer = win.PDFViewerApplication.pdfViewer;
        if (e.ctrlKey || e.metaKey || pdfViewer.scrollMode !== 3 || pdfViewer.isInPresentationMode) return;
        if (container.scrollHeight > container.clientHeight + 1) return;   // zoomed in
        e.preventDefault();
        const now = Date.now();
        if (now - lastTurn < 50) return;
        const delta = e.deltaMode === 0 ? e.deltaY / 900 : e.deltaMode === 1 ? e.deltaY / 30 : e.deltaY;
        if (accumulated * delta < 0) accumulated = 0;      // direction changed
        accumulated += delta;
        if (Math.abs(accumulated) >= 0.1) {
            const turned = accumulated > 0 ? pdfViewer.nextPage() : pdfViewer.previousPage();
            accumulated = 0;
            if (turned) lastTurn = now;
        }
    }, {passive: false});
});
```

Notes on the change:

- An inline `<style>` element injected into the viewer document does nothing: the probe tried
  it, and the viewer's CSP meta tag blocks it. Setting `style.setProperty` on `#viewer` works.
  Measured with it: `scrollHeight == clientHeight`, `scrollTop` 0, scale unchanged (0.7225 at
  1280×800). The border is transparent, so nothing visible is lost. In vertical-scroll mode,
  if chosen from the toolbar, pages sit 2px apart instead of about 11px. Presentation mode sets
  its own literal margin and border (`.pdfPresentationMode .pdfViewer .page`) and is unaffected.
- `#viewer` and `#viewerContainer` exist when `webviewerloaded` fires: `viewer.mjs` is a
  deferred module, so the viewer body has been parsed. `PDFViewerApplication` is read lazily
  inside the handler.
- The `isInPresentationMode` guard matters: presentation mode switches the scroll mode to PAGE
  and turns pages on the wheel itself, so without the guard every notch would turn two pages.
- The listener is on the container, so the sidebar (thumbnails, outline) scrolls normally.
  `passive: false` is required for `preventDefault`.
- Measured with the handler: page 1 → 2 → 3, then back to 2, one 100px notch each (a Windows
  mouse notch is 100px in Chromium).

**Files.** `src/archivum/web/templates/reader.html`, `tests/test_reader.py`, `help.html`,
`CHANGELOG.md`, `pyproject.toml`, `uv.lock`, this file.

**Test.** Add a browser test to `tests/test_reader.py` beside
`test_reader_opens_one_page_at_a_time`, on the same temp-library `app` (its PDF has three
letter pages) and its own `make_server` thread, as that test does. pytest-playwright's default
1280×720 viewport is landscape and height-limited, which is the failing case. Wait for
`pagesCount > 0` and `currentScaleValue === 'page-fit'`, then:

- `#viewerContainer` has `scrollHeight <= clientHeight`.
- Move the mouse to the viewport center, `page.mouse.wheel(0, 100)`, and wait until
  `currentPageNumber === 2`. Then `page.mouse.wheel(0, -100)`, and wait until it is 1.

**help.html.** In the Documents paragraph on `/view/<tag>` (the reader sentence: "showing one
page at a time at Page Fit (PgDn and the arrows page at once)"), add that the mouse wheel turns
pages while the page fits, and scrolls within the page when zoomed in.

**CHANGELOG 2.13.2, Fixed:** the reader's Page Fit shows the whole page with no scrollbar; the
mouse wheel turns pages at Page Fit and scrolls within the page when zoomed in.

**Commit:** `2.13.2 fit reader pages to the window and turn pages with the mouse wheel`.

**Acceptance.** Default suite and browser tests pass. Kick the tires (Steve): open a paper,
check there is no scrollbar, one notch turns one page, ctrl+wheel still zooms, and when zoomed
in the wheel scrolls.

**Not in scope.** Turning the page with the wheel when a zoomed-in page is scrolled to its
bottom, as Acrobat does. A long touchpad swipe can turn two or three pages, as in PDF.js
presentation mode.

**Execution notes (2.13.2, 2026-10-24).**

- `reader.html` carries the listener exactly as specified; the browser test confirms the listener version: no scroll slack at 1280×720, one notch forward turns to page 2.
- Divergence: the test waits 200 ms before the reverse notch. Playwright sends it at once, inside the 50 ms cooldown after a turn, and the handler swallows it as designed; a human notch never comes that fast.
- Hard constraint for this batch (Steve, 2026-10-24): no writes to the production library. `tests/test_read_tracking.py::test_record_read_only_touches_read_feather` writes, then restores, the production `read.feather`, so every run deselects it. Browser tests run without `--run-slow-web`, which would add the semantic network tests that can write `semantic-embeddings.feather`.
- The running server holds `.venv\Scripts\archivum.exe`, so `uv run` cannot reinstall the editable package after a bump (os error 32). Gate runs use `uv run --no-sync`, which tests the same `src` tree; the installed metadata stays at 2.13.1 until the server stops and `uv sync --extra test` runs.
- Gate: `uv run --no-sync pytest -q --deselect tests/test_read_tracking.py::test_record_read_only_touches_read_feather`: 125 passed, 25 skipped. `uv run --no-sync pytest -q -m "web and browser and not slow" --run-browser --deselect …`: 13 passed.

#### A.02 "Not secure" label in reader windows

Closed with no work (Steve, 2026-10-24). The installed app had been installed from the
machine's network IP over plain http, so the browser marked its pages "Not secure". Steve
reinstalled it from `http://127.0.0.1:9124`, which Chromium treats as a secure origin, and the
label went. The server still binds `0.0.0.0` for VPN use; which address a page is opened at is
what decides the label. Over the VPN the label remains, and that is accepted.

#### A.03 Reader window title and brand color

**Goal.** The reader window's title reads `TAG | title`, and the installed app's title bar is
the app's blue, so Archivum windows stand out.

**Current behavior.** `reader.html` sets
`<title>{{ tag }}{% if short_title %}: {{ short_title }}{% endif %}</title>`. `short_title` is
the clean title cut to 60 characters, built by `_render_reader` in
`src/archivum/web/routes/documents.py`. `static/manifest.json` has `"name": "archivum"`,
`"theme_color": "#ffffff"`, `"background_color": "#ffffff"`. No page has a
`<meta name="theme-color">`. `base.html` titles pages `Archivum | <library>`.

**Change.**

- `reader.html`: `<title>{{ tag }}{% if short_title %} | {{ short_title }}{% endif %}</title>`.
  The existing `{# tabs read as tags, not shard filenames #}` comment stays.
- `static/manifest.json`: `"theme_color": "#0d6efd"`. This is the app's `--primary-color`
  (`static/style.css` `:root`), Bootstrap primary. `background_color` stays white: it is the
  splash screen.
- `<meta name="theme-color" content="#0d6efd">` in the `<head>` of `base.html` (beside the PWA
  meta tags) and of `reader.html`. The meta tag takes effect in an installed-app window at
  once. Chrome refreshes an installed app's manifest lazily (at launch, at most daily), so the
  manifest change alone could lag.

**Caveat (already told to Steve).** A page can color the title bar only in an installed-app
window and in mobile browsers; a normal desktop tab ignores `theme-color`. If the reader's
window bar still shows only "archivum" after this change, Chrome is showing the manifest
`name`, not the page title. Note it in the execution notes for Steve; do not chase it.

**Tests** (`tests/test_reader.py`, temp-library `app`):

- In `test_view_renders_the_reader_and_counts_one_read`, change the title assertion to
  `"<title>Paper2024 | A Paper to Read</title>"` and assert
  `'<meta name="theme-color" content="#0d6efd">'` in the reader HTML.
- Add a test: `GET /` contains the same meta tag, and `GET /static/manifest.json` has
  `theme_color == "#0d6efd"`.

**help.html.** In the same reader sentence ("a browser tab titled with the tag"), say the
window is titled `TAG | title`.

**CHANGELOG 2.13.3, Changed:** reader windows are titled `TAG | title`; the app's theme color
is the brand blue, which colors the installed app's title bar.

**Commit:** `2.13.3 title reader windows TAG | title and set the app theme color to blue`.

**Acceptance.** Tests pass. Kick the tires (Steve): open a paper from the installed app and
check the window title and the blue bar.

**Execution notes (2.13.3, 2026-10-24).**

- Landed as specified, no divergence. The new test is `test_app_pages_and_manifest_carry_the_brand_theme_color` in `tests/test_reader.py`, on the temp library.
- For Steve: the manifest has `"display_override": ["tabbed"]`, so the installed app may open in tabbed mode, where each tab carries the page title and the window bar carries the app name. If the window bar still reads only "archivum", that is Chrome showing the manifest `name`, as the caveat above says; not chased.
- Templates and the manifest change only after the running server restarts, and Chrome refreshes an installed app's manifest lazily.
- Gate: default suite 126 passed, 25 skipped; browser (`-m "web and browser and not slow" --run-browser`) 13 passed; both with `uv run --no-sync` and the `read.feather` writer deselected, as in A.01.

#### A.04 Search box history from typed searches

**Goal.** ↑/↓ in the Query, Ripgrep and Network search boxes recall the searches Steve has run,
newest first, although the app has no Enter: the Query box searches as he types. One history,
whether the app is used locally or over the VPN.

**Current behavior.**

- `initSearchInput(inputId)` in `src/archivum/web/templates/base.html` (run on window load for
  `search-input` and `rg-input`) saves the box value to `localStorage`
  (`history_<inputId>`, 50 entries) **only on Enter**, and ↑/↓ walk that list.
- The Query box (`templates/query.html`, `#search-input`) runs
  `hx-get="/search"` on `keyup changed delay:250ms`, so Enter is almost never pressed: about six
  entries after months of daily use. The Ripgrep box (`templates/ripgrep.html`, `#rg-input`)
  searches on Enter (`keyup[key=='Enter']`) or on the Summary, Counts and Details buttons, and
  the buttons save nothing. The Network box (`templates/network.html`, `#network-search`)
  searches through `executeSearch(mode)` on Enter or the Social and Semantic buttons, and has no
  history at all.
- Neither box sets `autocomplete`, and both are `name="q"`, so Chrome offers its own
  autocomplete list of past `q` values. While that list is open, the arrow keys drive it, not the
  app's history. Highlighting an entry previews it in the box in Chrome's small UI font: the
  "sometimes large, sometimes small fonts" symptom (the box itself is 1.25rem). This diagnosis
  is not yet verified in a live browser.
- `localStorage` is per browser and per address, so `127.0.0.1` and the VPN address would keep
  separate histories however well they saved.

**Design: history from the searches themselves, kept on the server.** Every search already
reaches a route with the box text in `q`:

| box | routes | input id |
|---|---|---|
| `query` | `/search` (`routes/query.py` `search`) | `search-input` |
| `ripgrep` | `/rg-search` (`routes/ripgrep.py` `rg_search`) | `rg-input` |
| `network` | `/network-data` and `/semantic-data` (`routes/network.py`) | `network-search` |

Typing sends a stream of searches ("ri", "risk m", "risk measure"); history keeps where the
typing stopped. Rule, per box:

- The server holds one **pending** entry in memory: the latest search text and its time. Each
  search replaces it.
- The pending entry is **filed** into history:
  - when the next search for the box arrives more than **10 s** after it;
  - when the box is cleared (a search with empty text);
  - when the box's history is requested (the first ↑).

  Filing happens at that moment, inside that request. No timer thread.
- Filing a text already in the list moves it to the front, sets `last_used` and adds 1 to
  `uses`. A new text goes in at the front. Newest first, capped at 200 per box.
- Effects. A typing burst files one entry, its final text. Recalling an entry with ↑ re-runs
  it, so stopping on it files it, while entries passed over while cycling within 10 s are not
  filed and the order does not churn. View-mode toggles that resend the same text only refresh
  the pending entry. Button-made texts such as `q top 50 recent` are real searches and are
  filed like any other.
- Known gaps, accepted by Steve: a pause of more than 10 s mid-typing also files the fragment;
  select-all-and-retype within 10 s loses the first query; a pending entry is lost if the server
  stops before it is filed. No `atexit` flush.
- Decided by Steve: 10 s; one history per library (he uses one library).
- **Bridge traffic neither records nor reads history.** Only admin requests (`g.is_admin`, set
  in `web/app.py` `before_request`: anyone but the bridge `10.8.0.1`) call `note`, and the
  history route is `@admin_required`. Steve's queries never show on the public bridge.

**New module `src/archivum/web/services/search_history.py`** (about 80 lines, NumPy-style
docstrings):

- `QUIET_SECONDS = 10.0`, `LIMIT = 200`, `BOXES = ("query", "ripgrep", "network")`.
- `class SearchHistory(path: Path, clock=time.time)`:
  - `note(box, text)`: strips `text`. If a pending entry exists and either `text` is empty or
    `clock() - pending.time > QUIET_SECONDS`, file it. Then set pending to `(text, now)` if
    `text` is non-empty, else clear it.
  - `entries(box) -> list[str]`: files the pending entry (whatever its age), then returns the
    texts, newest first.
  - Filing rewrites the CSV atomically (write a sibling temp file, `os.replace`). The `csv`
    module is used, with columns `box,text,last_used,uses`; `last_used` is ISO 8601 local time
    to the second. The CSV is loaded lazily on first use, and a missing file means an empty
    history. One `threading.Lock` guards everything (waitress is threaded).
- `history_file(lib) -> Path`: returns `lib.config_path / "search-history.csv"`, the library
  directory beside `read.feather`. The library watcher ignores it: `LibraryChangeHandler` in
  `library.py` acts only on `ref.feather`, `doc.feather` and `ref-doc.feather`.
- `get_search_history(lib) -> SearchHistory`: one instance per `history_file(lib)`, cached in a
  module dict, so a library reload keeps its pending entries.
- `note_search(lib, box, text)`: returns at once unless `getattr(g, "is_admin", False)` (Flask
  `g`); otherwise `get_search_history(lib).note(box, text)`. The routes call this.

**Routes.**

- `routes/query.py` `search()`: call `note_search(lib, "query", raw_query)` after the
  `lib.is_empty` check and **before** the `if not raw_query` early return, so clearing the box
  files the pending entry.
- `routes/ripgrep.py` `rg_search()`: fetch `lib` first. If the library is not empty, call
  `note_search(lib, "ripgrep", options.query)` **before** the `has_work` check and before the
  cache lookup (`get_cached_ripgrep_search` returns early). Keep the existing responses
  unchanged.
- `routes/network.py` `network_data()` and `semantic_data()`: call
  `note_search(lib, "network", raw_query)` after the `lib.is_empty` check.
- New `GET /search-history/<box>` in `routes/query.py`, decorated `@admin_required`.
  `abort(404)` for a box not in `BOXES`; otherwise `jsonify(get_search_history(lib).entries(box))`.
  `routes/shared.py` does not export `g` or `jsonify`; import them explicitly
  (`from flask import jsonify`) rather than adding them to the star import.

**Client, `base.html`.** Rewrite `initSearchInput`, keeping its clear-icon wiring:

- A map from input id to box: `search-input → query`, `rg-input → ripgrep`,
  `network-search → network`. Call `initSearchInput('network-search')` on load as well.
- Drop the Enter-saves-to-`localStorage` code. Remove the old `history_<inputId>` key once, with
  `localStorage` access wrapped in try/catch. The six existing entries are not imported.
- State per box: `entries` (null until fetched), `index` (−1 means the typed text) and `typed`.
  An `input` listener (fired by typing, not by setting the value from code) resets
  `entries = null` and `index = -1`.
- On ArrowUp or ArrowDown, call `preventDefault`. If `entries` is null, ArrowDown does nothing;
  ArrowUp records `typed = input.value`, fetches `/search-history/<box>`, and keeps the entries
  that differ from `typed.trim()`. Filing the pending entry usually puts the current text first,
  and recalling it would look like nothing happened. A failed or 403 fetch gives `[]`. Then ↑
  steps toward older entries and stops at the last; ↓ steps back, and from index 0 restores
  `typed`. Set `input.value`, then call `htmx.trigger(input, 'keyup')`:
  - the Query box searches the recalled text through its `keyup changed delay:250ms` trigger;
  - Ripgrep's `keyup[key=='Enter']` filter does not fire, so a recalled ripgrep search runs on
    Enter or a button, as typed ones do (ripgrep is costly);
  - Network's inline `onkeyup` refreshes its feedback line.

  The explicit trigger is needed because the keydown handler awaits a fetch on the first ↑, so
  the real keyup can arrive before the value changes.

**autocomplete.** Add `autocomplete="off"` to `#search-input`, `#rg-input`,
`#network-search`, and to the filter boxes `#tag-filter` (`edit.html`), `#author-filter`
(`authors.html`), `#tag-search` (`components/edit_merge.html`) and `#help-search`
(`help.html`). Chrome ignores `off` for address and card autofill but honors it for plain
autocomplete entries, which these are.

**Keep tests off the production history (important).** The shared `app`, `client` and
`live_server` fixtures in `tests/conftest.py` run on the production library, and many web tests
call `/search`, `/rg-search` and `/network-data` as admin (`127.0.0.1`). Without a guard they
would write test queries into the real `search-history.csv`. Add an **autouse** fixture to
`tests/conftest.py` that monkeypatches
`archivum.web.services.search_history.history_file` to return
`tmp_path / "search-history.csv"` and clears the module's instance cache. The `live_server`
fixture serves in-process (a thread), so the patch reaches it too. Add a test that the
production path is untouched: record the mtime (or absence) of
`history_file(active_library)`, using the original function captured before patching, around a
`/search` call.

**Tests, new `tests/test_search_history.py`.**

- Unit tests on `SearchHistory(tmp_path / "h.csv", clock=fake)`:
  - "ri" at t=0, "risk m" at t=0.5, "risk measure" at t=1 → `entries("query") == ["risk measure"]`
    and the CSV has one row.
  - "a" at 0, "b" at 11 → `["b", "a"]`.
  - "a" at 0, "" at 1, "b" at 2 → `["b", "a"]` (clearing files).
  - "a" at 0, "b" at 20, "a" at 40 → `["a", "b"]`, with `uses` 2 for "a".
  - Boxes are independent; a new instance on the same path reloads the same entries; the cap of
    200 holds.
- Route tests on a temp-library app (the `lib` / `app` pattern from `tests/test_reader.py`):
  - `GET /search?q=Paper`, then `GET /search-history/query` → `["Paper"]`.
  - `/search-history/nope` → 404.
  - With `environ_base={"REMOTE_ADDR": "10.8.0.1"}`, `/search-history/query` → 403, and a
    `/search?q=secret` from the bridge does not appear in a later admin fetch.
- A browser test (own server on the temp library):
  - `#search-input` has `autocomplete="off"`.
  - Type `Paper` and wait for results. Clear the box (select all, Delete), which sends an empty
    search and files `Paper`.
  - Press ArrowUp: the box shows `Paper` and the results return.

**help.html.**

- Query Page, the "Search Bar" bullet ("Supports history (Up/Down arrows)"): what is saved. A
  search is remembered once you stop: 10 s without a new search, clearing the box, or pressing
  Up. A burst of typing leaves one entry, its final text. History is kept by the server in the
  library folder (`search-history.csv`), shared by every browser and address. Read-only bridge
  visitors neither add to it nor see it.
- Ripgrep and Network & Discovery: one bullet each. Up/Down recall past searches; Ripgrep runs
  a recalled search on Enter or a button.

**Speed.** Each search adds a lock and one assignment. Filing (a CSV of at most 600 short rows,
about a millisecond) happens once per finished burst, inside the next request for that box.
There is no thread, no polling, and nothing on page load. The first ↑ is one small GET.

**CHANGELOG 2.14.0.**

- Added: ↑/↓ history in the Query, Ripgrep and Network boxes, built from the searches you run
  (one entry per burst of typing, filed after 10 s, on clearing, or on ↑), kept per library in
  `search-history.csv`, and shared across browsers and addresses. Bridge visitors neither add to
  it nor see it.
- Changed: browser autocomplete is off in search and filter boxes; browser-stored history is
  retired.

**Commit:** `2.14.0 keep search-box history on the server, one entry per typing burst`.

**Acceptance.** The default suite and browser tests pass, and the production
`search-history.csv` is not created or touched by the test run. Kick the tires (Steve): use the
Query box normally for a few searches, then ↑ walks them, newest first, with no Chrome popup.

**Execution notes (2.14.0, 2026-10-24).**

- Names: module `web/services/search_history.py` (`SearchHistory.note` / `.entries`, `history_file`, `get_search_history`, `note_search`, `QUIET_SECONDS`, `LIMIT`, `BOXES`); route endpoint `main.search_history_entries`. No collisions in `src/`.
- Divergence: `#tag-filter`, `#author-filter` and `#tag-search` already had `autocomplete="off"`; it went onto `#search-input`, `#rg-input`, `#network-search` and `#help-search` only.
- Divergence (robustness, not in the plan): a failed CSV write (say, the file open in Excel) is logged and retried at the next filing, and `note_search` logs any history failure rather than raising, so a search never fails because of history. `/search-history/<box>` returns `[]` when no library is open.
- Divergence: `last_used` is the time the search ran (the pending entry's time), not the time it was filed.
- Divergence: the client keeps a counter bumped by typing, so a history fetch that returns after typing resumed is dropped instead of landing on new text.
- Divergence: the production-path test builds its own app with the semantic warmups stubbed, not the shared `client`. With the shared fixture its warmup thread started the real transformer load, which raced the fakes in `tests/test_semantic_model_cache.py` (run next, alphabetically): one failure seen, gone after the change, two clean runs since.
- Help: the Network bullet sits in the Social Graph list beside Live Interpretation, the list about the search box.
- Not verified: Chrome's own autocomplete popup as the small-font cause. Headless Chromium shows no such popup, so this rests on Steve's kick of the tires.
- Prod check: a snapshot of every file in the production library folder (189 files, size and mtime) taken before A.01 was identical after the A.04 gate, and no `search-history.csv` exists there.
- Gate: default suite 135 passed, 26 skipped (twice); browser (`-m "web and browser and not slow" --run-browser`) 14 passed; both `uv run --no-sync`, `read.feather` writer deselected.
- After the server restarts it writes `search-history.csv` into the library folder, by design: the first server write this feature makes.

