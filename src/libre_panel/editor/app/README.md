# The app

Libre Panel's app: Start, Design, Look, Live and Settings in one page, shown in its own
window (`libre_panel/window.py`) or a browser. No build step: plain ES modules with
Preact and htm, vendored in `vendor/` (versions and SHA-256 in `vendor/SOURCES.txt`).
The editor's server (`editor/server.py`) serves `index.html` at `/`, this folder at
`/app/` and the bundled fonts at `/app/fonts/`.

## What is where

| Path | What |
|---|---|
| `main.js` | starts the app: language, specs, themes, pages, the theme on the panel, the views |
| `lib/preact.js` | `html` (htm on Preact) and the hooks |
| `core/state.js` | the one store: `store.get()`, `store.set(patch)`, `useStore(selector)`, `setPref()` |
| `core/doc.js` | the theme being edited: `theme()`, `widgets()`, `widgetById()`, `selection()`, `setSelection()`, `edit(key, fn)`, `commit()` + `touch()`, `undo()`, `redo()`, `openDoc()` |
| `core/actions.js` | open, save, save as, import, export, `applyToPanel()`, `adaptPanel()`, `uploadAsset()`, the panel's pages |
| `core/api.js` | `endpoints.*`, one function per server route |
| `core/preview.js` | draws the theme with the real renderer, throttled; it follows `rev` by itself |
| `core/i18n.js` | `t("English text", { name })`, `fieldLabel`, `widgetLabel`, `enumLabel`, `setLanguage` |
| `core/shortcuts.js` | `bindKey("mod+k", handler, { when, inInputs })`, `keyLabel()` |
| `core/commands.js` | the command registry the palette searches |
| `core/app.js` | the background app (`store.get().app`, polled), `appAction()`, `quitApp()`, the window's bridge |
| `ui/` | controls (`Toggle`, `Segmented`, `Chips`, `NumberField`, `TextField`, `Select`, `Slider`, `Field`, `Row`, `Section`, `IconButton`), `Icon`, `Popover`, `Menu`, dialogs (`openDialog`, `confirmDialog`, `promptDialog`), `toast()`, the palette |
| `shell/` | views and routes, the header and its parts (`ThemeTitle`, `SearchTrigger`, `PanelChip`, `UndoRedo`, `ApplyButton`), app-wide commands, plugin pages (`#/plugin/<id>`) |
| `views/<id>/` | one folder per view; `design` is the editor, the others are empty places to fill |
| `app.css` | colours and fonts (`:root` tokens) and the shared controls; a view brings its own CSS |

## A view

`views/live/index.js` registers itself; `main.js` imports it:

```js
registerView({
  id: "live", // the route: #/live (#/live/more gives the component rest="more")
  title: () => t("Live"),
  order: 40, // among the tabs: Start 10, Design 20, Look 30, Live 40
  icon: "pulse",
  component: LiveView,
  header: { lead, center, actions }, // optional components in the header
});
```

The shell draws the header with the tabs, the command field (unless `center` replaces
it) and the settings gear; `tab: false` keeps a view out of the tabs (Settings). Go
to a view with `navigate("live")`. Link a view's stylesheet in `index.html` and use the
tokens of `app.css`. Look reuses `ThemeTitle`, `PanelChip` and `ApplyButton` from the
shell and edits the theme through `core/doc.js`, as Design does.

## A command

```js
registerCommand({
  id: "live.pause",
  title: () => t("Pause panel"), // texts are functions: they follow the language
  group: () => t("Panel"),
  icon: "pause",
  keys: "mod+p", // shown in the palette; bind: true also binds it
  when: () => store.get().app.available,
  run: () => appAction("pause"),
});
```

`registerProvider(() => [...])` adds commands made from data (one per theme, sensor,
module) each time the palette searches.

## A field in the inspector

The inspector shows a widget type's primary fields first (`PRIMARY_FIELDS` in
`theme/model.py`, sent as `specs.primary`); the others are under *Fine-tuning*,
grouped by `GROUPS` in `views/design/inspector.js`. `SECTION_OF` there puts a primary
field into a section (Shows, Style, Colour, Size, Behaviour). Each field kind of the
specs has its control in `FieldControl` (`views/design/fields.js`); a new kind gets a
branch there.

## Rules

- Texts: English through `t("...")` with a plain string, German in `locale/de.json`;
  `tests/test_i18n.py` collects the texts from these files and fails on a missing or
  unused one.
- The server's CSP allows no inline scripts and no `style="..."` in HTML; styles set by
  Preact (objects or strings) are fine.
- Change the theme only through `edit()` (or `commit()` and `touch()`): that is one undo
  step, and the preview follows.
- Floating things go into a `Popover` (Escape and a click outside close it, the focus
  comes back). Buttons get a name (`aria-label`) when they show only an icon.
- `tests/test_app_browser.py` drives the app in Chromium; `window.libre` gives the
  tests the store, the document and the commands.
