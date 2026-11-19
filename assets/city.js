/* Map and charts share UTC query state and source-specific observations. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id),
    H = window.AirWatchData;
  if (!H) return;
  const { api, time, dateRange, calendarDate } = H;
  const colors = ["#55c7a5", "#9bcf80", "#e9cf6e", "#ed9867", "#e96a72"];
  const sourceNames = {
    laqn: "LAQN",
    breathe: "Breathe London",
    breathe_wcc: "Westminster",
  };
  const S = {
    page: "overview",
    map: null,
    ready: false,
    data: null,
    charts: {},
    selected: null,
    start: null,
    end: null,
    hours: [],
    generation: 0,
    detailGeneration: 0,
    analysisGeneration: 0,
    series: null,
    weather: null,
    playing: false,
    lastRefresh: 0,
    selectedBoundary: null,
    pendingBoundaryFocus: false,
    gisLayer: "laqn",
    gisPage: 1,
    gisSelected: null,
    spatial: {},
    pointGroups: {},
  };
  const esc = (v) =>
    String(v ?? "—").replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
  const GIS_LABELS = {
    region: "大伦敦", borough: "行政区",
    laqn: "LAQN 监测点", breathe: "Breathe London 监测点",
    bikes: "自行车站", roadPoint: "道路点事件", roadArea: "道路面事件",
  };
  const GIS_CONTROLS = {
    region: ["layerBoundary", "layerRegion"], borough: ["layerBoundary", "layerBorough"],
    laqn: ["layerAir", "layerLaqn"], breathe: ["layerAir", "layerBreathe"],
    bikes: ["layerBikes"], roadPoint: ["layerRoads", "layerRoadPoints"],
    roadArea: ["layerRoads", "layerRoadAreas"],
  };
  const GIS_STATIC = {
    region: ["boundaries", "data/london_region_2025.geojson"],
    borough: ["boundaries", "data/london_boroughs_2025.geojson"],
  };
  function gisVisible(layer) {
    return GIS_CONTROLS[layer].every((id) => $(id).checked !== false);
  }
  function gisKey(layer, feature) {
    const p = feature.properties;
    if (layer === "region") return "region:" + p.RGN25CD;
    if (layer === "borough") return "borough:" + p.LAD25CD;
    return layer + ":" + (p.source ? p.source + ":" : "") + p.id;
  }
  async function loadSpatial(layer) {
    const path = GIS_STATIC[layer][1];
    if (!S.spatial[path])
      S.spatial[path] = api("/" + path).catch((e) => {
        delete S.spatial[path];
        throw e;
      });
    return S.spatial[path];
  }
  function gisFeatures(layer) {
    if (GIS_STATIC[layer]) {
      const path = GIS_STATIC[layer][1], data = S.spatial[path + "Data"];
      if (!data) return [];
      return data.features;
    }
    if (!S.data) return [];
    if (layer === "laqn" || layer === "breathe")
      return S.data.air.features.filter((f) => layer === "laqn" ? f.properties.source === "laqn" : f.properties.source !== "laqn");
    if (layer === "bikes") return S.data.bikes.features;
    return S.data.roads.features.filter((f) => layer === "roadPoint" ? f.geometry.type === "Point" : f.geometry.type !== "Point");
  }
  function gisColumns(layer) {
    if (layer === "region" || layer === "borough") return ["名称", "代码", "面积 km²", "周长 km", "类型"];
    if (layer === "laqn" || layer === "breathe") return ["站点", "来源", "类型", "指标值", "观测时间", "数据质量", "位置"];
    if (layer === "bikes") return ["站点", "车辆", "空位", "容量", "快照时间", "位置"];
    return ["事件", "编号", "类别", "严重程度", "状态", "开始时间", "修改时间"];
  }
  function gisValues(layer, f) {
    const p = f.properties, n = (v, digits = 2) => Number.isFinite(Number(v)) && v !== null ? Number(v).toLocaleString("zh-CN", {maximumFractionDigits:digits}) : "—";
    if (layer === "region" || layer === "borough") {
      const region = layer === "region";
      return [p[region ? "RGN25NM" : "LAD25NM"], p[region ? "RGN25CD" : "LAD25CD"], n(p.Shape__Area / 1e6), n(p.Shape__Length / 1000), region ? "大伦敦" : "行政区"];
    }
    if (layer === "laqn" || layer === "breathe") return [p.name, p.source === "breathe_wcc" ? "Breathe London · Westminster 导入" : sourceNames[p.source] || p.source, p.kind, p.value === null ? "缺测" : n(p.value) + (p.unit ? " " + p.unit : ""), time(p.observed_at), p.quality || "—", p.borough || f.geometry.coordinates.map(v => n(v, 4)).join(", ")];
    if (layer === "bikes") return [p.name, n(p.bikes, 0), n(p.empty_docks, 0), n(p.capacity, 0), time(p.captured_at), f.geometry.coordinates.map(v => n(v, 4)).join(", ")];
    return [p.name, p.id, p.category, p.severity, p.status, time(p.starts_at), time(p.modified_at)];
  }
  function renderGisTable() {
    const layer = S.gisLayer, all = gisFeatures(layer), search = $("gisTableSearch").value.trim().toLocaleLowerCase();
    const rows = all.map((feature) => ({feature, key:gisKey(layer, feature), values:gisValues(layer, feature)}))
      .filter((row) => !search || row.values.some((v) => String(v ?? "").toLocaleLowerCase().includes(search)));
    const pages = Math.max(1, Math.ceil(rows.length / 50));
    S.gisPage = Math.min(S.gisPage, pages);
    S.gisPageRows = rows.slice((S.gisPage - 1) * 50, S.gisPage * 50);
    $("gisTableTitle").textContent = GIS_LABELS[layer] + "属性表";
    $("gisTableCount").textContent = "共 " + all.length + " 个要素" + (search ? " · 匹配 " + rows.length + " 个" : "");
    $("gisTablePage").textContent = "第 " + S.gisPage + " / " + pages + " 页";
    $("gisTablePrevious").disabled = S.gisPage <= 1;
    $("gisTableNext").disabled = S.gisPage >= pages;
    $("gisTable").innerHTML = "<table><thead><tr>" + gisColumns(layer).map((v) => "<th scope='col'>" + esc(v) + "</th>").join("") + "</tr></thead><tbody>" +
      (S.gisPageRows.map((row, index) => "<tr data-gis-row='" + index + "' class='" + (S.gisSelected?.key === row.key ? "selected" : "") + "'><td><button type='button' class='gis-row-button'>" + esc(row.values[0]) + "</button></td>" + row.values.slice(1).map((v) => "<td>" + esc(v) + "</td>").join("") + "</tr>").join("") || "<tr><td class='empty' colspan='" + gisColumns(layer).length + "'>" + (GIS_STATIC[layer] && !S.spatial[GIS_STATIC[layer][1] + "Data"] ? "正在加载要素…" : "当前条件下暂无要素") + "</td></tr>") + "</tbody></table>";
    document.querySelectorAll("[data-gis-table]").forEach((b) => b.classList.toggle("active", b.dataset.gisTable === layer));
  }
  async function openGisTable(layer, reset = true) {
    if (!GIS_LABELS[layer]) return;
    const changed = S.gisLayer !== layer;
    S.gisLayer = layer;
    if (reset || changed) {
      S.gisPage = 1;
      $("gisTableSearch").value = "";
    }
    renderGisTable();
    if (GIS_STATIC[layer]) {
      try {
        const path = GIS_STATIC[layer][1], data = await loadSpatial(layer);
        S.spatial[path + "Data"] = data;
        if (S.gisLayer === layer) renderGisTable();
      } catch (e) {
        if (S.gisLayer === layer) $("gisTable").textContent = "属性加载失败：" + e.message;
      }
    }
  }
  function geometryBounds(geometry) {
    const b = [Infinity, Infinity, -Infinity, -Infinity];
    function visit(coords) {
      if (typeof coords[0] === "number") { b[0] = Math.min(b[0], coords[0]); b[1] = Math.min(b[1], coords[1]); b[2] = Math.max(b[2], coords[0]); b[3] = Math.max(b[3], coords[1]); }
      else coords.forEach(visit);
    }
    visit(geometry.coordinates);
    return [[b[0], b[1]], [b[2], b[3]]];
  }
  function highlightGis() {
    if (!S.ready || !S.map.getSource("city-selected")) return;
    S.map.getSource("city-selected").setData({type:"FeatureCollection", features:S.gisSelected ? [S.gisSelected.feature] : []});
  }
  async function chooseGisFeature(layer, feature, focus = true, fromMap = false, lngLat = null) {
    if (GIS_STATIC[layer]) {
      try {
        const path = GIS_STATIC[layer][1];
        S.spatial[path + "Data"] = await loadSpatial(layer);
        feature = gisFeatures(layer).find((f) => gisKey(layer, f) === gisKey(layer, feature)) || feature;
      } catch (_) { /* Rendered geometry still supports selection. */ }
    }
    for (const id of GIS_CONTROLS[layer]) $(id).checked = true;
    S.gisSelected = {layer, key:gisKey(layer, feature), feature};
    await openGisTable(layer, fromMap || S.gisLayer !== layer);
    if (fromMap) {
      const index = gisFeatures(layer).findIndex((f) => gisKey(layer, f) === S.gisSelected.key);
      if (index >= 0) S.gisPage = Math.floor(index / 50) + 1;
    }
    highlightGis();
    renderGisTable();
    if (S.map && focus) {
      if (feature.geometry.type === "Point")
        S.map.easeTo({center:feature.geometry.coordinates, zoom:Math.max(13, S.map.getZoom())});
      else S.map.fitBounds(geometryBounds(feature.geometry), {padding:45, maxZoom:13, duration:600});
    }
    if (["laqn", "breathe", "bikes", "roadPoint", "roadArea"].includes(layer))
      selectFeature(layer === "bikes" ? "bike" : layer.startsWith("road") ? "road" : "air", feature, lngLat, true);
  }
  const zone = () => $("timezone").value;
  const metric = () =>
    ({
      PM25: "PM₂.₅",
      NO2: "NO₂",
      PM10: "PM₁₀",
      O3: "O₃",
      PM25_INDEX: "PM₂.₅ 指数",
      NO2_INDEX: "NO₂ 指数",
    })[$("cityMetric").value];
  function hourKey(v) {
    const d = new Date(v);
    d.setUTCMinutes(0, 0, 0);
    return d.toISOString();
  }
  function error(message) {
    $("cityError").hidden = !message;
    $("cityError").textContent = message || "";
  }
  function query(extra = {}) {
    const p = {
      start: S.start.toISOString(),
      end: S.end.toISOString(),
      metric: $("cityMetric").value,
      period: $("cityPeriod").value,
      ...extra,
    };
    if ($("citySource").value) p.source = $("citySource").value;
    if ($("cityMode").value === "history" && S.hours.length) {
      const ms = S.hours[Number($("replayHour").value)];
      p.cursor = new Date(
        Math.min(ms + 3599000, S.end.getTime() - 1000),
      ).toISOString();
    }
    return p;
  }
  function resetWindow() {
    const now = new Date();
    if ($("cityRange").value === "custom") {
      const range = dateRange($("cityStart").value, $("cityEnd").value, zone());
      S.start = new Date(range.start);
      S.end = new Date(Math.min(Date.parse(range.end), now.getTime()));
    } else {
      S.end = now;
      S.start = new Date(
        now.getTime() - Number($("cityRange").value) * 3600000,
      );
    }
    if (!(S.start < S.end)) throw new Error("请选择早于当前时刻的日期范围");
    S.hours = [];
    for (
      let ms = Date.parse(hourKey(S.start));
      ms < S.end.getTime();
      ms += 3600000
    )
      S.hours.push(ms);
    $("replayHour").max = Math.max(0, S.hours.length - 1);
    $("replayHour").value = Math.max(0, S.hours.length - 2);
    replayLabel();
  }
  function replayLabel() {
    $("replayControls").hidden = $("cityMode").value !== "history";
    if (S.hours.length)
      $("replayLabel").textContent =
        time(new Date(S.hours[Number($("replayHour").value)]).toISOString()) +
        " 小时";
  }
  function showAnalysisSection(section) {
    S.analysisSection = section;
    Array.from(document.querySelectorAll("[data-analysis-section]"))
      .filter(node => node.dataset?.analysisSection)
      .forEach(node => { node.hidden = node.dataset.analysisSection !== section; });
    Array.from(document.querySelectorAll("[data-analysis-tab]"))
      .filter(node => node.dataset?.analysisTab)
      .forEach(node => node.classList.toggle("active", node.dataset.analysisTab === section));
    requestAnimationFrame(() => Object.values(S.charts).forEach(chart => chart.resize()));
  }
  function showPage(page) {
    const previousPage = S.page;
    S.page = page;
    $("cityOverview").hidden = page !== "overview";
    if ($("journeyPage")) $("journeyPage").hidden = page !== "journey";
    $("cityAnalysis").hidden = page !== "analysis";
    $("tablesPage").hidden = page !== "tables";
    $("cityControls").hidden = page === "tables" || page === "journey";
    const mapNode = $("cityMap"), journeyHost = $("journeyMapHost");
    if (page === "journey" && journeyHost?.append && mapNode?.parentElement !== journeyHost)
      journeyHost.append(mapNode);
    else if (page === "overview" && S.overviewMapHost?.append && mapNode?.parentElement !== S.overviewMapHost)
      S.overviewMapHost.append(mapNode);
    document.querySelectorAll("[data-page]").forEach((b) => {
      const active = b.dataset.page === page;
      b.classList.toggle("active", active);
      if (active) b.setAttribute("aria-current", "page");
      else b.removeAttribute("aria-current");
    });
    requestAnimationFrame(() => {
      if (S.map) {
        S.map.resize();
        if (page === "overview" && previousPage === "journey" && !S.selectedBoundary)
          fitLondonView();
      }
      Object.values(S.charts).forEach((c) => c.resize());
    });
    if (page === "analysis") {
      showAnalysisSection(S.analysisSection || "series");
      loadAnalysis();
    }
    if (page === "journey" && $("cityMode").value !== "latest") {
      $("cityMode").value = "latest";
      refreshCity(true).catch((e) => error(e.message));
    }
    if (page === "overview" && previousPage === "tables")
      refreshCity($("cityMode").value === "latest").catch((e) =>
        error(e.message),
      );
  }
  function fitLondonView() {
    if (!S.map || !S.ready) return;
    S.map.resize();
    S.map.fitBounds([[-0.51025, 51.28678], [0.33404, 51.69189]], {
      padding: {top: 36, bottom: 36, left: 40, right: 40}, maxZoom: 11.2, duration: 0,
    });
  }
  function fitInitialMap() {
    if (S.initialMapFitted || !S.map || !S.ready || !S.data || S.selectedBoundary) return;
    S.initialMapFitted = true;
    requestAnimationFrame(fitLondonView);
  }
  function initMap() {
    if (!window.maplibregl) {
      $("mapNotice").textContent = "地图组件未加载，图表与表格仍可查询。";
      return;
    }
    try {
      S.map = new maplibregl.Map({
        container: "cityMap",
        style: "https://tiles.openfreemap.org/styles/dark",
        center: [-0.12, 51.51],
        zoom: 10,
        minZoom: 7,
        maxZoom: 17,
        attributionControl: false,
      });
      S.map.addControl(
        new maplibregl.NavigationControl({ showCompass: false }),
        "top-right",
      );
      S.map.addControl(
        new maplibregl.AttributionControl({
          compact: true,
          customAttribution: "OpenFreeMap · © OpenMapTiles · © OpenStreetMap",
        }),
        "bottom-right",
      );
      S.map.on("style.load", () => {
        S.ready = true;
        installLayers();
        installBoundaryLayers();
        drawLayers();
        legend();
        $("mapNotice").textContent = "底图已加载 · ○ LAQN · ◇ Breathe London";
        fitInitialMap();
        if (S.pendingBoundaryFocus && S.selectedBoundary) {
          S.pendingBoundaryFocus = false;
          requestAnimationFrame(() => focusBoundary(S.selectedBoundary));
        }
      });
      S.map.on("error", () => {
        $("mapNotice").textContent =
          "部分底图资源暂不可用，业务数据和图表仍可查询。";
      });
      S.map.on("moveend", () => { drawBikes(); drawPointClusters(); });
    } catch (e) {
      $("mapNotice").textContent =
        "当前浏览器无法初始化地图，图表和表格仍可使用。";
    }
  }
  function boundaryColors() {
    return $("basemap").value === "positron"
      ? { outer: "#8b5700", borough: "#416578" }
      : { outer: "#f5cf72", borough: "#96bdcf" };
  }
  function selectedBoundaryFilter(level) {
    const field = level === "region" ? "RGN25CD" : "LAD25CD";
    const code = S.selectedBoundary?.level === level ? S.selectedBoundary.code : "";
    return ["==", ["get", field], code];
  }
  function focusBoundary(selected) {
    if (!S.map || !S.ready) return;
    S.map.resize();
    S.map.fitBounds(selected.bounds, {
      padding: 45, maxZoom: selected.level === "region" ? 10 : 12, duration: 600,
    });
  }
  function positionBoundaryLayers() {
    if (!S.map.getLayer("bike-clusters")) return;
    for (const id of ["london-region-fill", "london-borough-fill", "london-borough-boundaries", "london-outer-boundary", "london-selected-region", "london-selected-borough"])
      if (S.map.getLayer(id)) S.map.moveLayer(id, "bike-clusters");
  }
  function installBoundaryLayers() {
    if (!S.ready) return;
    const m = S.map, colors = boundaryColors();
    for (const [id, file] of [
      ["london-region", "data/london_region_2025.geojson"],
      ["london-boroughs", "data/london_boroughs_2025.geojson"],
    ])
      if (!m.getSource(id)) m.addSource(id, { type: "geojson", data: file });
    const before = m.getLayer("bike-clusters") ? "bike-clusters" : undefined;
    if (!m.getLayer("london-region-fill")) m.addLayer({
      id:"london-region-fill", type:"fill", source:"london-region",
      paint:{"fill-color":colors.outer,"fill-opacity":0.035},
    }, before);
    if (!m.getLayer("london-borough-fill")) m.addLayer({
      id:"london-borough-fill", type:"fill", source:"london-boroughs",
      paint:{"fill-color":colors.borough,"fill-opacity":0.055},
    }, before);
    if (!m.getLayer("london-borough-boundaries"))
      m.addLayer({
        id: "london-borough-boundaries",
        type: "line",
        source: "london-boroughs",
        paint: { "line-color": colors.borough, "line-width": 1, "line-opacity": 0.65 },
      }, before);
    if (!m.getLayer("london-outer-boundary"))
      m.addLayer({
        id: "london-outer-boundary",
        type: "line",
        source: "london-region",
        paint: { "line-color": colors.outer, "line-width": 3, "line-opacity": 0.95 },
      }, before);
    for (const [level, source] of [["region", "london-region"], ["borough", "london-boroughs"]]) {
      const id = "london-selected-" + level;
      if (!m.getLayer(id)) m.addLayer({
        id, type: "line", source, filter: selectedBoundaryFilter(level),
        paint: {
          "line-color": $("basemap").value === "positron" ? "#b12645" : "#ff8e71",
          "line-width": 5, "line-opacity": 0.98,
        },
      }, before);
    }
    positionBoundaryLayers();
    visibility();
  }
  function installLayers() {
    const m = S.map,
      empty = { type: "FeatureCollection", features: [] };
    for (const id of ["air", "bikes", "roads"])
      m.addSource("city-" + id, {
        type: "geojson",
        data: empty,
      });
    m.addSource("city-selected", {type:"geojson", data:empty});
    const roadColor = [
      "match",
      ["get", "severity"],
      "Serious",
      "#ef7b84",
      "Severe",
      "#ef7b84",
      "Moderate",
      "#e9bc70",
      "Minimal",
      "#8bbddd",
      "#98aabd",
    ];
    m.addLayer({
      id: "road-fill",
      type: "fill",
      source: "city-roads",
      filter: ["!=", ["geometry-type"], "Point"],
      paint: { "fill-color": roadColor, "fill-opacity": 0.2 },
    });
    m.addLayer({
      id: "road-line",
      type: "line",
      source: "city-roads",
      filter: ["!=", ["geometry-type"], "Point"],
      paint: { "line-color": roadColor, "line-width": 2 },
    });
    m.addLayer({
      id: "road-point",
      type: "circle",
      source: "city-roads",
      filter: ["==", ["geometry-type"], "Point"],
      paint: {
        "circle-color": roadColor,
        "circle-radius": 5,
        "circle-stroke-width": 1,
        "circle-stroke-color": "#172737",
      },
    });
    m.addLayer({
      id: "bike-clusters",
      type: "circle",
      source: "city-bikes",
      filter: ["has", "point_count"],
      paint: {
        "circle-color": "#497da0",
        "circle-radius": ["step", ["get", "point_count"], 15, 20, 20, 100, 25],
        "circle-opacity": 0.85,
        "circle-stroke-width": 1.5,
        "circle-stroke-color": "#d9f4fc",
      },
    });
    m.addLayer({
      id: "bike-cluster-label",
      type: "symbol",
      source: "city-bikes",
      filter: ["has", "point_count"],
      layout: {
        "text-field": ["get", "point_count_abbreviated"],
        "text-size": 11,
        "text-allow-overlap": true,
      },
      paint: { "text-color": "#fff" },
    });
    m.addLayer({
      id: "bike-points",
      type: "circle",
      source: "city-bikes",
      filter: ["!", ["has", "point_count"]],
      paint: {
        "circle-color": [
          "match",
          ["get", "state"],
          "empty",
          "#ee9a69",
          "full",
          "#bf92df",
          "available",
          "#58b9d1",
          "#7b8c9c",
        ],
        "circle-radius": [
          "interpolate",
          ["linear"],
          ["coalesce", ["get", "bikes"], 0],
          0,
          4,
          20,
          8,
          60,
          12,
        ],
        "circle-opacity": 0.8,
        "circle-stroke-width": 0.8,
        "circle-stroke-color": "#0d2432",
      },
    });
    m.addLayer({
      id: "air-laqn",
      type: "circle",
      source: "city-air",
      filter: ["==", ["get", "source"], "laqn"],
      paint: {
        "circle-color": ["get", "color"],
        "circle-radius": 7,
        "circle-stroke-width": 1.4,
        "circle-stroke-color": "#f0f7ff",
      },
    });
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 32;
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#fff";
    ctx.beginPath();
    ctx.moveTo(16, 2);
    ctx.lineTo(30, 16);
    ctx.lineTo(16, 30);
    ctx.lineTo(2, 16);
    ctx.closePath();
    ctx.fill();
    m.addImage("sensor-diamond", ctx.getImageData(0, 0, 32, 32), { sdf: true });
    m.addLayer({
      id: "air-breathe",
      type: "symbol",
      source: "city-air",
      filter: ["!=", ["get", "source"], "laqn"],
      layout: {
        "icon-image": "sensor-diamond",
        "icon-size": 0.6,
        "icon-allow-overlap": true,
      },
      paint: {
        "icon-color": ["get", "color"],
        "icon-halo-width": 1,
        "icon-halo-color": "#f0f7ff",
      },
    });
    m.addLayer({
      id: "air-values",
      type: "symbol",
      source: "city-air",
      minzoom: 13,
      layout: {
        "text-field": [
          "case",
          ["==", ["get", "value"], null],
          "—",
          ["to-string", ["get", "value"]],
        ],
        "text-size": 11,
        "text-offset": [0, 1.4],
      },
      paint: {
        "text-color": "#ecf4fb",
        "text-halo-color": "#102436",
        "text-halo-width": 1.5,
      },
    });
    m.addLayer({id:"city-selected-fill", type:"fill", source:"city-selected",
      filter:["in",["geometry-type"],["literal",["Polygon","MultiPolygon"]]],
      paint:{"fill-color":"#ffe174","fill-opacity":0.28}});
    m.addLayer({id:"city-selected-line", type:"line", source:"city-selected",
      filter:["!=",["geometry-type"],"Point"],
      paint:{"line-color":"#ffe174","line-width":5,"line-opacity":0.95}});
    m.addLayer({id:"city-selected-point", type:"circle", source:"city-selected",
      filter:["==",["geometry-type"],"Point"],
      paint:{"circle-color":"#ffe174","circle-radius":11,"circle-opacity":0.2,"circle-stroke-color":"#ffe174","circle-stroke-width":3}});
    highlightGis();
    positionBoundaryLayers();
    if (S.mapEventsBound) {
      visibility();
      return;
    }
    S.mapEventsBound = true;
    for (const layer of [
      "air-laqn",
      "air-breathe",
      "bike-points",
      "road-point",
      "road-fill",
      "road-line",
    ]) {
      m.on("click", layer, (event) => {
        if ($("journeyPage")?.dataset?.picking) return;
        if (event.originalEvent?.gisHandled) return;
        if (event.originalEvent) event.originalEvent.gisHandled = true;
        const point = event.features[0],
          kind = layer.startsWith("air")
            ? "air"
            : layer.startsWith("bike")
              ? "bike"
              : "road";
        const bucket =
          kind === "air" ? "air" : kind === "bike" ? "bikes" : "roads";
        const f = S.data[bucket].features.find(
          (f) =>
            f.properties.id === point.properties.id &&
            (kind !== "air" || f.properties.source === point.properties.source),
        );
        if (f) chooseGisFeature(kind === "air" ? (f.properties.source === "laqn" ? "laqn" : "breathe") : kind === "bike" ? "bikes" : f.geometry.type === "Point" ? "roadPoint" : "roadArea", f, false, true, event.lngLat);
      });
      m.on("mouseenter", layer, () => (m.getCanvas().style.cursor = "pointer"));
      m.on("mouseleave", layer, () => (m.getCanvas().style.cursor = ""));
    }
    m.on("click", "bike-clusters", async (event) => {
      if ($("journeyPage")?.dataset?.picking) return;
      if (!S.bikeIndex) return;
      const f = event.features[0],
        zoom = S.bikeIndex.getClusterExpansionZoom(f.properties.cluster_id);
      m.easeTo({ center: f.geometry.coordinates, zoom });
    });
    m.on("mouseenter", "bike-clusters", () => (m.getCanvas().style.cursor = "pointer"));
    m.on("mouseleave", "bike-clusters", () => (m.getCanvas().style.cursor = ""));
    for (const [layer, gisLayer] of [["london-borough-fill","borough"],["london-region-fill","region"]]) {
      m.on("click", layer, (event) => {
        if ($("journeyPage")?.dataset?.picking) return;
        if (event.originalEvent?.gisHandled) return;
        if (event.originalEvent) event.originalEvent.gisHandled = true;
        if (event.features?.[0]) chooseGisFeature(gisLayer, event.features[0], false, true);
      });
      m.on("mouseenter", layer, () => (m.getCanvas().style.cursor = "pointer"));
      m.on("mouseleave", layer, () => (m.getCanvas().style.cursor = ""));
    }
    visibility();
  }
  function color(f) {
    const p = f.properties;
    if (p.value === null) return "#758290";
    if ($("mapColor").value === "freshness")
      return p.stale ? "#d6946c" : "#63dfc6";
    if ($("cityMetric").value.endsWith("_INDEX"))
      return ["#55c7a5", "#e9cf6e", "#ed9867", "#c37be5"][
        p.value <= 3 ? 0 : p.value <= 6 ? 1 : p.value <= 9 ? 2 : 3
      ];
    return colors[S.data.breaks.filter((b) => p.value > b).length];
  }
  function drawLayers() {
    if (!S.ready || !S.data || !S.map.getSource("city-air")) return;
    S.airDrawData = {
      ...S.data.air,
      features: S.data.air.features.map((f) => ({
        ...f,
        properties: { ...f.properties, color: color(f) },
      })),
    };
    S.map.getSource("city-air").setData(S.airDrawData);
    drawBikes();
    S.map.getSource("city-roads").setData(S.data.roads);
    drawPointClusters();
    visibility();
  }
  function drawBikes() {
    if (!S.ready || !S.data || !S.map.getSource("city-bikes")) return;
    // Keep raw stations in the GL source. At overview zooms use accessible DOM
    // cluster markers, avoiding the grouped-GeoJSON rendering failure.
    if (S.bikeData !== S.data.bikes) {
      S.bikeData = S.data.bikes;
      S.bikeIndex = window.Supercluster
        ? new window.Supercluster({ radius: 42, maxZoom: 12 }).load(S.bikeData.features)
        : null;
    }
    S.map.getSource("city-bikes").setData(S.bikeData);
    for (const item of S.bikeMarkers || []) item.marker.remove();
    S.bikeMarkers = [];
    if (S.bikeIndex && S.map.getZoom() < 13) {
      const features = S.bikeIndex.getClusters([-180, -85.051129, 180, 85.051129], Math.floor(S.map.getZoom()));
      for (const feature of features) {
        const p = feature.properties, element = document.createElement("button");
        element.type = "button";
        element.className = p.cluster ? "bike-map-cluster" : "bike-map-station";
        element.textContent = p.cluster ? String(p.point_count_abbreviated) : "";
        element.title = p.cluster ? p.point_count + " 个自行车站，点击展开" : p.name;
        element.setAttribute("aria-label", element.title);
        if (!p.cluster) {
          element.style.background = ({empty:"#ee9a69",full:"#bf92df",available:"#58b9d1"})[p.state] || "#7b8c9c";
        }
        element.addEventListener("click", (event) => {
          if ($("journeyPage")?.dataset?.picking) return;
          event.stopPropagation();
          if (p.cluster) {
            S.map.easeTo({center:feature.geometry.coordinates,zoom:S.bikeIndex.getClusterExpansionZoom(p.cluster_id)});
          } else chooseGisFeature("bikes", feature, false, true);
        });
        const marker = new maplibregl.Marker({element}).setLngLat(feature.geometry.coordinates).addTo(S.map);
        S.bikeMarkers.push({marker,element,feature});
      }
    }
    visibility();
  }
  function drawPointClusters() {
    if (!S.ready || !S.data || !S.map || !window.maplibregl) return;
    for (const layer of ["laqn", "breathe", "roadPoint"]) {
      const group = S.pointGroups[layer] || (S.pointGroups[layer] = {markers:[]});
      const token = layer === "roadPoint" ? S.data.roads : S.airDrawData;
      if (group.token !== token) {
        group.token = token;
        const features = layer === "roadPoint"
          ? S.data.roads.features.filter((f) => f.geometry.type === "Point")
          : (S.airDrawData?.features || []).filter((f) => layer === "laqn" ? f.properties.source === "laqn" : f.properties.source !== "laqn");
        group.index = window.Supercluster ? new window.Supercluster({radius:42,maxZoom:12}).load(features) : null;
      }
      for (const item of group.markers) item.marker.remove();
      group.markers = [];
      if (!group.index || S.map.getZoom() >= 13) continue;
      const features = group.index.getClusters([-180,-85.051129,180,85.051129], Math.floor(S.map.getZoom()));
      for (const feature of features) {
        const p = feature.properties, element = document.createElement("button");
        element.type = "button";
        element.className = (p.cluster ? "gis-map-cluster " : "gis-map-point ") + layer;
        element.textContent = p.cluster ? String(p.point_count_abbreviated) : "";
        element.title = p.cluster ? p.point_count + " 个" + GIS_LABELS[layer] + "，点击展开" : p.name;
        element.setAttribute("aria-label", element.title);
        if (!p.cluster) element.style.background = layer === "roadPoint" ? ({Serious:"#ef7b84",Severe:"#ef7b84",Moderate:"#e9bc70",Minimal:"#8bbddd"})[p.severity] || "#98aabd" : p.color || "#758290";
        element.addEventListener("click", (event) => {
          if ($("journeyPage")?.dataset?.picking) return;
          event.stopPropagation();
          if (p.cluster) S.map.easeTo({center:feature.geometry.coordinates, zoom:group.index.getClusterExpansionZoom(p.cluster_id)});
          else chooseGisFeature(layer, feature, false, true);
        });
        const marker = new maplibregl.Marker({element}).setLngLat(feature.geometry.coordinates).addTo(S.map);
        group.markers.push({marker,element,feature});
      }
    }
    visibility();
  }
  function visibility() {
    if (!S.ready) return;
    const low = (S.map.getZoom?.() ?? 10) < 13;
    const flags = {
      "london-region-fill":gisVisible("region"), "london-outer-boundary":gisVisible("region"), "london-selected-region":gisVisible("region"),
      "london-borough-fill":gisVisible("borough"), "london-borough-boundaries":gisVisible("borough"), "london-selected-borough":gisVisible("borough"),
      "air-laqn":gisVisible("laqn") && !(low && S.pointGroups.laqn?.index),
      "air-breathe":gisVisible("breathe") && !(low && S.pointGroups.breathe?.index),
      "air-values":(gisVisible("laqn") || gisVisible("breathe")) && !(low && (S.pointGroups.laqn?.index || S.pointGroups.breathe?.index)),
      "bike-clusters":gisVisible("bikes"), "bike-cluster-label":gisVisible("bikes"),
      "bike-points":gisVisible("bikes") && !(low && S.bikeIndex),
      "road-point":gisVisible("roadPoint") && !(low && S.pointGroups.roadPoint?.index),
      "road-fill":gisVisible("roadArea"), "road-line":gisVisible("roadArea"),
    };
    for (const [layer, show] of Object.entries(flags))
      if (S.map.getLayer(layer)) S.map.setLayoutProperty(layer, "visibility", show ? "visible" : "none");
    if (S.map.getLayer("air-values") && S.map.setFilter)
      S.map.setFilter("air-values", ["in", ["get", "source"], ["literal", ["laqn", "breathe", "breathe_wcc"].filter((v) => gisVisible(v === "laqn" ? "laqn" : "breathe"))]]);
    for (const item of S.bikeMarkers || []) item.element.hidden = !gisVisible("bikes");
    for (const [layer, group] of Object.entries(S.pointGroups))
      for (const item of group.markers) item.element.hidden = !gisVisible(layer);
  }
  function legend() {
    const boundary = boundaryColors();
    const parts = [];
    if (gisVisible("region")) parts.push("<span><i class='boundary-key' style='border-top:3px solid " + boundary.outer + "'></i>大伦敦外边界</span>");
    if (gisVisible("borough")) parts.push("<span><i class='boundary-key' style='border-top:1px solid " + boundary.borough + "'></i>行政区边界</span>");
    if (!S.data) {
      $("mapLegend").innerHTML = parts.join("");
      return;
    }
    let entries;
    if ($("mapColor").value === "freshness")
      entries = [
        ["#63dfc6", "3 小时内"],
        ["#d6946c", "超过 3 小时"],
        ["#758290", "缺测"],
      ];
    else if ($("cityMetric").value.endsWith("_INDEX"))
      entries = [
        ["#55c7a5", "低 1–3"],
        ["#e9cf6e", "中 4–6"],
        ["#ed9867", "高 7–9"],
        ["#c37be5", "很高 10"],
      ];
    else {
      const b = S.data.breaks;
      entries = b.length
        ? [
            ...b.map((v, i) => [
              colors[i],
              (i ? "> " + b[i - 1] + " 至 " : "≤ ") + v,
            ]),
            [colors[b.length], "> " + b[b.length - 1]],
          ]
        : [["#758290", "暂无有效浓度"]];
    }
    if (gisVisible("laqn") || gisVisible("breathe")) parts.push(...entries
        .map(
          ([c, t]) =>
            "<span><i style='background:" + c + "'></i>" + esc(t) + "</span>",
        ));
    if (gisVisible("laqn") || gisVisible("breathe")) parts.push($("cityMetric").value.endsWith("_INDEX")
        ? "<span>来源空气质量指数</span>"
        : "<span>浓度 µg/m³ · 固定于所选时间范围</span>");
    if (gisVisible("laqn")) parts.push("<span>○ LAQN · 聚合圈数字为站点数</span>");
    if (gisVisible("breathe")) parts.push("<span>◇ Breathe London · 聚合圈数字为站点数</span>");
    if (gisVisible("bikes")) parts.push("<span><i style='background:#286f96'></i>聚合圈数字 = 自行车站数</span><span><i style='background:#ee9a69'></i>空站</span><span><i style='background:#bf92df'></i>满站</span><span><i style='background:#58b9d1'></i>可用</span>");
    if (gisVisible("roadPoint")) parts.push("<span><i style='background:#a76558'></i>道路点事件 · 聚合圈数字为事件数</span>");
    if (gisVisible("roadArea")) parts.push("<span><i style='background:#e9bc70'></i>道路面事件 · 颜色表示严重程度</span>");
    $("mapLegend").innerHTML = parts.join("");
  }
  function summaries() {
    const d = S.data;
    $("cityKpis").innerHTML = [
      [
        "有值空气监测点",
        d.coverage.valid + " / " + d.coverage.total,
        "其中 " + d.coverage.fresh + " 站在最近 3 小时内",
      ],
      ["自行车资源", d.counts.bikes, "同一份成功列表中的自行车站"],
      [
        "空站 / 满站",
        d.counts.empty + " / " + d.counts.full,
        "按可用车辆和空位统计",
      ],
      ["道路事件", d.counts.roads, "成功获取的事件列表"],
    ]
      .map(
        ([label, value, note]) =>
          "<div class='city-kpi'><span>" +
          esc(label) +
          "</span><strong>" +
          esc(value) +
          "</strong><small>" +
          esc(note) +
          "</small></div>",
      )
      .join("");
    $("mapWindow").textContent =
      time(d.start) +
      " 至 " +
      time(d.end) +
      ($("cityMode").value === "history"
        ? " · 统一小时回放"
        : " · 各点保留来源时间");
    $("coverageSummary").innerHTML =
      Object.entries(d.source_stats)
        .map(
          ([source, stats]) =>
            "<span><strong>" +
            esc(sourceNames[source]) +
            "</strong> · " +
            stats.valid +
            " 个有值监测点" +
            (stats.mean === null
              ? ""
              : " · 监测点均值 " + stats.mean.toFixed(2)) +
            "</span>",
        )
        .join("") +
      "<span>自行车快照 " +
      esc(time(d.bike_info.captured_at)) +
      (d.bike_info.gap ? " · 间断超过 10 分钟" : "") +
      (d.bike_info.stale ? " · 快照已过期" : "") +
      "</span>" +
      "<span>道路列表 " +
      esc(time(d.road_info.captured_at)) +
      (d.road_info.gap ? " · 间断超过 10 分钟" : "") +
      (d.road_info.stale ? " · 列表已过期" : "") +
      "</span>";
    const old = $("quickStation").value;
    $("quickStation").innerHTML = d.air.features
      .map(
        (f) =>
          "<option value='" +
          esc(f.properties.source + ":" + f.properties.id) +
          "'>" +
          esc(
            sourceNames[f.properties.source] +
              " · " +
              f.properties.name +
              " · " +
              f.properties.id,
          ) +
          "</option>",
      )
      .join("");
    if ([...$("quickStation").options].some((o) => o.value === old))
      $("quickStation").value = old;
    const bike = $("bikeStation").value;
    $("bikeStation").innerHTML =
      "<option value=''>选择自行车站</option>" +
      d.bikes.features
        .map(
          (f) =>
            "<option value='" +
            esc(f.properties.id) +
            "'>" +
            esc(f.properties.name) +
            "</option>",
        )
        .join("");
    if ([...$("bikeStation").options].some((o) => o.value === bike))
      $("bikeStation").value = bike;
    legend();
    fitInitialMap();
  }
  async function refreshCity(reset = false) {
    if (location.protocol === "file:") return;
    if (reset || !S.start) resetWindow();
    const generation = ++S.generation;
    error("");
    try {
      const d = await api("/api/map", query({ bbox: "-.55,51.28,.34,51.72" }));
      if (generation !== S.generation) return;
      S.data = d;
      if (S.gisSelected && !GIS_STATIC[S.gisSelected.layer]) {
        const selected = S.gisSelected;
        const replacement = gisFeatures(selected.layer).find((f) => gisKey(selected.layer, f) === selected.key);
        S.gisSelected = replacement ? {...selected, feature:replacement} : null;
      }
      if (S.popup) {
        S.popup.remove();
        S.popup = null;
      }
      S.lastRefresh = Date.now();
      summaries();
      drawLayers();
      highlightGis();
      renderGisTable();
      if (!S.selected) {
        const f =
          d.air.features.find(
            (f) => f.properties.id === "BL0027" && f.properties.value !== null,
          ) || d.air.features.find((f) => f.properties.value !== null);
        if (f) await selectFeature("air", f, null, false);
      } else {
        const bucket =
            S.selected.kind === "air"
              ? "air"
              : S.selected.kind === "bike"
                ? "bikes"
                : "roads",
          p = S.selected.feature.properties;
        const f = d[bucket].features.find(
          (f) =>
            f.properties.id === p.id &&
            (bucket !== "air" || f.properties.source === p.source),
        );
        if (f) await selectFeature(S.selected.kind, f, null, false);
        else {
          S.selected = null;
          $("openSelected").disabled = $("analyseSelected").disabled = true;
          $("linkTitle").textContent = "周边环境与出行";
          $("linkSubtitle").textContent = "点击地图中的站点或道路事件";
          $("nearbyContent").innerHTML =
            "<p class='muted'>所选对象在该时刻没有有效记录，请选择其他对象。</p>";
          $("citizenCard").textContent = "";
          chart("miniTrend").clear();
        }
      }
      if (S.page === "analysis") await loadAnalysis();
    } catch (e) {
      if (generation === S.generation) error(e.message);
    }
  }
  function chart(id) {
    if (!S.charts[id])
      S.charts[id] = echarts.init($(id), null, { renderer: "canvas" });
    return S.charts[id];
  }
  function base(extra = {}) {
    return {
      animation: false,
      color: ["#63dfc6", "#e9bc70", "#9ea7ef", "#e58291"],
      backgroundColor: "transparent",
      textStyle: {
        color: "#a8bfce",
        fontFamily: "Inter, Segoe UI, sans-serif",
        fontSize: 13,
      },
      tooltip: {
        trigger: "axis",
        confine: true,
        backgroundColor: "#122638",
        borderColor: "#3b5a6d",
        textStyle: { color: "#e5edf5", fontSize: 14 },
      },
      grid: { left: 60, right: 25, top: 45, bottom: 65 },
      xAxis: {
        type: "time",
        axisLabel: {
          formatter: (value) =>
            time(new Date(value).toISOString()).slice(5, 16),
          color: "#94a8ba", fontSize: 13,
        },
        splitLine: { show: false },
      },
      yAxis: {
        type: "value",
        splitLine: { lineStyle: { color: "#283d4c" } },
        axisLabel: { color: "#94a8ba", fontSize: 13 },
      },
      ...extra,
    };
  }
  function pointsWithGaps(points, interval = 3600000) {
    const values = new Map(
        points.map((p) => [Date.parse(p.time), p.value_num]),
      ),
      output = [];
    const start =
      interval === 3600000
        ? Date.parse(hourKey(S.start))
        : Math.ceil(S.start.getTime() / interval) * interval;
    for (let ms = start; ms < S.end.getTime(); ms += interval)
      output.push([ms, values.get(ms) ?? null]);
    return output;
  }
  function lines(stations, interval = 3600000) {
    return stations.map((station) => ({
      name: station.name,
      type: "line",
      symbol: "none",
      connectNulls: false,
      lineStyle: { width: 2 },
      data: pointsWithGaps(station.points, interval),
    }));
  }
  function popup(kind, f, lngLat) {
    if (!S.map || !S.ready) return;
    if (S.popup) S.popup.remove();
    const p = f.properties,
      content = document.createElement("div"),
      heading = document.createElement("h3"),
      text = document.createElement("div");
    heading.className = "map-popup-name";
    heading.textContent = p.name;
    content.append(heading);
    text.className = "map-popup-text";
    text.style.whiteSpace = "pre-line";
    if (kind === "air")
      text.textContent =
        sourceNames[p.source] +
        " · " +
        p.kind +
        "\n" +
        metric() +
        " " +
        (p.value === null
          ? "缺测"
          : p.value + " " + (p.unit || "") + " · " + p.quality) +
        "\n观测 " +
        time(p.observed_at) +
        (p.age_minutes === null
          ? ""
          : " · 相隔 " +
            p.age_minutes +
            " 分钟" +
            (p.stale ? " · 已过期" : "")) +
        "\n时长 " +
        (p.period_seconds / 60 || "—") +
        " 分钟 · 获取 " +
        time(p.fetched_at);
    if (kind === "bike")
      text.textContent =
        "车辆 " +
        (p.bikes ?? "—") +
        " · 空位 " +
        (p.empty_docks ?? "—") +
        "\n获取 " +
        time(p.captured_at) +
        "\n来源修改 " +
        time(p.source_modified);
    if (kind === "road")
      text.textContent = [
        p.category,
        p.severity,
        p.status,
        "修改 " + time(p.modified_at),
        "列表 " + time(p.captured_at),
      ]
        .filter(Boolean)
        .join("\n");
    content.append(text);
    const button = document.createElement("button");
    button.textContent = "查看元数据与原始记录";
    button.className = "map-popup-button";
    button.addEventListener("click", openSelected);
    content.append(button);
    const nearbyButton = document.createElement("button");
    nearbyButton.textContent = "周边详情";
    nearbyButton.className = "map-popup-button";
    nearbyButton.addEventListener("click", () => $("linkageDialog")?.showModal?.());
    content.append(nearbyButton);
    const bounds = f.geometry.type === "Point" ? null : geometryBounds(f.geometry);
    S.popup = new maplibregl.Popup({ maxWidth: "310px" })
      .setLngLat(lngLat || (bounds ? [(bounds[0][0] + bounds[1][0]) / 2, (bounds[0][1] + bounds[1][1]) / 2] : f.geometry.coordinates))
      .setDOMContent(content)
      .addTo(S.map);
  }
  async function selectFeature(kind, f, lngLat = null, withPopup = true) {
    const previous = S.selected;
    S.selected = { kind, feature: f };
    const p = f.properties;
    $("linkTitle").textContent = p.name;
    $("linkSubtitle").textContent =
      kind === "air"
        ? sourceNames[p.source] + " · " + p.id + " · " + p.kind
        : p.id + " · " + (kind === "bike" ? "自行车停靠站" : "道路事件");
    $("openSelected").disabled = $("analyseSelected").disabled = false;
    if (
      kind === "air" &&
      (!previous ||
        previous.feature.properties.id !== p.id ||
        previous.feature.properties.source !== p.source)
    ) {
      $("analysisSource").value = p.source;
      $("compareStations").innerHTML = "";
    }
    if (kind === "bike") $("bikeStation").value = p.id;
    if (withPopup) popup(kind, f, lngLat);
    await loadNearby();
  }
  function populateStations(options, selected) {
    $("compareStations").innerHTML = options
      .map(
        (o) =>
          "<option value='" +
          esc(o.id) +
          "'>" +
          esc(o.name + " · " + o.id + " · " + (o.kind || "类型未提供")) +
          "</option>",
      )
      .join("");
    for (const option of $("compareStations").options)
      option.selected = selected.includes(option.value);
    if (
      !$("compareStations").selectedOptions.length &&
      $("compareStations").options.length
    )
      $("compareStations").options[0].selected = true;
  }
  function renderCitizen(air, nearby, hourlyPoints = []) {
    const cursor = query().cursor;
    const reference = cursor ? Date.parse(cursor) : Date.now();
    const item = window.AirWatchInsights.citizen(air, nearby, hourlyPoints, reference, !!cursor);
    const label = cursor ? "历史回放参考" : "当前出行参考";
    let html = "<h3>" + label + "</h3>";
    if (!item.air || item.air.value === null || item.air.observed_at === null) {
      html += "<p>附近暂无有效空气监测读数，无法判断当前环境。</p>";
    } else {
      html += "<p>" + esc(item.air.name) +
        (item.air.distance_m !== undefined ? " · 距离 " + esc(item.air.distance_m) + " 米" : "") +
        "<br>" + esc(metric()) + "：<strong>" + esc(Number(item.air.value).toFixed(1)) +
        " " + esc(item.air.unit || "") + "</strong> · " + esc(time(item.air.observed_at)) +
        " · " + (item.airFresh ? "观测有效" : "已超过 3 小时，仅供历史查看") + "</p>";
      html += item.airFresh && item.threeHourChange !== null
        ? "<p>较 3 小时前" + (item.threeHourChange >= 0 ? "增加 " : "减少 ") +
          esc(Math.abs(item.threeHourChange).toFixed(1)) + " " + esc(item.air.unit || "") + "。</p>"
        : "<p class='muted'>近 3 小时连续观测不足，暂不判断变化。</p>";
    }
    html += "<p>" + (item.bikeFresh
      ? "附近快照记录可用车辆 " + esc(item.availableBikes) + " 辆（" + esc(time(item.bikeTime)) + "）。"
      : "自行车快照缺失或超过 15 分钟，" + (cursor ? "该时刻" : "当前") + "可用车辆数未知。") + "</p>";
    html += "<p>" + (item.roadFresh
      ? "附近快照记录道路事件 " + esc(item.roadCount) + " 件（" + esc(time(item.roadTime)) + "）。"
      : "道路事件快照缺失或超过 15 分钟，" + (cursor ? "该时刻" : "当前") + "事件状态未知。") + "</p>";
    html += "<p class='muted'>" + (cursor ? "以上均为所选时刻的历史快照。" : "以上为站点附近的当前观测参考。") +
      "站点读数不代表整条路线；单项指数不等于完整官方 DAQI。</p>";
    $("citizenCard").innerHTML = html;
  }
  async function loadNearby() {
    if (!S.selected) return;
    const selected = S.selected,
      p = selected.feature.properties,
      generation = ++S.detailGeneration;
    $("nearbyContent").textContent = "正在查询附近资源…";
    $("citizenCard").textContent = "正在整理出行参考…";
    try {
      const data = await api(
        "/api/analysis/nearby",
        query({
          kind: selected.kind,
          source: p.source || "",
          site: p.id,
          radius: $("nearRadius").value,
          road_radius: $("roadRadius").value,
        }),
      );
      if (generation !== S.detailGeneration) return;
      const cursor = query().cursor;
      const snapshot = window.AirWatchInsights.citizen(null, data, [], cursor ? Date.parse(cursor) : Date.now(), !!cursor);
      let html =
        "<div class='nearby-stat'><div><b>" +
        (snapshot.bikeFresh ? data.available_bikes : "—") +
        "</b><span>附近可用车辆</span></div><div><b>" +
        (snapshot.bikeFresh ? data.bikes.length : "—") +
        "</b><span>自行车站</span></div><div><b>" +
        (snapshot.roadFresh ? data.roads.length : "—") +
        "</b><span>道路事件</span></div></div>";
      if (data.nearest_air)
        html +=
          "<p>附近监测点浓度 · " +
          esc(data.nearest_air.name) +
          " · " +
          data.nearest_air.distance_m +
          " 米 · " +
          esc(data.nearest_air.value) +
          " " +
          esc(data.nearest_air.unit || "") +
          "<br><small>" +
          esc(time(data.nearest_air.observed_at)) +
          "</small></p>";
      if (selected.kind === "bike" && !data.nearest_air)
        html += "<p class='muted'>1 公里内暂无有值监测点。</p>";
      html +=
        "<div class='selection-title'>附近自行车站</div><ul class='nearby-list'>" +
        ((snapshot.bikeFresh ? data.bikes : [])
          .slice(0, 8)
          .map(
            (b) =>
              "<li><span>" +
              esc(b.name) +
              "<small>" +
              b.distance_m +
              " 米</small></span><span>" +
              (b.bikes ?? "—") +
              " 辆 / " +
              (b.empty_docks ?? "—") +
              " 空位</span></li>",
          )
          .join("") || "<li>该时刻或范围内无有效快照</li>") +
        "</ul>";
      html +=
        "<div class='selection-title'>同期道路事件</div><ul class='nearby-list'>" +
        ((snapshot.roadFresh ? data.roads : [])
          .slice(0, 8)
          .map(
            (r) =>
              "<li><span>" +
              esc(r.name) +
              "<small>" +
              esc(r.severity) +
              (r.distance_m !== undefined ? " · " + r.distance_m + " 米" : "") +
              "</small></span></li>",
          )
          .join("") || "<li>该时刻或范围内无有效事件快照</li>") +
        "</ul>";
      if (selected.kind === "road")
        html += "<p class='muted'>观察性分析 · 展示附近监测点与同期变化。</p>";
      const air =
        selected.kind === "air"
          ? p
          : data.nearest_air || data.air.find((a) => a.value !== null);
      renderCitizen(air, data);
      $("nearbyContent").innerHTML = html;
      if (air) {
        $("analysisSource").value = air.source;
        const [series, weather] = await Promise.all([
          api("/api/analysis/series", {
            ...query(),
            source: air.source,
            sites: air.id,
          }),
          api("/api/analysis/weather", {
            ...query(),
            source: air.source,
            site: air.id,
            weather_site: $("weatherStation").value,
          }),
        ]);
        if (generation !== S.detailGeneration) return;
        renderCitizen(air, data, series.stations[0]?.hourly_points || []);
        if (!$("bikeStation").value && data.bikes.length)
          $("bikeStation").value = data.bikes[0].id;
        const latestWeather = weather.points[weather.points.length - 1];
        $("nearbyContent").innerHTML +=
          "<div class='selection-title'>伦敦气象背景 · " +
          esc(weather.station || "暂无站点") +
          "</div><p class='city-info'>" +
          (latestWeather
            ? esc(time(latestWeather.time)) +
              "<br>风速 " +
              esc(latestWeather.WIND_SPEED?.value_num) +
              " · 温度 " +
              esc(latestWeather.TEMPERATURE?.value_num) +
              " · 湿度 " +
              esc(latestWeather.HUMIDITY?.value_num) +
              "<br>气象单位未提供"
            : "所选范围暂无气象观测") +
          "</p>";
        chart("miniTrend").setOption(
          base({
            grid: { left: 45, right: 15, top: 20, bottom: 45 },
            legend: { show: false },
            series: lines(series.stations),
          }),
          true,
        );
        const old = [...$("compareStations").selectedOptions].map(
          (o) => o.value,
        );
        populateStations(
          series.available_sites,
          selected.kind === "road"
            ? data.air
                .filter((a) => a.source === air.source)
                .slice(0, 4)
                .map((a) => a.id)
            : old.length
              ? old
              : [air.id],
        );
      } else chart("miniTrend").clear();
    } catch (e) {
      if (generation === S.detailGeneration) {
        $("nearbyContent").textContent = e.message;
        $("citizenCard").textContent = "出行参考暂不可用：" + e.message;
      }
    }
  }
  function openSelected() {
    if (!S.selected) return;
    const { kind, feature } = S.selected;
    const cursor = query().cursor;
    if (kind === "road") H.openRoad(feature.properties.id, cursor || null);
    else
      H.openStation(
        kind === "bike" ? "tfl" : feature.properties.source,
        feature.properties.id,
        {
          start: S.start.toISOString(),
          end: cursor
            ? new Date(Date.parse(cursor) + 1000).toISOString()
            : S.end.toISOString(),
        },
      );
  }
  async function loadAnalysis() {
    if (!S.start || !window.echarts) return;
    const generation = ++S.analysisGeneration;
    error("");
    try {
      const source = $("analysisSource").value;
      let sites = [...$("compareStations").selectedOptions].map((o) => o.value);
      if (sites.length > 4) throw new Error("最多选择 4 个站点进行对比");
      const q = {
        ...query(),
        source,
        sites: sites.join(","),
        interval: $("analysisInterval").value,
        bike_site: $("bikeStation").value,
      };
      const series = await api("/api/analysis/series", q);
      if (generation !== S.analysisGeneration) return;
      if (!sites.length) sites = series.stations.map((s) => s.id);
      populateStations(series.available_sites, sites);
      S.series = series;
      const weather = await api("/api/analysis/weather", {
        ...q,
        site: sites[0] || "",
        weather_site: $("weatherStation").value,
      });
      if (generation !== S.analysisGeneration) return;
      S.weather = weather;
      $("weatherStation").innerHTML =
        "<option value=''>按有效观测数选择</option>" +
        weather.candidates
          .map(
            (s) =>
              "<option value='" +
              esc(s.id) +
              "'>" +
              esc(s.name + " · " + s.id + " · " + s.count + " 条") +
              "</option>",
          )
          .join("");
      $("weatherStation").value = weather.station || "";
      drawAnalysis();
    } catch (e) {
      if (generation === S.analysisGeneration) error(e.message);
    }
  }
  function heatmap(id, cells, missing = false) {
    const days = [
      ...new Set(cells.map((p) => calendarDate(new Date(p.time), zone()))),
    ];
    function label(v) {
      const parts = new Intl.DateTimeFormat("en-GB", {
        timeZone: zone(),
        hour: "2-digit",
        hourCycle: "h23",
        timeZoneName: "short",
      }).formatToParts(new Date(v));
      return (
        parts.find((p) => p.type === "hour").value +
        " " +
        parts.find((p) => p.type === "timeZoneName").value
      );
    }
    const hours = [...new Set(cells.map((p) => label(p.time)))].sort();
    const data = cells.map((p) => [
      hours.indexOf(label(p.time)),
      days.indexOf(calendarDate(new Date(p.time), zone())),
      p.value,
      p.time,
    ]);
    const values = cells.map((p) => p.value).filter((v) => v !== null);
    chart(id).setOption(
      base({
        grid: { left: 95, right: 22, top: 15, bottom: 80 },
        tooltip: {
          trigger: "item",
          formatter: (item) =>
            esc(time(item.data[3])) +
            "<br>" +
            (missing
              ? item.data[2]
                ? "缺测"
                : "有值"
              : esc(item.data[2] ?? "缺测")),
        },
        xAxis: {
          type: "category",
          data: hours,
          axisLabel: { color: "#94a8ba", fontSize: 13 },
        },
        yAxis: {
          type: "category",
          data: days,
          axisLabel: { color: "#94a8ba", fontSize: 13 },
        },
        visualMap: {
          min: missing ? 0 : Math.min(...values, 0),
          max: missing ? 1 : Math.max(...values, 1),
          calculable: false,
          orient: "horizontal",
          left: "center",
          bottom: 5,
          textStyle: { color: "#94a8ba" },
          inRange: {
            color: missing
              ? ["#3da58f", "#8c5563"]
              : ["#163e45", "#63c0a5", "#e9ca6b", "#e27e6a"],
          },
        },
        series: [
          {
            type: "heatmap",
            data,
            itemStyle: { borderColor: "#182837", borderWidth: 1 },
          },
        ],
      }),
      true,
    );
  }
  function drawAnalysis() {
    if (!S.series || !S.weather) return;
    const series = S.series,
      weather = S.weather,
      first = series.stations[0],
      cursor = query().cursor;
    const interval =
      $("analysisInterval").value === "900" && series.source === "laqn"
        ? 900000
        : 3600000;
    $("analysisNote").textContent =
      sourceNames[series.source] +
      " · " +
      metric() +
      " · " +
      time(S.start.toISOString()) +
      " 至 " +
      time(S.end.toISOString()) +
      "\n小时值优先使用来源记录；由 15 分钟值汇总时至少需要 3 / 4 个时段。缺测保持为空。" +
      (series.source === "breathe_wcc"
        ? "\n年度导入覆盖 Westminster 的所选站点。"
        : "");
    $("seriesSummary").textContent =
      series.stations.reduce((n, s) => n + s.points.length, 0) + " 个有效时段";
    const pollutionLines = lines(series.stations, interval);
    if (cursor && pollutionLines[0])
      pollutionLines[0].markLine = {
        silent: true,
        symbol: "none",
        data: [{ xAxis: Date.parse(cursor) }],
        lineStyle: { color: "#e9bc70" },
      };
    chart("pollutionChart").setOption(
      base({
        legend: { top: 5, textStyle: { color: "#b7cbd8" } },
        dataZoom: [
          { type: "inside" },
          {
            type: "slider",
            bottom: 7,
            height: 18,
            borderColor: "#304b5d",
            textStyle: { color: "#9fb8ca" },
          },
        ],
        series: pollutionLines,
      }),
      true,
    );
    const hourlyValues = new Map();
    for (const p of first?.hourly_points || []) {
      const h = hourKey(p.time);
      hourlyValues.set(h, [p.value_num]);
    }
    const cells = S.hours
      .filter((ms) => !cursor || ms <= Date.parse(cursor))
      .map((ms) => {
        const group = hourlyValues.get(hourKey(new Date(ms)));
        return {
          time: new Date(ms).toISOString(),
          value: group ? group[0] : null,
        };
      });
    heatmap(
      "pollutionHeatmap",
      cells.filter((p) => p.value !== null),
    );
    heatmap(
      "missingHeatmap",
      cells.map((p) => ({ ...p, value: p.value === null ? 1 : 0 })),
      true,
    );
    chart("coverageChart").setOption(
      base({
        yAxis: {
          type: "value",
          max: series.available_sites.length,
          splitLine: { lineStyle: { color: "#283d4c" } },
        },
        series: [
          {
            name: "有效站点",
            type: "bar",
            data: S.hours
              .filter((ms) => !cursor || ms <= Date.parse(cursor))
              .map((ms) => [
                ms,
                series.coverage.find((p) => Date.parse(p.time) === ms)?.count ||
                  0,
              ]),
            itemStyle: { color: "#63c4b5" },
          },
        ],
      }),
      true,
    );
    const weatherByHour = new Map(weather.points.map(point => [Date.parse(point.time),point]));
    const weatherSeries = [
      ["WIND_SPEED", "风速"],
      ["TEMPERATURE", "温度"],
      ["HUMIDITY", "湿度"],
    ].map(([key, name], i) => ({
      name,
      type: "line",
      symbol: "none",
      connectNulls: false,
      yAxisIndex: i + 1,
      data: S.hours.map((ms) => [
        ms,
        weatherByHour.get(ms)?.[key]?.value_num ?? null,
      ]),
    }));
    chart("weatherChart").setOption(
      base({
        grid: { left: 48, right: 115, top: 45, bottom: 50 },
        legend: { top: 3, textStyle: { color: "#aac2d2" } },
        yAxis: [
          {
            type: "value",
            name: "污染",
            splitLine: { lineStyle: { color: "#263d4c" } },
          },
          {
            type: "value",
            name: "风速",
            position: "right",
            offset: 0,
            splitLine: { show: false },
          },
          {
            type: "value",
            name: "温度",
            position: "right",
            offset: 36,
            splitLine: { show: false },
          },
          {
            type: "value",
            name: "湿度",
            position: "right",
            offset: 72,
            splitLine: { show: false },
          },
        ],
        series: [
          ...(first
            ? [
                {
                  name: metric(),
                  type: "line",
                  symbol: "none",
                  data: pointsWithGaps(first.points, interval),
                  yAxisIndex: 0,
                },
              ]
            : []),
          ...weatherSeries,
        ],
      }),
      true,
    );
    $("weatherNote").textContent =
      "区域气象背景 · " +
      (weather.station || "暂无伦敦站点") +
      " · " +
      weather.unit_note +
      "。";
    chart("weatherScatter").setOption(
      base({
        tooltip: {
          trigger: "item",
          formatter: (item) =>
            esc(time(item.data[2])) +
            "<br>风速来源值 " +
            item.data[0] +
            "<br>" +
            esc(metric()) +
            " " +
            item.data[1],
        },
        xAxis: {
          type: "value",
          name: "风速（来源值）",
          axisLabel: { color: "#94a8ba" },
          splitLine: { lineStyle: { color: "#283d4c" } },
        },
        yAxis: {
          type: "value",
          name: metric(),
          splitLine: { lineStyle: { color: "#283d4c" } },
        },
        series: [
          {
            type: "scatter",
            symbolSize: 7,
            itemStyle: { color: "#63dfc6", opacity: 0.75 },
            data: weather.pairs.map((p) => [p.wind_speed, p.pollution, p.time]),
          },
        ],
      }),
      true,
    );
    $("correlationNote").textContent =
      weather.sample_count +
      " 对同小时有效样本 · " +
      (weather.correlation === null
        ? "达到 24 对且数值有变化后显示相关系数"
        : "Pearson r = " + weather.correlation.toFixed(3)) +
      " · 观察性分析。";
    const bikes = series.bike_series;
    function bikePoints(key) {
      const values = [];
      bikes.forEach((p, i) => {
        const ms = Date.parse(p.captured_at_utc);
        if (i && ms - Date.parse(bikes[i - 1].captured_at_utc) > 600000)
          values.push([ms - 1, null]);
        values.push([ms, p[key]]);
      });
      return values;
    }
    chart("bikeChart").setOption(
      base({
        legend: { top: 4, textStyle: { color: "#abc4d4" } },
        series: [
          {
            name: "可用车辆",
            type: "line",
            step: "end",
            symbol: "none",
            connectNulls: false,
            data: bikePoints("bikes"),
          },
          {
            name: "空位",
            type: "line",
            step: "end",
            symbol: "none",
            connectNulls: false,
            data: bikePoints("empty_docks"),
          },
        ],
      }),
      true,
    );
    $("bikeNote").textContent = bikes.length
      ? bikes.length + " 份成功快照 · 按获取时间展示 · 超过 10 分钟的间隔断开"
      : "选择自行车站后查看已归档快照。";
    $("boroughCoverage").innerHTML = series.boroughs
      .map(
        (row) =>
          "<div class='borough-item'>" +
          esc(row.name) +
          "<span>" +
          row.stations +
          " 个站点 · 有效时段 " +
          (row.coverage * 100).toFixed(1) +
          "%</span></div>",
      )
      .join("");
  }
  S.overviewMapHost = $("cityMap")?.parentElement;
  if ($("gisTableDialog")?.append) $("gisTableDialog").append(document.querySelector(".gis-table-panel"));
  if ($("linkageDialog")?.append) $("linkageDialog").append(document.querySelector(".linkage"));
  $("closeGisTable")?.addEventListener?.("click", () => $("gisTableDialog").close());
  $("closeLinkage")?.addEventListener?.("click", () => $("linkageDialog").close());
  document
    .querySelectorAll("[data-page]")
    .forEach((b) =>
      b.addEventListener("click", () => showPage(b.dataset.page)),
    );
  document.querySelectorAll("[data-analysis-tab]").forEach(button => {
    if (button.dataset?.analysisTab)
      button.addEventListener("click", () => showAnalysisSection(button.dataset.analysisTab));
  });
  window.addEventListener("airwatch:show-boundary", (event) => {
    const selected = event.detail;
    if (!selected || !["region", "borough"].includes(selected.level) ||
        !Array.isArray(selected.bounds) || !Array.isArray(selected.bounds[0])) return;
    S.selectedBoundary = selected;
    showPage("overview");
    requestAnimationFrame(() => $("cityMap").scrollIntoView({ behavior: "smooth", block: "center" }));
    if (!S.map || !S.ready) {
      S.pendingBoundaryFocus = true;
      $("mapNotice").textContent = "等待地图加载后显示所选行政区。";
      return;
    }
    for (const level of ["region", "borough"]) {
      const id = "london-selected-" + level;
      if (S.map.getLayer(id)) S.map.setFilter(id, selectedBoundaryFilter(level));
    }
    requestAnimationFrame(() => focusBoundary(selected));
  });
  for (const id of ["layerBoundary", "layerRegion", "layerBorough", "layerAir", "layerLaqn", "layerBreathe", "layerBikes", "layerRoads", "layerRoadPoints", "layerRoadAreas"])
    $(id).addEventListener("change", () => {
      if (S.gisSelected && !gisVisible(S.gisSelected.layer)) S.gisSelected = null;
      visibility();
      highlightGis();
      legend();
      renderGisTable();
    });
  document.querySelectorAll("[data-gis-table]").forEach((button) =>
    button.addEventListener("click", () => { openGisTable(button.dataset.gisTable); $("gisTableDialog")?.showModal?.(); }));
  $("gisTable").addEventListener("click", (event) => {
    const button = event.target.closest?.("[data-gis-row]");
    if (!button) return;
    const row = S.gisPageRows?.[Number(button.dataset.gisRow)];
    if (row) chooseGisFeature(S.gisLayer, row.feature);
  });
  $("gisTableSearch").addEventListener("input", () => { S.gisPage = 1; renderGisTable(); });
  $("gisTablePrevious").addEventListener("click", () => { S.gisPage--; renderGisTable(); });
  $("gisTableNext").addEventListener("click", () => { S.gisPage++; renderGisTable(); });
  $("mapColor").addEventListener("change", () => {
    drawLayers();
    if (S.data) legend();
  });
  $("basemap").addEventListener("change", () => {
    if (!S.map) return;
    S.ready = false;
    if (S.popup) S.popup.remove();
    $("mapNotice").textContent = "正在切换底图…";
    S.map.setStyle(
      "https://tiles.openfreemap.org/styles/" + $("basemap").value,
      { diff: false },
    );
  });
  $("cityRange").addEventListener("change", () => {
    $("cityStartLabel").hidden = $("cityEndLabel").hidden =
      $("cityRange").value !== "custom";
  });
  $("cityApply").addEventListener("click", () => {
    S.playing = false;
    $("replayPlay").textContent = "播放";
    const source = $("citySource").value;
    if (source) {
      $("analysisSource").value = source;
      $("compareStations").innerHTML = "";
      if (
        S.selected?.kind === "air" &&
        S.selected.feature.properties.source !== source
      )
        S.selected = null;
    }
    refreshCity(true).catch((e) => error(e.message));
  });
  $("cityMode").addEventListener("change", () => {
    replayLabel();
    refreshCity();
  });
  $("replayHour").addEventListener("change", () => {
    replayLabel();
    refreshCity();
  });
  $("replayHour").addEventListener("input", replayLabel);
  $("replayPlay").addEventListener("click", async () => {
    S.playing = !S.playing;
    $("replayPlay").textContent = S.playing ? "暂停" : "播放";
    while (S.playing) {
      const next = Number($("replayHour").value) + 1;
      if (next > Number($("replayHour").max)) {
        S.playing = false;
        $("replayPlay").textContent = "播放";
        break;
      }
      $("replayHour").value = next;
      replayLabel();
      await refreshCity();
      await new Promise((resolve) => setTimeout(resolve, 1200));
    }
  });
  for (const id of ["nearRadius", "roadRadius"])
    $(id).addEventListener("change", loadNearby);
  $("openSelected").addEventListener("click", openSelected);
  $("analyseSelected").addEventListener("click", () => showPage("analysis"));
  $("chooseQuick").addEventListener("click", () => {
    const [source, id] = $("quickStation").value.split(":");
    const f = S.data?.air.features.find(
      (f) => f.properties.source === source && f.properties.id === id,
    );
    if (f) {
      selectFeature("air", f);
      if (S.map) S.map.easeTo({ center: f.geometry.coordinates, zoom: 13 });
    }
  });
  $("analysisSource").addEventListener("change", () => {
    $("compareStations").innerHTML = "";
    $("weatherStation").value = "";
    loadAnalysis();
  });
  $("analysisApply").addEventListener("click", loadAnalysis);
  $("weatherStation").addEventListener("change", loadAnalysis);
  $("bikeStation").addEventListener("change", loadAnalysis);
  $("analysisInterval").addEventListener("change", loadAnalysis);
  window.addEventListener("resize", () => {
    Object.values(S.charts).forEach((c) => c.resize());
    if (S.map) S.map.resize();
  });
  window.addEventListener("airwatch:refreshed", () => {
    if (!S.start || S.page === "tables") return;
    refreshCity($("cityMode").value === "latest").catch((e) =>
      error(e.message),
    );
  });
  window.addEventListener("airwatch:timezone", () => {
    replayLabel();
    if (S.data) summaries();
    if (S.series) drawAnalysis();
  });
  $("cityStart").value = calendarDate(
    new Date(Date.now() - 48 * 3600000),
    zone(),
  );
  $("cityEnd").value = calendarDate(new Date(), zone());
  const gisSidebar = document.querySelector?.(".gis-sidebar");
  if (gisSidebar) {
    gisSidebar.open = false;
    gisSidebar.addEventListener("toggle", () => S.map?.resize());
  }
  renderGisTable();
  window.AirWatchCity = {getMap: () => S.map, showPage, fitLondonView, getData: () => S.data};
  if (location.protocol !== "file:") {
    initMap();
    refreshCity(true).catch((e) => error(e.message));
  }
})();
