/** TypeScript types mirroring BAS v1.0 schema. */

export type Sport = "cycling" | "running" | "hiking" | "walking" | "swimming" | "skiing" | "other";
export type SubSport = "road" | "mountain" | "gravel" | "indoor" | "trail" | "track" | "nordic" | "alpine" | "open_water" | "pool" | null;
/** "unlisted" = not shown in the public feed; GPS track still published (security by obscurity).
 *  "private" is the legacy alias for "unlisted" — accepted when reading old data. */
export type Privacy = "public" | "blur_start" | "no_gps" | "unlisted" | "private";

/** [duration_s, avg_watts] pairs, sorted by duration ascending. */
export type MmpCurve = [number, number][];

export interface AthletePowerCurve {
  all_time:  MmpCurve | null;
  last_365d: MmpCurve | null;
  last_90d:  MmpCurve | null;
}

export interface EffortRecord {
  time_s: number;
  activity_id: string;
  started_at: string;
  title: string;
}

export interface ValueRecord {
  value: number;
  activity_id: string;
  started_at: string;
  title: string;
}

export interface BestClimb {
  climb_m: number;
  activity_id: string;
  started_at: string;
  title: string;
}

export interface AthleteJson {
  bas_version: string;
  generated_at: string;
  power_curve: AthletePowerCurve;
  records?: Record<string, Record<string, EffortRecord | ValueRecord>>;
  best_climbs?: BestClimb[];
  max_hr?: number;
  ftp_w?: number;
  hr_zones?: [number, number][];
  power_zones?: [number, number][];
  seasons?: { name: string; start: string; end: string }[];
}

export interface ActivitySummary {
  id: string;
  title: string;
  sport: Sport;
  sub_sport: SubSport;
  started_at: string;        // ISO 8601
  distance_m: number | null;
  duration_s: number | null;
  moving_time_s: number | null;
  elevation_gain_m: number | null;
  avg_speed_kmh: number | null;
  max_speed_kmh: number | null;
  avg_hr_bpm: number | null;
  max_hr_bpm: number | null;
  avg_cadence_rpm: number | null;
  avg_power_w: number | null;
  mmp: MmpCurve | null;
  source: string | null;
  privacy: Privacy;
  detail_url: string | null;
  track_url: string | null;
  /** ~20 [lat, lon] pairs for card thumbnail — no separate fetch needed. */
  preview_coords: [number, number][] | null;
  /** Set on multi-user instances — the handle of the activity owner. */
  handle?: string;
}

export interface AthleteZones {
  max_hr?: number;
  ftp_w?: number;
  hr_zones?: [number, number][];
  power_zones?: [number, number][];
}

export interface BASIndex {
  bas_version: string;
  owner?: { handle: string; display_name: string; avatar_url?: string | null; athlete?: AthleteZones };
  instance?: { name?: string; url?: string };
  generated_at: string;
  // Shards can be user shards (multi-user manifest) or year shards (pagination).
  // handle present → user shard; year present → pagination shard.
  shards: Array<{ url: string; handle?: string; year?: number; count?: number }>;
  activities: ActivitySummary[];
}

export interface Timeseries {
  t: number[];
  lat: number[] | null;
  lon: number[] | null;
  elevation_m: (number | null)[];
  speed_kmh: (number | null)[];
  hr_bpm: (number | null)[];
  cadence_rpm: (number | null)[];
  power_w: (number | null)[];
  temperature_c: (number | null)[];
}

export interface ActivityDetail extends Omit<ActivitySummary, 'detail_url' | 'track_url' | 'preview_coords'> {
  description: string | null;
  elevation_loss_m: number | null;
  max_power_w: number | null;
  gear: string | null;
  device: string | null;
  bbox: [number, number, number, number] | null;
  start_latlng: [number, number] | null;
  end_latlng: [number, number] | null;
  laps: Lap[];
  /** Embedded timeseries — present for IDB-stored (locally converted) activities. */
  timeseries?: Timeseries | null;
  /** URL to fetch the timeseries — present for server-extracted activities. */
  timeseries_url?: string | null;
  mmp: MmpCurve | null;
  strava_id: string | null;
  duplicate_of: string | null;
  custom: Record<string, unknown>;
}

export interface Lap {
  index: number;
  started_at: string;
  duration_s: number | null;
  distance_m: number | null;
  elevation_gain_m: number | null;
  avg_speed_kmh: number | null;
  avg_hr_bpm: number | null;
  avg_power_w: number | null;
}
