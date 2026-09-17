# 07 — Real-data pipeline (OPTIONAL)

**Only start this if specs 01–05 are green.** The synthetic township is the
reference implementation and must keep working unchanged.

**Builds:** `civictwin/pipeline/*`, `data/SOURCES.md`
**Gate:** `python -m civictwin.pipeline.assemble --bbox ... --out data/real.json`
produces a township that passes `validate()` and runs through the full analysis.

---

## 1. Principle

The pipeline's output is a `Township` identical in type to the synthetic one.
Everything downstream is unchanged. Where real data is missing, generate it and
mark it `SYNTHETIC`. **Never fabricate an `OBSERVED` tag.**

## 2. Township selection

Run `python -m civictwin.pipeline.survey --bbox W,S,E,N` first. It counts
available features and prints a go/no-go table:

| Feature | Query | Minimum |
|---|---|---|
| Road segments | `highway` in OSM | 200 |
| Bridges | `bridge=yes` | 1 |
| Substations | `power=substation` | 2 |
| Water facilities | `man_made` in {water_works, water_tower, reservoir_covered, pumping_station} | 2 |
| Hospitals | `amenity=hospital` | 1 |
| Cell sites | OpenCelliD within bbox, clustered | 4 |
| Area | — | 5–25 km² |
| Terrain relief | DEM range | ≥ 8 m (so flooding differentiates) |

If any minimum fails, print which one and recommend a different bbox.

## 3. Sources and licences (record all of this in `data/SOURCES.md`)

| Layer | Source | Access | Licence | Provenance tag |
|---|---|---|---|---|
| Roads, bridges, POIs | OpenStreetMap via Overpass | `osmnx` | ODbL (share-alike) | `OBSERVED` |
| Buildings | OSM; supplement with Google Open Buildings v3 | download | ODbL / CC BY | `OBSERVED` / `INFERRED` |
| Population | WorldPop 100 m or Meta HRSL | download | CC BY | `INFERRED` |
| Substations, HV lines | OSM `power=*` | Overpass | ODbL | `OBSERVED` |
| Distribution feeders | Generated (MST along roads) | — | — | `SYNTHETIC` |
| Cell sites | OpenCelliD | registered CSV export | CC BY-SA | `INFERRED` |
| Exchanges, masts | OSM `telecom=*`, `man_made=mast` | Overpass | ODbL | `OBSERVED` |
| Fibre | Assumed along primary roads | — | — | `SYNTHETIC` |
| Water facilities | OSM `man_made=*` | Overpass | ODbL | `OBSERVED` |
| Water mains | Generated along roads | — | — | `SYNTHETIC` |
| Hospitals, clinics | OSM `amenity=*`; cross-check ABDM HFR | Overpass | ODbL | `OBSERVED` |
| Terrain | Copernicus GLO-30 DEM | download | free, attribution | `OBSERVED` |
| HAND | Derived from DEM | `pysheds` / WhiteboxTools | — | `INFERRED` |
| Rainfall statistics | IMD gridded rainfall | download | see IMD terms | `OBSERVED` |
| Historical flood extent | Sentinel-1 via Google Earth Engine; NRSC/Bhuvan maps | GEE / portal | see terms | `OBSERVED` |

**Licence obligations:** OSM (ODbL) and OpenCelliD (CC BY-SA) are share-alike.
If derived data is published, it must carry the same licence and attribution.
State this in the README.

## 4. Stages

### 4.1 `fetch.py`
One function per source, each writing to `data/raw/<source>/` and **never**
re-downloading if the file exists. Record the query, timestamp, and file hash in
`data/raw/manifest.json`.

### 4.2 `clean.py`
- Reproject everything to the local UTM zone (compute from the bbox centroid).
- Clip to the bbox.
- **Cell site clustering:** OpenCelliD rows are cells, not towers. Cluster with
  DBSCAN (`eps=60 m`, `min_samples=1`); each cluster becomes one `TOWER` asset
  with `capacity` proportional to the number of cells. Tag `INFERRED`.
- Deduplicate OSM features by `osmid`; prefer ways over nodes for facilities.
- Snap every asset to the nearest road node within 300 m; if none, create a
  connector edge to the nearest node and tag it `SYNTHETIC`.

### 4.3 `synthesize.py`
- **Zones:** Voronoi polygons around road-graph nodes, merged until each zone
  has 1,500–10,000 people. Population from WorldPop, aggregated by polygon.
- **Feeders:** for each substation, build a minimum spanning tree over its
  assigned zones along the road graph. Assign each zone's transformer.
  Substation assignment by nearest along-road distance.
- **Water mains:** same MST approach from treatment works → pumps → tanks →
  zones, following roads (PipeNetGen-style, simplified).
- **Fibre:** shortest path along primary roads between exchanges; where that
  path crosses a bridge, add a `HOSTED_ON` link. **This is how real co-location
  traps are discovered rather than invented.**
- **Tank/zone assignment:** nearest tank by along-road distance.
- **Crews and depots:** if no depot exists in OSM, place one per utility at the
  municipal office or the largest facility; tag `SYNTHETIC`.

### 4.4 `assemble.py`
Build the `Township`, run `validate()`, and write a provenance report:
```json
{"assets": {"observed": 34, "inferred": 41, "synthetic": 58},
 "by_portfolio": {"water": {"observed": 3, "synthetic": 22}, ...},
 "warnings": ["No stormwater pumps found; pluvial feedback disabled"]}
```

## 5. Fragility parameters for a non-US context

Hazus curves are the starting point but transfer poorly. Required handling:
- Store `fragility_median_m` per kind in a single editable table,
  `data/fragility.json`, with a `source` field per row.
- Widen `fragility_beta` to 0.5 (from 0.4) for all `INFERRED`/`SYNTHETIC` assets
  to reflect greater uncertainty.
- The ensemble (spec 03 §8) perturbs these; the value-of-information output
  will show how much this uncertainty matters.

## 6. Calibration against a real event

```
usage: python -m civictwin.pipeline.calibrate --event data/events/<name>.json
```

Event file schema:
```json
{"name": "flood-2015-12", "rain_mm": 294,
 "flood_extent": "data/events/flood-2015-12/extent.geojson",
 "reported_closures": ["road names or OSM ids"],
 "reported_outages": [{"area": "...", "start_h": 6, "end_h": 60,
                       "service": "energy", "source": "news URL"}]}
```

Procedure:
1. Run the event's rainfall through the model.
2. Compare flooded road segments against the observed extent:
   report hit rate, false alarm ratio, and a confusion matrix.
3. Compare outage timing per service where reported.
4. Re-weight ensemble members by agreement (approximate Bayesian computation:
   keep members within the top 50% by agreement score).
5. Write `results/validation.json` (schema in spec 08 §2).

## 7. Acceptance tests

1. `test_survey_rejects_empty_bbox`: a bbox with no infrastructure prints a
   clear no-go.
2. `test_cell_clustering`: 100 synthetic cells at 5 locations cluster to 5 towers.
3. `test_snap_creates_connector`: an asset 500 m from any road gets a connector
   edge tagged `SYNTHETIC`.
4. `test_provenance_never_upgraded`: no synthesised object is tagged `OBSERVED`.
5. `test_assemble_validates`: the assembled township passes `validate()`.
6. `test_fibre_bridge_hosting`: if a synthesised fibre path crosses a bridge
   edge, a `HOSTED_ON` link exists.
7. `test_pipeline_idempotent`: running twice with cached raw files produces
   an identical township hash.
