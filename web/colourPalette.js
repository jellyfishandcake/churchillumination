// colourPalette.js — JS port of src/output/effects/colour_palette.py's
// primitives, so a sketch can pick colours from the exact same named
// palettes (winter/autumn/festive, plus anything contributed via
// build_palette.py/colormind_palette.py) that the Python effects use.
//
// Operates on palette data broadcast from Python (window.paletteData,
// set by app.js from the server's palette_data field) - there's one
// source of truth for what colours exist, not a second hand-typed copy
// living here.
//
// Colours interpolate in plain RGB, in the order given - same rule as
// the Python version: order is a deliberate path through colour space,
// and adjacent anchors that are near-complementary will always have a
// muddier middle (that's what a straight line through the RGB cube does,
// not a bug).

function hexToRgb(hex) {
  const h = hex.replace("#", "");
  return [
    parseInt(h.substring(0, 2), 16),
    parseInt(h.substring(2, 4), 16),
    parseInt(h.substring(4, 6), 16),
  ];
}

function colorAt(palette, t) {
  const anchors = palette.map(hexToRgb);
  if (anchors.length === 1) return anchors[0];

  t = Math.min(Math.max(t, 0), 1);
  const scaled = t * (anchors.length - 1);
  const i = Math.min(Math.floor(scaled), anchors.length - 2);
  const frac = scaled - i;
  const a = anchors[i];
  const b = anchors[i + 1];
  return [0, 1, 2].map((c) => a[c] + (b[c] - a[c]) * frac);
}

function paletteLut(palette, size = 256) {
  const lut = [];
  for (let i = 0; i < size; i++) {
    lut.push(colorAt(palette, i / (size - 1)));
  }
  return lut;
}

window.hexToRgb = hexToRgb;
window.colorAt = colorAt;
window.paletteLut = paletteLut;
