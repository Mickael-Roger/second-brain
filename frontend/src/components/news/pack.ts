// Force-based circle packing.
//
// Inputs are the radii in original order; the output keeps that same
// order (`out[i]` corresponds to `radii[i]`). Bubbles are seeded on a
// loose spiral around the center, then nudged apart by a few iterations
// of pairwise overlap resolution combined with a gentle center pull.
//
// This isn't as tight as D3's front-chain pack but it has zero deps and
// looks clean for the small N (< ~50) we expect per period.

export interface PackedCircle {
  x: number;
  y: number;
  r: number;
}

export function packCircles(
  radii: number[],
  width: number,
  height: number,
): PackedCircle[] {
  const cx = width / 2;
  const cy = height / 2;
  const n = radii.length;
  if (n === 0) return [];

  // Largest first. Keep the original index so we can restore order.
  const order = radii
    .map((r, i) => ({ r, i }))
    .sort((a, b) => b.r - a.r);

  const out: PackedCircle[] = new Array(n);
  let theta = 0;
  let spiralR = 0;
  for (let k = 0; k < order.length; k++) {
    const { r, i } = order[k];
    if (k === 0) {
      out[i] = { x: cx, y: cy, r };
      continue;
    }
    spiralR += r * 0.85;
    theta += Math.PI * 0.6;
    out[i] = {
      x: cx + spiralR * Math.cos(theta),
      y: cy + spiralR * Math.sin(theta),
      r,
    };
  }

  // Resolve overlaps + pull toward the center.
  const pad = 4;
  for (let iter = 0; iter < 200; iter++) {
    let moved = 0;
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const a = out[i];
        const b = out[j];
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 0.0001;
        const need = a.r + b.r + pad;
        if (dist < need) {
          const push = (need - dist) / 2;
          const nx = dx / dist;
          const ny = dy / dist;
          a.x -= nx * push;
          a.y -= ny * push;
          b.x += nx * push;
          b.y += ny * push;
          moved += push;
        }
      }
      // Gentle pull toward center so the cluster stays compact.
      const a = out[i];
      a.x += (cx - a.x) * 0.015;
      a.y += (cy - a.y) * 0.015;
    }
    if (moved < 0.5) break;
  }

  // Clamp inside the viewport (a bubble whose radius exceeds the box
  // can still poke outside, but we at least keep the centers in).
  for (const c of out) {
    c.x = Math.max(c.r, Math.min(width - c.r, c.x));
    c.y = Math.max(c.r, Math.min(height - c.r, c.y));
  }
  return out;
}
