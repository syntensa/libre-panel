# Bundled fonts

| Files | Font | License |
|---|---|---|
| `Barlow-*.ttf`, `BarlowCondensed-*.ttf` | [Barlow](https://github.com/jpt/barlow) by Jeremy Tribby | SIL Open Font License 1.1, see `OFL-Barlow.txt` |
| `JetBrainsMono-*.ttf` | [JetBrains Mono](https://github.com/JetBrains/JetBrainsMono) | SIL Open Font License 1.1, see `OFL-JetBrainsMono.txt` |
| `LiberationSans-*.ttf` | [Liberation Sans](https://github.com/liberationfonts/liberation-fonts) by Red Hat (metrics compatible with Arial) | SIL Open Font License 1.1, see `OFL-LiberationSans.txt` |

Themes refer to them as `builtin:<file name without .ttf>`, e.g. `builtin:Barlow-SemiBold`.

These fonts carry Latin (Liberation Sans and JetBrains Mono also Greek and Cyrillic). A character they
lack is drawn with a font of the system (`libre_panel.fonts.fallback`, docs/THEMES.md); no other font
file is shipped.
