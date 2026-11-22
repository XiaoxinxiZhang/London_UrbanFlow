(() => {
  "use strict";
  const METRICS = {
    PM25_CONCENTRATION: "PM₂.₅", PM10_CONCENTRATION: "PM₁₀", NO2_CONCENTRATION: "NO₂", O3_CONCENTRATION: "O₃",
    PM10_INDEX: "PM₁₀ 指数", O3_INDEX: "O₃ 指数",
    BIKES_AVAILABLE: "可用车辆", DOCKS_EMPTY: "空位", DOCKS_TOTAL: "总停靠位",
    TEMPERATURE: "温度", HUMIDITY: "相对湿度", MSLP: "海平面气压", WIND_SPEED: "风速",
    WIND_DIRECTION: "风向", WIND_GUST: "阵风", VISIBILITY: "能见度", WEATHER_CODE: "天气代码",
    PRESSURE_TENDENCY: "气压变化趋势"
  };
  function calendarDate(date, zone) {
    const parts = new Intl.DateTimeFormat("en-CA", { timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(date);
    const get = type => parts.find(p => p.type === type).value;
    return get("year") + "-" + get("month") + "-" + get("day");
  }
  function midnightUTC(value, zone) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) throw new Error("请选择有效日期");
    const target = Date.parse(value + "T00:00:00Z");
    if (!Number.isFinite(target) || new Date(target).toISOString().slice(0, 10) !== value) throw new Error("日期无效");
    let guess = target;
    const formatter = new Intl.DateTimeFormat("en-CA", { timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });
    for (let step = 0; step < 4; step++) {
      const parts = formatter.formatToParts(new Date(guess));
      const get = type => parts.find(p => p.type === type).value;
      const rendered = Date.parse(get("year") + "-" + get("month") + "-" + get("day") + "T" + get("hour") + ":" + get("minute") + ":" + get("second") + "Z");
      const delta = target - rendered;
      guess += delta;
      if (delta === 0) return new Date(guess).toISOString();
    }
    throw new Error("无法转换所选时区的日期");
  }
  function dateRange(start, end, zone) {
    if (start > end) throw new Error("开始日期不能晚于结束日期");
    const next = new Date(end + "T00:00:00Z");
    next.setUTCDate(next.getUTCDate() + 1);
    if (!Number.isFinite(next.getTime())) throw new Error("请选择有效日期");
    return { start: midnightUTC(start, zone), end: midnightUTC(next.toISOString().slice(0, 10), zone) };
  }
  function recentWindow(now = Date.now()) {
    const end = Math.floor(now / 1000) * 1000;
    return { start: new Date(end - 48 * 3600000).toISOString(), end: new Date(end).toISOString() };
  }
  if (typeof module !== "undefined") module.exports = { calendarDate, midnightUTC, dateRange, recentWindow };
  if (typeof document === "undefined") return;

  const $ = id => document.getElementById(id);
  const state = { source: "laqn", page: 1, total: 0, items: [], status: null, generation: 0, detail: null, detailGeneration: 0, historyPage: 1, updating: false, lastUpdate: Date.now(), timer: null, boundaryPromise: null };
  const escape = value => String(value ?? "—").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
  const zone = () => $("timezone").value;
  const roads = () => state.source === "tfl" && $("tflMode").value === "roads";
  function time(value, compact = false) {
    if (!value) return "—";
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return String(value);
    const stamp = new Intl.DateTimeFormat("zh-CN", { timeZone: zone(), year: compact === true ? undefined : "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(date);
    if (compact === true) return stamp;
    const name = new Intl.DateTimeFormat("en-GB", { timeZone: zone(), timeZoneName: "short" }).formatToParts(date).find(p => p.type === "timeZoneName").value;
    return stamp + " " + name;
  }
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  function errorAt(id, message) {
    $(id).textContent = message || "";
    $(id).hidden = !message;
  }
  async function api(path, params = {}, options = {}) {
    const query = new URLSearchParams(params).toString();
    const response = await fetch(path + (query ? "?" + query : ""), { cache: "no-store", ...options });
    const data = await response.json().catch(() => ({ error: "本地服务返回了无法读取的响应" }));
    if (!response.ok) throw new Error(data.error || "请求失败");
    return data;
  }
  function params() {
    const value = { source: state.source, page: state.page, page_size: $("pageSize").value, active: $("showAll").checked ? "0" : "1", search: $("search").value.trim() };
    if (state.source === "laqn") {
      value.period = $("laqnMode").value === "index" ? "3600" : $("laqnMode").value;
      value.kind = $("laqnMode").value === "index" ? "index" : "concentration";
    }
    if (state.source === "metoffice") value.region = $("region").value;
    return value;
  }
  function columns() {
    if (state.source === "laqn") return $("laqnMode").value === "index" ? ["PM10_INDEX", "O3_INDEX"] :
      ["PM25_CONCENTRATION", "PM10_CONCENTRATION", "NO2_CONCENTRATION", "O3_CONCENTRATION"];
    if (state.source === "breathe") return ["PM25_CONCENTRATION", "NO2_CONCENTRATION"];
    if (state.source === "tfl") return ["BIKES_AVAILABLE", "DOCKS_EMPTY"];
    return ["TEMPERATURE", "HUMIDITY", "MSLP", "WIND_SPEED", "WIND_DIRECTION", "WIND_GUST", "VISIBILITY", "WEATHER_CODE", "PRESSURE_TENDENCY"];
  }
  function readingTime(value) {
    const full = escape(time(value));
    if (!value || state.source !== "metoffice") return full;
    return "<time datetime='" + escape(value) + "' title='" + full + "'>" + escape(time(value, true)) + "</time>";
  }
  function measurementCell(row) {
    if (!row) return '<span class="muted">—</span>';
    const limit = state.source === "tfl" ? 15 * 60 : state.source === "metoffice" ? 27 * 3600 : 3 * 3600;
    const stale = Date.now() - Date.parse(row.valid_at_utc) > limit * 1000;
    const value = row.value_num === null ? row.value_text : Number(row.value_num).toLocaleString("zh-CN", { maximumFractionDigits: 5 });
    const unit = row.unit || (state.source === "metoffice" ? "单位未提供" : "");
    return '<div class="reading' + (stale ? " stale" : "") + '"><span class="value">' + escape(value) +
      '</span><span class="unit">' + escape(unit) + '</span><span class="quality" title="U 未提供审定状态，P 暂定，R 已审定">' +
      escape(row.quality) + '</span><span class="obs-time">' + readingTime(row.valid_at_utc) + (stale ? " · 已过期" : "") +
      '</span><span class="sub">获取 ' + readingTime(row.fetched_at_utc) +
      (row.time_basis === "retrieval_time" ? " · 以获取时间记快照" : "") + "</span></div>";
  }
  function responsiveTable(headings, rows, emptyMessage, tableClass = "") {
    const labels = headings.map(escape);
    const header = labels.map(label => "<th scope='col' role='columnheader'>" + label + "</th>").join("");
    const records = rows.map(cells => "<tr role='row'>" + cells.map((cell, index) =>
      "<td role='cell'><span class='data-cell-label' aria-hidden='true'>" + labels[index] + "</span>" + cell + "</td>"
    ).join("") + "</tr>").join("");
    return "<table class='data-center-table " + tableClass + "' role='table'><thead role='rowgroup'><tr role='row'>" + header +
      "</tr></thead><tbody role='rowgroup'>" + (records || "<tr role='row'><td role='cell' class='empty' colspan='" +
        headings.length + "'>" + escape(emptyMessage) + "</td></tr>") + "</tbody></table>";
  }
  function detailButton(label, index) {
    return "<button type='button' class='site-link' data-item='" + index + "'>" + escape(label) + "</button>";
  }
  function pageLabel(page, total, size) {
    return total ? "第 " + page + " / " + Math.ceil(total / size) + " 页 · 共 " + total.toLocaleString() + " 条" : "暂无记录";
  }
  function boundaryBounds(geometry) {
    let west = Infinity, south = Infinity, east = -Infinity, north = -Infinity;
    function visit(coords) {
      if (typeof coords[0] === "number") {
        west = Math.min(west, coords[0]); east = Math.max(east, coords[0]);
        south = Math.min(south, coords[1]); north = Math.max(north, coords[1]);
      } else coords.forEach(visit);
    }
    visit(geometry.coordinates);
    return [[west, south], [east, north]];
  }
  function boundaryRecord(feature, level) {
    const p = feature.properties, region = level === "region";
    return {
      level, code: p[region ? "RGN25CD" : "LAD25CD"],
      name: p[region ? "RGN25NM" : "LAD25NM"],
      kind: region ? "大伦敦" : p.LAD25CD === "E09000001" ? "伦敦金融城" : "自治市",
      area: Number(p.Shape__Area) / 1e6,
      perimeter: Number(p.Shape__Length) / 1000,
      longitude: Number(p.LONG), latitude: Number(p.LAT),
      bounds: boundaryBounds(feature.geometry),
    };
  }
  function boundaries() {
    if (!state.boundaryPromise) state.boundaryPromise = Promise.all([
      api("/data/london_region_2025.geojson"),
      api("/data/london_boroughs_2025.geojson"),
    ]).then(([region, boroughs]) => {
      if (region.features?.length !== 1 || boroughs.features?.length !== 33)
        throw new Error("伦敦边界数据不完整");
      return [boundaryRecord(region.features[0], "region"),
        ...boroughs.features.map(feature => boundaryRecord(feature, "borough"))
          .sort((a, b) => a.name.localeCompare(b.name, "en"))];
    }).catch(error => { state.boundaryPromise = null; throw error; });
    return state.boundaryPromise;
  }
  const boundaryNumber = value => Number.isFinite(value)
    ? value.toLocaleString("zh-CN", { maximumFractionDigits: 2 }) : "—";
  function renderTable() {
    let headings, rows;
    if (state.source === "boundaries") {
      headings = ["区划名称", "类别", "标准代码", "面积（平方公里）", "周长（公里）"];
      rows = state.items.map((row, index) => [detailButton(row.name, index), escape(row.kind), escape(row.code),
        boundaryNumber(row.area), boundaryNumber(row.perimeter)]);
    } else if (roads()) {
      headings = ["道路事件", "类别 / 严重程度", "状态", "开始 / 结束时间", "来源修改时间"];
      rows = state.items.map((row, index) => [
        detailButton(row.location_text || row.event_id, index) + "<span class='sub'>" + escape(row.event_id) + "</span>",
        escape(row.category) + "<span class='sub'>" + escape(row.severity) + "</span>",
        escape(row.status) + "<span class='sub'>" + (row.is_active ? "进行中" : "已结束") + "</span>",
        escape(time(row.starts_at_utc)) + "<span class='sub'>" + escape(time(row.ends_at_utc)) + "</span>",
        escape(time(row.modified_at_utc)) + "<span class='sub'>最近见于列表 " + escape(time(row.last_seen_utc)) + "</span>",
      ]);
    } else {
      const metrics = columns();
      headings = ["站点", state.source === "tfl" ? "坐标" : "类型 / 区域", ...metrics.map(metric => METRICS[metric] || metric)];
      rows = state.items.map((row, index) => {
        const meta = row.metadata || {};
        const info = state.source === "tfl" ? [row.latitude ?? "—", row.longitude ?? "—"].join(", ") : [row.kind, meta.Borough || meta.region || meta.area].filter(Boolean).join(" / ");
        const station = detailButton(row.name || row.source_id, index) + "<span class='sub'>" +
          escape(row.source_id) + (row.active ? "" : " · 停用") + "</span>";
        return [station, escape(info || "—"), ...metrics.map(metric => measurementCell(row.values[metric]))];
      });
    }
    $("dataTable").innerHTML = responsiveTable(headings, rows, "当前筛选条件下暂无记录",
      state.source === "metoffice" ? "data-center-table--weather" : "");
    $("pageInfo").textContent = pageLabel(state.page, state.total, Number($("pageSize").value));
    $("previous").disabled = state.page <= 1;
    $("next").disabled = state.page * Number($("pageSize").value) >= state.total;
  }
  function configure() {
    for (const [id, source] of [["laqnFilter", "laqn"], ["tflFilter", "tfl"], ["weatherFilter", "metoffice"]]) $(id).hidden = state.source !== source;
    const geographic = state.source === "boundaries";
    $("showAllFilter").hidden = geographic;
    $("collectionStatus").hidden = geographic;
    $("searchLabel").textContent = geographic ? "搜索区划名称或代码" : "搜索站名或编号";
    $("search").placeholder = geographic ? "输入区划名称或代码" : "输入名称或编号";
    for (const button of $("sources").querySelectorAll("button")) {
      const active = button.dataset.source === state.source;
      button.classList.toggle("active", active);
      if (active) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
    }
    const titles = { laqn: "LAQN · " + $("laqnMode").selectedOptions[0].text, breathe: "Breathe London", tfl: "TfL · " + $("tflMode").selectedOptions[0].text, metoffice: "英国气象局 · 地面观测", boundaries: "伦敦行政区划" };
    $("tableTitle").textContent = titles[state.source];
    $("showAllLabel").textContent = roads() ? "包括已结束事件" : "包括停用站点";
    $("tableDescription").textContent = geographic ? "ONS 2025 年边界 · 点击名称查看区划资料与地图位置" : roads() ? "道路范围、状态及来源修改时间" : "各站最近 48 小时内的最新值 · 点击站名查看元数据与观测";
  }
  async function loadTable() {
    const generation = ++state.generation;
    configure();
    $("dataTable").setAttribute("aria-busy", "true");
    $("dataTable").style.opacity = ".55";
    $("dataTable").style.pointerEvents = "none";
    errorAt("tableError", "");
    try {
      let data;
      if (state.source === "boundaries") {
        const all = await boundaries(), search = $("search").value.trim().toLowerCase();
        const matching = all.filter(row => (row.name + " " + row.code + " " + row.kind).toLowerCase().includes(search));
        const size = Number($("pageSize").value);
        data = { total: matching.length, items: matching.slice((state.page - 1) * size, state.page * size) };
      } else if (roads()) data = await api("/api/road-events", params());
      else data = await api("/api/measurements", { ...params(), ...recentWindow() });
      if (generation !== state.generation) return;
      state.items = data.items;
      state.total = data.total;
      renderTable();
    } catch (error) {
      if (generation === state.generation) errorAt("tableError", error.message);
    } finally {
      if (generation === state.generation) {
        $("dataTable").setAttribute("aria-busy", "false");
        $("dataTable").style.opacity = "";
        $("dataTable").style.pointerEvents = "";
      }
    }
  }
  const jobNames = { laqn_sites: "站点目录", laqn_hourly: "小时浓度与指数", laqn_15min: "15 分钟浓度", breathe_web_hourly: "官网地图", breathe_web_reconcile: "逐站曲线补查", tfl_bikes: "自行车", tfl_bike_archive: "自行车公开归档补录", tfl_roads: "道路列表", tfl_road_archive: "道路公开归档补录", met_sites: "站点发现", met_hourly: "气象观测" };
  function jobState(job) {
    const labels = { success: "成功", error: "失败", pending_credentials: "缺少密钥", credentials_error: "密钥配置错误", not_started: "尚未采集" };
    return (labels[job.status] || job.status) + (job.stale_run ? " · 采集过期" : "") + (job.stale_data ? " · 数据过期" : "");
  }
  function renderStatus() {
    if (!state.status) return;
    const data = state.status;
    for (const source of ["laqn", "breathe", "tfl", "metoffice"]) {
      const jobs = data.jobs.filter(job => job.source === source);
      const issues = jobs.filter(job => job.status !== "success" || job.stale_data || job.stale_run);
      $("badge-" + source).textContent = issues.length ? issues.length + " 项需关注" : "采集正常";
      $("badge-" + source).classList.toggle("warning", issues.length > 0);
    }
    const jobs = data.jobs.filter(job => job.source === state.source);
    $("statusSummary").textContent = data.collector_running ? "正在采集" : "按来源采集频率更新";
    const headings = ["任务", "状态", "最近成功", "最新观测 / 修改", "说明"];
    const rows = jobs.map(job => [escape(jobNames[job.job] || job.job), escape(jobState(job)),
      escape(time(job.last_success_utc)), escape(time(job.latest_observation_utc)),
      job.error ? "<span class='job-error'>" + escape(job.error) + "</span>" : "—"]);
    let extra = "";
    if (state.source === "breathe") extra = "<p class='status-extra'>过去 24 小时曲线补查 " + data.breathe_coverage.checked + " / " + data.breathe_coverage.active + " 个活动站 · " + data.breathe_coverage.failed + " 站失败</p>";
    if (state.source === "metoffice") extra = "<p class='status-extra'>今日 UTC 请求用量 " + data.metoffice_budget.used + " / " + data.metoffice_budget.limit + "</p>";
    $("sourceStatus").innerHTML = "<div class='table-wrap data-center-table-wrap'>" +
      responsiveTable(headings, rows, "暂无采集任务", "jobs") + "</div>" + extra;
  }
  async function loadStatus() {
    state.status = await api("/api/status");
    const selected = $("region").value;
    $("region").innerHTML = "<option value=''>全部区域</option>" + state.status.regions.map(region => "<option>" + escape(region) + "</option>").join("");
    $("region").value = selected;
    renderStatus();
  }
  async function refresh(notify = true) {
    await Promise.all([loadTable(), loadStatus()]);
    if (notify) window.dispatchEvent(new CustomEvent("airwatch:refreshed"));
  }

  const metaLabels = { source_id: "站点编号", source: "来源", name: "名称", kind: "监测环境", latitude: "纬度", longitude: "经度", active: "活动状态", updated_at_utc: "元数据更新时间", SiteCode: "站点编号", SiteName: "站名", Borough: "行政区", DeviceCode: "设备编号", InstallationCode: "安装编号", Facility: "设施", SensorHeightAboveGround: "离地高度（米）", DistanceToKerb: "距路缘（米）", StartDate: "安装开始日期", EndDate: "安装结束日期", PowerTag: "供电", SiteClassification: "环境类型", SiteLocationType: "场所类型", SponsorName: "资助方", SensorContract: "合同", SitePhotoURL: "照片", profileUrl: "详情页", area: "区域", region: "地区", country: "国家", geohash: "Geohash" };
  function metadataHTML(row) {
    const fields = { source: row.source, source_id: row.source_id, name: row.name, kind: row.kind, latitude: row.latitude, longitude: row.longitude, active: row.active ? "活动" : "停用", updated_at_utc: time(row.updated_at_utc), ...row.metadata };
    return Object.entries(fields).map(([key, value]) => "<div class='metadata-item'><span>" + escape(metaLabels[key] || key) + "</span>" + escape(value !== null && typeof value === "object" ? JSON.stringify(value) : value) + "</div>").join("");
  }
  function openBoundaryDetail(row) {
    state.detailGeneration++;
    state.detail = { source: "boundaries", row, isRoad: false, isBoundary: true };
    $("detailTitle").textContent = row.name;
    $("detailSubtitle").textContent = row.code;
    $("stationDetail").hidden = true;
    $("roadDetail").hidden = true;
    $("boundaryDetail").hidden = false;
    errorAt("detailError", "");
    const fields = [
      ["区划类别", row.kind], ["标准代码", row.code],
      ["面积", boundaryNumber(row.area) + " 平方公里"],
      ["周长", boundaryNumber(row.perimeter) + " 公里"],
      ["参考坐标", Math.abs(row.latitude).toFixed(5) + "°" + (row.latitude < 0 ? "S" : "N") + ", " + Math.abs(row.longitude).toFixed(5) + "°" + (row.longitude < 0 ? "W" : "E")],
      ["边界版本", "2025 年 12 月 · 20 米概化边界"],
    ];
    $("boundaryDetail").innerHTML = "<div class='metadata'>" + fields.map(([label, value]) =>
      "<div class='metadata-item'><span>" + escape(label) + "</span>" + escape(value) + "</div>").join("") +
      "</div><p class='muted'>来源：<a href='https://www.ons.gov.uk/methodology/geography/licences' target='_blank' rel='noopener'>英国国家统计局（ONS）</a>，含 Ordnance Survey 数据。面积与周长按来源字段换算。</p>" +
      "<button id='viewBoundaryOnMap' class='primary'>在地图查看</button>";
    $("boundaryDetail").querySelector("#viewBoundaryOnMap").addEventListener("click", () => {
      $("detail").close();
      window.dispatchEvent(new CustomEvent("airwatch:show-boundary", {
        detail: { level: row.level, code: row.code, bounds: row.bounds },
      }));
    });
    $("detail").showModal();
    $("detail").scrollTop = 0;
  }
  async function openDetail(index, suppliedRow = null, suppliedSource = null, suppliedRoad = null, initialRange = null) {
    const row = suppliedRow || state.items[index];
    if (!row) return;
    if ((suppliedSource || state.source) === "boundaries") {
      openBoundaryDetail(row);
      return;
    }
    state.detail = { source: suppliedSource || state.source, row, isRoad: suppliedRoad ?? roads(), replayRange: initialRange };
    const generation = ++state.detailGeneration;
    $("detailTitle").textContent = row.name || row.location_text || row.event_id || row.source_id;
    $("detailSubtitle").textContent = row.source_id || row.event_id;
    $("stationDetail").hidden = state.detail.isRoad;
    $("roadDetail").hidden = !state.detail.isRoad;
    $("boundaryDetail").hidden = true;
    errorAt("detailError", "");
    $("detail").showModal();
    $("detail").scrollTop = 0;
    if (state.detail.isRoad) {
      $("roadDetail").textContent = "读取事件详情…";
      try {
        const data = await api("/api/road-events", { id: row.event_id, ...(initialRange?.at ? {at: initialRange.at} : {}) });
        if (generation !== state.detailGeneration) return;
        const labels = { event_id: "事件 ID", category: "类别", severity: "严重程度", status: "状态", location_text: "道路范围", starts_at_utc: "开始", ends_at_utc: "结束", modified_at_utc: "来源修改", last_seen_utc: "最近见于列表" };
        if (data.replay_mode === "reconstructed") labels.replayed_at_utc = "回放重建于";
        const revisions = data.versions.map(version => "<details><summary>版本 " + escape(version.old_revision) +
          " · 替换于 " + escape(time(version.replaced_at_utc)) + "</summary><pre class='raw-data'>" +
          escape(prettyJSON(version.old_payload_json)) + "</pre></details>").join("");
        $("roadDetail").innerHTML = (data.replay_mode === "reconstructed" ?
          "<p class='muted'>该小时没有原始列表快照，事件按前后两次列表的交集重建。</p>" : "") +
          (data.snapshot_source === "public_road_archive" ?
            "<p class='muted'>位置和事件状态来自 <a href='https://github.com/farhanmunim/london-buses/commits/main/data/api/roadworks.json' target='_blank' rel='noopener'>公开归档</a> · Powered by TfL Open Data</p>" : "") +
          "<div class='road-summary'>" + Object.entries(labels).map(([key, label]) => "<p><span>" + label +
            "</span>" + escape(key.endsWith("_utc") ? time(data[key]) : data[key]) + "</p>").join("") +
          "</div><details class='road-detail-section'><summary>几何范围</summary><pre class='raw-data'>" +
          escape(prettyJSON(data.geometry_json)) + "</pre></details>" +
          (revisions ? "<details class='road-detail-section'><summary>历史版本（" + data.versions.length +
            "）</summary>" + revisions + "</details>" : "<p class='muted'>暂无修订记录</p>");
      } catch (error) {
        if (generation === state.detailGeneration) {
          $("roadDetail").textContent = "";
          errorAt("detailError", error.message);
        }
      }
      return;
    }
    $("stationMetadata").open = false;
    $("metadata").innerHTML = metadataHTML(row);
    $("historyTable").innerHTML = "";
    $("historyBounds").textContent = "";
    $("historyPageInfo").textContent = "";
    $("historyPrevious").disabled = true;
    $("historyNext").disabled = true;
    state.detail.range = initialRange || recentWindow();
    $("historyMetric").innerHTML = "<option value=''>全部指标</option>";
    $("historyPeriod").value = "";
    state.historyPage = 1;
    await loadHistory(true);
  }
  function prettyJSON(value) {
    try { return JSON.stringify(JSON.parse(value), null, 2); } catch { return value || "—"; }
  }
  async function loadHistory(populate = false) {
    if (!state.detail || state.detail.isRoad || state.detail.isBoundary) return;
    const generation = ++state.detailGeneration;
    $("historyTable").style.opacity = ".5";
    errorAt("detailError", "");
    try {
      const range = state.detail.range;
      const value = { source: state.detail.source, site: state.detail.row.source_id, mode: "history", page: state.historyPage, page_size: $("pageSize").value, metric: $("historyMetric").value, period: $("historyPeriod").value, ...range };
      const data = await api("/api/measurements", value);
      if (generation !== state.detailGeneration || !$("detail").open) return;
      if (populate) {
        const metrics = [...new Set(data.metrics.map(row => row.metric))];
        $("historyMetric").innerHTML = "<option value=''>全部指标</option>" + metrics.map(metric => "<option value='" + escape(metric) + "'>" + escape(METRICS[metric] || metric) + "</option>").join("");
      }
      $("historyBounds").textContent = "查询范围 " + time(range.start) + " 至 " + time(range.end);
      const headings = ["观测时间", "指标", "数值", "单位", "时长", "质量", "获取时间", "时间依据"];
      const rows = data.items.map(row => [escape(time(row.valid_at_utc)), escape(METRICS[row.metric] || row.metric),
        escape(row.value_num ?? row.value_text), escape(row.unit || "单位未提供"),
        row.period_seconds ? row.period_seconds / 60 + " 分钟" : "快照", escape(row.quality),
        escape(time(row.fetched_at_utc)), escape(row.time_basis)]);
      $("historyTable").innerHTML = responsiveTable(headings, rows, "该时段暂无符合条件的观测");
      $("historyPageInfo").textContent = pageLabel(state.historyPage, data.total, Number($("pageSize").value));
      $("historyPrevious").disabled = state.historyPage <= 1;
      $("historyNext").disabled = state.historyPage * Number($("pageSize").value) >= data.total;
    } catch (error) {
      if (generation === state.detailGeneration) errorAt("detailError", error.message);
    } finally {
      if (generation === state.detailGeneration) $("historyTable").style.opacity = "";
    }
  }
  function refreshHistory() {
    state.detail.range = state.detail.replayRange || recentWindow();
    state.historyPage = 1;
    return loadHistory();
  }
  const updateLabels = {
    checking: "检查中…", running: "更新中…", waiting: "等待更新…",
    completed: "更新完成", completed_external: "已有任务完成", partial: "部分失败", error: "更新失败"
  };
  function renderUpdate(updateState, jobs = {}, message = "", finishedAt = null) {
    $("updateProgress").hidden = false;
    const summary = updateLabels[updateState] || updateState;
    if ($("updateStatus").textContent !== summary) $("updateStatus").textContent = summary;

    const rows = Object.entries(jobs).map(([name, result]) => {
      const row = document.createElement("li");
      row.textContent = (jobNames[name] || name) + " · " + (result === "running" ? "采集中" : result);
      return row;
    });
    $("updateJobs").replaceChildren(...rows);
    $("updateJobs").hidden = rows.length === 0;
    const emptyMessages = {
      checking: "正在检查到期任务…", running: "等待首个任务报告进度…",
      waiting: "已有采集任务运行，正在等待完成。", completed: "本轮没有到期任务。",
      completed_external: "已有采集任务已完成。"
    };
    errorAt("updateMessage", message || (rows.length === 0 ? emptyMessages[updateState] : ""));
    errorAt("updateFinished", finishedAt ? "完成时间 " + time(finishedAt) : "");
  }
  async function update() {
    if (state.updating) return;
    state.updating = true;
    state.lastUpdate = Date.now();
    $("updateButton").disabled = true;
    $("updateButton").textContent = "更新中…";
    renderUpdate("checking");
    let run;
    try {
      run = await api("/api/update", {}, { method: "POST" });
      while (run.state === "running" || run.state === "waiting") {
        renderUpdate(run.state, run.jobs);
        await refresh(false);
        await wait(2500);
        run = await api("/api/update/" + run.id);
      }
      await refresh();
      if ($("detail").open && !state.detail.isRoad && !state.detail.isBoundary) await refreshHistory();
      renderUpdate(run.state, run.jobs, run.error, run.finished_at);
    } catch (error) {
      renderUpdate("error", run?.jobs, error.message + "。可重新点击检查更新。", run?.finished_at);
    } finally {
      state.updating = false;
      $("updateButton").disabled = false;
      $("updateButton").textContent = "更新数据";
      state.lastUpdate = Date.now();
    }
  }
  function maybeUpdate() {
    const minutes = Number($("autoInterval").value);
    if (!document.hidden && minutes > 0 && Date.now() - state.lastUpdate >= minutes * 60000) update();
  }
  function resetTable() { state.page = 1; renderStatus(); loadTable(); }
  $("sources").addEventListener("click", event => {
    const button = event.target.closest("[data-source]");
    if (!button) return;
    state.source = button.dataset.source;
    $("search").value = "";
    $("showAll").checked = false;
    resetTable();
  });
  for (const id of ["laqnMode", "tflMode", "showAll", "pageSize", "region"]) $(id).addEventListener("change", resetTable);
  let searchTimer;
  $("search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(resetTable, 250); });
  $("previous").addEventListener("click", () => { state.page--; loadTable(); });
  $("next").addEventListener("click", () => { state.page++; loadTable(); });
  $("dataTable").addEventListener("click", event => {
    const button = event.target.closest("[data-item]");
    if (button) openDetail(Number(button.dataset.item));
  });
  $("closeDetail").addEventListener("click", () => $("detail").close());
  $("detail").addEventListener("close", () => { state.detailGeneration++; });
  $("queryHistory").addEventListener("click", refreshHistory);
  $("historyPrevious").addEventListener("click", () => { state.historyPage--; loadHistory(); });
  $("historyNext").addEventListener("click", () => { state.historyPage++; loadHistory(); });
  $("timezone").addEventListener("change", () => {
    renderTable(); renderStatus();
    if ($("detail").open && !state.detail.isRoad && !state.detail.isBoundary) { state.historyPage = 1; loadHistory(); }
    window.dispatchEvent(new CustomEvent("airwatch:timezone"));
  });
  $("updateButton").addEventListener("click", update);
  $("autoInterval").addEventListener("change", () => { state.lastUpdate = Date.now(); });
  document.addEventListener("visibilitychange", maybeUpdate);
  window.AirWatchData = {
    api, time, dateRange, calendarDate, recentWindow, metrics: METRICS,
    async openStation(source, site, range = null) {
      const data = await api("/api/locations", {source, site});
      if (data.items.length) await openDetail(-1, data.items[0], source, false, range);
    },
    async openRoad(id, at = null) {
      const row = await api("/api/road-events", {id, ...(at ? {at} : {})});
      await openDetail(-1, row, "tfl", true, at ? {at} : null);
    },
    showSource(source) {
      if (!Array.from($("sources").querySelectorAll("button")).some(button => button.dataset.source === source)) return;
      state.source = source;
      $("showAll").checked = false;
      $("search").value = "";
      resetTable();
    }
  };
  if (location.protocol === "file:") {
    errorAt("startupError", "请通过本地服务打开 index.html（例如在项目根目录运行 python run.py）。");
    $("updateButton").disabled = true;
    $("dataTable").setAttribute("aria-busy", "false");
  } else {
    refresh().catch(error => errorAt("startupError", error.message));
    state.timer = setInterval(maybeUpdate, 10000);
  }
})();
