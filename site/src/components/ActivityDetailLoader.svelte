<script lang="ts">
  import { onMount } from 'svelte';
  import { loadIndexPaged } from '../lib/dataloader';
  import ActivityDetail from './ActivityDetail.svelte';
  import { isUnlisted } from '../lib/format';
  import type { ActivitySummary, BASIndex } from '../lib/types';

  export let base: string = '/';

  let activity: ActivitySummary | null = null;
  let notFound = false;
  let loading = true;

  /**
   * Build an ActivitySummary stub from a detail JSON object.
   * Used when we fetch the detail file directly without going through the index.
   */
  function summaryFromDetail(d: any, detailUrl: string, handle?: string): ActivitySummary {
    return {
      id: d.id,
      title: d.title ?? d.id,
      sport: d.sport ?? 'other',
      sub_sport: d.sub_sport ?? null,
      started_at: d.started_at ?? '',
      distance_m: d.distance_m ?? null,
      duration_s: d.duration_s ?? null,
      moving_time_s: d.moving_time_s ?? null,
      elevation_gain_m: d.elevation_gain_m ?? null,
      avg_speed_kmh: d.avg_speed_kmh ?? null,
      max_speed_kmh: d.max_speed_kmh ?? null,
      avg_hr_bpm: d.avg_hr_bpm ?? null,
      max_hr_bpm: d.max_hr_bpm ?? null,
      avg_cadence_rpm: d.avg_cadence_rpm ?? null,
      avg_power_w: d.avg_power_w ?? null,
      mmp: d.mmp ?? null,
      source: d.source ?? null,
      privacy: d.privacy ?? 'public',
      detail_url: detailUrl,
      track_url: d.bbox && d.privacy !== 'no_gps'
        ? detailUrl.replace(/\.json$/, '.geojson')
        : null,
      preview_coords: null,
      ...(handle ? { handle } : {}),
    };
  }

  /**
   * Fallback: fetch the activity detail file directly without loading the index.
   * Tries single-user path first, then each multi-user handle shard.
   */
  async function fetchActivityDirect(id: string): Promise<ActivitySummary | null> {
    // Single-user: public/data → _merged/, so activities/ resolves directly
    try {
      const url = `${base}data/activities/${id}.json`;
      const r = await fetch(url);
      if (r.ok) {
        const d = await r.json();
        if (d.id === id) return summaryFromDetail(d, `activities/${id}.json`);
      }
    } catch { /* fall through */ }

    // Multi-user: try each handle shard
    try {
      const r = await fetch(`${base}data/index.json`);
      if (!r.ok) return null;
      const root: BASIndex = await r.json();
      for (const shard of (root.shards ?? [])) {
        if (!shard.handle) continue;
        const url = `${base}data/${shard.handle}/_merged/activities/${id}.json`;
        try {
          const dr = await fetch(url);
          if (!dr.ok) continue;
          const d = await dr.json();
          if (d.id === id) {
            return summaryFromDetail(
              d,
              `${shard.handle}/_merged/activities/${id}.json`,
              shard.handle,
            );
          }
        } catch { /* try next */ }
      }
    } catch { /* ignore */ }
    return null;
  }

  onMount(async () => {
    // Extract activity ID from the URL path: /activity/{id}/
    const match = window.location.pathname.match(/\/activity\/([^/]+)/);
    const id = match?.[1];
    if (!id) { notFound = true; loading = false; return; }

    try {
      // Load only the most-recent year shard — avoids downloading all years just
      // to look up one activity. Falls back to a direct file fetch if not found.
      const { index } = await loadIndexPaged(base);
      activity = index.activities.find(a => a.id === id) ?? null;

      if (!activity) {
        // Not in first year shard (old activity) or shard fetch failed —
        // fetch the detail file directly to avoid loading all remaining shards.
        activity = await fetchActivityDirect(id);
      }

      if (!activity) notFound = true;
    } catch {
      notFound = true;
    }
    loading = false;
  });
</script>

{#if loading}
  <p class="text-zinc-500 text-sm mt-8 text-center">Loading activity…</p>
{:else if notFound}
  <div class="text-center mt-16">
    <p class="text-zinc-400 text-sm mb-2">Activity not found.</p>
    <p class="text-zinc-600 text-xs">It may still be processing — try refreshing in a moment.</p>
    <a href={base} class="mt-4 inline-block text-blue-400 hover:text-blue-300 text-sm">← Back to feed</a>
  </div>
{:else if activity}
  <ActivityDetail {activity} {base} />
{/if}
