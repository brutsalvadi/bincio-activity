export type PaletteKey = 'auto' | 'default' | 'giro' | 'tour' | 'vuelta';

export const PALETTES = {
  default: { accent: '#60a5fa', dim: 'rgba(96,165,250,0.15)',  label: 'Default' },
  giro:    { accent: '#f472b6', dim: 'rgba(244,114,182,0.15)', label: "Giro d'Italia" },
  tour:    { accent: '#facc15', dim: 'rgba(250,204,21,0.15)',  label: 'Tour de France' },
  vuelta:  { accent: '#ef4444', dim: 'rgba(239,68,68,0.15)',   label: 'Vuelta a España' },
} as const satisfies Record<string, { accent: string; dim: string; label: string }>;

export type Theme = (typeof PALETTES)[keyof typeof PALETTES];

// Race windows [month 0-indexed, day inclusive] — update each year
const RACES: Array<{ key: Exclude<PaletteKey, 'auto' | 'default'>; start: [number, number]; end: [number, number] }> = [
  { key: 'giro',   start: [4, 8],  end: [5, 1]  },  // May 8 – Jun 1
  { key: 'tour',   start: [5, 27], end: [6, 19] },  // Jun 27 – Jul 19
  { key: 'vuelta', start: [7, 15], end: [8, 6]  },  // Aug 15 – Sep 6
];

export function autoKey(): Exclude<PaletteKey, 'auto'> {
  const now = new Date();
  const y   = now.getFullYear();
  for (const r of RACES) {
    const start = new Date(y, r.start[0], r.start[1]);
    const end   = new Date(y, r.end[0],   r.end[1] + 1);
    if (now >= start && now < end) return r.key;
  }
  return 'default';
}

