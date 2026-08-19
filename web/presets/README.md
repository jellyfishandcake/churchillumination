# Shadow backdrop presets

Drop real photos here, named:

- `college_1.<ext>`
- `college_2.<ext>`
- `college_3.<ext>`

`<ext>` can be `.jpg`, `.jpeg`, or `.png`, any casing (`.JPG` etc. is
fine too) - the server matches the name and extension case-insensitively
(`_find_preset_file` in `src/net/server.py`), so you don't need to rename
whatever your phone/camera actually calls the file.

These three ids are hardcoded on both ends - `PRESET_IDS` in
`src/net/server.py` (the server-side allowlist; nothing outside this list
can ever be selected) and the three preset buttons in `web/backdrop.html`.
Rename/add more in both places together if you want a different set.

Recommended: similar treatment to what `backdrop.js` already does for a
regular upload - reasonably sized photos (a mobile photo straight off a
phone is fine, no need to hand-resize), since these get displayed
full-screen behind the shadow the same way an uploaded backdrop does.

Until a matching file exists here, the corresponding preset button on
`backdrop.html` shows a broken-image icon and disables itself
automatically (see `backdrop.js`) - nothing crashes, it just quietly does
nothing until you add the file.
