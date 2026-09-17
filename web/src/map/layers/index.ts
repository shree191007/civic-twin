/** deck.gl layer builders. Memoisation happens in DeckMap via updateTriggers. */
import { ArcLayer, PathLayer, PolygonLayer, ScatterplotLayer, TextLayer } from "@deck.gl/layers";
import type { Layer } from "@deck.gl/core";
import type {
  AssetProperties,
  DependencyLink,
  Portfolio,
  RoadProperties,
  TownshipResponse,
  ZoneProperties,
} from "../../api/types";
import { PORTFOLIO_COLOR, PROVENANCE_COLOR, dim, floodColor, type RGBA } from "../../lib/colors";
import type { Frame } from "../../lib/frames";
import { assetColor, zoneColor, type ModeContext } from "../modes";
import type { AnalysisMode } from "../../store";

export interface LayerOptions {
  township: TownshipResponse;
  frame: Frame;
  mode: AnalysisMode;
  ctx: ModeContext;
  activeLayers: Set<Portfolio>;
  selectedAsset: string | null;
  traceIds: Set<string>;
  traceEdges: Set<string>;
  /** Live phase of the travelling pulse. A ref, so advancing it does not
   *  re-render the map; the layer accessors read `.current` directly. */
  dashOffset: { current: number };
  flat: boolean;
  onClick: (assetId: string) => void;
  onHover: (assetId: string | null) => void;
}

type AssetFeature = TownshipResponse["layers"]["energy"]["features"][number];
type ZoneFeature = TownshipResponse["zones"]["features"][number];
type RoadFeature = TownshipResponse["roads"]["features"][number];

//: How long a mode swap takes to repaint, per spec 05 section 5.
const MODE_TRANSITION_MS = 200;

const radius = (served: number): number => 30 + Math.pow(Math.max(served, 1), 0.4) * 2.2;
const alt = (a: AssetProperties, flat: boolean): number => (flat ? 0 : a.layer_altitude_m);

/** A faint plate under each portfolio, so the layer cake reads as stacked planes. */
export function plateLayers(o: LayerOptions): Layer[] {
  if (o.flat) return [];
  const [minLon, minLat, maxLon, maxLat] = o.township.bbox;
  return [...o.activeLayers].map((portfolio) => {
    const features = o.township.layers[portfolio]?.features ?? [];
    const altitude = features[0]?.properties.layer_altitude_m ?? 0;
    const color = PORTFOLIO_COLOR[portfolio];
    const ring: [number, number, number][] = [
      [minLon, minLat, altitude],
      [maxLon, minLat, altitude],
      [maxLon, maxLat, altitude],
      [minLon, maxLat, altitude],
      [minLon, minLat, altitude],
    ];
    return new PolygonLayer<{ ring: [number, number, number][] }>({
      id: `plate-${portfolio}`,
      data: [{ ring }],
      getPolygon: (d) => d.ring,
      extruded: false,
      filled: true,
      stroked: true,
      getFillColor: dim(color, 0.04),
      getLineColor: dim(color, 0.25),
      getLineWidth: 12,
      lineWidthUnits: "meters",
      // The plates are almost transparent, but they would still write depth and
      // hide every layer underneath them. They are backdrops, not geometry.
      parameters: { depthWriteEnabled: false },
      pickable: false,
    });
  });
}

export function assetLayers(o: LayerOptions): Layer[] {
  const layers: Layer[] = [];
  for (const portfolio of o.activeLayers) {
    const collection = o.township.layers[portfolio];
    if (!collection) continue;
    const data = collection.features;
    layers.push(
      new ScatterplotLayer<AssetFeature>({
        id: `assets-${portfolio}`,
        data,
        pickable: true,
        stroked: true,
        filled: true,
        radiusUnits: "meters",
        lineWidthUnits: "pixels",
        getLineWidth: 1.5,
        getPosition: (d) => [
          d.geometry.coordinates[0] as number,
          d.geometry.coordinates[1] as number,
          alt(d.properties, o.flat),
        ],
        getRadius: (d) => radius(d.properties.served_population),
        getFillColor: (d) => {
          const base = assetColor(o.mode, d.properties, o.ctx);
          return o.traceIds.size > 0 && !o.traceIds.has(d.properties.id)
            ? dim(base, 0.25)
            : base;
        },
        getLineColor: (d) =>
          d.properties.id === o.selectedAsset
            ? ([255, 255, 255, 255] as RGBA)
            : PROVENANCE_COLOR[d.properties.provenance],
        onClick: (info) => {
          const props = (info.object as AssetFeature | undefined)?.properties;
          if (props) o.onClick(props.id);
        },
        onHover: (info) => {
          const props = (info.object as AssetFeature | undefined)?.properties;
          o.onHover(props?.id ?? null);
        },
        updateTriggers: {
          getFillColor: [o.mode, o.frame.t, o.traceIds.size],
          getLineColor: [o.selectedAsset],
          getPosition: [o.flat],
        },
        // Swapping what colour means should read as a change of lens, not a
        // flicker. Status itself never interpolates -- the values step at frame
        // boundaries -- this animates only the paint.
        transitions: { getFillColor: MODE_TRANSITION_MS },
      }),
    );
    layers.push(
      new TextLayer<AssetFeature>({
        id: `labels-${portfolio}`,
        data,
        getPosition: (d) => [
          d.geometry.coordinates[0] as number,
          d.geometry.coordinates[1] as number,
          alt(d.properties, o.flat),
        ],
        getText: (d) => d.properties.id,
        getSize: 10,
        getColor: [216, 224, 234, 190],
        getPixelOffset: [0, -13],
        fontFamily: '"JetBrains Mono", ui-monospace, monospace',
        characterSet: "auto",
        pickable: false,
        updateTriggers: { getPosition: [o.flat] },
      }),
    );
  }
  return layers;
}

