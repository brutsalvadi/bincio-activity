<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import maplibregl from 'maplibre-gl';
  import 'maplibre-gl/dist/maplibre-gl.css';
  import type { Timeseries } from '../lib/types';

  export let trackUrl: string;
  export let timeseries: Timeseries | null = null;
  export let bbox: [number, number, number, number] | null = null;
  /** ~20 [lat, lon] preview coords from the activity summary — used to position
   *  the map immediately on mount so there's no flash of world view before the
   *  detail JSON loads and bbox arrives. */
  export let initialCoords: [number, number][] | null = null;
  export let accentColor: string = '#00c8ff';
  export let hoveredIdx: number | null = null;

  let mapEl: HTMLDivElement;
  let map: any;
  const MarkerClass = maplibregl.Marker;
  let hoverMarker: any;
  let markersAdded = false;

  const TILE_STYLE = 'https://tiles.openfreemap.org/styles/positron';

  onMount(() => {
    // Derive initial center and zoom from preview_coords so the map starts at
    // the right location without waiting for the async detail JSON / bbox load.
    let initCenter: [number, number] = [0, 0];
    let initZoom = 1;
    if (initialCoords && initialCoords.length > 0) {
      const lats = initialCoords.map(c => c[0]);
      const lons = initialCoords.map(c => c[1]);
      initCenter = [
        (Math.min(...lons) + Math.max(...lons)) / 2,
        (Math.min(...lats) + Math.max(...lats)) / 2,
      ];
      initZoom = 10; // rough default; fitBounds will correct this when bbox arrives
    }

    map = new maplibregl.Map({
      container: mapEl,
      style: TILE_STYLE,
      center: initCenter,
      zoom: initZoom,
      attributionControl: false,
    });

    map.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-right');

    // Hover dot marker — must set lngLat before addTo in MapLibre v5
    const el = document.createElement('div');
    el.style.cssText = `
      width:12px;height:12px;border-radius:50%;
      background:white;border:2px solid ${accentColor};
      box-shadow:0 0 6px ${accentColor};display:none;pointer-events:none;
    `;
    hoverMarker = new maplibregl.Marker({ element: el, anchor: 'center' })
      .setLngLat([0, 0])
      .addTo(map);

    map.on('load', () => {
      map.addSource('track', {
        type: 'geojson',
        data: trackUrl,
        lineMetrics: true,
      });

      map.addLayer({
        id: 'track-shadow',
        type: 'line',
        source: 'track',
        paint: { 'line-color': 'rgba(0,0,0,0.3)', 'line-width': 5, 'line-blur': 2 },
      });

      map.addLayer({
        id: 'track-line',
        type: 'line',
        source: 'track',
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: {
          'line-width': 3,
          'line-gradient': [
            'interpolate', ['linear'], ['line-progress'],
            0,   accentColor,
            0.5, '#ff6b35',
            1,   accentColor,
          ],
        },
      });
    });
  });

  // Fit to bbox when detail JSON loads (bbox is null at map init).
  // Always resize first so MapLibre knows the real container dimensions,
  // and defer with rAF so the browser has finished laying out the container.
  $: if (map && bbox) {
    const fit = () => requestAnimationFrame(() => {
      map.resize();
      map.fitBounds(
        [[bbox![0], bbox![1]], [bbox![2], bbox![3]]],
        { padding: 40, animate: false },
      );
    });
    map.loaded() ? fit() : map.once('load', fit);
  }

  // Add start/end markers when timeseries arrives
  $: if (map && MarkerClass && timeseries && !markersAdded) {
    markersAdded = true;
    const add = () => {
      // Filter lat/lon together so indices stay aligned
      const pts = (timeseries!.lat ?? [])
        .map((lat, i) => ({ lat, lon: (timeseries!.lon ?? [])[i] }))
        .filter(p => p.lat != null && p.lon != null) as { lat: number; lon: number }[];
      if (!pts.length) return;
      new MarkerClass({ element: makeDot('#4ade80'), anchor: 'center' })
        .setLngLat([pts[0].lon, pts[0].lat]).addTo(map);
      new MarkerClass({ element: makeDot('#f87171'), anchor: 'center' })
        .setLngLat([pts[pts.length - 1].lon, pts[pts.length - 1].lat]).addTo(map);
    };
    map.loaded() ? add() : map.once('load', add);
  }

  // Hover dot linked to chart crosshair
  $: if (hoverMarker && timeseries && hoveredIdx != null) {
    const lat = timeseries.lat?.[hoveredIdx];
    const lon = timeseries.lon?.[hoveredIdx];
    if (lat != null && lon != null) {
      hoverMarker.getElement().style.display = 'block';
      hoverMarker.setLngLat([lon, lat]);
    }
  } else if (hoverMarker) {
    hoverMarker.getElement().style.display = 'none';
  }

  function makeDot(color: string): HTMLDivElement {
    const el = document.createElement('div');
    el.style.cssText = `
      width:10px;height:10px;border-radius:50%;
      background:${color};border:2px solid white;
      box-shadow:0 0 4px rgba(0,0,0,0.5);
    `;
    return el;
  }

  onDestroy(() => {
    resizeObserver?.disconnect();
    map?.remove();
  });

  let resizeObserver: ResizeObserver;
  $: if (mapEl && map) {
    resizeObserver?.disconnect();
    resizeObserver = new ResizeObserver(() => map?.resize());
    resizeObserver.observe(mapEl);
  }
</script>

<div bind:this={mapEl} class="w-full h-full"></div>
