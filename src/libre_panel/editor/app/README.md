# The app

Libre Panel's app: Start, Design, Look, Live and Settings in one page, shown in its own
window (`libre_panel/window.py`) or a browser. No build step: plain ES modules with
Preact and htm, vendored in `vendor/` (versions and SHA-256 in `vendor/SOURCES.txt`).
The editor's server (`editor/server.py`) serves `index.html` at `/`, this folder at
`/app/` and the bundled fonts at `/app/fonts/`. What the console's pages (`pages/web`)
and plugins' editor pages share is in `editor/static/`, served at `/static/`: `kit.js`,
`stats.js`, `token.js`, `editor.css` and the icon (the app itself uses only the icon and
`token.js`).

## What is where

| Path | What |
|---|---|
| `main.js` | starts the app: language, specs, themes, pages, the theme on the panel, the views |
| `lib/preact.js` | `html` (htm on Preact) and the hooks |
| `core/state.js` | the one store: `store.get()`, `store.set(patch)`, `useStore(selector)`, `setPref()` |
| `core/doc.js` | the theme being edited: `theme()`, `widgets()`, `widgetById()`, `selection()`, `setSelection()`, `edit(key, fn)`, `commit()` + `touch()`, `undo()`, `redo()`, `openDoc()` |
| `core/actions.js` | open, save, save as, import, export, `applyToPanel()`, `adaptPanel()`, `uploadAsset()`, the panel's pages, `themeName(id)` |
| `core/api.js` | `endpoints.*`, one function per server route; every request carries the run's access token (`/static/token.js` takes it from the address, `#token=…`, and keeps it for the tab), pictures have it in their address (`previewUrl`, `assetUrl`), and a request that does not go through `api()` sends `authHeaders()`; a refusal's reason reaches the person in plain words (`readable()`: no exception names or error numbers, a file another program holds or a full disk said in the app's language) |
| `core/preview.js` | draws the theme with the real renderer, throttled; it follows `rev` by itself |
| `core/console.js` | the console API (docs/ARCHITECTURE.md): `control.panel()`, `.sources()`, `.services()`, `.probe(kind)`, `.enableService()`, …, and the polling hooks `usePolled(load, ms)` and `useLivePicture()` |
| `core/stats.js` | the panel's numbers (`RunStatus.stats`) as tiles: `statTiles(stats)`, and `runtime(seconds)` |
| `core/i18n.js` | `t("English text", { name })`, `fieldLabel`, `widgetLabel`, `enumLabel`, `setLanguage` |
| `core/shortcuts.js` | `bindKey("mod+k", handler, { when, inInputs })`, `keyLabel()` |
| `core/commands.js` | the command registry the palette searches |
| `core/app.js` | the background app (`store.get().app`, polled), `appAction()`, `quitApp()`, the window's bridge |
| `core/hardware.js` | the hardware's names (`/api/hardware`): `loadHardware()`, `setHardwareName(part, text)`; `store.get().hardware` is `false` where the server has none |
| `core/settings.js` | what Settings reads and writes (`/api/settings`, `/api/panel`, `/api/system`, `/api/unlock`, `/api/about`, the console's sources and services): `loadSettings()`, `saveSettings(changes)`, `useConfig()`, `useCatalog()`, `loadPanelInfo()`, `loadClipQuestion()`, `answerClipQuestion(name)`, `saveSystem(change)`, `searchPlaces(query)`, `enableService()`, `restartPanel()`, and the setup of the system, Windows' sensor service and Linux' rights (`loadUnlock()`, `runUnlock("unlock" or "remove", options)`: Start's card and Settings → Sensors) |
| `core/setup.js` | the one-click setup of Linux (`libre-panel system-setup --linux`) in the app: it is what `/api/unlock` tells where the system is Linux (`linuxSetup(info)`, `useSetup()`; the same route, store key `unlockInfo` and polling as Windows' *Unlock all sensors*), `runSetup({rapl, remove})` asks for the rights, `dismissSetup()` is Start's *Not now*. The card in Settings → Sensors is `views/settings/setup.js`, the one on Start `views/home/setup.js` |
| `core/video.js` | what the video mode is doing in words, `videoState(video, mode, fps, running)` → `{tone, text}`, for Settings and Live; the server's `video_mode()` has worked out `video` (`/api/panel`) |
| `ui/` | controls (`Toggle`, `Segmented`, `Chips`, `NumberField`, `TextField`, `Select`, `Slider`, `Field`, `Row`, `Section`, `IconButton`), `Icon`, `Popover`, `Menu`, dialogs (`openDialog`, `confirmDialog`, `promptDialog`), `toast()`, the palette |
| `shell/` | views and routes, the header and its parts (`ThemeTitle`, `SearchTrigger`, `PanelChip`, `UndoRedo`, `ApplyButton`), app-wide commands (`commands.js`), a command for every feature that lives in a card of Start or Settings and more words to find each by, in English and German (`features.js`), the help (`help.js`: ? or F1; a view lists its keys with `listShortcuts`), plugin pages (`#/plugin/<id>`) |
| `views/<id>/` | one folder per view: `home` is Start (`#/home`, also `#/start`: the launcher, or on a first start the question "How should it look?"; `#/home/themes` the gallery; where the video mode lacks only the name of the panel's standby clip, the card in `clip.js` asks for it once, and `#/home/clip`, which the tray's line opens, puts the focus on it), `design` the editor, `look` the theme's look and finish, `live` the running panel, `settings` the settings (the frame with the sections on the left, and one file for each section: `panel.js`, `sensors.js`, `services.js`, `weather.js`, `system.js`, `about.js`; `parts.js` has the cards, rows and notes they are made of) |
| `app.css` | colours and fonts (`:root` tokens) and the shared controls; a view brings its own CSS |

## A view

`views/live/index.js` registers itself; `main.js` imports it:

```js
registerView({
  id: "live", // the route: #/live (#/live/more gives the component rest="more")
  title: () => t("Live"),
  order: 40, // among the tabs: Start 10, Design 20, Look 30, Live 40
  edits: false, // true: shows the theme being edited, so Ctrl+Z and Ctrl+Y act here (Design, Look)
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

## Polling

A view that shows the running panel asks the console API while it is shown, never faster
than it needs: `usePolled(control.panel, 1000)` asks again a second after the last answer
(and stops while the page is hidden or the view is left), `useLivePicture({ every })` asks
for the picture on the panel when the last one has come. `store.get().app` is the
background app's state every two seconds, for what needs no more.

## Themes in a list

`store.get().themes` is the theme list (`/api/themes`, again after `refreshThemes()`):
`{id, name, builtin, source, model, size, collection}` for every built-in, plugin and own
theme. A gallery lists these, not a fixed set: `Gallery` (views/home/themes.js) shows the
themes that name no `collection` (Libre Panel's own, and yours) first and keeps each
collection closed at the end, whatever its name; `endpoints.previewUrl(id, width)` is a theme's
picture (the server keeps it until the theme changes), `openTheme(id)` opens one for
Design and `showOnPanel(id)` puts one on the panel. `store.get().pages.chosen` is false
until config.toml names a theme: Start then asks which look the panel should have, and
marks the panel's own default (`pages.default`, or `default_theme` of the model the themes
are shown for: Lumen on a bar, Libre Default elsewhere) as the first card. The normal
page's theme (`pages.pages[0]`) is that default as long as config.toml names none.

## A group in Settings

Settings has sections (Panel, Sensors, Services, Weather and place, Start and system,
About); a part of the app adds its group to one without touching the others:

```js
registerSettingsGroup("sensors", {
  id: "hardware",
  order: 40,
  component: HardwareNames, // a .settings-card with .settings-row rows
  load: () => loadHardware(true), // when the section opens
  shows: () => Boolean(store.get().hardware), // nothing to show: the section says what is to come
});
```

A group that changes a setting writes it to config.toml through `saveSettings`, by the
setting's name there, and reads it from `useConfig()`; the server checks each setting
(`editor/settings.py`) and keeps the file's comments:

```js
const config = useConfig(); // undefined until config.toml is read
saveSettings({ "device.brightness": 70 }); // all that are given go in one write
saveSettings((now) => ({ "sensors.providers": [...now.sensors.providers, "demo"] })); // from what is there
```

A change that depends on what is there (a list gets one more item) is given as a function
of the config as the last write left it, so two quick ones do not undo each other. The
sensor sources and the weather are made when the panel starts: a group that writes them
shows `<RestartNote prefix="sensors." />`, which says so and has the button.

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
