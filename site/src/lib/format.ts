import type { Privacy, Sport } from './types';

/** True for "unlisted" activities (and the legacy "private" alias).
 *  Use this everywhere instead of comparing against 'private' directly. */
export function isUnlisted(privacy: Privacy | string | null | undefined): boolean {
  return privacy === 'unlisted' || privacy === 'private';
}

export function formatDistance(m: number | null, unit: 'metric' | 'imperial' = 'metric'): string {
  if (m == null) return '—';
  if (unit === 'imperial') {
    const miles = m / 1609.344;
    return miles >= 10 ? `${miles.toFixed(1)} mi` : `${miles.toFixed(2)} mi`;
  }
  const km = m / 1000;
  return km >= 10 ? `${km.toFixed(1)} km` : `${km.toFixed(2)} km`;
}

export function formatDuration(s: number | null): string {
  if (s == null) return '—';
  s = Math.floor(s);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}h ${m.toString().padStart(2, '0')}m`;
  return `${m}m ${sec.toString().padStart(2, '0')}s`;
}

export function formatSpeed(kmh: number | null): string {
  if (kmh == null) return '—';
  return `${kmh.toFixed(1)} km/h`;
}

export function formatElevation(m: number | null): string {
  if (m == null) return '—';
  return `${Math.round(m)} m`;
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-GB', {
    day: 'numeric', month: 'short', year: 'numeric',
  });
}

export function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-GB', {
    hour: '2-digit', minute: '2-digit',
  });
}

export function formatDateShort(iso: string): string {
  return new Date(iso).toLocaleDateString('en-GB', {
    day: 'numeric', month: 'short',
  });
}

const SPORT_ICONS: Record<Sport, string> = {
  cycling:  '🚴',
  running:  '🏃',
  hiking:   '🥾',
  walking:  '🚶',
  swimming: '🏊',
  skiing:   '⛷️',
  other:    '⚡',
};

const SPORT_COLORS: Record<Sport, string> = {
  cycling:  '#00c8ff',
  running:  '#ff6b35',
  hiking:   '#4ade80',
  walking:  '#a3e635',
  swimming: '#38bdf8',
  skiing:   '#e0f2fe',
  other:    '#a78bfa',
};

export function sportIcon(sport: Sport): string {
  return SPORT_ICONS[sport] ?? '⚡';
}

export function sportColor(sport: Sport): string {
  return SPORT_COLORS[sport] ?? '#a78bfa';
}

const SUB_SPORT_LABELS: Record<string, string> = {
  road:       'Road',
  mountain:   'MTB',
  gravel:     'Gravel',
  indoor:     'Indoor',
  trail:      'Trail',
  track:      'Track',
  nordic:     'Nordic',
  alpine:     'Alpine',
  open_water: 'Open Water',
  pool:       'Pool',
};

export function sportLabel(sport: Sport, subSport?: string | null): string {
  const base = sport.charAt(0).toUpperCase() + sport.slice(1);
  if (subSport && subSport !== 'generic') {
    const sub = SUB_SPORT_LABELS[subSport] ?? (subSport.charAt(0).toUpperCase() + subSport.slice(1));
    return `${sub} ${base}`;
  }
  return base;
}
