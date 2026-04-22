# BincioActivity Schema (BAS) — v1.0

The BincioActivity Schema defines how activity data is stored and shared as
plain JSON files. It is the **federation protocol**: if you publish a
BAS-compliant data store, any BincioActivity instance can read it.

Any tool — in any language — can produce BAS-compliant JSON without using the
`bincio` Python package. The schema is the contract; the package is one
implementation.

---

## Files

A BAS data store is a directory (or URL prefix) with this structure:

```
{store_root}/
  index.json                      ← user manifest and activity feed
  index_{year}.json               ← optional yearly shards (large datasets)
  activities/
    {id}.json                     ← full activity detail
    {id}.geojson                  ← simplified GPS track (optional)
```

All files are UTF-8 JSON. All timestamps are ISO 8601 with timezone offset.
All distances are in metres. All speeds are in km/h. All durations are in
seconds. `null` means "not recorded / not available".

---

## `index.json`

The entry point for a data store.

```json
{
  "bas_version": "1.0",
  "owner": {
    "handle": "brutsalvadi",
    "display_name": "Bru",
    "avatar_url": null
  },
  "generated_at": "2026-03-28T10:00:00Z",
  "shards": [
    { "year": 2024, "url": "index_2024.json", "count": 312 }
  ],
  "activities": [ ... ]
}
```

### Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `bas_version` | string | yes | Schema version. Currently `"1.0"`. |
| `owner.handle` | string | yes | URL-safe identifier, e.g. `"brutsalvadi"`. |
| `owner.display_name` | string | yes | Human-readable name. |
| `owner.avatar_url` | string\|null | no | Absolute URL to an avatar image. |
| `generated_at` | string | yes | ISO 8601 timestamp of when this file was generated. |
| `shards` | array | no | Pointers to yearly shard files. See below. |
| `activities` | array | yes | Array of **Activity Summary** objects. May be empty. |

`index.json` should contain all activities when the total count is under ~5,000.
Above that, use yearly shards and keep only the most recent 200 activities
inline in `index.json` for fast feed rendering.

### Shard object

| Field | Type | Description |
|---|---|---|
| `year` | integer | Calendar year covered by this shard. |
| `url` | string | Relative or absolute URL to the shard file. |
| `count` | integer | Number of activities in the shard. |

---

## Activity Summary object

Appears in `index.json` (and yearly shard files). Contains only the fields
needed to render an activity card in a feed — no timeseries, no full track.

```json
{
  "id": "2024-06-01T073012Z-morning-ride",
  "title": "Morning Ride",
  "sport": "cycling",
  "sub_sport": "road",
  "started_at": "2024-06-01T07:30:12+02:00",
  "distance_m": 42300.0,
  "duration_s": 5400,
  "moving_time_s": 5100,
  "elevation_gain_m": 620.0,
  "avg_speed_kmh": 28.2,
  "max_speed_kmh": 52.1,
  "avg_hr_bpm": 148,
  "max_hr_bpm": 178,
  "avg_cadence_rpm": 88,
  "avg_power_w": null,
  "source": "strava_export",
  "privacy": "public",
  "detail_url": "activities/2024-06-01T073012Z-morning-ride.json",
  "track_url": "activities/2024-06-01T073012Z-morning-ride.geojson"
}
```

### Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string | yes | Unique identifier. See **Activity ID** section. |
| `title` | string | yes | Human-readable name. May be auto-generated if not in source. |
| `sport` | string | yes | One of: `cycling`, `running`, `hiking`, `walking`, `swimming`, `skiing`, `other`. |
| `sub_sport` | string\|null | no | e.g. `road`, `mountain`, `gravel`, `indoor`, `trail`, `track`, `nordic`, `alpine`, `open_water`, `pool`. |
| `started_at` | string | yes | ISO 8601 timestamp with timezone. |
| `distance_m` | number\|null | no | Total distance in metres. |
| `duration_s` | integer\|null | no | Total elapsed time in seconds. |
| `moving_time_s` | integer\|null | no | Time in motion (stopped periods excluded). |
| `elevation_gain_m` | number\|null | no | Cumulative positive elevation in metres. |
| `avg_speed_kmh` | number\|null | no | Average speed over moving time. |
| `max_speed_kmh` | number\|null | no | Maximum instantaneous speed. |
| `avg_hr_bpm` | integer\|null | no | Average heart rate. |
| `max_hr_bpm` | integer\|null | no | Maximum heart rate. |
| `avg_cadence_rpm` | integer\|null | no | Average cadence (rpm for cycling, spm for running). |
| `avg_power_w` | integer\|null | no | Average power in watts. |
| `source` | string\|null | no | Origin of data. See **Source values**. |
| `privacy` | string | yes | One of: `public`, `blur_start`, `no_gps`, `unlisted`. (`private` is a deprecated alias for `unlisted`.) |
| `mmp` | array\|null | no | Mean Maximal Power curve — `[[duration_s, avg_watts], ...]`. |
| `best_efforts` | array\|null | no | Best efforts by distance — `[[distance_km, time_s], ...]`. |
| `best_climb_m` | number\|null | no | Best single climb in metres (Kadane's algorithm). |
| `detail_url` | string\|null | no | Relative or absolute URL to the full activity JSON. |
| `track_url` | string\|null | no | Relative or absolute URL to the GeoJSON track. `null` if `privacy` is `no_gps`. |
| `preview_coords` | array\|null | no | Simplified track preview — `[[lon, lat], ...]` for card thumbnails. |

### Activity ID

The canonical ID format is:

```
{started_at_compact}[-{slug}]
```

Where `started_at_compact` is the start timestamp with special characters
removed: `2024-06-01T073012Z`, and `slug` is an optional URL-safe
lowercase title (spaces → hyphens, non-ASCII stripped).

Example: `2024-06-01T073012Z-morning-ride`

IDs must be unique within a data store. When a title is unavailable, the
timestamp alone is sufficient: `2024-06-01T073012Z`.

### Source values

| Value | Description |
|---|---|
| `strava_export` | Strava bulk data export |
| `garmin_connect` | Garmin Connect bulk export |
| `wahoo` | Wahoo ELEMNT / SYSTM export |
| `komoot` | Komoot GPX export |
| `gpx_file` | Generic GPX file |
| `fit_file` | Generic FIT file |
| `tcx_file` | Generic TCX file |
| `karoo` | Hammerhead Karoo device export |
| `manual` | Manually created |

### Privacy levels

| Level | GPS track published | Timeseries lat/lon | Shown in feed |
|---|---|---|---|
| `public` | Full track | Included | Yes — everyone |
| `blur_start` | First/last 200 m removed | Trimmed | Yes — everyone |
| `no_gps` | Not published | Not included | Yes — everyone |
| `unlisted` | Full track | Included | No — owner only (via direct URL) |
| `private` | *(deprecated alias for `unlisted`)* | Included | No — owner only |

**`unlisted`** activities are not shown in the public feed but are fully accessible
by direct URL — the GPS track, timeseries, and detail JSON are all served as normal
static files. This is "security by obscurity": knowing the URL is sufficient to
access the activity. If you need true data exclusion, use `no_gps` for GPS removal
while keeping stats public, or delete the activity entirely.

The legacy `private` value is accepted everywhere `unlisted` is valid.

---

## `activities/{id}.json`

Full activity record. Extends the Summary with timeseries and metadata.

```json
{
  "bas_version": "1.0",
  "id": "2024-06-01T073012Z-morning-ride",
  "title": "Morning Ride",
  "description": "Easy morning spin before work.",
  "sport": "cycling",
  "sub_sport": "road",
  "started_at": "2024-06-01T07:30:12+02:00",
  "distance_m": 42300.0,
  "duration_s": 5400,
  "moving_time_s": 5100,
  "elevation_gain_m": 620.0,
  "elevation_loss_m": 615.0,
  "avg_speed_kmh": 28.2,
  "max_speed_kmh": 52.1,
  "avg_hr_bpm": 148,
  "max_hr_bpm": 178,
  "avg_cadence_rpm": 88,
  "avg_power_w": null,
  "max_power_w": null,
  "gear": "Canyon Ultimate CF SL",
  "device": "Hammerhead Karoo 2",
  "bbox": [9.1234, 45.4321, 9.5678, 45.8765],
  "start_latlng": [45.4321, 9.1234],
  "end_latlng": [45.4321, 9.1235],
  "laps": [],
  "timeseries": {
    "t": [0, 1, 2],
    "lat": [45.4321, 45.4322, 45.4323],
    "lon": [9.1234, 9.1235, 9.1236],
    "elevation_m": [120.0, 120.5, 121.0],
    "speed_kmh": [0.0, 15.2, 22.4],
    "hr_bpm": [null, 142, 145],
    "cadence_rpm": [null, 85, 88],
    "power_w": [null, null, null],
    "temperature_c": [null, null, null]
  },
  "source": "karoo",
  "source_file": "13957.activity.abc123.fit",
  "source_hash": "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "strava_id": null,
  "privacy": "public",
  "custom": {}
}
```

### Additional fields (beyond Summary)

| Field | Type | Required | Description |
|---|---|---|---|
| `description` | string\|null | no | Free-text description. |
| `elevation_loss_m` | number\|null | no | Cumulative negative elevation. |
| `max_power_w` | integer\|null | no | Maximum power in watts. |
| `gear` | string\|null | no | Equipment used (bike name, shoe model…). |
| `device` | string\|null | no | Recording device (e.g. `"Garmin Edge 530"`). |
| `bbox` | array\|null | no | `[min_lon, min_lat, max_lon, max_lat]`. Null if no GPS. |
| `start_latlng` | array\|null | no | `[lat, lon]` of activity start. |
| `end_latlng` | array\|null | no | `[lat, lon]` of activity end. |
| `laps` | array | yes | Array of **Lap** objects. Empty array if no laps. |
| `timeseries` | object | yes | Parallel arrays of sensor data. See below. |
| `source_file` | string\|null | no | Original filename (basename only, no path). |
| `source_hash` | string\|null | no | `sha256:{hex}` of the original raw file bytes. Used for deduplication. |
| `strava_id` | string\|null | no | Strava activity ID if origin is a Strava export. |
| `custom` | object | yes | Free dict for plugin-computed fields. Must be present, may be `{}`. |

### Timeseries object

Parallel arrays, all the same length. Index `i` corresponds to `t[i]` seconds
after the activity start.

| Key | Type | Unit | Description |
|---|---|---|---|
| `t` | int[] | seconds | Seconds since `started_at`. Always present. |
| `lat` | float[]\|null | degrees | Latitude. `null` if no GPS or privacy=`no_gps`. |
| `lon` | float[]\|null | degrees | Longitude. `null` if no GPS or privacy=`no_gps`. |
| `elevation_m` | float[] | metres | Elevation. Array of nulls if unavailable. |
| `speed_kmh` | float[] | km/h | Speed. Array of nulls if unavailable. |
| `hr_bpm` | int[] | bpm | Heart rate. Array of nulls if no HR sensor. |
| `cadence_rpm` | int[] | rpm/spm | Cadence. Array of nulls if unavailable. |
| `power_w` | int[] | watts | Power. Array of nulls if no power meter. |
| `temperature_c` | float[] | °C | Temperature. Array of nulls if unavailable. |

Timeseries are downsampled to at most 1 sample per second. The exact
downsampling strategy is implementation-defined; linear interpolation or
nearest-neighbour are both acceptable.

`lat` and `lon` arrays are either both present (both non-null arrays) or both
`null`. Treat `null` the same as an array of nulls.

### Lap object

```json
{
  "index": 0,
  "started_at": "2024-06-01T07:30:12+02:00",
  "duration_s": 1800,
  "distance_m": 21150.0,
  "elevation_gain_m": 310.0,
  "avg_speed_kmh": 28.2,
  "avg_hr_bpm": 145,
  "avg_power_w": null
}
```

---

## `activities/{id}.geojson`

Simplified GPS track for map rendering. Omitted entirely when
`privacy` is `no_gps` or `private`.

```json
{
  "type": "Feature",
  "geometry": {
    "type": "LineString",
    "coordinates": [
      [9.1234, 45.4321, 120.0],
      [9.1235, 45.4322, 120.5]
    ]
  },
  "properties": {
    "id": "2024-06-01T073012Z-morning-ride",
    "speeds": [0.0, 15.2],
    "simplification": "rdp",
    "rdp_epsilon": 0.0001,
    "point_count_original": 7200,
    "point_count_simplified": 843
  }
}
```

Coordinates are `[longitude, latitude, elevation_metres]` per GeoJSON spec.
The `speeds` property is a parallel array to `coordinates` — one speed value
per point — used for gradient coloring on the map.

---

## Deduplication

Activities from different sources (e.g. a Strava export and a Karoo export)
may represent the same real-world ride. Producers should detect and handle
duplicates before writing the data store.

### Exact duplicate
Two files with the same `source_hash` are byte-for-byte identical. Only one
should be processed; the other is silently skipped.

### Near-duplicate (same ride, different source)
Two activities are considered near-duplicates if:
- `|started_at difference|` < 5 minutes, **and**
- `|distance_m difference| / max(distance_m)` < 5%

When a near-duplicate is detected:
1. One is kept as the **canonical** record (priority: FIT > GPX > TCX,
   then prefer the source with more sensor channels).
2. The duplicate is written with `"duplicate_of": "{canonical_id}"` and
   `"privacy": "private"` so it is excluded from feeds but remains auditable.

### Deduplication metadata in detail record

```json
{
  "source_hash": "sha256:e3b0c...",
  "duplicate_of": null
}
```

| Field | Type | Description |
|---|---|---|
| `source_hash` | string\|null | `sha256:{hex}` of original file bytes. |
| `duplicate_of` | string\|null | ID of the canonical activity, if this is a duplicate. |

---

## Instance manifest (`index.json` — multi-user mode)

In multi-user mode, the root `index.json` is a **shard manifest** rather than a user feed. It lists pointers to per-user BAS feeds. The browser fetches all shards concurrently and merges them.

```json
{
  "bas_version": "1.0",
  "instance": {
    "name": "Our Rides",
    "private": true
  },
  "generated_at": "2026-04-07T10:00:00Z",
  "shards": [
    { "handle": "dave",  "url": "dave/_merged/index.json" },
    { "handle": "alice", "url": "alice/_merged/index.json" },
    { "handle": "bob",   "url": "https://bob.example.com/index.json" }
  ],
  "activities": []
}
```

### Fields

| Field | Type | Description |
|---|---|---|
| `instance.name` | string | Human-readable instance name. |
| `instance.private` | boolean | If `true`, the site redirects unauthenticated visitors to `/login/`. |
| `shards` | array | Per-user shard entries. |

### Shard object (multi-user)

| Field | Type | Description |
|---|---|---|
| `handle` | string | User handle. Used for attribution (activities show `@handle`). |
| `url` | string | Relative or absolute URL to the user's `index.json`. |

The `url` field is relative to the location of the root manifest. Absolute URLs (starting with `http`) are fetched cross-origin — this is the federation mechanism.

Each user's `{handle}/index.json` is a valid standalone BAS feed. It can be used independently or included in another instance's shard manifest (federation).

---

## Versioning

The `bas_version` field allows consumers to handle schema evolution. Consumers
should:
- Reject files with a major version higher than they support.
- Accept and ignore unknown fields (forward compatibility).
- Treat missing optional fields as `null` (backward compatibility).

Current version: **1.0**

---

## Changelog

| Version | Date | Changes |
|---|---|---|
| 1.0 | 2026-03-28 | Initial release. |
