<script lang="ts">
  import { onMount } from 'svelte';
  import { getLocalActivity } from '../lib/localstore';
  import type { ActivitySummary } from '../lib/types';
  import ActivityDetail from './ActivityDetail.svelte';

  export let base: string = '/';

  let activity: ActivitySummary | null = null;
  let error = '';

  onMount(async () => {
    const id = new URLSearchParams(window.location.search).get('id');
    if (!id) { error = 'No activity ID in URL.'; return; }
    const found = await getLocalActivity(id);
    if (!found) { error = `Activity "${id}" not found on this device.`; return; }
    activity = found;
  });
</script>

{#if error}
  <p class="text-red-400 text-sm py-12 text-center">{error}</p>
{:else if activity}
  <ActivityDetail {activity} {base} athlete={null} />
{:else}
  <div class="h-32 rounded-xl bg-zinc-900 border border-zinc-800 animate-pulse mt-4"></div>
{/if}
