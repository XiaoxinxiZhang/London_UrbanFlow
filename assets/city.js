/* Map and charts share UTC query state and source-specific observations. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id),
    H = window.AirWatchData;
  if (!H) return;
  const { api, time, calendarDate, recentWindow } = H;
  const HOUR_MS = 3600000;
  const MAX_COMPARE_STATIONS = 4;
  const OUTLOOK_TABS = ["advice", "air", "nearby"];
  const OUTLOOK_AIR_METRICS = ["PM25", "PM10", "NO2", "O3"];
  const colors = ["#55c7a5", "#9bcf80", "#e9cf6e", "#ed9867", "#e96a72"];
  const missingAirColor = "#758290";
  const roadSeverityColors = { Serious: "#ef7b84", Severe: "#ef7b84", Moderate: "#e9bc70", Minimal: "#8bbddd" };
  const unknownRoadSeverityColor = "#98aabd";
  const gisPointStyles = {
    laqn: { shape: "triangle", clusterColor: "#2b8b7b" },
    breathe: { shape: "diamond", clusterColor: "#7965a9" },
    roadPoint: { shape: "square", clusterColor: "#a76558" },
  };
  const boundaryColors = { outer: "#8b5700", borough: "#416578", selected: "#b12645" };
  const sourceNames = {
    laqn: "LAQN",
    breathe: "Breathe London",
  };
  const S = {
    page: "overview",
    map: null,
    ready: false,
    data: null,
    charts: {},
    selected: null,
    outlookTab: "advice",
    pickingLocation: false,
    start: null,
    end: null,
    hours: [],
    generation: 0,
    detailGeneration: 0,
    analysisGeneration: 0,
    compareRevision: 0,
    comparePrefill: null,
    series: null,
    weather: null,
    playback: null,
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
    if (S.page === "journey")
      return ["region", "borough", "bikes", "roadPoint", "roadArea"].includes(layer);
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
    if (layer === "laqn" || layer === "breathe") return [p.name, sourceNames[p.source] || p.source, p.kind, p.value === null ? "缺测" : n(p.value) + (p.unit ? " " + p.unit : ""), time(p.observed_at), p.quality || "—", p.borough || f.geometry.coordinates.map(v => n(v, 4)).join(", ")];
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
    const selected = S.gisSelected;
    const features = selected && gisVisible(selected.layer) ? [selected.feature] : [];
    S.map.getSource("city-selected").setData({type:"FeatureCollection", features});
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
  const metric = (value = $("cityMetric").value) =>
    ({
      PM25: "PM₂.₅",
      NO2: "NO₂",
      PM10: "PM₁₀",
      O3: "O₃",
    })[value];
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
      period: "900",
      ...extra,
    };
    if ($("cityMode").value === "history" && S.hours.length) {
      const ms = S.hours[Number($("replayHour").value)];
      p.cursor = new Date(
        Math.min(ms + HOUR_MS, S.end.getTime()) - 1000,
      ).toISOString();
    }
    return p;
  }
  function resetWindow() {
    const { start, end } = recentWindow();
    S.start = new Date(start);
    S.end = new Date(end);
    const firstHour = Math.ceil(S.start.getTime() / HOUR_MS) * HOUR_MS;
    const endMs = S.end.getTime();
    S.hours = [];
    for (let ms = firstHour; ms < endMs; ms += HOUR_MS) S.hours.push(ms);
    $("replayHour").max = S.hours.length - 1;
    $("replayHour").value = S.hours.length - (endMs % HOUR_MS ? 2 : 1);
    replayLabel();
  }
  function replayLabel() {
    $("replayControls").hidden = $("cityMode").value !== "history";
    if (S.hours.length)
      $("replayLabel").textContent =
        time(new Date(S.hours[Number($("replayHour").value)]).toISOString()) +
        " 小时";
  }
  function stopReplay() {
    S.playback = null;
    $("replayPlay").textContent = "播放";
  }
  function showAnalysisSection(section) {
    S.analysisSection = section;
    Array.from(document.querySelectorAll("[data-analysis-section]"))
      .filter(node => node.dataset?.analysisSection)
      .forEach(node => { node.hidden = node.dataset.analysisSection !== section; });
    Array.from(document.querySelectorAll("[data-analysis-tab]"))
      .filter(node => node.dataset?.analysisTab)
      .forEach(node => node.classList.toggle("active", node.dataset.analysisTab === section));
    requestAnimationFrame(() => {
      Object.values(S.charts).forEach(chart => chart.resize());
      if (S.series && S.weather) drawAnalysis();
    });
  }
  function showPage(page) {
    const previousPage = S.page;
    S.page = page;
    if (page !== "analysis") $("compareStationsPicker").open = false;
    if (S.map?.getLayer?.("pollution-fill")) S.map.setLayoutProperty("pollution-fill", "visibility", page === "overview" && $("pollutionZones")?.checked ? "visible" : "none");
    for (const id of ["journey-leg-line", "journey-point-dot", "journey-point-label"]) {
      if (S.map?.getLayer?.(id)) S.map.setLayoutProperty(id, "visibility", page === "journey" ? "visible" : "none");
    }
    $("cityOverview").hidden = page !== "overview";
    if ($("journeyPage")) $("journeyPage").hidden = page !== "journey";
    $("cityAnalysis").hidden = page !== "analysis";
    $("tablesPage").hidden = page !== "tables";
    if (page !== "overview") {
      stopReplay();
      setOverviewPicking(false);
    }
    drawOverviewLocation();
    $("sourceFooter").hidden = page === "overview" || page === "journey";
    const mapNode = $("cityMap"), journeyHost = $("journeyMapHost");
    mapNode.classList.toggle("map-shows-journey", page === "journey");
    if (page === "journey" && journeyHost?.append && mapNode?.parentElement !== journeyHost)
      journeyHost.append(mapNode);
    else if (page === "overview" && S.overviewMapHost?.append && mapNode?.parentElement !== S.overviewMapHost)
      S.overviewMapHost.append(mapNode);
    if (page === "journey" && S.selected?.kind === "air" && S.popup) {
      S.popup.remove();
      S.popup = null;
    }
    visibility();
    highlightGis();
    if (page === "overview" || page === "journey") legend();
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
      if ($("cityMode").value === "latest") refreshCity();
      else loadAnalysis();
    }
    if (page === "journey" && $("cityMode").value !== "latest") {
      $("cityMode").value = "latest";
      refreshCity(true).catch((e) => error(e.message));
    }
    if (page === "overview" && previousPage !== "overview") refreshCity();
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
  function setMapNotice(message = "") {
    const notice = $("mapNotice");
    notice.textContent = message;
    notice.hidden = !message;
  }
  function removeAttributionSeparator(node, pattern) {
    if (node?.nodeType !== Node.TEXT_NODE) return;
    node.data = node.data.replace(pattern, "");
    if (!node.data) node.remove();
  }
  function groupBasemapAttribution(mapNode) {
    const inner = mapNode.querySelector?.(".maplibregl-ctrl-attrib-inner");
    if (!inner) return;
    const group = () => {
      const card = inner.querySelector(":scope > .map-attribution-card");
      if (!card || inner.childNodes.length === 1) return;
      // MapLibre joins the sanitized basemap credits and the custom card with pipes.
      if (!card.nextSibling) removeAttributionSeparator(card.previousSibling, /\s*\|\s*$/);
      removeAttributionSeparator(card.nextSibling, /^\s*\|\s*/);
      const credits = Array.from(inner.childNodes).filter(node => node !== card);
      card.querySelector(".map-basemap-live").append(...credits);
    };
    group();
    new MutationObserver(group).observe(inner, { childList: true });
  }
  function collapseAttributionAfterLoad(map, mapNode) {
    const disclosure = mapNode.querySelector?.(".maplibregl-ctrl-attrib");
    if (!disclosure) return;
    const toggle = disclosure.querySelector("summary");
    let interacted = false;
    let scheduled = false;
    const markInteraction = () => { interacted = true; };
    disclosure.addEventListener("pointerdown", markInteraction, { once: true });
    disclosure.addEventListener("focusin", markInteraction, { once: true });
    toggle.addEventListener("click", markInteraction, { once: true });
    const schedule = () => {
      if (scheduled) return;
      scheduled = true;
      setTimeout(() => {
        if (!interacted && disclosure.open && disclosure.classList.contains("maplibregl-compact-show"))
          toggle.click();
      }, 5000);
    };
    map.once("load", schedule);
    map.once("error", schedule);
  }
  function initMap() {
    const mapNode = $("cityMap");
    const fallback = $("mapAttributionTemplate")?.content?.firstElementChild?.cloneNode(true);
    const card = fallback?.querySelector(".map-attribution-card");
    if (card) {
      const footer = $("sourceFooter");
      card.querySelector(".map-source-summary").innerHTML = footer.querySelector(".footer-source-summary").innerHTML;
      card.querySelector(".map-quality-summary").innerHTML = footer.querySelector(".footer-quality-summary").innerHTML;
    }
    if (!window.maplibregl) {
      setMapNotice("地图组件未加载，图表与表格仍可查询。");
      if (fallback) mapNode.append(fallback, $("mapNotice"));
      return;
    }
    try {
      S.map = new maplibregl.Map({
        container: "cityMap",
        style: "https://tiles.openfreemap.org/styles/positron",
        center: [-0.12, 51.51],
        zoom: 10,
        minZoom: 7,
        maxZoom: 17,
        attributionControl: false,
        locale: {"AttributionControl.ToggleAttribution": "数据来源与许可"},
      });
      S.map.addControl(
        new maplibregl.NavigationControl({ showCompass: false }),
        "top-right",
      );
      S.map.addControl(
        new maplibregl.AttributionControl({
          compact: true,
          customAttribution: card?.outerHTML,
        }),
        "bottom-right",
      );
      groupBasemapAttribution(mapNode);
      collapseAttributionAfterLoad(S.map, mapNode);
      S.map.on("style.load", () => {
        S.ready = true;
        $("pickOverviewLocation").disabled = $("pickLocationFromPanel").disabled = false;
        installLayers();
        installBoundaryLayers();
        drawLayers();
        legend();
        setMapNotice();
        fitInitialMap();
        if (S.pendingBoundaryFocus && S.selectedBoundary) {
          S.pendingBoundaryFocus = false;
          requestAnimationFrame(() => focusBoundary(S.selectedBoundary));
        }
      });
      S.map.on("error", () => {
        setMapNotice("部分底图资源暂不可用，业务数据和图表仍可查询。");
      });
      S.map.on("moveend", () => { drawBikes(); drawPointClusters(); });
    } catch (e) {
      S.map?.remove?.();
      S.map = null;
      if (fallback) mapNode.append(fallback);
      setMapNotice("当前浏览器无法初始化地图，图表和表格仍可使用。");
    }
    mapNode.append?.($("mapNotice"));
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
    const m = S.map;
    for (const [id, file] of [
      ["london-region", "data/london_region_2025.geojson"],
      ["london-boroughs", "data/london_boroughs_2025.geojson"],
    ])
      if (!m.getSource(id)) m.addSource(id, { type: "geojson", data: file });
    const before = m.getLayer("bike-clusters") ? "bike-clusters" : undefined;
    if (!m.getLayer("london-region-fill")) m.addLayer({
      id:"london-region-fill", type:"fill", source:"london-region",
      paint:{"fill-color":boundaryColors.outer,"fill-opacity":0.035},
    }, before);
    if (!m.getLayer("london-borough-fill")) m.addLayer({
      id:"london-borough-fill", type:"fill", source:"london-boroughs",
      paint:{"fill-color":boundaryColors.borough,"fill-opacity":0.055},
    }, before);
    if (!m.getLayer("london-borough-boundaries"))
      m.addLayer({
        id: "london-borough-boundaries",
        type: "line",
        source: "london-boroughs",
        paint: { "line-color": boundaryColors.borough, "line-width": 1, "line-opacity": 0.65 },
      }, before);
    if (!m.getLayer("london-outer-boundary"))
      m.addLayer({
        id: "london-outer-boundary",
        type: "line",
        source: "london-region",
        paint: { "line-color": boundaryColors.outer, "line-width": 3, "line-opacity": 0.95 },
      }, before);
    for (const [level, source] of [["region", "london-region"], ["borough", "london-boroughs"]]) {
      const id = "london-selected-" + level;
      if (!m.getLayer(id)) m.addLayer({
        id, type: "line", source, filter: selectedBoundaryFilter(level),
        paint: {
          "line-color": boundaryColors.selected,
          "line-width": 5, "line-opacity": 0.98,
        },
      }, before);
    }
    positionBoundaryLayers();
    visibility();
  }
  function addPointImages(map, shape, vertices, fills, outline, strokeWidth) {
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 32;
    const ctx = canvas.getContext("2d");
    ctx.strokeStyle = outline;
    ctx.lineWidth = strokeWidth;
    ctx.lineJoin = "round";
    ctx.beginPath();
    ctx.moveTo(...vertices[0]);
    for (const vertex of vertices.slice(1)) ctx.lineTo(...vertex);
    ctx.closePath();
    for (const fill of fills) {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = fill;
      ctx.fill();
      ctx.stroke();
      map.addImage(`city-${shape}-${fill}`, ctx.getImageData(0, 0, canvas.width, canvas.height), { pixelRatio: 2 });
    }
  }
  function pointSymbolLayout(shape, fill) {
    return {
      "icon-image": ["concat", `city-${shape}-`, fill],
      "icon-allow-overlap": true,
      "icon-ignore-placement": true,
    };
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
    m.addSource("city-outlook", {type:"geojson", data:empty});
    const roadColor = [
      "match",
      ["get", "severity"],
      ...Object.entries(roadSeverityColors).flat(),
      unknownRoadSeverityColor,
    ];
    const airColors = [...colors, missingAirColor];
    const airColor = ["coalesce", ["get", "color"], missingAirColor];
    addPointImages(m, gisPointStyles.laqn.shape, [[16, 3], [30, 28], [2, 28]], airColors, "#f0f7ff", 2.8);
    addPointImages(m, gisPointStyles.breathe.shape, [[16, 1.5], [30.5, 16], [16, 30.5], [1.5, 16]], airColors, "#f0f7ff", 2);
    addPointImages(m, gisPointStyles.roadPoint.shape, [[6, 6], [26, 6], [26, 26], [6, 26]],
      new Set([...Object.values(roadSeverityColors), unknownRoadSeverityColor]), "#172737", 2);
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
      type: "symbol",
      source: "city-roads",
      filter: ["==", ["geometry-type"], "Point"],
      layout: pointSymbolLayout(gisPointStyles.roadPoint.shape, roadColor),
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
      type: "symbol",
      source: "city-air",
      filter: ["==", ["get", "source"], "laqn"],
      layout: pointSymbolLayout(gisPointStyles.laqn.shape, airColor),
    });
    m.addLayer({
      id: "air-breathe",
      type: "symbol",
      source: "city-air",
      filter: ["!=", ["get", "source"], "laqn"],
      layout: pointSymbolLayout(gisPointStyles.breathe.shape, airColor),
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
    for (const [kind, color] of [["resources", "#128978"], ["roads", "#915620"]]) {
      m.addLayer({id:"city-outlook-"+kind, type:"line", source:"city-outlook", filter:["==",["get","kind"],kind],
        paint:{"line-color":color,"line-width":2,...(kind === "roads" ? {"line-dasharray":[3,2]} : {})}});
    }
    m.addLayer({id:"city-outlook-point", type:"circle", source:"city-outlook", filter:["==",["get","kind"],"selected"],
      paint:{"circle-radius":6,"circle-color":"#126e65","circle-stroke-color":"#f2fff9","circle-stroke-width":2}});
    drawOverviewLocation();
    highlightGis();
    positionBoundaryLayers();
    if (S.mapEventsBound) {
      visibility();
      return;
    }
    S.mapEventsBound = true;
    m.on("click", (event) => {
      if (!S.pickingLocation || S.page !== "overview") return;
      if (event.originalEvent) event.originalEvent.overviewPicked = true;
      const p = m.queryRenderedFeatures?.(event.point, {layers:["air-laqn", "air-breathe"]})?.[0]?.properties;
      const station = p && S.data?.air.features.find(f => f.properties.source === p.source && f.properties.id === p.id);
      finishOverviewPick([event.lngLat.lng, event.lngLat.lat], station);
    });
    for (const layer of [
      "air-laqn",
      "air-breathe",
      "bike-points",
      "road-point",
      "road-fill",
      "road-line",
    ]) {
      m.on("click", layer, (event) => {
        if (S.pickingLocation || event.originalEvent?.overviewPicked) return;
        if (S.page === "journey" && $("journeyPage")?.dataset?.picking) return;
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
      if (S.pickingLocation || event.originalEvent?.overviewPicked) return;
      if (S.page === "journey" && $("journeyPage")?.dataset?.picking) return;
      if (!S.bikeIndex) return;
      const f = event.features[0],
        zoom = S.bikeIndex.getClusterExpansionZoom(f.properties.cluster_id);
      m.easeTo({ center: f.geometry.coordinates, zoom });
    });
    m.on("mouseenter", "bike-clusters", () => (m.getCanvas().style.cursor = "pointer"));
    m.on("mouseleave", "bike-clusters", () => (m.getCanvas().style.cursor = ""));
    for (const [layer, gisLayer] of [["london-borough-fill","borough"],["london-region-fill","region"]]) {
      m.on("click", layer, (event) => {
        if (S.pickingLocation || event.originalEvent?.overviewPicked) return;
        if (S.page === "journey" && $("journeyPage")?.dataset?.picking) return;
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
    if (p.value === null) return missingAirColor;
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
    drawPollutionZones();
    visibility();
  }
  async function drawPollutionZones() {
    const enabled = !!$("pollutionZones")?.checked;
    const box = $("pollutionZoneLegend");
    if (!box) return;
    box.hidden = !enabled;
    if (!enabled) {
      if (S.map?.getLayer?.("pollution-fill")) S.map.setLayoutProperty("pollution-fill", "visibility", "none");
      return;
    }
    if (!S.ready || !S.data) return;
    try {
      const boundary = await loadSpatial("borough");
      if (!$("pollutionZones").checked || !S.ready) return;
      const zones = window.AirWatchInsights.pollutionZones(boundary, S.data.air.features, S.data.breaks);
      if (!S.map.getSource("pollution-zones")) S.map.addSource("pollution-zones", {type:"geojson", data:zones});
      else S.map.getSource("pollution-zones").setData(zones);
      if (!S.map.getLayer("pollution-fill")) S.map.addLayer({id:"pollution-fill",type:"fill",source:"pollution-zones",paint:{"fill-color":["get","pollution_color"],"fill-opacity":0.58,"fill-outline-color":"#bca99e"}}, S.map.getLayer("road-fill") ? "road-fill" : undefined);
      S.map.setLayoutProperty("pollution-fill", "visibility", S.page === "overview" ? "visible" : "none");
      const palette = window.AirWatchInsights.pollutionPalette;
      const b = S.data.breaks;
      const labels = b.length ? b.map((v,i)=>(i ? "> "+b[i-1]+"–" : "≤ ")+v).concat("> "+b[b.length-1]) : ["同值 / 单一等级"];
      const title = metric() + "（µg/m³）";
      const scale = labels.map((label, i) => {
        const color = palette[i];
        return "<span><i style='background:" + color + "'></i>" + esc(label) + "</span>";
      }).join("");
      box.innerHTML = "<strong>" + esc(title) + "</strong><div class='zone-scale'>" + scale +
        "<span><i style='background:#657481'></i>无有效观测</span></div>";
    } catch(e) { box.textContent="污染划分暂不可用："+e.message; }
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
          if (S.page === "journey" && $("journeyPage")?.dataset?.picking) return;
          event.stopPropagation();
          if (S.pickingLocation) return finishOverviewPick(feature.geometry.coordinates);
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
    for (const [layer, {shape, clusterColor}] of Object.entries(gisPointStyles)) {
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
        element.className = (p.cluster ? "gis-map-cluster" : "gis-map-point") + " shape-" + shape;
        element.textContent = p.cluster ? String(p.point_count_abbreviated) : "";
        element.title = p.cluster ? p.point_count + " 个" + GIS_LABELS[layer] + "，点击展开" : p.name;
        element.setAttribute("aria-label", element.title);
        const fill = p.cluster ? clusterColor : layer === "roadPoint"
          ? roadSeverityColors[p.severity] || unknownRoadSeverityColor
          : p.color || missingAirColor;
        element.style.setProperty("--marker-color", fill);
        element.addEventListener("click", (event) => {
          if (S.page === "journey" && $("journeyPage")?.dataset?.picking) return;
          event.stopPropagation();
          if (S.pickingLocation) {
            const station = !p.cluster && layer !== "roadPoint" ? feature : null;
            return finishOverviewPick(feature.geometry.coordinates, station);
          }
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
      S.map.setFilter("air-values", ["in", ["get", "source"], ["literal", ["laqn", "breathe"].filter(gisVisible)]]);
    for (const item of S.bikeMarkers || []) item.element.hidden = !gisVisible("bikes");
    for (const [layer, group] of Object.entries(S.pointGroups))
      for (const item of group.markers) item.element.hidden = !gisVisible(layer);
  }
  function legendSwatch(color, label, {lineWidth, shape} = {}) {
    const style = lineWidth ? "border-top:" + lineWidth + "px solid " : "background:";
    const className = lineWidth ? " class='boundary-key'" : shape ? " class='shape-" + shape + "'" : "";
    return "<span><i" + className + " aria-hidden='true' style='" + style + color + "'></i>" + esc(label) + "</span>";
  }
  function legend() {
    const stationCount = "聚合点数字为站点数";
    const bikeLegend = {shape:"circle"};
    const layerLegends = {
      region: legendSwatch(boundaryColors.outer, "大伦敦外边界", {lineWidth:3}),
      borough: legendSwatch(boundaryColors.borough, "行政区边界", {lineWidth:1}),
      laqn: legendSwatch(gisPointStyles.laqn.clusterColor, stationCount, gisPointStyles.laqn),
      breathe: legendSwatch(gisPointStyles.breathe.clusterColor, stationCount, gisPointStyles.breathe),
      bikes: legendSwatch("#286f96", "聚合点数字为自行车站数", bikeLegend) +
        legendSwatch("#ee9a69", "空站", bikeLegend) + legendSwatch("#bf92df", "满站", bikeLegend) + legendSwatch("#58b9d1", "可用", bikeLegend),
      roadPoint: legendSwatch(gisPointStyles.roadPoint.clusterColor, "聚合点数字为事件数", gisPointStyles.roadPoint),
      roadArea: legendSwatch("#e9bc70", "颜色表示严重程度"),
    };
    const journey = S.page === "journey";
    const prefix = journey ? "journey-legend-" : "legend-";
    for (const [layer, content] of Object.entries(layerLegends)) {
      const target = $(prefix + layer);
      if (target) target.innerHTML = gisVisible(layer) ? content : "";
    }
    if (journey) return;

    const airLegend = $("legend-air");
    if (!S.data || !(gisVisible("laqn") || gisVisible("breathe"))) {
      airLegend.innerHTML = "";
      return;
    }
    const thresholds = S.data.breaks.map((value) => `${value} µg/m³`);
    const entries = thresholds.length
        ? [
            ...thresholds.map((value, i) => [
              colors[i],
              (i ? "> " + thresholds[i - 1] + " 至 " : "≤ ") + value,
            ]),
            [colors[thresholds.length], "> " + thresholds[thresholds.length - 1]],
          ]
        : [[missingAirColor, "暂无有效浓度"]];
    airLegend.innerHTML = entries.map(([color, label]) => legendSwatch(color, label)).join("");
  }
  function summaries() {
    const d = S.data;
    const renderCounts = (counts) => counts
      .map(([label, value]) => `<div><small>${esc(label)}</small><strong>${esc(value)}</strong></div>`)
      .join("");
    const snapshotStatus = (info) => {
      if (!info?.captured_at || info.gap)
        return { available: false, label: d.cursor ? "所选小时无可用快照" : "暂无采集快照" };
      return { available: true, label: (info.stale ? "快照已过期 · " : "采集于 ") + time(info.captured_at) };
    };
    const airCounts = Object.entries(sourceNames).map(([source, name]) => [
      name, d.source_stats?.[source]?.valid ?? 0,
    ]);
    const bikes = snapshotStatus(d.bike_info);
    const roads = snapshotStatus(d.road_info);
    const bikeCounts = [
      ["空站数量", bikes.available ? d.counts.empty : "—"],
      ["满站数量", bikes.available ? d.counts.full : "—"],
      ["有车有空位", bikes.available ? d.counts.available : "—"],
    ];
    $("cityKpis").innerHTML = `
      <div class="city-kpi">
        <h2>有值空气监测点</h2>
        <div class="city-kpi-metrics">${renderCounts(airCounts)}</div>
        ${d.cursor ? `<small>所选小时 · ${esc(metric(d.metric))}</small>` : ""}
      </div>
      <div class="city-kpi">
        <h2>自行车资源</h2>
        <div class="city-kpi-metrics">${renderCounts(bikeCounts)}</div>
        <small>${esc(bikes.label)}</small>
      </div>
      <div class="city-kpi">
        <h2>道路事件</h2>
        <strong>${esc(roads.available ? d.counts.roads : "—")}</strong>
        <small>${esc(roads.label)}</small>
      </div>`;
    $("mapWindow").textContent = "时间范围：" + time(d.start) + " 至 " + time(d.end);
    if ($("changeLocation").getAttribute?.("aria-expanded") === "true") renderLocationSearch();
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
    if (reset || !S.start || $("cityMode").value === "latest") resetWindow();
    const generation = ++S.generation;
    S.detailGeneration++;
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
      } else if (S.selected.kind === "point") {
        await loadNearby();
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
        else if (S.selected.kind === "air") await loadNearby();
        else {
          S.detailGeneration++;
          S.selected = null;
          S.comparePrefill = null;
          $("selectedActions").hidden = true;
          $("linkTitle").textContent = "尚未选择地点";
          $("linkSubtitle").textContent = "在地图上选点或搜索监测站";
          $("nearbyContent").textContent = "所选对象在该时刻没有有效记录，请选择其他对象。";
          $("nearbySummary").textContent = "";
          $("citizenCard").textContent = "";
          $("localAir").textContent = "选点后查看附近的污染物浓度。";
          S.overviewWeatherKey = null;
          $("outlookWeather").textContent = "选点后查看气象观测。";
          renderNearbyTrend(null);
          drawOverviewLocation();
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
    const start = Math.ceil(S.start.getTime() / interval) * interval;
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
    if (!S.map || !S.ready || (kind === "air" && S.page === "journey")) return;
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
    nearbyButton.addEventListener("click", () => {
      if (S.page !== "overview") showPage("overview");
      showOutlookTab("nearby");
      requestAnimationFrame(() => {
        const tab = $("outlookTab-nearby");
        tab.focus({preventScroll:true});
        tab.scrollIntoView({block:"nearest", behavior:"smooth"});
      });
    });
    content.append(nearbyButton);
    const bounds = f.geometry.type === "Point" ? null : geometryBounds(f.geometry);
    S.popup = new maplibregl.Popup({ maxWidth: "310px" })
      .setLngLat(lngLat || (bounds ? [(bounds[0][0] + bounds[1][0]) / 2, (bounds[0][1] + bounds[1][1]) / 2] : f.geometry.coordinates))
      .setDOMContent(content)
      .addTo(S.map);
  }
  function setLocationPicker(open) {
    $("locationOptions").hidden = !open;
    $("changeLocation").setAttribute("aria-expanded", String(open));
    if (open) {
      const source = S.selected?.kind === "air" && S.selected.feature.properties.source;
      if (source === "laqn" || source === "breathe") {
        $("locationNetworkLaqn").checked = source === "laqn";
        $("locationNetworkBreathe").checked = source === "breathe";
      }
      renderLocationSearch();
      ($("locationNetworkLaqn").checked ? $("locationNetworkLaqn") : $("locationNetworkBreathe")).focus?.();
    }
  }
  function renderLocationSearch() {
    const source = $("locationNetworkLaqn").checked ? "laqn" : "breathe";
    const terms = $("locationSearch").value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const stations = S.data?.air.features || [];
    const matches = stations.filter(({properties: p}) => {
      if (p.source !== source) return false;
      const text = `${p.name} ${p.id}`.toLowerCase();
      return terms.every(term => text.includes(term));
    }).sort((a, b) => a.properties.name.localeCompare(b.properties.name, "en"));
    const picker = $("locationStation");
    picker.innerHTML = '<option value="">请选择监测站</option>' + matches.map(({properties: p}) =>
      `<option value="${esc(p.id)}">${esc(p.name)} · ${esc(p.id)}</option>`).join("");
    picker.disabled = !matches.length;
    $("locationSearchStatus").textContent = !S.data ? "监测站列表正在加载。" :
      matches.length ? `${matches.length} 个监测站` : terms.length ? "找不到匹配的监测站。" : "此网络暂无监测站。";
  }
  function setOverviewPicking(active) {
    S.pickingLocation = active;
    $("pickOverviewLocation").setAttribute("aria-pressed", String(active));
    $("pickOverviewLocation").textContent = active ? "取消选点" : "选点";
    $("overviewPickHint").hidden = !active;
    $("cityMap").classList.toggle("map-picking-location", active);
  }
  function beginOverviewPick(fromPanel) {
    if (!S.ready) return;
    setLocationPicker(false);
    setOverviewPicking(true);
    if (fromPanel) $("pickOverviewLocation").focus?.();
  }
  function chooseLocationStation(station, moveMap = false) {
    const pending = selectFeature("air", station, null, false);
    if (moveMap) S.map?.easeTo({center:station.geometry.coordinates, zoom:Math.max(13, S.map.getZoom())});
    return pending;
  }
  function finishOverviewPick(coordinates, station = null) {
    setOverviewPicking(false);
    if (station) return chooseLocationStation(station);
    const [longitude, latitude] = coordinates;
    const point = [longitude >= -180 && longitude <= 180 ? longitude : ((longitude+180)%360+360)%360-180, latitude];
    return selectFeature("point", {type:"Feature", geometry:{type:"Point", coordinates:point},
      properties:{name:"地图选点"}}, null, false);
  }
  function rangeRing([lon, lat], radius) {
    const radians = Math.PI / 180, phi = lat * radians, arc = radius / 6371008.8;
    const points = Array.from({length:64}, (_, i) => {
      const heading = i * Math.PI / 32;
      const y = Math.asin(Math.sin(phi)*Math.cos(arc) + Math.cos(phi)*Math.sin(arc)*Math.cos(heading));
      const x = Math.atan2(Math.sin(heading)*Math.sin(arc)*Math.cos(phi), Math.cos(arc)-Math.sin(phi)*Math.sin(y));
      return [lon + x/radians, y/radians];
    });
    return [...points, points[0]];
  }
  function drawOverviewLocation() {
    const source = S.map?.getSource?.("city-outlook");
    if (!source) return;
    const center = S.page === "overview" && S.selected?.center;
    const features = [];
    if (center) {
      features.push({type:"Feature", geometry:{type:"Point", coordinates:center}, properties:{kind:"selected"}});
      if (S.outlookTab === "nearby") {
        for (const [kind, input] of [["resources", "nearRadius"], ["roads", "roadRadius"]]) {
          features.push({type:"Feature", geometry:{type:"LineString", coordinates:rangeRing(center, Number($(input).value))}, properties:{kind}});
        }
      }
    }
    source.setData({type:"FeatureCollection", features});
  }
  function showOutlookTab(tab, focus = false) {
    S.outlookTab = tab;
    for (const name of OUTLOOK_TABS) {
      const selected = name === tab;
      const button = $("outlookTab-" + name);
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
      $("outlookPanel-" + name).hidden = !selected;
      if (selected && focus) button.focus();
    }
    drawOverviewLocation();
    if (tab === "advice" && S.selected && S.start) void refreshOverviewWeather(query());
    if (tab === "nearby" && S.nearbyTrend) requestAnimationFrame(() => {
      if (S.outlookTab === "nearby" && S.nearbyTrend) renderNearbyTrend(S.nearbyTrend);
    });
  }
  async function selectFeature(kind, f, lngLat = null, withPopup = true) {
    const previous = S.selected;
    const p = f.properties;
    const changed = !previous || previous.kind !== kind || kind === "point" ||
      previous.feature.properties.id !== p.id || previous.feature.properties.source !== p.source;
    let center = f.geometry.coordinates;
    if (kind === "road") {
      if (lngLat) center = [lngLat.lng, lngLat.lat];
      else if (!changed && !withPopup) center = previous.center;
      else if (f.geometry.type !== "Point") {
        const [a, b] = geometryBounds(f.geometry);
        center = [(a[0]+b[0])/2, (a[1]+b[1])/2];
      }
    }
    S.selected = {kind, feature:f, center};
    $("linkTitle").textContent = p.name;
    $("linkSubtitle").textContent =
      kind === "air"
        ? [sourceNames[p.source] || p.source, p.id, p.kind].filter(Boolean).join(" · ")
        : kind === "point" ? `${center[1].toFixed(5)}°, ${center[0].toFixed(5)}°`
          : p.id + " · " + (kind === "bike" ? "自行车停靠站" : "道路事件");
    $("selectedActions").hidden = kind === "point";
    $("analyseSelected").textContent = kind === "air" ? "分析此站" : "查看环境与交通分析";
    if (kind === "point") S.comparePrefill = null;
    else if (S.page !== "analysis" && (changed || withPopup)) {
      if (kind === "air" && (changed || $("analysisSource").value !== p.source)) {
        $("analysisSource").value = p.source;
        resetCompareStations();
      }
      S.comparePrefill = { site: kind === "air" ? p.id : null };
    }
    if (kind === "bike") $("bikeStation").value = p.id;
    if (withPopup) popup(kind, f, lngLat);
    else if (S.popup) {
      S.popup.remove();
      S.popup = null;
    }
    drawOverviewLocation();
    await loadNearby();
  }
  let compareStationsMarkup;
  function compareStationInputs() {
    return [...$("compareStations").querySelectorAll('input[type="checkbox"]')];
  }
  function selectedCompareStations() {
    const sites = compareStationInputs().filter((input) => input.checked).map((input) => input.value);
    if (!sites.length && S.comparePrefill?.site) return [S.comparePrefill.site];
    return sites;
  }
  function updateCompareSummary(inputs = compareStationInputs()) {
    const selected = inputs.filter((input) => input.checked);
    $("compareStationsSummary").textContent = selected.length > 1
      ? "已选 " + selected.length + " 站"
      : selected[0]?.dataset.name || "请选择站点";
    for (const input of inputs)
      input.disabled = !input.checked && selected.length >= MAX_COMPARE_STATIONS;
  }
  function populateStations(options, selected, useDefault = true) {
    const markup = options.map(({ id, name, kind }) =>
      "<label><input type='checkbox' value='" + esc(id) + "' data-name='" + esc(name) +
      "'><span>" + esc(name + " · " + id + " · " + (kind || "类型未提供")) + "</span></label>",
    ).join("") || "<p>暂无可选站点</p>";
    if (markup !== compareStationsMarkup) {
      $("compareStations").innerHTML = markup;
      compareStationsMarkup = markup;
    }
    const inputs = compareStationInputs();
    const ids = new Set(selected.slice(0, MAX_COMPARE_STATIONS));
    for (const input of inputs) input.checked = ids.has(input.value);
    if (useDefault && inputs.length && !inputs.some((input) => input.checked))
      inputs[0].checked = true;
    updateCompareSummary(inputs);
  }
  function resetCompareStations() {
    S.compareRevision++;
    S.comparePrefill = null;
    $("compareStationsPicker").open = false;
    populateStations([], []);
  }
  function renderCitizen() {
    if (!S.selected) return;
    const replay = $("cityMode").value === "history";
    const health = window.AirWatchInsights.healthAdvice(S.officialDAQI, $("healthGroup").value || "general", replay);
    $("citizenCard").innerHTML = `<div class="health-band ${health.tone}">
      <span>${esc(health.basis)}</span>${health.tone !== "unknown" ? `<small>发布于 ${esc(time(S.officialDAQI.observed_at))}</small>` : ""}</div>
      <p class="health-action">${esc(health.advice)}</p><p class="health-note">${esc(health.note)}</p>
      <p class="health-source"><a href="https://www.gov.uk/government/publications/health-effects-of-air-pollution/health-advice-for-the-daily-air-quality-index-daqi" target="_blank" rel="noopener">Defra 分人群健康指导 ↗</a></p>`;
  }
  async function refreshOverviewAdvice(replay) {
    const generation = S.adviceGeneration = (S.adviceGeneration || 0) + 1;
    renderCitizen();
    if (replay) return;
    const advice = await api("/api/health", {}).catch(() => null);
    if (generation === S.adviceGeneration) {
      S.officialDAQI = advice;
      renderCitizen();
    }
  }
  function airUnit(unit) {
    return unit === "ug/m3" ? "µg/m³" : unit;
  }
  function renderLocalAir(readings) {
    const items = OUTLOOK_AIR_METRICS.map(pollutant => {
      const label = metric(pollutant);
      const reading = readings[pollutant];
      if (!reading || reading.status === "insufficient") {
        return `<li><span class="local-air-label">${esc(label)}</span>
          <p class="muted">${reading ? "附近观测不足" : "读数暂不可用"}</p></li>`;
      }
      const {status, value, unit, observed_at, period_seconds, stations} = reading;
      const source = stations[0];
      const network = sourceNames[source.source] || source.source;
      const basis = status === "measured" ? `监测站实测 · ${network} · ${source.name}` :
        status === "nearest" ? `最近站点读数 · ${network} · ${source.name} · ${source.distance_m} 米` :
          `附近站点估算 · ${network} · ${stations.length} 个参考站`;
      const duration = period_seconds === 3600 ? "小时值" : period_seconds === 900 ? "15 分钟值" : "";
      const contributors = status === "estimated" ? `<details class="air-contributors"><summary>查看 ${esc(label)} 参考站</summary>
        <ul class="nearby-list">${stations.map(station => `<li><span>${esc(station.name)}<small>${esc(station.id)} · ${esc(station.distance_m)} 米</small></span>
          <span>${esc(station.value)} ${esc(airUnit(station.unit))}</span></li>`).join("")}</ul></details>` : "";
      return `<li><p class="local-air-value"><span class="local-air-label">${esc(label)}</span> <strong>${esc(value)}</strong> ${esc(airUnit(unit))}</p>
        <p>${esc(basis)}</p><p class="muted">${esc(time(observed_at))}${duration ? ` · ${duration}` : ""}</p>${contributors}</li>`;
    });
    $("localAir").innerHTML = `<ul class="outlook-air-list">${items.join("")}</ul>`;
  }
  async function refreshOverviewWeather({start, end, cursor}) {
    const params = {start, end, ...(cursor ? {cursor} : {}), weather_site:$("weatherStation").value, context_only:"1"};
    const key = JSON.stringify(params);
    if (S.overviewWeatherKey === key) return;
    S.overviewWeatherKey = key;
    $("outlookWeather").textContent = "正在查询气象观测…";
    try {
      const weather = await api("/api/analysis/weather", params);
      if (S.overviewWeatherKey !== key) return;
      const latest = weather.latest ?? weather.points?.at(-1);
      if (!latest) {
        $("outlookWeather").textContent = "所选时段暂无伦敦气象观测。";
        return;
      }
      const fields = [["温度", latest.TEMPERATURE], ["湿度", latest.RELATIVE_HUMIDITY], ["风速", latest.WIND_SPEED]];
      const readings = fields.map(([label, reading]) => {
        const value = reading?.value_num != null ? `${reading.value_num}${reading.unit ? " " + reading.unit : ""}` : "—";
        return `<div><dt>${label}</dt><dd>${esc(value)}</dd></div>`;
      }).join("");
      $("outlookWeather").innerHTML = `<p class="muted">${esc(weather.station_name || weather.station)} · ${esc(time(latest.time))}</p>
        <dl>${readings}</dl>${fields.some(([, reading]) => reading?.value_num != null && !reading.unit) ? '<p class="muted">部分观测未提供单位。</p>' : ""}`;
    } catch (_) {
      if (S.overviewWeatherKey !== key) return;
      S.overviewWeatherKey = null;
      $("outlookWeather").textContent = "伦敦气象观测暂不可用。";
    }
  }
  function renderNearbyResources(data, cursor) {
    const snapshot = window.AirWatchInsights.citizen(null, data, [], cursor ? Date.parse(cursor) : Date.now(), !!cursor);
    const {bikeFresh, roadFresh} = snapshot;
    const bikeTotal = (field, total) => {
      if (!bikeFresh || (data.bikes.length && data.bikes.every(bike => bike[field] == null))) return "暂无数据";
      const count = Number.isFinite(total) ? total : data.bikes.reduce((sum, bike) => sum + (bike[field] ?? 0), 0);
      return count + (data.bikes.some(bike => bike[field] == null) ? "（部分站点）" : "");
    };
    const bikes = bikeTotal("bikes", data.available_bikes);
    const docks = bikeTotal("empty_docks", data.empty_docks);
    $("nearbySummary").textContent = `${bikes === "暂无数据" ? "车辆暂无数据" : "可用车辆 " + bikes} · ${roadFresh ? "道路事件 " + data.roads.length : "道路暂无数据"}`;
    const count = (label, value) => `<div><b>${esc(value)}</b><span>${label}</span></div>`;
    const list = (label, items, empty) =>
      `<h4>${label}</h4><ul class="nearby-list">${items || `<li class="muted">${empty}</li>`}</ul>`;
    const bikeItems = bikeFresh ? data.bikes.map(b => `<li><span>${esc(b.name)}<small>${esc(b.distance_m)} 米</small></span>
      <span>${esc(b.bikes)} 辆 / ${esc(b.empty_docks)} 空位</span></li>`).join("") : "";
    const roadItems = roadFresh ? data.roads.map(r => `<li><span>${esc(r.name)}<small>${esc(r.severity)} · ${esc(r.distance_m)} 米</small></span></li>`).join("") : "";
    const airItems = data.air.map(a => `<li><span>${esc(a.name)}<small>${esc(sourceNames[a.source] || a.source)} · ${esc(a.distance_m)} 米</small></span>
      <span>${a.value == null || a.stale ? "暂无有效读数" : `${esc(a.value)} ${esc(airUnit(a.unit || ""))}`}</span></li>`).join("");
    const snapshots = [bikeFresh ? `自行车采集于 ${time(snapshot.bikeTime)}` : "",
      roadFresh ? `道路事件采集于 ${time(snapshot.roadTime)}` : ""].filter(Boolean);
    $("nearbyContent").innerHTML = `<div class="nearby-stat">
      ${count("可用车辆", bikes)}${count("空车位", docks)}${count("自行车站", bikeFresh ? data.bikes.length : "暂无数据")}
      ${count("道路事件", roadFresh ? data.roads.length : "暂无数据")}${count("监测点", data.air.length)}</div>
      ${snapshots.length ? `<p class="muted">${esc(snapshots.join(" · "))}</p>` : ""}
      ${list("自行车站", bikeItems, bikeFresh ? "范围内没有自行车站。" : "自行车快照暂无数据。")}
      ${list("道路事件", roadItems, roadFresh ? "范围内没有道路事件。" : "道路事件快照暂无数据。")}
      ${list("监测点", airItems, "范围内没有监测点。")}`;
  }
  function renderNearbyTrend(trend, missing = "") {
    S.nearbyTrend = trend;
    $("miniTrend").hidden = !trend || !window.echarts;
    $("miniTrendLabel").textContent = trend
      ? `${trend.name} · ${trend.distance_m ? `距选点 ${trend.distance_m} 米 · ` : ""}${metric()} 小时观测（${airUnit(trend.unit)}）`
      : missing;
    if (!trend || !window.echarts) {
      S.charts.miniTrend?.clear();
      return;
    }
    if (S.outlookTab !== "nearby") return;
    const [series] = lines([trend]);
    if (trend.points.length === 1) series.symbol = "circle";
    chart("miniTrend").setOption(base({
      grid:{left:45,right:15,top:20,bottom:40},
      legend:{show:false},
      series:[series],
    }), true);
    S.charts.miniTrend.resize();
  }
  async function prefillNearbyComparison(selected, data, queryParams, generation) {
    const comparePrefill = S.comparePrefill;
    if (!comparePrefill || selected.kind === "point") return;
    const station = selected.kind === "air" ? selected.feature.properties : data.trend;
    if (!station) return;
    const series = await api("/api/analysis/series", {...queryParams, source:station.source, sites:station.id}).catch(() => null);
    if (!series || generation !== S.detailGeneration || comparePrefill !== S.comparePrefill) return;
    S.comparePrefill = null;
    S.compareRevision++;
    if ($("analysisSource").value !== station.source) {
      $("analysisSource").value = station.source;
      resetCompareStations();
    }
    const old = selectedCompareStations();
    const ids = selected.kind === "road" ? data.air.filter(a => a.source === station.source).slice(0, MAX_COMPARE_STATIONS).map(a => a.id) :
      old.length ? old : [station.id];
    populateStations(series.available_sites, ids);
  }
  async function loadNearby({resourcesOnly = false} = {}) {
    const selected = S.selected;
    if (!selected) return;
    const p = selected.feature.properties;
    const queryParams = query();
    const key = JSON.stringify([selected.kind, p.source, p.id, selected.center, queryParams]);
    const refreshAir = !resourcesOnly || S.localAirKey !== key;
    const generation = ++S.detailGeneration;
    const isCoordinate = selected.kind === "point" || selected.kind === "road";
    const [lon, lat] = selected.center;
    const anchor = isCoordinate ? {kind:"point", lon, lat} : {kind:selected.kind, source:p.source || "", site:p.id};
    $("nearbySummary").textContent = "正在查询…";
    $("nearbyContent").textContent = "正在查询附近资源…";
    if (refreshAir) {
      S.localAirKey = null;
      $("localAir").textContent = "正在查询地点空气质量…";
    }
    renderNearbyTrend(null);
    if (!resourcesOnly) {
      void refreshOverviewAdvice(!!queryParams.cursor);
      void refreshOverviewWeather(queryParams);
    }
    try {
      const data = await api("/api/analysis/nearby", {
        ...queryParams, ...anchor, all_sources:"1", include_trend:"1",
        ...(refreshAir ? {include_air_metrics:"1"} : {resources_only:"1"}),
        radius:$("nearRadius").value, road_radius:$("roadRadius").value,
      });
      if (generation !== S.detailGeneration) return;
      if (refreshAir) {
        renderLocalAir(data.local_air_by_metric ?? {[queryParams.metric]:data.local_air});
        S.localAirKey = key;
      }
      renderNearbyResources(data, queryParams.cursor);
      renderNearbyTrend(data.trend, selected.kind === "air" ? "本站在所选时段暂无有效观测趋势。" : "范围内暂无可显示的监测站观测趋势。");
      if (!$("bikeStation").value && data.bikes.length) $("bikeStation").value = data.bikes[0].id;
      await prefillNearbyComparison(selected, data, queryParams, generation);
    } catch (e) {
      if (generation !== S.detailGeneration) return;
      $("nearbySummary").textContent = "周边数据暂不可用";
      $("nearbyContent").textContent = e.message;
      if (refreshAir) renderLocalAir({});
    }
  }
  function openSelected() {
    if (!S.selected || S.selected.kind === "point") return;
    const { kind, feature } = S.selected;
    const cursor = query().cursor;
    if (kind === "road") H.openRoad(feature.properties.id, cursor || null);
    else
      H.openStation(
        kind === "bike" ? "tfl" : feature.properties.source,
        feature.properties.id,
        cursor ? {
          start: S.start.toISOString(),
          end: new Date(Date.parse(cursor) + 1000).toISOString(),
        } : null,
      );
  }
  async function loadAnalysis() {
    if (!S.start || !window.echarts) return;
    const generation = ++S.analysisGeneration;
    const compareRevision = S.compareRevision;
    const source = $("analysisSource").value;
    error("");
    try {
      let sites = selectedCompareStations();
      if (sites.length > MAX_COMPARE_STATIONS)
        throw new Error("最多选择 " + MAX_COMPARE_STATIONS + " 个站点进行对比");
      const q = {
        ...query(),
        source,
        sites: sites.join(","),
        interval: $("analysisInterval").value,
        bike_site: $("bikeStation").value,
      };
      const series = await api("/api/analysis/series", q);
      if (generation !== S.analysisGeneration || source !== $("analysisSource").value) return;
      if (!sites.length) sites = series.stations.map((s) => s.id);
      const compareEdited = compareRevision !== S.compareRevision;
      populateStations(series.available_sites, compareEdited ? selectedCompareStations() : sites, !compareEdited);
      S.series = series;
      const weather = await api("/api/analysis/weather", {
        ...q,
        site: sites[0] || "",
        weather_site: $("weatherStation").value,
      });
      if (generation !== S.analysisGeneration || source !== $("analysisSource").value) return;
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
      if (generation === S.analysisGeneration && source === $("analysisSource").value) error(e.message);
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
          dimension: 2,
          seriesIndex: 1,
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
              : ["#fee5d9", "#fcae91", "#fb6a4a", "#de2d26", "#99000d"],
          },
        },
        series: [
          {type:"heatmap", silent:true, data:cells.map(p=>[hours.indexOf(label(p.time)),days.indexOf(calendarDate(new Date(p.time),zone())),0]), itemStyle:{color:"#253848",borderColor:"#182837",borderWidth:1}},
          {
            type: "heatmap",
            data: data.filter(p => p[2] !== null),
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
      "\n小时值优先使用来源记录；由 15 分钟值汇总时至少需要 3 / 4 个时段。缺测保持为空。";
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
      cells,
    );
    if ($("heatmapNote")) $("heatmapNote").textContent = (first ? first.name + " · " + first.id : "尚未选择站点") + " · " + metric() + " · 有值 " + cells.filter(p=>p.value!==null).length + " / " + cells.length + " 个小时。" + (cells.some(p=>p.value!==null) ? "颜色越深表示数值越高；灰色格为缺测。" : "所选站点、指标和时间范围内没有有效小时数据，请调整筛选。") + " 时区：" + zone();
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
  $("healthGroup").addEventListener("change", renderCitizen);
  $("closeGisTable")?.addEventListener?.("click", () => $("gisTableDialog").close());
  $("closeLinkage")?.addEventListener?.("click", () => $("linkageDialog").close());
  OUTLOOK_TABS.forEach((name, index) => {
    const button = $("outlookTab-" + name);
    button.addEventListener("click", () => showOutlookTab(name));
    button.addEventListener("keydown", event => {
      const count = OUTLOOK_TABS.length;
      const targets = {ArrowLeft:(index+count-1)%count, ArrowRight:(index+1)%count, Home:0, End:count-1};
      if (!Object.hasOwn(targets, event.key)) return;
      event.preventDefault();
      showOutlookTab(OUTLOOK_TABS[targets[event.key]], true);
    });
  });
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
      setMapNotice("等待地图加载后显示所选行政区。");
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
  $("pollutionZones")?.addEventListener?.("change", drawPollutionZones);
  $("analysisMetric").innerHTML = $("cityMetric").innerHTML;
  $("analysisMetric").value = $("cityMetric").value;
  for (const id of ["cityMetric", "analysisMetric"]) {
    $(id).addEventListener("change", () => {
      const value = $(id).value;
      $("cityMetric").value = $("analysisMetric").value = value;
      refreshCity();
    });
  }
  $("cityMode").addEventListener("change", () => {
    stopReplay();
    refreshCity(true);
  });
  $("replayHour").addEventListener("change", () => {
    replayLabel();
    refreshCity();
  });
  $("replayHour").addEventListener("input", () => {
    stopReplay();
    replayLabel();
  });
  $("replayPlay").addEventListener("click", async () => {
    if (S.playback) {
      stopReplay();
      return;
    }
    const playback = S.playback = {};
    $("replayPlay").textContent = "暂停";
    const slider = $("replayHour");
    const max = Number(slider.max);
    let next = Number(slider.value) + 1;
    if (next > max) next = 0;
    while (S.playback === playback && next <= max) {
      slider.value = next;
      replayLabel();
      await refreshCity();
      next++;
      if (S.playback === playback && next <= max)
        await new Promise((resolve) => setTimeout(resolve, 1200));
    }
    if (S.playback === playback) stopReplay();
  });
  for (const id of ["nearRadius", "roadRadius"])
    $(id).addEventListener("change", () => {
      drawOverviewLocation();
      return loadNearby({resourcesOnly:true});
    });
  $("openSelected").addEventListener("click", openSelected);
  $("analyseSelected").addEventListener("click", () => showPage("analysis"));
  $("changeLocation").addEventListener("click", () => setLocationPicker($("locationOptions").hidden));
  for (const id of ["locationNetworkLaqn", "locationNetworkBreathe"])
    $(id).addEventListener("change", renderLocationSearch);
  $("locationSearch").addEventListener("input", renderLocationSearch);
  $("locationStation").addEventListener("change", () => {
    const source = $("locationNetworkLaqn").checked ? "laqn" : "breathe";
    const id = $("locationStation").value;
    const station = id && S.data?.air.features.find(({properties: p}) => p.source === source && p.id === id);
    if (!station) return;
    $("locationSearch").value = "";
    setLocationPicker(false);
    $("changeLocation").focus?.();
    return chooseLocationStation(station, true);
  });
  $("pickLocationFromPanel").addEventListener("click", () => beginOverviewPick(true));
  $("pickOverviewLocation").addEventListener("click", () => {
    if (S.pickingLocation) setOverviewPicking(false);
    else beginOverviewPick(false);
  });
  window.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || S.page !== "overview") return;
    if (S.pickingLocation) {
      setOverviewPicking(false);
      $("pickOverviewLocation").focus?.({preventScroll:true});
    } else if (!$("locationOptions").hidden && $("locationOptions").contains(event.target)) {
      setLocationPicker(false);
      $("changeLocation").focus?.();
    }
  });
  const comparePicker = $("compareStationsPicker");
  $("compareStations").addEventListener("change", () => {
    S.compareRevision++;
    S.comparePrefill = null;
    updateCompareSummary();
  });
  comparePicker.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !comparePicker.open) return;
    comparePicker.open = false;
    $("compareStationsToggle").focus();
  });
  window.addEventListener("click", (event) => {
    if (comparePicker.open && !comparePicker.contains(event.target)) comparePicker.open = false;
  });
  $("analysisSource").addEventListener("change", () => {
    resetCompareStations();
    $("weatherStation").value = "";
    loadAnalysis();
  });
  $("analysisApply").addEventListener("click", () => {
    comparePicker.open = false;
    loadAnalysis();
  });
  $("weatherStation").addEventListener("change", () => {
    if (S.selected && S.start) void refreshOverviewWeather(query());
    return loadAnalysis();
  });
  $("bikeStation").addEventListener("change", loadAnalysis);
  $("analysisInterval").addEventListener("change", loadAnalysis);
  window.addEventListener("resize", () => {
    Object.values(S.charts).forEach((c) => c.resize());
    if (S.map) S.map.resize();
  });
  window.addEventListener("airwatch:refreshed", () => {
    if (!S.start || S.page === "tables") return;
    refreshCity();
  });
  window.addEventListener("airwatch:timezone", () => {
    replayLabel();
    if (S.data) summaries();
    if (S.series) drawAnalysis();
  });
  const gisSidebar = document.querySelector?.(".gis-sidebar");
  gisSidebar?.addEventListener("toggle", () => S.map?.resize());
  legend();
  renderGisTable();
  window.AirWatchCity = {getMap: () => S.map, showPage, fitLondonView, getData: () => S.data};
  if (location.protocol !== "file:") {
    initMap();
    refreshCity(true).catch((e) => error(e.message));
  }
})();
