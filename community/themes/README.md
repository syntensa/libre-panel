# Community themes

Themes that people share with everyone who uses Libre Panel. The app shows them on
*Start → Themes* under **Community themes**, with their pictures, those made for the chosen
panel first, and installs one with a click. It reads `index.json` in this folder (on the
`main` branch) and downloads a theme's `.zip` from here, nowhere else; before anything is put
in place it checks the zip against the SHA-256 in `index.json` and that the theme in it is
made for the sizes the entry names, and then as it checks any shared theme
([Sharing a theme](../../docs/THEMES.md#sharing-a-theme)).

## Share yours

1. In Libre Panel, on *Start → Themes*, click the globe on your theme's card: **Share with
   everyone…**. Say what it is called, who made it and what it shows, and tick that every
   picture and font in it may be shared.
2. **Save the theme**: the `.zip` lands in your downloads. **Open the form**: GitHub's
   *Share a theme* form opens, filled in. Drag the `.zip` into *The theme* and send it (that
   needs a free GitHub account).
3. A check runs by itself ([community-theme.yml](../../.github/workflows/community-theme.yml)):
   it looks at the theme as the app looks at every shared theme, works out the panel size it is
   made for, draws its picture with made-up readings, and opens a pull request with the zip,
   the picture and the entry. It answers on your issue, with what to change where something is
   wrong; edit the issue and it runs again.
4. Someone has a look and merges it. Within the hour it shows in everyone's app.

### By hand

The same without the app's help, for themes made elsewhere or more at once:

1. Make the theme in Libre Panel. On *Start → Themes*, **Export…** on its card saves it as
   `<name>.zip`: its `theme.json` and the pictures and fonts it uses, nothing else.
2. Make its picture: `libre-panel preview <name> -o preview.png` (with the demo readings,
   so no reading of your computer is in it), or a screenshot of the panel showing it.
3. Fork this repository and add both in a folder of their own here:

   ```
   community/themes/aurora/aurora.zip
   community/themes/aurora/preview.png
   ```

4. Add an entry for it to `themes` in `index.json` (see below), with the zip's SHA-256:
   `sha256sum aurora.zip` on Linux, `shasum -a 256 aurora.zip` on macOS,
   `Get-FileHash aurora.zip` in PowerShell on Windows.
5. Open a pull request. Say which panels you tried it on.

A theme is reviewed before it is merged: that it loads, shows what its entry says, and that
you may share every font and picture in it (put the license in `theme.json`). A change to a
theme is a new zip with its new SHA-256 in the entry.

## index.json

```json
{
  "format": "libre-panel-community/1",
  "themes": [
    {
      "name": "Aurora",
      "author": "your name or handle",
      "description": "What it shows, in a sentence or two.",
      "sizes": ["480x320", "1920x480"],
      "preview": "aurora/preview.png",
      "zip": "aurora/aurora.zip",
      "sha256": "9f2c…(64 hexadecimal digits)"
    }
  ]
}
```

| Field | What it is |
|---|---|
| `name` | the theme's name as the app shows it, up to 80 characters |
| `author` | who made it, up to 80 characters |
| `description` | what it shows, up to 400 characters |
| `sizes` | the panel sizes it is made for, width × height in pixels: `"480x320"`, one to 16 of them |
| `preview` | its picture: a PNG of at most 2 MB, as a path in this folder |
| `zip` | the theme: the `.zip` that **Export…** saves, at most 20 MB, as a path in this folder. Its file name (`aurora` for `aurora/aurora.zip`) is the entry's name in the app and the name of the theme's folder where it is installed (with a number after it where that name is taken), so it is one of a–z, A–Z, 0–9, `.`, `_` and `-`, and no other entry has it |
| `sha256` | the SHA-256 of the zip |

The paths are relative to this folder: letters, digits, `.`, `_`, `-` and `/`, no `..`, no
address of another place. An entry that does not follow this is left out; the others still
show.

What a zip may hold is what a theme folder holds: `theme.json`, pictures (PNG, JPEG, GIF,
WebP) and fonts (TTF, OTF), at most 50 MB unpacked. Of those, only the files the theme names
are installed.
