<script lang="ts">
  import { onMount } from 'svelte';
  import type { BASIndex, ActivitySummary } from '../lib/types';
  import { formatDistance, formatDuration, isUnlisted, sportIcon } from '../lib/format';

  export let base: string = '/';

  type Period = 'week' | 'month' | 'year' | 'all';
  type SortKey = 'display_name' | 'count' | 'distance_m' | 'elevation_m' | 'duration_s' | 'sports' | 'streak';

  interface UserRaw {
    handle: string;
    display_name: string;
    activities: ActivitySummary[];
  }

  interface UserStat {
    handle: string;
    display_name: string;
    count: number;
    distance_m: number;
    elevation_m: number;
    duration_s: number;
    sports: string[];
    streak: number;
  }

  interface Totals {
    count: number;
    distance_m: number;
    elevation_m: number;
    duration_s: number;
    users: number;
  }

  let period: Period = 'month';
  let sortKey: SortKey = 'distance_m';
  let sortAsc = false;
  let users: UserRaw[] = [];
  let stats: UserStat[] = [];
  let totals: Totals = { count: 0, distance_m: 0, elevation_m: 0, duration_s: 0, users: 0 };
  let loading = true;
  let error: string | null = null;

  // ── Helpers ───────────────────────────────────────────────────────────────

  function periodStart(p: Period): Date {
    const now = new Date();
    if (p === 'all') return new Date(0);
    if (p === 'year') return new Date(now.getFullYear(), 0, 1);
    if (p === 'month') return new Date(now.getFullYear(), now.getMonth(), 1);
    const d = new Date(now);
    const day = d.getDay();
    d.setDate(d.getDate() - (day === 0 ? 6 : day - 1));
    d.setHours(0, 0, 0, 0);
    return d;
  }

  function maxStreak(activities: ActivitySummary[]): number {
    if (!activities.length) return 0;
    const days = [...new Set(activities.map(a => a.started_at.slice(0, 10)))].sort();
    let max = 1, cur = 1;
    for (let i = 1; i < days.length; i++) {
      const diff = (new Date(days[i]).getTime() - new Date(days[i - 1]).getTime()) / 86_400_000;
      cur = diff === 1 ? cur + 1 : 1;
      if (cur > max) max = cur;
    }
    return max;
  }

  function computeStats(rawUsers: UserRaw[], p: Period): { stats: UserStat[]; totals: Totals } {
    const start = periodStart(p);
    const result: UserStat[] = [];
    const tot: Totals = { count: 0, distance_m: 0, elevation_m: 0, duration_s: 0, users: 0 };

    for (const u of rawUsers) {
      const pub = u.activities.filter(a => !isUnlisted(a.privacy));
      const filtered = pub.filter(a => new Date(a.started_at) >= start);

      const stat: UserStat = {
        handle: u.handle,
        display_name: u.display_name,
        count: filtered.length,
        distance_m: filtered.reduce((s, a) => s + (a.distance_m ?? 0), 0),
        elevation_m: filtered.reduce((s, a) => s + (a.elevation_gain_m ?? 0), 0),
        duration_s: filtered.reduce((s, a) => s + (a.duration_s ?? 0), 0),
        sports: [...new Set(filtered.map(a => a.sport))],
        streak: maxStreak(pub),
      };

      tot.count += stat.count;
      tot.distance_m += stat.distance_m;
      tot.elevation_m += stat.elevation_m;
      tot.duration_s += stat.duration_s;
      tot.users++;

      result.push(stat);
    }

    return { stats: result, totals: tot };
  }

  // ── Data loading ──────────────────────────────────────────────────────────

  async function fetchShard(url: string): Promise<ActivitySummary[]> {
    const data: BASIndex = await fetch(url).then(r => { if (!r.ok) throw new Error(String(r.status)); return r.json(); });
    const own = data.activities ?? [];
    if (!data.shards?.length) return own;
    const shardBase = url.substring(0, url.lastIndexOf('/') + 1);
    const nested = await Promise.allSettled(
      data.shards.map(s => fetchShard(s.url.startsWith('http') ? s.url : `${shardBase}${s.url}`))
    );
    return [...own, ...nested.flatMap(r => r.status === 'fulfilled' ? r.value : [])];
  }

  async function loadData() {
    try {
      const rootUrl = `${base}data/index.json`;
      const root: BASIndex = await fetch(rootUrl).then(r => r.json());
      const userShards = (root.shards ?? []).filter(s => s.handle);
      if (userShards.length === 0) { error = 'No community members found.'; return; }

      const results = await Promise.allSettled(
        userShards.map(async shard => {
          const url = shard.url.startsWith('http') ? shard.url : `${base}data/${shard.url}`;
          const shardIndex: BASIndex = await fetch(url).then(r => r.json());
          const activities = await fetchShard(url);
          return { handle: shard.handle!, display_name: shardIndex.owner?.display_name ?? shard.handle!, activities } as UserRaw;
        })
      );

      users = results.flatMap(r => r.status === 'fulfilled' ? [r.value] : []);
      ({ stats, totals } = computeStats(users, period));
    } catch (e: any) {
      error = e.message;
    } finally {
      loading = false;
    }
  }

  $: if (users.length) ({ stats, totals } = computeStats(users, period));

  $: sorted = [...stats].sort((a, b) => {
    let av: number | string, bv: number | string;
    if (sortKey === 'display_name') { av = a.display_name.toLowerCase(); bv = b.display_name.toLowerCase(); }
    else if (sortKey === 'sports')  { av = a.sports.length; bv = b.sports.length; }
    else                            { av = a[sortKey] as number; bv = b[sortKey] as number; }
    if (av < bv) return sortAsc ? -1 : 1;
    if (av > bv) return sortAsc ? 1 : -1;
    return 0;
  });

  function setSort(key: SortKey) {
    if (sortKey === key) sortAsc = !sortAsc;
    else { sortKey = key; sortAsc = false; }
  }

  function chevron(key: SortKey) {
    if (sortKey !== key) return '';
    return sortAsc ? ' ↑' : ' ↓';
  }

  onMount(loadData);

  const PERIODS: { key: Period; label: string }[] = [
    { key: 'week',  label: 'This week'  },
    { key: 'month', label: 'This month' },
    { key: 'year',  label: 'This year'  },
    { key: 'all',   label: 'All time'   },
  ];
