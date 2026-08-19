# Shadow backdrop presets

Drop real photos here, named exactly:

- `college_1.jpg`
- `college_2.jpg`
- `college_3.jpg`

These three names are hardcoded on both ends - `PRESET_BACKDROPS` in
`src/net/server.py` (the server-side allowlist; nothing outside this list
can ever be selected) and the three preset buttons in `web/backdrop.html`.
Rename/add more in both places together if you want a different set.

Recommended: similar treatment to what `backdrop.js` already does for a
regular upload - reasonably sized JPEGs (a mobile photo straight off a
phone is fine, no need to hand-resize), since these get displayed
full-screen behind the shadow the same way an uploaded backdrop does.

Until real files exist here, the corresponding preset button on
`backdrop.html` shows a broken-image icon and disables itself
automatically (see `backdrop.js`) - nothing crashes, it just quietly does
nothing until you add the file.
