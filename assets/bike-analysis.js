(() => {
  "use strict";
  const $ = id => document.getElementById(id);
  const {api, time} = window.AirWatchData;
  const endpoint = "/api/analysis/bikes/";
  const metrics = {
    borrow: {field: "bikes", label: "可用车辆", action: "借车"},
    return: {field: "empty_docks", label: "空位", action: "还车"},
  };
  // Fixed inventory bins shared by both metrics. The first two bands mirror the
  // API's tight_max (BIKE_TIGHT_MAX = 2); keep them in sync if the backend changes.
  const tiers = [
    {label: "0", max: 0, color: "#e77d8d", radius: 6},
    {label: "1–2", max: 2, color: "#efba62", radius: 6},
    {label: "3–5", max: 5, color: "#7dd4e2", radius: 6.5},
    {label: "6–9", max: 9, color: "#55bfd2", radius: 7.25},
    {label: "10–15", max: 15, color: "#37a2ba", radius: 8},
    {label: "16–20", max: 20, color: "#277e99", radius: 8.75},
    {label: "21+", max: Infinity, color: "#1a5e77", radius: 9.5},
  ];
  const unknownTier = {label: "未知", color: "#8194a4", radius: 6};
  const emptyGeoJSON = {type: "FeatureCollection", features: []};
  const S = {
    visible: false, view: "map", mode: "borrow", site: "", timeline: null, batches: [],
    cursor: null, target: null, frame: null, frameStatus: "", stationStatus: "", gap: false,
    frames: new Map(), points: null, charts: [], markers: [],
  };
  const viewObserver = typeof window.ResizeObserver === "function" ? new window.ResizeObserver(resize) : null;
  const esc = value => String(value).replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[char]);
  const display = value => value ?? "—";
  const stamp = ms => new Date(ms).toISOString();
  const at = ms => ms === null ? "—" : time(stamp(ms));
  const sourceLabel = source => source === "public_archive" ? "公开归档" : "TfL 本地采集";
  const expiresAt = batch => batch.ms + batch.freshnessSeconds * 1000;
  const currentPoint = () => S.points?.get(S.frame?.capturedAt);
  const currentStation = () => S.frame?.stations.get(S.site);
  const mode = () => metrics[S.mode];
  const windowParams = () => ({start: S.timeline.start, end: S.timeline.end});
  const settled = () => !S.target || S.frame?.capturedAt === S.target;
  const status = value => value === null ? "unknown"
    : value === 0 ? "zero" : value <= S.timeline.tight_max ? "low" : "available";
  const tier = value => value === null || value === undefined ? unknownTier
    : tiers.find(entry => value <= entry.max) ?? unknownTier;

  function frameAt(ms) {
    let lo = 0, hi = S.batches.length;
    while (lo < hi) {
      const mid = Math.floor((lo + hi) / 2);
      if (S.batches[mid].ms <= ms) lo = mid + 1;
      else hi = mid;
    }
    return S.batches[lo - 1];
  }

  function renderRange() {
    const slider = $("bikeTimeSlider");
    slider.min = S.start / 1000;
    slider.max = S.end / 1000;
    $("bikeTimeStart").textContent = at(S.start);
    $("bikeTimeEnd").textContent = at(S.end);
    const gaps = [];
    let start = S.start;
    for (const batch of S.batches) {
      if (start < batch.ms) gaps.push([start, batch.ms]);
      start = expiresAt(batch);
    }
    if (start < S.end) gaps.push([start, S.end]);
    $("bikeGaps").innerHTML = gaps.map(([left, right]) =>
      `<span style="left:${100 * (left - S.start) / (S.end - S.start)}%;width:${100 * (right - left) / (S.end - S.start)}%"></span>`).join("");
  }

  function renderLegend() {
    const node = $("bikeLegendClasses");
    if (!node) return;
    node.innerHTML = [...tiers, unknownTier].map(({label, color, radius}) =>
      `<span class="bike-legend-entry" style="--bike-dot:${color};--bike-dot-size:${Math.round(radius * 1.3)}px">${esc(label)}</span>`).join("");
  }

  function renderTime() {
    const slider = $("bikeTimeSlider");
    const active = S.cursor !== null;
    slider.disabled = !S.batches.length;
    $("bikePrevious").disabled = !active || S.cursor <= S.batches[0].ms;
    $("bikeNext").disabled = !active || S.cursor >= S.batches.at(-1).ms;
    $("bikePlay").disabled = S.batches.length < 2;
    const label = at(S.cursor);
    if (active) slider.value = S.cursor / 1000;
    slider.setAttribute("aria-valuetext", label);
    $("bikeTime").textContent = active ? "回放 " + label : "";
    const capture = $("bikeCapture");
    capture.title = S.frame?.capturedAt || "";
    capture.textContent = S.timelineStatus || (!S.batches.length ? "最近 7 天没有自行车快照。"
      : !S.target ? (S.gap ? "缺测 · 该时刻没有可用快照，地图变淡显示上一批点位。"
        : "缺测 · 该时刻没有可用快照。")
      : S.frameStatus || (S.frame ? sourceLabel(S.frame.source) + " · " + time(S.frame.capturedAt) + " · " + S.frame.stations.size + " 个站点"
        : "正在读取该批快照…"));
    $("bikeArchiveCredit").hidden = !S.target || !S.frame
      || S.frame.capturedAt !== S.target || S.frame.source !== "public_archive";
    capture.classList.toggle("bike-gap-message", active && !S.target);
    const trendStatus = $("bikeTrendStatus");
    trendStatus.textContent = S.timelineStatus;
    trendStatus.hidden = !S.timelineStatus;
  }

  function renderNearby() {
    const selected = S.gap ? null : currentStation(), point = S.gap ? null : currentPoint();
    $("bikeCurrent").hidden = !S.site;
    $("bikeCurrentName").textContent = selected?.name || S.catalog?.get(S.site) || "";
    $("bikeCurrentBikes").textContent = display(selected?.bikes);
    $("bikeCurrentDocks").textContent = display(selected?.empty_docks);
    $("bikeCurrentCapacity").textContent = display(selected?.capacity);
    const neighbors = S.frame && selected ? point?.neighbors || [] : [];
    $("bikeNeighborTable").hidden = !neighbors.length;
    const ratio = point?.[S.mode];
    $("bikeNearbyStatus").textContent = !S.site ? "选择站点后显示邻站。"
      : S.frameStatus ? S.frameStatus
      : S.gap ? "该时刻缺测，地图变淡显示上一批站点。"
      : !S.frame ? "该时刻无可用快照。"
      : !selected ? "该批快照未记录所选站点。"
      : !selected.coordinates ? "该批快照缺少所选站点坐标。"
      : S.stationStatus ? S.stationStatus
      : !neighbors.length ? "500 米内没有记录到其他站点。"
      : !ratio ? "所选站点的" + mode().label + "缺测。"
      : ratio.valid ? `${mode().action}紧张 ${ratio.tight} / ${ratio.valid} 个有效邻站`
      : "邻站的" + mode().label + "均缺测。";
    const cells = (station, key) => `<td class="${key === mode().field ? "bike-" + status(station[key]) : ""}">${display(station[key])}</td>`;
    $("bikeNeighbors").innerHTML = neighbors.map(({id, distance_m}) => {
      const station = S.frame.stations.get(id);
      if (!station) return "";
      return `<tr><th scope="row"><button type="button" data-station="${esc(id)}">${esc(station.name)}</button><small>${distance_m} 米</small></th>` +
        cells(station, "bikes") + cells(station, "empty_docks") + cells(station, "capacity") + "</tr>";
    }).join("");
  }

  function repaintFrame() {
    renderTime();
    renderNearby();
    drawMap();
  }

  function loadFrame(target, ghost = false) {
    clearTimeout(S.dragTimer);
    const token = S.frameToken = {};
    if (!target) {
      S.gap = Boolean(ghost && S.frame);
      if (!S.gap) S.frame = null;
      S.frameStatus = "";
      repaintFrame();
      return;
    }
    const source = S.timeline.batch_meta?.[target]?.source || "tfl";
    if (S.frame?.capturedAt === target && S.frame.source === source) {
      S.gap = false;
      S.frameStatus = "";
      repaintFrame();
      return;
    }
    const cached = S.frames.get(target);
    if (cached && cached.source === source) {
      S.frames.delete(target);
      S.frames.set(target, cached);
      S.frame = cached;
      S.gap = false;
      S.frameStatus = "";
      repaintFrame();
      return;
    }
    // Keep the previous frame on screen while the next batch loads; only the
    // caption and side panel acknowledge the pending target.
    S.frameStatus = "正在读取该批快照…";
    renderTime();
    renderNearby();
    S.dragTimer = setTimeout(async () => {
      try {
        const data = await api(endpoint + "frame", {captured_at: target});
        if (S.frameToken !== token) return;
        S.frame = {capturedAt: data.captured_at, source: data.source || "tfl",
          stations: new Map(data.stations.map(station => [station.id, station]))};
        S.frames.set(target, S.frame);
        if (S.frames.size > 12) S.frames.delete(S.frames.keys().next().value);
        S.gap = false;
        S.frameStatus = "";
      } catch (error) {
        if (S.frameToken !== token) return;
        S.frameStatus = "快照读取失败 · " + error.message;
        S.frame = null;
        S.gap = false;
        stop();
      }
      repaintFrame();
    }, 85);
  }

  function setCursor(ms, retry = false) {
    S.cursor = ms;
    const batch = ms === null ? null : frameAt(ms);
    const target = batch && ms <= expiresAt(batch) ? batch.capturedAt : null;
    const gap = target === null && Boolean(S.frame) && S.frame.capturedAt === batch?.capturedAt;
    if (S.target !== target || retry) {
      S.target = target;
      loadFrame(target, gap);
    } else if (target === null && S.gap !== gap) {
      S.gap = gap;
      if (!gap) S.frame = null;
      repaintFrame();
    } else {
      renderTime();
    }
    drawCursor();
  }

  function stop() {
    clearTimeout(S.playTimer);
    S.playing = false;
    $("bikePlay").textContent = "播放";
  }

  function play() {
    if (S.playing) return stop();
    if (S.batches.length < 2) return;
    S.playing = true;
    $("bikePlay").textContent = "暂停";
    if (S.cursor >= S.batches.at(-1).ms) setCursor(S.batches[0].ms);
    else if (S.target && !S.frame) loadFrame(S.target);
    const advance = () => {
      if (!S.visible || !S.playing) return stop();
      if (!settled()) {
        S.playTimer = setTimeout(advance, 150);
        return;
      }
      const next = S.batches.find(batch => batch.ms > S.cursor);
      if (!next) return stop();
      const previous = frameAt(S.cursor);
      const gap = previous && expiresAt(previous) + 1;
      setCursor(gap > S.cursor && gap < next.ms ? gap : next.ms);
      S.playTimer = setTimeout(advance, 700);
    };
    S.playTimer = setTimeout(advance, 700);
  }

  function step(direction) {
    stop();
    const batch = direction > 0 ? S.batches.find(row => row.ms > S.cursor)
      : S.batches.findLast(row => row.ms < S.cursor);
    if (batch) setCursor(batch.ms);
  }

  async function loadStation() {
    const token = S.stationToken = {};
    S.points = null;
    S.stationStatus = S.site ? "正在读取本站趋势与邻站…" : "";
    renderNearby();
    drawCharts();
    if (!S.site) return;
    try {
      const data = await api(endpoint + "station", {...windowParams(), site: S.site});
      if (S.stationToken !== token) return;
      S.points = new Map(data.points.map(point => [point.captured_at, point]));
      S.stationStatus = "";
    } catch (error) {
      if (S.stationToken !== token) return;
      S.stationStatus = "邻站与趋势读取失败 · " + error.message;
    }
    renderNearby();
    drawCharts();
  }

  function selectStation(site) {
    if (site && !S.catalog?.has(site)) return;
    if (S.site !== site) {
      S.site = site;
      $("bikeStation").value = site;
      S.focusStation = Boolean(site);
      loadStation();
    }
    drawMap();
  }

  async function refresh() {
    const token = S.timelineToken = {};
    S.timelineStatus = "正在读取批次…";
    renderTime();
    try {
      const data = await api(endpoint + "timeline");
      if (S.timelineToken !== token) return;
      const last = S.batches.at(-1)?.ms;
      const wasLatest = S.cursor === null || S.cursor === last;
      S.timeline = data;
      S.start = Date.parse(data.start);
      S.end = Date.parse(data.end);
      S.batches = data.batches.map(capturedAt => ({
        capturedAt,
        ms: Date.parse(capturedAt),
        source: data.batch_meta?.[capturedAt]?.source || "tfl",
        freshnessSeconds: data.batch_meta?.[capturedAt]?.freshness_seconds ?? data.freshness_seconds,
      }));
      S.catalog = new Map(data.stations.map(station => [station.id, station.name]));
      const site = S.prefill || S.site;
      S.focusStation = S.catalog.has(site) && Boolean(S.prefill || S.focusStation);
      S.prefill = "";
      S.site = S.catalog.has(site) ? site : "";
      const select = $("bikeStation");
      select.innerHTML = ['<option value="">选择自行车站</option>', ...data.stations.map(station =>
        `<option value="${esc(station.id)}">${esc(station.name)}</option>`)].join("");
      select.value = S.site;
      select.disabled = !data.stations.length;
      S.timelineStatus = "";
      renderRange();
      const cursor = !S.batches.length ? null : wasLatest || S.cursor < S.start || S.cursor > S.end
        ? S.batches.at(-1).ms : S.cursor;
      if (S.batches.length < 2) stop();
      setCursor(cursor, true);
      loadStation();
    } catch (error) {
      if (S.timelineToken !== token) return;
      S.timelineStatus = "批次读取失败 · " + error.message;
      renderTime();
    }
  }

  function setMode(key) {
    S.mode = key;
    $("bikeBorrow").setAttribute("aria-pressed", String(key === "borrow"));
    $("bikeReturn").setAttribute("aria-pressed", String(key === "return"));
    $("bikeLegendMetric").textContent = mode().label;
    $("bikePressureMode").textContent = mode().action;
    renderNearby();
    drawMap();
    drawCharts();
  }

  function initMap() {
    const lib = window.maplibregl;
    const notice = message => {
      $("bikeMapNotice").textContent = message;
      $("bikeMapNotice").hidden = !message;
    };
    try {
      if (!lib) throw new Error("missing map library");
      const map = S.map = new lib.Map({
        container: "bikeMap", style: "https://tiles.openfreemap.org/styles/positron",
        center: [-0.12, 51.51], zoom: 11, minZoom: 7, maxZoom: 17, attributionControl: false,
        locale: {"AttributionControl.ToggleAttribution": "底图与站点数据来源"},
      });
      map.addControl(new lib.NavigationControl({showCompass: false}), "top-right");
      map.addControl(new lib.AttributionControl({compact: true,
        customAttribution: '<a href="https://api.tfl.gov.uk/BikePoint">TfL BikePoint</a>',
      }), "bottom-right");
      let fallback = false;
      const useLocalStyle = () => {
        if (S.mapReady || fallback) return;
        fallback = true;
        notice("在线底图暂不可用，当前显示伦敦行政区简图。");
        map.setStyle({
          version: 8,
          sources: {boroughs: {type: "geojson", data: "/data/london_boroughs_2025.geojson",
            attribution: "Contains OS data © Crown copyright and database right 2025"}},
          layers: [
            {id: "background", type: "background", paint: {"background-color": "#dce6ea"}},
            {id: "boroughs", type: "fill", source: "boroughs", paint: {"fill-color": "#eff3f4", "fill-outline-color": "#a1b1bd"}},
          ],
        });
      };
      const styleTimer = setTimeout(useLocalStyle, 5000);
      map.on("error", () => {
        if (!S.mapReady && !fallback) useLocalStyle();
        else if (!fallback) notice("部分底图资源暂不可用。");
      });
      map.on("style.load", () => {
        clearTimeout(styleTimer);
        S.mapReady = true;
        addMapLayers(map);
        if (S.visible && S.view === "map") map.resize();
        drawMap();
      });
      map.on("zoomend", drawMap);
      for (const layer of ["bike-analysis-stations", "bike-analysis-selected"]) {
        map.on("click", layer, event => {
          const id = event.features?.[0]?.properties.id;
          if (id) selectStation(id);
        });
        map.on("mouseenter", layer, () => { map.getCanvas().style.cursor = "pointer"; });
        map.on("mouseleave", layer, () => { map.getCanvas().style.cursor = ""; });
      }
      viewObserver?.observe($("bikeMap"));
    } catch {
      S.map?.remove();
      S.map = null;
      $("bikeMap").hidden = true;
      notice("地图暂不可用。可用站点下拉框和时间轴查看数据。");
    }
  }

  function addMapLayers(map) {
    map.addSource("bike-analysis-points", {type: "geojson", data: emptyGeoJSON});
    map.addSource("bike-analysis-selection", {type: "geojson", data: emptyGeoJSON});
    map.addLayer({id: "bike-analysis-area-fill", type: "fill", source: "bike-analysis-selection",
      filter: ["==", "$type", "Polygon"], paint: {"fill-color": "#258b8a",
        "fill-opacity": ["case", ["boolean", ["get", "stale"], false], 0.04, 0.12]}});
    map.addLayer({id: "bike-analysis-area-line", type: "line", source: "bike-analysis-selection",
      filter: ["==", "$type", "Polygon"], paint: {"line-color": "#226b70", "line-dasharray": [2, 2], "line-width": 2,
        "line-opacity": ["case", ["boolean", ["get", "stale"], false], 0.3, 1]}});
    const pointPaint = {"circle-color": ["get", "color"], "circle-radius": ["get", "radius"],
      "circle-opacity": ["case", ["boolean", ["get", "stale"], false], 0.32, 1],
      "circle-stroke-color": "#f1f8fe", "circle-stroke-width": 1.5,
      "circle-stroke-opacity": ["case", ["boolean", ["get", "stale"], false], 0.35, 1]};
    map.addLayer({id: "bike-analysis-stations", type: "circle", source: "bike-analysis-points", paint: pointPaint});
    map.addLayer({id: "bike-analysis-selected", type: "circle", source: "bike-analysis-selection",
      filter: ["==", "$type", "Point"], paint: {...pointPaint,
        "circle-stroke-width": 2.5, "circle-stroke-color": "#163a46"}});
  }

  function radiusPolygon([lon, lat], meters) {
    const rad = Math.PI / 180, angle = meters / 6371008.8, phi = lat * rad;
    const ring = [];
    for (let i = 0; i < 64; i++) {
      const bearing = i * Math.PI / 32;
      const y = Math.asin(Math.sin(phi) * Math.cos(angle) + Math.cos(phi) * Math.sin(angle) * Math.cos(bearing));
      const x = Math.atan2(Math.sin(bearing) * Math.sin(angle) * Math.cos(phi), Math.cos(angle) - Math.sin(phi) * Math.sin(y));
      ring.push([lon + x / rad, y / rad]);
    }
    return {type: "Polygon", coordinates: [[...ring, ring[0]]]};
  }

  function drawMap() {
    const map = S.map;
    if (!S.visible || S.view !== "map" || !S.mapReady) return;
    const pointsSource = map.getSource("bike-analysis-points");
    const selectionSource = map.getSource("bike-analysis-selection");
    if (!pointsSource || !selectionSource) return;
    if (S.indexedFrame !== S.frame) {
      S.indexedFrame = S.frame;
      S.geoPoints = [...(S.frame?.stations.values() || [])].filter(station => station.coordinates).map(station => ({
        type: "Feature", geometry: {type: "Point", coordinates: station.coordinates},
        properties: {id: station.id, bikes: station.bikes, empty_docks: station.empty_docks},
      }));
      S.clusterIndex = window.Supercluster ? new window.Supercluster({
        radius: 55, maxZoom: 12,
        map: props => Object.fromEntries(Object.entries(metrics).flatMap(([key, entry]) => {
          const value = props[entry.field];
          return [[key + "Valid", Number(value !== null)], [key + "Tight", Number(value !== null && value <= S.timeline.tight_max)]];
        })),
        reduce: (total, next) => { for (const key of Object.keys(next)) total[key] += next[key]; },
      }).load(S.geoPoints) : null;
    }
    for (const marker of S.markers) marker.remove();
    S.markers = [];
    const points = [];
    const displayed = S.clusterIndex && map.getZoom() < 13
      ? S.clusterIndex.getClusters([-180, -85, 180, 85], Math.floor(map.getZoom())) : S.geoPoints;
    for (const point of displayed) {
      const props = point.properties;
      if (!props.cluster) {
        const style = tier(props[mode().field]);
        points.push({...point, properties: {...props, color: style.color, radius: style.radius, stale: S.gap}});
        continue;
      }
      const tight = props[S.mode + "Tight"], valid = props[S.mode + "Valid"];
      const element = document.createElement("button");
      element.type = "button";
      element.className = "bike-analysis-cluster" + (tight ? " has-tight" : "") + (S.gap ? " is-stale" : "");
      element.textContent = tight + " / " + valid;
      element.title = `${mode().action}紧张 ${tight} 个，有效 ${valid} 个，共 ${props.point_count} 站。点击展开`;
      element.setAttribute("aria-label", element.title);
      element.addEventListener("click", event => {
        event.stopPropagation();
        map.easeTo({center: point.geometry.coordinates, zoom: S.clusterIndex.getClusterExpansionZoom(props.cluster_id)});
      });
      S.markers.push(new window.maplibregl.Marker({element}).setLngLat(point.geometry.coordinates).addTo(map));
    }
    pointsSource.setData({type: "FeatureCollection", features: points});
    const selected = currentStation(), selection = [];
    if (selected?.coordinates) {
      const style = tier(selected[mode().field]);
      selection.push({type: "Feature", geometry: radiusPolygon(selected.coordinates, S.timeline.radius_m),
          properties: {stale: S.gap}},
        {type: "Feature", geometry: {type: "Point", coordinates: selected.coordinates},
          properties: {id: selected.id, color: style.color, radius: style.radius + 4, stale: S.gap}});
    }
    selectionSource.setData({type: "FeatureCollection", features: selection});
    if (selected?.coordinates && S.focusStation) {
      S.focusStation = false;
      S.mapFitted = true;
      map.easeTo({center: selected.coordinates, zoom: Math.max(14, map.getZoom())});
    } else if (!S.mapFitted && S.geoPoints.length) {
      S.mapFitted = true;
      const bounds = new window.maplibregl.LngLatBounds();
      for (const point of S.geoPoints) bounds.extend(point.geometry.coordinates);
      map.fitBounds(bounds, {padding: 34, maxZoom: 12, duration: 0});
    }
  }

  function chartPoints(value) {
    const data = [];
    let previous = null;
    for (const batch of S.batches) {
      if (previous && batch.ms > expiresAt(previous)) data.push([expiresAt(previous) + 1, null]);
      data.push([batch.ms, ...value(S.points?.get(batch.capturedAt))]);
      previous = batch;
    }
    return data;
  }

  function marker() {
    return {silent: true, symbol: "none", label: {show: false}, lineStyle: {color: "#75e2cd", type: "dashed"},
      data: S.cursor === null ? [] : [{xAxis: S.cursor}]};
  }

  function drawCursor() {
    if (S.view !== "trends" || !S.site) return;
    S.charts.forEach(chart => chart.setOption({series: [{id: "cursor", markLine: marker()}]}));
  }

  function drawCharts() {
    if (!S.visible || S.view !== "trends") return;
    const library = window.echarts;
    const panel = $("bikeTrendCharts"), wasHidden = panel.hidden;
    $("bikeTrendEmpty").hidden = Boolean(S.site);
    panel.hidden = !S.site;
    if (!S.site) return;
    const total = [...(S.points?.values() || [])].filter(point => point.bikes !== null || point.empty_docks !== null).length;
    $("bikeNote").textContent = !library ? "图表组件暂不可用。" : S.stationStatus ||
      (total ? total + " 批有效快照 · 缺测阈值为本地 10 分钟、公开归档 20 分钟" : "该时段没有本站有效库存记录。");
    const valid = [...(S.points?.values() || [])].filter(point => point[S.mode]?.valid).length;
    $("bikePressureNote").textContent = valid ? "仅剩 0–2 " + (S.mode === "borrow" ? "辆车" : "个空位") + "的邻站数 / 有效邻站数"
      : S.stationStatus || "该时段没有可计算的邻站占比。";
    if (!library) return;
    if (!S.charts.length) {
      S.charts = ["bikeChart", "bikePressureChart"].map(id => {
        const element = $(id);
        const chart = library.init(element, null, {renderer: "canvas"});
        viewObserver?.observe(element);
        return chart;
      });
      S.charts.forEach(chart => { chart.group = "bike-analysis"; });
      library.connect("bike-analysis");
    } else if (wasHidden) {
      S.charts.forEach(chart => chart.resize());
    }
    const line = (id, name, color, data) => ({id, name, type: "line", step: "end", showSymbol: false, connectNulls: false,
      lineStyle: {color, width: 2}, itemStyle: {color}, data});
    const base = {
      animation: false, textStyle: {color: "#b7cbd8"},
      grid: {top: 36, left: 54, right: 20, bottom: 33},
      xAxis: {type: "time", min: S.start, max: S.end, axisLabel: {color: "#a6bdca", hideOverlap: true,
        formatter: value => at(value).replace(/^\d{4}\//, "").replace(/ [^ ]+$/, "")}},
    };
    const yAxis = {type: "value", min: 0, minInterval: 1, splitLine: {lineStyle: {color: "#2a4251"}},
      axisLabel: {color: "#a6bdca"}};
    const tooltip = format => ({
      trigger: "axis", backgroundColor: "#172b3b", borderColor: "#426071", textStyle: {color: "#e9f0f8"},
      formatter(items) {
        const entries = items.filter(item => item.value[1] != null);
        if (!entries.length) return "";
        const ms = entries[0].value[0];
        return esc(at(ms)) + " · " + sourceLabel(frameAt(ms)?.source) + "<br>" + entries.map(format).join("<br>");
      },
    });
    S.charts[0].setOption({...base, yAxis,
      legend: {top: 3, selectedMode: false, textStyle: {color: "#b7cbd8"}},
      tooltip: tooltip(item => item.marker + item.seriesName + " " + item.value[1]),
      series: [
        {...line("cursor", "可用车辆", "#63dfc6", chartPoints(point => [point?.bikes ?? null])), markLine: marker()},
        line("docks", "空位", "#e9bc70", chartPoints(point => [point?.empty_docks ?? null])),
      ],
    }, true);
    S.charts[1].setOption({...base, yAxis: {...yAxis, max: 100, axisLabel: {...yAxis.axisLabel, formatter: "{value}%"}},
      tooltip: tooltip(item => {
        const [, percent, tight, count] = item.value;
        return `${percent.toFixed(1)}% · ${tight} / ${count} 个有效邻站`;
      }),
      series: [{...line("cursor", "周边紧张站点占比", "#e9bc70", chartPoints(point => {
        const ratio = point?.[S.mode];
        return ratio?.valid ? [100 * ratio.tight / ratio.valid, ratio.tight, ratio.valid] : [null];
      })), markLine: marker()}],
    }, true);
  }

  function resize() {
    if (!S.visible) return;
    if (S.view === "map") S.map?.resize();
    else if (S.site) S.charts.forEach(chart => chart.resize());
  }

  function showView() {
    const showingMap = S.view === "map";
    $("bikeMapView").hidden = !showingMap;
    $("bikeTrends").hidden = showingMap;
    $("bikeShowMap").setAttribute("aria-pressed", String(showingMap));
    $("bikeShowTrends").setAttribute("aria-pressed", String(!showingMap));
    if (showingMap && !S.mapAttempted) {
      S.mapAttempted = true;
      initMap();
    }
    requestAnimationFrame(() => {
      if (!S.visible) return;
      if (S.view === "trends") drawCharts();
      resize();
      if (S.view === "map") drawMap();
    });
  }

  function changeView(view) {
    if (S.view === view) return;
    stop();
    S.view = view;
    showView();
  }

  $("bikeStation").addEventListener("change", event => selectStation(event.target.value));
  $("bikeApply").addEventListener("click", refresh);
  $("bikeBorrow").addEventListener("click", () => setMode("borrow"));
  $("bikeReturn").addEventListener("click", () => setMode("return"));
  $("bikeShowMap").addEventListener("click", () => changeView("map"));
  $("bikeShowTrends").addEventListener("click", () => changeView("trends"));
  $("bikePrevious").addEventListener("click", () => step(-1));
  $("bikeNext").addEventListener("click", () => step(1));
  $("bikePlay").addEventListener("click", play);
  $("bikeTimeSlider").addEventListener("input", event => {
    stop();
    setCursor(Number(event.target.value) * 1000);
  });
  $("bikeNeighbors").addEventListener("click", event => {
    const button = event.target.closest("button[data-station]");
    if (button) {
      selectStation(button.dataset.station);
      $("bikeStation").focus({preventScroll: true});
    }
  });
  window.addEventListener("resize", resize);
  window.addEventListener("airwatch:timezone", () => {
    if (S.visible && S.timeline) {
      renderRange();
      renderTime();
      drawCharts();
    }
  });
  renderLegend();
  window.AirWatchBikeAnalysis = {
    show() {
      if (S.visible) return;
      S.visible = true;
      showView();
      refresh();
    },
    hide() {
      if (!S.visible) return;
      S.visible = false;
      stop();
    },
    refresh,
    prefill(site) { S.prefill = site; },
  };
})();
