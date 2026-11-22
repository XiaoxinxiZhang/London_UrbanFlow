(() => {
  "use strict";
  const $ = id => document.getElementById(id);
  const city = window.AirWatchCity;
  if (!city || !$('journeyPage')) return;
  const state = {start:null, end:null, picking:null, result:null, request:null};
  const labels = {walk_to_bike:"① 步行取车", cycle:"② 骑行至还车站",
    walk_to_destination:"③ 步行到终点", walk:"步行", personal_bike:"个人自行车骑行"};
  const metrics = [["PM25", "PM₂.₅"], ["PM10", "PM₁₀"], ["NO2", "NO₂"], ["O3", "O₃"]];
  const airSources = [["laqn", "LAQN"], ["breathe", "Breathe London"]];
  const tabs = [["journeyRouteTab", "journeyRoutePanel"], ["journeyEnvironmentTab", "journeyEnvironmentPanel"]];
  const esc = value => String(value ?? "—").replace(/[&<>"']/g, c =>
    ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  const km = meters => meters >= 1000 ? (meters/1000).toFixed(2) + " 公里" : Math.round(meters) + " 米";
  const minutes = seconds => Math.max(1, Math.round(seconds/60)) + " 分钟";
  const time = value => window.AirWatchData?.time?.(value) || value || "—";
  function updatePlanButton() {
    $("planJourney").disabled = !!state.request || !state.start || !state.end;
  }
  function setLabels() {
    $("journeyPage").dataset.picking = state.picking || "";
    $("startLabel").textContent = state.start ? state.start.map(v => v.toFixed(5)).join(", ") : "尚未选择";
    $("endLabel").textContent = state.end ? state.end.map(v => v.toFixed(5)).join(", ") : "尚未选择";
    const mapHint = $("journeyMapHint");
    const hintText = state.picking ? "点击地图设置" + (state.picking === "start" ? "起点" : "终点") :
      state.start && state.end ? "" : "选择起点与终点";
    mapHint.textContent = hintText;
    mapHint.hidden = !hintText;
    $("pickStart").setAttribute("aria-pressed", String(state.picking === "start"));
    $("pickEnd").setAttribute("aria-pressed", String(state.picking === "end"));
    $("maxWalkLabel").hidden = $("journeyMode").value !== "shared_bike";
    updatePlanButton();
    draw();
  }
  function showResult(route, environment) {
    $("journeyResult").innerHTML = route;
    $("journeyEnvironment").innerHTML = environment;
    for (const [, panel] of tabs) $(panel).scrollTop = 0;
  }
  function selectTab(index) {
    tabs.forEach(([tab, panel], current) => {
      const selected = current === index;
      $(tab).setAttribute("aria-selected", String(selected));
      $(tab).tabIndex = selected ? 0 : -1;
      $(panel).hidden = !selected;
    });
  }
  function resetPlan() {
    state.request?.abort();
    state.request = null;
    state.result = null;
    const message = state.start && state.end ? "出行设置已更新，请重新规划路线。" : "在地图上依次选择起点和终点。";
    showResult(`<p class="journey-status">${message}</p>`,
      '<p class="journey-status">规划路线后查看沿途空气质量。</p>');
    setLabels();
  }
  function blank() { return {type:"FeatureCollection",features:[]}; }
  function ensureLayers(map) {
    // The style can accept layers while its sources are still loading.
    if (!map || (!map.getLayer("journey-point-label") && !map.getStyle())) return false;
    const visibility = $("journeyPage").hidden ? "none" : "visible";
    if (!map.getSource("journey-legs")) map.addSource("journey-legs", {type:"geojson",data:blank()});
    if (!map.getSource("journey-points")) map.addSource("journey-points", {type:"geojson",data:blank()});
    if (!map.getLayer("journey-leg-line")) map.addLayer({id:"journey-leg-line",type:"line",source:"journey-legs",
      layout:{visibility,"line-cap":"round","line-join":"round"},paint:{"line-color":["match",["get","kind"],"cycle","#63dfc6","personal_bike","#63dfc6","#f5c36c"],"line-width":6,"line-opacity":.9}});
    if (!map.getLayer("journey-point-dot")) map.addLayer({id:"journey-point-dot",type:"circle",source:"journey-points",
      layout:{visibility},
      paint:{"circle-radius":9,"circle-color":["match",["get","kind"],"start","#65e3bd","end","#ef8b8b","pickup","#59bbdf","#b18ade"],
        "circle-stroke-color":"#102131","circle-stroke-width":2}});
    if (!map.getLayer("journey-point-label")) map.addLayer({id:"journey-point-label",type:"symbol",source:"journey-points",
      layout:{visibility,"text-field":["get","label"],"text-size":13,"text-offset":[0,1.5],"text-allow-overlap":true},
      paint:{"text-color":"#fff","text-halo-color":"#102131","text-halo-width":2}});
    return true;
  }
  function draw() {
    const map = city.getMap();
    if (!ensureLayers(map)) return;
    const point = (coords, kind, label) => ({type:"Feature",geometry:{type:"Point",coordinates:coords},properties:{kind,label}});
    const features = [];
    if (state.start) features.push(point(state.start,"start","起点"));
    if (state.end) features.push(point(state.end,"end","终点"));
    const pair = state.result?.alternatives?.[0];
    if (state.result?.legs?.length === 3 && pair) {
      features.push(point(state.result.legs[0].geometry.coordinates.at(-1),"pickup","取车"));
      features.push(point(state.result.legs[1].geometry.coordinates.at(-1),"dropoff","还车"));
    }
    map.getSource("journey-points").setData({type:"FeatureCollection",features});
    map.getSource("journey-legs").setData({type:"FeatureCollection",features:(state.result?.legs || []).map(leg =>
      ({type:"Feature",geometry:leg.geometry,properties:{kind:leg.kind}}))});
  }
  const distanceRange = segment => `${km(segment.from_m)}–${km(segment.to_m)}`;
  const airUnit = unit => unit === "ug/m3" ? "µg/m³" : esc(unit);
  function renderEstimate(reading) {
    if (!reading) return '<span class="journey-air-meta">暂无数据</span>';
    const metadata = [`${esc(reading.station_count)} 站`];
    if (Number.isFinite(reading.nearest_m)) metadata.push(`最近 ${km(reading.nearest_m)}`);
    if (reading.latest_at) metadata.push(`最新 ${esc(time(reading.latest_at))}`);
    const value = reading.status === "estimated" ? `${esc(reading.value)} ${airUnit(reading.unit)}` : "站点不足";
    return `<strong class="journey-air-reading">${value}</strong><span class="journey-air-meta">${metadata.join(" · ")}</span>`;
  }
  function renderAirTable(title, cell, showCaption = true) {
    const headers = airSources.map(([, name]) => `<th scope="col">${name}</th>`).join("");
    const rows = metrics.map(([metric, name]) => `<tr><th scope="row">${name}</th>${airSources
      .map(([source]) => `<td>${cell(metric, source)}</td>`).join("")}</tr>`).join("");
    return `<table class="journey-air-table"${showCaption ? "" : ` aria-label="${title}"`}>
      ${showCaption ? `<caption>${title}</caption>` : ""}
      <thead><tr><th scope="col">污染物</th>${headers}</tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  }
  function renderAirSummary(summary, segments) {
    if (!summary || summary.peak_segment_index == null) {
      return '<strong>站点不足</strong><span class="journey-air-meta">覆盖 0%</span>';
    }
    const percent = Math.round(summary.coverage * 100);
    const coverage = percent === 100 && summary.coverage < 1 ? "&gt;99%" : percent === 0 ? "&lt;1%" : `${percent}%`;
    const range = summary.min === summary.max ? esc(summary.max) : `${esc(summary.min)}–${esc(summary.max)}`;
    const peak = segments[summary.peak_segment_index];
    return `<strong class="journey-air-reading">${range} ${airUnit(summary.unit)}</strong>
      <span class="journey-air-meta">覆盖 ${coverage}</span>${peak ? `<span class="journey-air-meta">最高路段 ${distanceRange(peak)}</span>` : ""}`;
  }
  function renderRouteAir(routeAir, legs) {
    if (legs.length && routeAir == null) {
      return '<p class="journey-status">路线已生成。本地服务缺少沿途估算结果，请重启本地服务后重新规划。</p>';
    }
    if (routeAir?.status !== "available" || !routeAir.segments?.length) {
      return '<p class="journey-status">沿途空气质量估算需要有效的道路路线。</p>';
    }
    const segments = routeAir.segments;
    const overview = renderAirTable("沿途浓度估计", (metric, source) =>
      renderAirSummary(routeAir.summary?.[metric]?.[source], segments));
    const details = segments.map(segment => {
      const range = distanceRange(segment);
      const mode = labels[legs[segment.leg_index]?.kind] || "";
      return `<details class="journey-air-segment"><summary>${range} · ${mode}</summary>${
        renderAirTable(`${range} 浓度估计`, (metric, source) => renderEstimate(segment.readings?.[metric]?.[source]), false)}</details>`;
    }).join("");
    return `${overview}
      <p class="journey-air-meta">浓度按各段中点周边监测站加权估算。覆盖率按路线距离计算，路段按距起点的累计距离标示。</p>
      <p class="journey-air-meta">估算需 2 公里内至少 2 个近 3 小时有有效观测的同源站点，且最近站点在 1 公里内。</p>
      <details class="journey-context journey-air-segments"><summary>按距离查看各段</summary>${details}</details>`;
  }
  function renderContext(context, legs) {
    const daqi = context.daqi || {};
    let advice = "官方 DAQI 暂无有效数据，不自动判断是否适合外出。";
    if (daqi.status === "fresh") {
      advice = daqi.index <= 3 ? "普通人群可按通常方式安排户外活动；敏感人群仍应遵循个人健康计划。" :
        daqi.index <= 6 ? "普通人群可照常活动；有心肺疾病者若症状加重，应调整户外体力活动。" :
        "建议减少污染暴露；老年人及心肺疾病敏感人群应调整较费力的户外活动。";
    }
    const observedAt = daqi.observed_at ? `（${esc(time(daqi.observed_at))}）` : "";
    const regionalIndex = daqi.status === "fresh" ? `${esc(daqi.index)} · ${esc(daqi.band)}${observedAt}` : "缺失或过期";
    return `${renderRouteAir(context.route_air, legs)}
      <section class="journey-environment"><h3>大伦敦官方 DAQI</h3><p>${regionalIndex}</p><p>${advice}</p><p><a href="https://www.gov.uk/government/publications/health-effects-of-air-pollution/health-advice-for-the-daily-air-quality-index-daqi" target="_blank" rel="noopener">查看 Defra 分人群指导</a></p></section>`;
  }
  function render() {
    const result = state.result;
    if (!result) return;
    let html = "";
    const legs = result.legs || [];
    if (legs.length) {
      const totalDistance = legs.reduce((sum, leg) => sum + leg.distance_m, 0);
      const totalTime = legs.reduce((sum, leg) => sum + leg.duration_s, 0);
      html += `<dl class="journey-total">
        <div><dt>预计用时</dt><dd>${minutes(totalTime)}</dd></div>
        <div><dt>总距离</dt><dd>${km(totalDistance)}</dd></div>
        <div><dt>路线段数</dt><dd>${legs.length} 段</dd></div>
      </dl>`;
    }
    if (result.routing_error) html += `<p class="journey-warning">${esc(result.routing_error)}</p>`;
    if (result.message) {
      const fallback = result.mode === "shared_bike" && legs[0]?.kind === "walk";
      html += `<p class="journey-warning">${fallback ? "当前显示步行备选路线。" : ""}${esc(result.message)}</p>`;
    }
    if (legs.length) {
      const pair = result.alternatives[0];
      if (legs.length === 3 && pair) {
        html += `<dl class="journey-stops">
          <div class="journey-stop"><dt>取车</dt><dd><span>${esc(pair.pickup.name)}</span><span class="journey-stop-count">${pair.pickup.bikes} 辆</span></dd></div>
          <div class="journey-stop"><dt>还车</dt><dd><span>${esc(pair.dropoff.name)}</span><span class="journey-stop-count">${pair.dropoff.empty_docks} 空位</span></dd></div>
        </dl>`;
      }
      html += `<section><h3>路线分段</h3>${legs.map(leg => `<details class="journey-leg"${legs.length === 1 && leg.kind !== "cycle" ? " open" : ""}>
        <summary>${labels[leg.kind]}<span>${km(leg.distance_m)} · ${minutes(leg.duration_s)}</span></summary>
        <ol>${(leg.steps || []).map(step => `<li>${esc(step.instruction)}</li>`).join("")}</ol>
      </details>`).join("")}</section>`;
      if (result.alternatives.length > 1) html += `<div class="journey-alternatives"><h3>备选站点</h3>${result.alternatives.slice(1).map(pair => `<button type="button" data-pickup="${esc(pair.pickup.id)}" data-dropoff="${esc(pair.dropoff.id)}">${esc(pair.pickup.name)} → ${esc(pair.dropoff.name)} · 约 ${minutes(pair.duration_s)}</button>`).join("")}</div>`;
      if (result.alerts.length) html += `<div class="journey-alerts"><h3>路线附近道路事件</h3><ul>${result.alerts.map(a => `<li>${esc(a.name)} · ${esc(a.category)} · 距路线约 ${km(a.distance_m)}</li>`).join("")}</ul></div>`;
    }
    if (result.bike_info?.captured_at && result.mode === "shared_bike") html += `<p class="muted">站点快照：${esc(time(result.bike_info.captured_at))}。车辆和空位未预留，出发前请重新规划。</p>`;
    showResult(html, renderContext(result.context || {}, legs));
    draw();
    if (!$("journeyPage").hidden && legs.length) {
      const coords = legs.flatMap(leg => leg.geometry.coordinates);
      const xs = coords.map(p => p[0]), ys = coords.map(p => p[1]);
      city.getMap()?.fitBounds?.([[Math.min(...xs),Math.min(...ys)],[Math.max(...xs),Math.max(...ys)]],
        {padding:70,maxZoom:15,duration:650});
    }
  }
  async function plan(pair) {
    if (!state.start || !state.end || state.request) return;
    const request = new AbortController();
    state.request = request;
    state.result = null;
    updatePlanButton();
    draw();
    showResult('<p class="journey-status">正在检查站点并计算道路路线…</p>',
      '<p class="journey-status">正在规划路线并估算沿途空气质量…</p>');
    try {
      const payload = {start:state.start,end:state.end,mode:$("journeyMode").value,
        max_walk_m:Number($("maxWalk").value)};
      if (pair) { payload.pickup_id = pair[0]; payload.dropoff_id = pair[1]; }
      const response = await fetch("/api/journeys/plan", {method:"POST",cache:"no-store",
        headers:{"Content-Type":"application/json"},body:JSON.stringify(payload),signal:request.signal});
      const data = await response.json();
      if (state.request !== request) return;
      if (!response.ok) throw new Error(data.error || "规划失败");
      state.result = data;
      render();
    } catch (error) {
      if (state.request !== request) return;
      state.result = null;
      draw();
      showResult(`<p class="journey-warning journey-error">${esc(error.message)}</p>`,
        '<p class="journey-status">路线规划失败，请重新规划后查看沿途空气质量。</p>');
    } finally {
      if (state.request === request) {
        state.request = null;
        updatePlanButton();
      }
    }
  }
  function bindMap() {
    const map = city.getMap();
    if (!map || map.__airwatchJourneyBound) return;
    map.__airwatchJourneyBound = true;
    map.on("style.load", draw);
    map.on("click", event => {
      if ($("journeyPage").hidden || !state.picking) return;
      state[state.picking] = [event.lngLat.lng,event.lngLat.lat];
      state.picking = null;
      resetPlan();
    });
  }
  $("pickStart").addEventListener("click", () => {state.picking="start";city.showPage("journey");setLabels();bindMap();});
  $("pickEnd").addEventListener("click", () => {state.picking="end";city.showPage("journey");setLabels();bindMap();});
  $("swapJourney").addEventListener("click", () => {
    [state.start, state.end] = [state.end, state.start];
    resetPlan();
  });
  $("clearJourney").addEventListener("click", () => {
    state.start = state.end = state.picking = null;
    resetPlan();
  });
  $("journeyMode").addEventListener("change", resetPlan);
  $("maxWalk").addEventListener("change", resetPlan);
  $("planJourney").addEventListener("click", () => plan());
  tabs.forEach(([tab], index) => {
    $(tab).addEventListener("click", () => selectTab(index));
    $(tab).addEventListener("keydown", event => {
      const destination = {ArrowLeft: (index + tabs.length - 1) % tabs.length,
        ArrowRight: (index + 1) % tabs.length, Home: 0, End: tabs.length - 1}[event.key];
      if (destination === undefined) return;
      event.preventDefault();
      selectTab(destination);
      $(tabs[destination][0]).focus();
    });
  });
  $("journeyResult").addEventListener("click", event => {
    const button = event.target.closest("[data-pickup]");
    if (button) return plan([button.dataset.pickup,button.dataset.dropoff]);
  });
  document.querySelector('[data-page="journey"]')?.addEventListener("click", () => {
    bindMap();
    setTimeout(bindMap,0);
    requestAnimationFrame(() => {
      if ($("journeyPage").hidden) return;
      if (state.result) render(); else city.fitLondonView?.();
    });
  });
  bindMap();
  setLabels();
})();