</script>

<div class="space-y-6">
  <div>
    <h1 class="text-2xl font-bold text-white mb-1">Community</h1>
    <p class="text-zinc-400 text-sm">What everyone's been up to — together.</p>
  </div>

  {#if loading}
    <p class="text-zinc-400 text-sm">Loading…</p>
  {:else if error}
    <p class="text-red-400 text-sm">{error}</p>
  {:else}

    <!-- Period selector -->
    <div class="flex gap-2 flex-wrap">
      {#each PERIODS as p}
        <button
          on:click={() => period = p.key}
          class="px-3 py-1.5 rounded-full text-sm font-medium border transition-colors"
          class:bg-blue-500={period === p.key}
          class:border-blue-500={period === p.key}
          class:text-white={period === p.key}
          class:border-zinc-700={period !== p.key}
          class:text-zinc-400={period !== p.key}
          class:hover:text-white={period !== p.key}
        >{p.label}</button>
      {/each}
    </div>

    <!-- Community totals -->
    {#if totals.users > 0}
      <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {#each [
          { label: 'Activities', value: totals.count.toLocaleString() },
          { label: 'Distance',   value: formatDistance(totals.distance_m) },
          { label: 'Elevation',  value: `${Math.round(totals.elevation_m / 1000).toLocaleString()} km↑` },
          { label: 'Time',       value: formatDuration(totals.duration_s) },
        ] as item}
          <div class="bg-zinc-900 border border-zinc-800 rounded-xl p-4 text-center">
            <div class="text-xl font-bold text-white">{item.value}</div>
            <div class="text-xs text-zinc-500 mt-0.5">{item.label}</div>
          </div>
        {/each}
      </div>
    {/if}

    <!-- Table -->
    {#if totals.users === 0}
      <p class="text-zinc-500 text-sm">No public activities in this period yet.</p>
    {:else}
      <div class="overflow-x-auto rounded-xl border border-zinc-800">
        <table class="w-full text-sm">
          <thead>
            <tr class="border-b border-zinc-800 text-zinc-400 text-xs uppercase tracking-wide">
              <th class="text-left px-4 py-3 font-medium w-6">#</th>
              <th class="text-left px-4 py-3 font-medium">
                <button on:click={() => setSort('display_name')} class="hover:text-white transition-colors">
                  Athlete{chevron('display_name')}
                </button>
              </th>
              <th class="text-right px-4 py-3 font-medium">
                <button on:click={() => setSort('count')} class="hover:text-white transition-colors">
                  Activities{chevron('count')}
                </button>
              </th>
              <th class="text-right px-4 py-3 font-medium">
                <button on:click={() => setSort('distance_m')} class="hover:text-white transition-colors">
                  Distance{chevron('distance_m')}
                </button>
              </th>
              <th class="text-right px-4 py-3 font-medium">
                <button on:click={() => setSort('elevation_m')} class="hover:text-white transition-colors">
                  Elevation{chevron('elevation_m')}
                </button>
              </th>
              <th class="text-right px-4 py-3 font-medium">
                <button on:click={() => setSort('duration_s')} class="hover:text-white transition-colors">
                  Time{chevron('duration_s')}
                </button>
              </th>
              <th class="text-right px-4 py-3 font-medium hidden sm:table-cell">
                <button on:click={() => setSort('sports')} class="hover:text-white transition-colors">
                  Sports{chevron('sports')}
                </button>
              </th>
              <th class="text-right px-4 py-3 font-medium hidden md:table-cell">
                <button on:click={() => setSort('streak')} class="hover:text-white transition-colors">
                  Streak{chevron('streak')}
                </button>
              </th>
            </tr>
          </thead>
          <tbody>
            {#each sorted as u, i}
              <tr class="border-b border-zinc-800/50 last:border-0 hover:bg-zinc-800/30 transition-colors">
                <td class="px-4 py-3 text-zinc-600 tabular-nums">{i + 1}</td>
                <td class="px-4 py-3">
                  <a href="{base}u/{u.handle}/" class="text-white font-medium hover:text-[--accent] transition-colors">
                    {u.display_name}
                  </a>
                  <span class="text-zinc-600 text-xs ml-1">@{u.handle}</span>
                </td>
                <td class="px-4 py-3 text-right tabular-nums text-zinc-300">{u.count}</td>
                <td class="px-4 py-3 text-right tabular-nums text-zinc-300">{u.distance_m > 0 ? formatDistance(u.distance_m) : '—'}</td>
                <td class="px-4 py-3 text-right tabular-nums text-zinc-300">{u.elevation_m > 0 ? `${Math.round(u.elevation_m).toLocaleString()} m` : '—'}</td>
                <td class="px-4 py-3 text-right tabular-nums text-zinc-300">{u.duration_s > 0 ? formatDuration(u.duration_s) : '—'}</td>
                <td class="px-4 py-3 text-right hidden sm:table-cell">
                  {#each u.sports as s}{sportIcon(s)}{/each}
                </td>
                <td class="px-4 py-3 text-right tabular-nums text-zinc-300 hidden md:table-cell">
                  {u.streak > 0 ? `${u.streak}d` : '—'}
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}

  {/if}
</div>
