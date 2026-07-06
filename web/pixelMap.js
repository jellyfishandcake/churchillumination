// pixelMap.js — builds the array of canvas (x, y) points to sample, one per
// LED, for a given physical layout. This replaces the old assumption baked
// into sampleCanvas (evenly-spaced points along one horizontal row) with an
// explicit map, so a future non-strip installation only needs a new case
// here — sketch.js/app.js don't need to change.
//
// Only "strip" is implemented so far: the real install site (and therefore
// whether the physical layout is a strip/grid/ring) isn't confirmed yet —
// see CLAUDE.md's Phase I open questions.

function buildPixelMap(layout, count, width, height) {
  if (layout !== "strip") {
    throw new Error(`pixelMap: layout "${layout}" isn't implemented yet — only "strip" is supported so far`);
  }

  const y = Math.floor(height / 2);
  const points = [];
  for (let i = 0; i < count; i++) {
    const x = Math.floor((i / (count - 1)) * (width - 1));
    points.push({ x, y });
  }
  return points;
}

window.buildPixelMap = buildPixelMap;