export function roadLayer(o: LayerOptions): Layer[] {
  if (!o.activeLayers.has("transport")) return [];
  const closed = new Set(o.frame.closed_roads);
  return [
    new PathLayer<RoadFeature>({
      id: "roads",
      data: o.township.roads.features,
      widthUnits: "meters",
      getWidth: 26,
      getPath: (d) =>
        (d.geometry.coordinates as [number, number][]).map(
          (c) => [c[0], c[1], 0] as [number, number, number],
        ),
      getColor: (d): RGBA => {
        const props = d.properties as RoadProperties;
        if (closed.has(props.id)) return [255, 59, 78, 220];
        const depth = o.frame.flood[props.id];
        if (depth && depth > 0.02) return floodColor(depth);
        return [60, 72, 88, 170];
      },
      updateTriggers: { getColor: [o.frame.t] },
      pickable: false,
    }),
  ];
}

export function zoneLayer(o: LayerOptions): Layer[] {
  const pulse = 0.55 + 0.45 * Math.abs(Math.sin(o.dashOffset.current / 9));
  return [
    new ScatterplotLayer<ZoneFeature>({
      id: "zones",
      data: o.township.zones.features,
      stroked: true,
      filled: true,
      radiusUnits: "meters",
      lineWidthUnits: "pixels",
      getLineWidth: 1,
      getPosition: (d) => [
        d.geometry.coordinates[0] as number,
        d.geometry.coordinates[1] as number,
        0,
      ],
      getRadius: (d) => 90 + Math.sqrt((d.properties as ZoneProperties).population) * 3.2,
      getFillColor: (d): RGBA => {
        const props = d.properties as ZoneProperties;
        const color = zoneColor(o.mode, props.id, o.ctx);
        const affected = o.traceIds.size > 0 && isZoneAffected(props, o.traceIds);
        return [color[0], color[1], color[2], Math.round((affected ? pulse : 0.4) * 130)];
      },
      getLineColor: [125, 139, 158, 110],
      pickable: false,
      updateTriggers: {
        getFillColor: [o.mode, o.frame.t, o.traceIds.size, Math.round(o.dashOffset.current)],
      },
      transitions: { getFillColor: MODE_TRANSITION_MS },
    }),
  ];
}

function isZoneAffected(zone: ZoneProperties, traceIds: Set<string>): boolean {
  return (
    traceIds.has(zone.substation) ||
    traceIds.has(zone.feeder) ||
    traceIds.has(zone.tank) ||
    zone.towers.some((t) => traceIds.has(t))
  );
}

/** Cross-portfolio dependency arcs: the visual claim of the whole project. */
export function arcLayer(o: LayerOptions): Layer[] {
  if (o.flat) return [];
  const links = o.township.links.filter((l) => l.source_altitude_m !== l.target_altitude_m);
  if (links.length === 0) return [];
  const index = portfolioIndex(o.township);
  return [
    new ArcLayer<DependencyLink>({
      id: "dependency-arcs",
      data: links,
      getSourcePosition: (d) => [d.source_lonlat[0], d.source_lonlat[1], d.source_altitude_m],
      getTargetPosition: (d) => [d.target_lonlat[0], d.target_lonlat[1], d.target_altitude_m],
      getWidth: (d) => (o.traceEdges.has(`${d.source}>${d.target}`) ? 3.5 : 1.5),
      getSourceColor: (d): RGBA => arcColor(d, o, index, true),
      getTargetColor: (d): RGBA => arcColor(d, o, index, false),
      pickable: false,
      updateTriggers: {
        getSourceColor: [o.traceEdges.size, Math.round(o.dashOffset.current)],
        getTargetColor: [o.traceEdges.size, Math.round(o.dashOffset.current)],
        getWidth: [o.traceEdges.size],
      },
    }),
  ];
}

function arcColor(
  link: DependencyLink,
  o: LayerOptions,
  index: Map<string, Portfolio>,
  isSource: boolean,
): RGBA {
  const onTrace = o.traceEdges.has(`${link.source}>${link.target}`);
  const portfolio = index.get(isSource ? link.source : link.target) ?? "transport";
  const base = PORTFOLIO_COLOR[portfolio];
  if (o.traceEdges.size > 0 && !onTrace) return dim(base, 0.05);
  if (!onTrace) return dim(base, 0.35);
  // A pulse travelling from the failing asset toward what it takes down.
  const phase = (o.dashOffset.current % 12) / 12;
  const strength = isSource ? 1 - phase : phase;
  return dim([255, 255, 255, 255], 0.45 + 0.55 * strength);
}

const indexCache = new WeakMap<TownshipResponse, Map<string, Portfolio>>();

function portfolioIndex(township: TownshipResponse): Map<string, Portfolio> {
  let index = indexCache.get(township);
  if (!index) {
    index = new Map<string, Portfolio>();
    for (const [portfolio, collection] of Object.entries(township.layers)) {
      for (const feature of collection.features) {
        index.set(feature.properties.id, portfolio as Portfolio);
      }
    }
    indexCache.set(township, index);
  }
  return index;
}

export function buildLayers(o: LayerOptions): Layer[] {
  return [
    ...plateLayers(o),
    ...roadLayer(o),
    ...zoneLayer(o),
    ...arcLayer(o),
    ...assetLayers(o),
  ];
}
