// Parse a print-style slide-range string ("3-6, 12, 15") into a validated,
// deduped, sorted list of 1-based positions. Shared by RegenerateRangeModal for
// live UX feedback; the backend independently re-validates every position.
//
// Returns { positions: number[], error: string | null }. `positions` is empty
// whenever `error` is set (or the input is blank).
export function parseSlideRange(input, slideCount) {
  const raw = (input || '').trim();
  if (!raw) return { positions: [], error: null };

  const positions = new Set();
  for (const rawPart of raw.split(',')) {
    const part = rawPart.trim();
    if (!part) continue;

    const range = part.match(/^(\d+)\s*-\s*(\d+)$/);
    const single = part.match(/^(\d+)$/);

    if (range) {
      const lo = Number(range[1]);
      const hi = Number(range[2]);
      if (lo > hi) {
        return { positions: [], error: `Invalid range "${part}" (start after end)` };
      }
      for (let p = lo; p <= hi; p += 1) {
        if (p < 1 || p > slideCount) {
          return { positions: [], error: `Slide ${p} is outside 1–${slideCount}` };
        }
        positions.add(p);
      }
    } else if (single) {
      const p = Number(single[1]);
      if (p < 1 || p > slideCount) {
        return { positions: [], error: `Slide ${p} is outside 1–${slideCount}` };
      }
      positions.add(p);
    } else {
      return { positions: [], error: `Couldn't read "${part}" — use e.g. "3-6, 12"` };
    }
  }

  if (positions.size === 0) {
    return { positions: [], error: 'Enter at least one slide' };
  }
  return { positions: [...positions].sort((a, b) => a - b), error: null };
}
