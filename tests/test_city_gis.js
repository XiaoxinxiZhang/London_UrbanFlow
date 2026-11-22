const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'assets/city.js'), 'utf8');
const elements = new Map(), handlers = new Map(), sources = new Map(), layers = new Map(), images = new Map(), visibility = new Map();
const tabs = ['overview','analysis','tables'].map(page => ({dataset:{page},addEventListener(){},classList:{toggle(){}},setAttribute(){},removeAttribute(){}}));
const tableButtons = ['region','borough','laqn','breathe','bikes','roadPoint','roadArea'].map(gisTable => ({dataset:{gisTable},addEventListener(){},classList:{toggle(){}}}));
const layerIds = new Set(['layerBoundary','layerRegion','layerBorough','layerAir','layerLaqn','layerBreathe','layerBikes','layerRoads','layerRoadPoints','layerRoadAreas']);
function element(id) {
  if (!elements.has(id)) elements.set(id, {
    value:({timezone:'Europe/London',cityMode:'latest',cityMetric:'PM25'})[id] || '',
    checked:layerIds.has(id), hidden:false, innerHTML:'', textContent:'', options:[], style:{},
    addEventListener(name,fn){this[name]=fn;}, classList:{toggle(){}}, setAttribute(){}, removeAttribute(){},
    append(){}, querySelector(){return null;}, querySelectorAll(){return [];},
  });
  return elements.get(id);
}
const map = {
  zoom:10, center:null, bounds:null,
  addControl(){}, addImage(id,image){images.set(id,image);}, on(name,layer,handler){handlers.set(name + ':' + (typeof layer === 'string' ? layer : ''), handler || layer);},
  addSource(id,source){sources.set(id,{...source,setData(data){this.data=data;}});}, getSource(id){return sources.get(id);},
  addLayer(layer){layers.set(layer.id,layer);}, getLayer(id){return layers.get(id);}, moveLayer(){},
  setLayoutProperty(id,key,value){if(key==='visibility') visibility.set(id,value);}, setFilter(){},
  getZoom(){return this.zoom;}, getCanvas(){return {style:{}};}, resize(){},
  easeTo(options){this.center=options.center; this.zoom=options.zoom;},
  fitBounds(bounds){this.bounds=bounds;},
};
const library = {
  Map:function(){return map;}, NavigationControl:function(){}, AttributionControl:function(){},
  Marker:function({element}){this.element=element; this.setLngLat=()=>this; this.addTo=()=>this; this.remove=()=>{this.removed=true;};},
  Popup:function(){this.setLngLat=this.setDOMContent=this.addTo=()=>this; this.remove=()=>{};},
};
let mapReply = null;
const staticFiles = {
  '/data/london_region_2025.geojson':JSON.parse(fs.readFileSync(path.join(root,'data/london_region_2025.geojson'))),
  '/data/london_boroughs_2025.geojson':JSON.parse(fs.readFileSync(path.join(root,'data/london_boroughs_2025.geojson'))),
};
const context = {
  window:{AirWatchData:{api(url){if(staticFiles[url]) return Promise.resolve(staticFiles[url]); if(url==='/api/map' && mapReply) return Promise.resolve(mapReply); return new Promise(()=>{});},time:v=>v || '—',calendarDate:d=>d.toISOString().slice(0,10),recentWindow:require('../assets/live.js').recentWindow},maplibregl:library,Supercluster:require('../assets/vendor/supercluster-8.0.1.min.js'),addEventListener(){}},
  maplibregl:library,
  document:{getElementById:element,querySelectorAll(selector){return selector==='[data-page]' ? tabs : selector==='[data-gis-table]' ? tableButtons : [];},querySelector(){return null;},createElement(name){
    if(name==='canvas')return {getContext(){return {beginPath(){},moveTo(){},lineTo(){},closePath(){},fill(){},stroke(){},clearRect(){},getImageData(){return {data:new Uint8ClampedArray(4096)};}};}};
    return {style:{setProperty(key,value){this[key]=value;}},setAttribute(key,value){this[key]=value;},addEventListener(name,fn){this[name]=fn;},append(){},textContent:'',className:''};
  }},
  location:{protocol:'http:'},requestAnimationFrame:fn=>fn(),console,Date,Intl,Map,Set,Promise,
};
context.window.AirWatchInsights = require('../assets/insights.js');
vm.runInNewContext(source.replace(/\}\)\(\);\s*$/, 'window.gisTest={S,renderGisTable,openGisTable,chooseGisFeature,drawLayers,drawPointClusters,visibility,legend,refreshCity};})();'), context);
handlers.get('style.load:')();
const {S,renderGisTable,openGisTable,chooseGisFeature,drawLayers,drawPointClusters,visibility:applyVisibility,legend,refreshCity} = context.window.gisTest;
const point = (id,lon,props={}) => ({type:'Feature',geometry:{type:'Point',coordinates:[lon,51.5]},properties:{id,name:id,...props}});
const polygon = {type:'Feature',geometry:{type:'Polygon',coordinates:[[[-.2,51.49],[-.19,51.49],[-.19,51.51],[-.2,51.51],[-.2,51.49]]]},properties:{id:'area',name:'Road area',severity:'Moderate'}};
const fixture = {
  air:{type:'FeatureCollection',features:[point('L1',-.1,{source:'laqn',value:5,unit:'µg/m³',observed_at:'2026-11-18T10:00:00Z',stale:true}),point('L2',-.1001,{source:'laqn',value:null}),point('B1',-.11,{source:'breathe',value:7}),point('B2',-.1101,{source:'breathe',value:8})]},
  bikes:{type:'FeatureCollection',features:[point('bike1',-.15,{bikes:0}),point('bike2',-.1501,{bikes:2})]},
  roads:{type:'FeatureCollection',features:[point('road1',-.18,{severity:'Serious'}),point('road2',-.1801,{severity:'Minimal'}),polygon]},
  breaks:[6],start:'2026-11-16T10:00:00Z',end:'2026-11-18T10:00:00Z',coverage:{total:4,valid:3,fresh:3},counts:{bikes:2,empty:1,full:0,roads:3},bike_info:{captured_at:null},road_info:{captured_at:null},source_stats:{},
};
const represented = markers => markers.reduce((sum,item)=>sum+(item.feature.properties.point_count || 1),0);
const pointStyles = [['laqn','air-laqn','triangle','#55c7a5'],['breathe','air-breathe','diamond','#9bcf80'],['roadPoint','road-point','square','#ef7b84']];
(async () => {
  S.data=fixture;
  drawLayers();
  legend();
  const airColors = () => Array.from(sources.get('city-air').data.features, f => f.properties.color);
  assert.deepEqual(airColors(), ['#55c7a5','#758290','#9bcf80','#9bcf80']);
  assert.match(element('legend-air').innerHTML, /#55c7a5.*≤ 6.*#9bcf80.*&gt; 6/);
  assert.match(element('legend-bikes').innerHTML, /shape-circle.*自行车站数.*空站.*满站.*可用/);
  assert.match(element('legend-laqn').innerHTML, /shape-triangle.*聚合点数字为站点数/);
  assert.match(element('legend-breathe').innerHTML, /shape-diamond.*聚合点数字为站点数/);
  assert.match(element('legend-roadPoint').innerHTML, /shape-square.*聚合点数字为事件数/);
  element('layerAir').checked = false;
  element('layerAir').change();
  for (const id of ['legend-laqn', 'legend-breathe', 'legend-air'])
    assert.equal(element(id).innerHTML, '');
  element('layerAir').checked = true;
  element('layerAir').change();
  assert.match(element('legend-air').innerHTML, /≤ 6/);
  assert.equal(represented(S.pointGroups.laqn.markers),2);
  assert.equal(represented(S.pointGroups.breathe.markers),2);
  assert.equal(represented(S.pointGroups.roadPoint.markers),2);
  assert.equal(represented(S.bikeMarkers),2);
  for (const [group,,shape] of pointStyles) {
    const {element: button, feature} = S.pointGroups[group].markers[0];
    assert.equal(button.className, `gis-map-cluster shape-${shape}`);
    assert.equal(button.textContent, '2');
    assert.match(button['aria-label'], /2 个.*点击展开/);
    button.click({stopPropagation(){}});
    assert.deepEqual(map.center, feature.geometry.coordinates);
    assert.equal(map.zoom, S.pointGroups[group].index.getClusterExpansionZoom(feature.properties.cluster_id));
    map.zoom = 10;
  }
  assert.equal(S.bikeMarkers[0].element.className, 'bike-map-cluster');
  assert.equal(visibility.get('road-point'),'none');
  assert.equal(visibility.get('road-fill'),'visible');
  assert.equal(sources.get('city-roads').data.features.length,3);
  element('layerRoadPoints').checked=false; applyVisibility(); legend();
  assert.ok(S.pointGroups.roadPoint.markers.every(item=>item.element.hidden));
  assert.equal(visibility.get('road-fill'),'visible');
  assert.equal(element('legend-roadPoint').innerHTML,'');
  assert.match(element('legend-roadArea').innerHTML, /颜色表示严重程度/);
  element('layerRoadPoints').checked=true;
  map.zoom=13; handlers.get('moveend:')();
  for (const [group,layer,shape,fill] of pointStyles) {
    assert.equal(S.pointGroups[group].markers.length,0);
    assert.equal(visibility.get(layer),'visible');
    assert.equal(layers.get(layer).type, 'symbol');
    assert.equal(layers.get(layer).layout['icon-image'][1], `city-${shape}-`);
    assert.ok(images.has(`city-${shape}-${fill}`));
  }
  assert.equal(S.bikeMarkers.length,0);
  assert.equal(layers.get('bike-points').type,'circle');

  const separated = {...fixture,
    air:{...fixture.air,features:[point('L3',-.6,{source:'laqn',value:5}),point('B3',-.7,{source:'breathe',value:8})]},
    roads:{...fixture.roads,features:[point('road3',-.5,{severity:'Severe'}),polygon]},
    bikes:{...fixture.bikes,features:[point('bike3',-.4,{state:'available'})]},
  };
  map.zoom=10;
  S.data=separated;
  drawLayers();
  for (const [group,,shape,fill] of pointStyles) {
    const {element: button} = S.pointGroups[group].markers[0];
    assert.equal(button.className, `gis-map-point shape-${shape}`);
    assert.equal(button.textContent, '');
    assert.equal(button.style['--marker-color'], fill);
  }
  assert.equal(S.bikeMarkers[0].element.className,'bike-map-station');

  const clusterLibrary = context.window.Supercluster;
  context.window.Supercluster = null;
  S.data = fixture;
  drawLayers();
  for (const [group,layer] of pointStyles) {
    assert.equal(S.pointGroups[group].markers.length,0);
    assert.equal(visibility.get(layer),'visible');
  }
  assert.equal(visibility.get('bike-points'),'visible');
  context.window.Supercluster = clusterLibrary;
  S.data = {...fixture, bikes:{...fixture.bikes}, roads:{...fixture.roads}};
  drawLayers();

  for (const [layer,expected] of [['laqn','L1'],['breathe','B2'],['bikes','bike1'],['roadPoint','road1'],['roadArea','Road area']]) {
    await openGisTable(layer);
    assert.match(element('gisTable').innerHTML,new RegExp(expected));
  }
  await openGisTable('breathe');
  assert.match(element('gisTable').innerHTML,/Breathe London/);
  await openGisTable('region'); assert.match(element('gisTable').innerHTML,/>London</);
  await openGisTable('borough'); assert.match(element('gisTableCount').textContent,/33/);
  assert.match(element('gisTable').innerHTML,/面积 km²/);
  await openGisTable('borough');
  handlers.get('click:london-borough-fill')({features:[staticFiles['/data/london_boroughs_2025.geojson'].features[0]],originalEvent:{}});
  await new Promise(setImmediate);
  assert.equal(S.gisSelected.key,'borough:E09000001');
  assert.match(element('gisTable').innerHTML,/class='selected'/);
  await openGisTable('borough');
  element('gisTableSearch').value='Westminster'; renderGisTable();
  assert.match(element('gisTableCount').textContent,/匹配/);

  await openGisTable('laqn');
  await chooseGisFeature('laqn',fixture.air.features[0]);
  assert.equal(map.zoom,13);
  assert.match(element('gisTable').innerHTML,/class='selected'/);
  assert.equal(sources.get('city-selected').data.features[0].properties.id,'L1');

  const {showPage} = context.window.AirWatchCity;
  const airLayers = ['air-laqn','air-breathe','air-values'];
  const journeyLayers = ['region','borough','bikes','roadPoint','roadArea'];
  legend();
  const overviewLegends = journeyLayers.map(layer => element('legend-' + layer).innerHTML);
  assert.ok(overviewLegends.every(Boolean));
  handlers.get('moveend:')();
  assert.ok(airLayers.every(id => visibility.get(id) === 'visible'));
  element('layerBoundary').checked = false;
  element('layerBikes').checked = false;
  showPage('journey');
  assert.deepEqual(journeyLayers.map(layer => element('journey-legend-' + layer).innerHTML), overviewLegends);
  for (const layer of ['laqn','breathe','air'])
    assert.equal(element('journey-legend-' + layer).innerHTML, '');
  assert.ok(airLayers.every(id => visibility.get(id) === 'none'));
  assert.equal(sources.get('city-selected').data.features.length,0);
  assert.equal(S.popup,null);
  for (const id of ['london-outer-boundary','london-borough-boundaries','bike-points','road-point','road-fill','road-line'])
    assert.equal(visibility.get(id),'visible');

  map.zoom = 10;
  sources.clear(); layers.clear(); images.clear(); handlers.get('style.load:')();
  for (const [,,shape,fill] of pointStyles) assert.ok(images.has(`city-${shape}-${fill}`));
  assert.ok(airLayers.every(id => visibility.get(id) === 'none'));
  for (const name of ['laqn','breathe']) {
    assert.ok(S.pointGroups[name].markers.length);
    assert.ok(S.pointGroups[name].markers.every(item => item.element.hidden));
  }
  assert.ok(S.bikeMarkers.some(item => !item.element.hidden));
  assert.ok(S.pointGroups.roadPoint.markers.some(item => !item.element.hidden));

  showPage('overview');
  for (const layer of ['region','borough','bikes']) assert.equal(element('legend-' + layer).innerHTML, '');
  assert.match(element('legend-air').innerHTML, /≤ 6/);
  assert.equal(visibility.get('london-outer-boundary'),'none');
  assert.ok(S.bikeMarkers.every(item => item.element.hidden));
  for (const name of ['laqn','breathe'])
    assert.ok(S.pointGroups[name].markers.every(item => !item.element.hidden));
  assert.equal(sources.get('city-selected').data.features[0].properties.id,'L1');
  element('layerBoundary').checked = true;
  element('layerBikes').checked = true;
  applyVisibility();

  await chooseGisFeature('roadArea',polygon);
  assert.equal(map.bounds[0][0],-.2);
  assert.equal(sources.get('city-selected').data.features[0].geometry.type,'Polygon');
  const click = handlers.get('click:road-point');
  click({features:[fixture.roads.features[0]],originalEvent:{},lngLat:{lng:-.18,lat:51.5}});
  await Promise.resolve(); await Promise.resolve();
  assert.equal(S.gisSelected.key,'roadPoint:road1');
  assert.equal(S.gisLayer,'roadPoint');
  element('layerRoads').checked=false; applyVisibility();
  assert.equal(visibility.get('road-fill'),'none');
  element('layerRoads').checked=true;

  sources.clear(); layers.clear(); handlers.get('style.load:')();
  assert.equal(sources.get('city-selected').data.features[0].properties.id,'road1');
  assert.equal(visibility.get('road-fill'),'visible');
  S.selected=null;
  element('cityMetric').value = 'NO2';
  mapReply={...fixture,breaks:[12],air:{type:'FeatureCollection',features:[]},bikes:{type:'FeatureCollection',features:[]},roads:{type:'FeatureCollection',features:[]},coverage:{total:0,valid:0,fresh:0},counts:{bikes:0,empty:0,full:0,roads:0}};
  await refreshCity();
  assert.match(element('legend-air').innerHTML, /#55c7a5.*≤ 12.*#9bcf80.*&gt; 12/);
  assert.equal(element('mapWindow').textContent, `时间范围：${fixture.start} 至 ${fixture.end}`);
  assert.equal(S.gisSelected,null);
  assert.equal(sources.get('city-selected').data.features.length,0);
  assert.equal(S.pointGroups.roadPoint.markers.length,0);
  console.log('PASS GIS attribute tables, selection, visibility, point clustering, polygons, refresh and style reload');
})().catch(error=>{console.error(error);process.exitCode=1;});
