# AI Agent FM — Spectrum Cone Brand Spec (v2.2a "Arc")

Selected mark: the straight-sided spectrum cone with a **radial hold-and-blend
gradient centered on the source point** (v2.2a). This file is the single source
of truth for anyone (human or agent) producing brand assets. Do not re-derive
geometry; copy the path data and gradient stops verbatim.

## Canvas and geometry

All coordinates are for a 1024 × 1024 viewBox. Scale proportionally.

- Outer ink silhouette (keyline 30 units, top corner radius 34, half-angle
  25.4°, virtual apex (512, 931.9), tip circle r=60 centered (512, 792)):

```
M206 216H818Q852 216 834 253.9L566.2 817.7A60 60 0 0 1 457.8 817.7L190 253.9Q172 216 206 216Z
```

- Inner clip (outer inset by the 30-unit keyline; inner tip circle r=30
  concentric at (512, 792)):

```
M233.5 246H790.5Q804.5 246 797.6 260.5L539.1 804.9A30 30 0 0 1 484.9 804.9L226.4 260.5Q219.5 246 233.5 246Z
```

- Fill: one rect `x=172 y=216 width=680 height=640` clipped by the inner path.

## The arc gradient (canonical)

Radial, centered on the tip circle center, so every color seam is a true
circle around the source point:

```xml
<radialGradient id="spectrum-arc" gradientUnits="userSpaceOnUse" cx="512" cy="792" r="546">
  <stop offset="0"     stop-color="#ef4938"/>
  <stop offset="0.159" stop-color="#ef4938"/>
  <stop offset="0.232" stop-color="#f39a2b"/>
  <stop offset="0.381" stop-color="#f39a2b"/>
  <stop offset="0.454" stop-color="#c83c88"/>
  <stop offset="0.586" stop-color="#c83c88"/>
  <stop offset="0.660" stop-color="#704fbc"/>
  <stop offset="0.766" stop-color="#704fbc"/>
  <stop offset="0.839" stop-color="#285db5"/>
  <stop offset="1"     stop-color="#285db5"/>
</radialGradient>
```

When the mark is translated or scaled inside a larger composition, wrap the
mark (silhouette + clipped fill) in a `<g transform=…>` so the
userSpaceOnUse gradient moves with it. Never restyle the stops.

## Palette

| Role | Hex |
|---|---|
| Cobalt | #285DB5 |
| Violet | #704FBC |
| Magenta | #C83C88 |
| Amber | #F39A2B |
| Vermilion | #EF4938 |
| Ink (keyline, dark ground, text) | #101228 |
| Ivory (light ground) | #F4F0E6 |

Reference SVGs in this directory: `mark-arc.svg` (selected), `mark-mono.svg`
(one-ink reduction), `icon-ivory-gradient.svg` / `icon-ink-gradient.svg`
(tile construction — swap their linear gradient for the radial arc gradient).

## Grounds

- **Ivory ground**: full mark — ink keyline + arc fill on #F4F0E6.
- **Ink ground**: drop the keyline; render only the clipped arc fill. The
  ground reads as the keyline. Nothing else is redrawn.
- **Monochrome / one ink**: solid ink silhouette with four ground-color slits
  (22 units tall) at y = 340, 454, 570, 690 clipped by the inner path
  (see `mark-mono.svg`). Gradients never appear in one-ink contexts.

## Size rules

- ≥ 48 px rendered size: full arc mark.
- < 48 px (favicon 16/32): use the monochrome slit reduction, or the ink-tile
  variant with bands merged to three; five blended bands do not survive 16 px.
- App icon tiles: 1024 × 1024, corner radius 232 (host OS masks anyway), mark
  at canonical position — do not enlarge past the 172–852 span.

## Typography

- Wordmark and display: **Space Grotesk Bold** (committed at
  `artwork/fonts/SpaceGrotesk-Bold.ttf`). Uppercase "AI AGENT FM" with
  letter-spacing 0.10 em for lockups and display (amended from 0.08 em —
  0.10 em is what the produced suite uses consistently); sentence case for
  long copy.
- In deterministic SVG assets, text must be converted to outlines (e.g. via
  fontTools) — never rely on installed fonts inside committed SVGs.

## Asset inventory (target layout)

```
artwork/brand/
  mark.svg                 transparent-ground canonical arc mark
  logo-horizontal.svg      mark + outlined wordmark, ivory + transparent ok
  logo-stacked.svg         mark above wordmark
  app-icon-ivory.svg/.png  1024 tile + 512/256/180/120 png exports
  app-icon-ink.svg/.png    same, ink ground
  favicon.svg              mono reduction; favicon-32.png, favicon-16.png
  podcast-cover.svg/.jpg   3000×3000 show cover base
  og-card.svg/.png         1200×630
  banner-x.svg/.png        1500×500
  promo-square.svg/.png    1080×1080
  badge.svg                README-scale badge (flat, no gradient below 20 px tall)
  badge-arc.svg/-512.png   keyline-free arc mark for episode-cover stamping
                           (see docs/design/episode-artwork-arc-integration.md)
```

Render checks: `qlmanage -t -s <px> -o . file.svg` then inspect the PNG.
PNG/JPG conversion and resizing: `sips`.

## Provenance

Exploration and rejected siblings: `docs/visual-identity-shaping.md` (§B2-O)
and this directory. The flat-band v2 and wave v2.2b were tested and not
selected. Amendment: gradients are now permitted **only** as the structural
hold-and-blend spectrum fill of the mark itself — never as page decoration,
text effects, or backgrounds.
