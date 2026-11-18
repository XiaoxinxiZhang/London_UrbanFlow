const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'assets/city.js'), 'utf8');
const elements = new Map(), handlers = new Map(), sources = new Map(), layers = new Map(), visibility = new Map();
const tabs = ['overview','analysis','tables'].map(page => ({dataset:{page},addEventListener(){},classList:{toggle(){}},setAttribute(){},removeAttribute(){}}));
const tableButtons = ['region','borough','thames','tributary','laqn','breathe','bikes','roadPoint','roadArea'].map(gisTable => ({dataset:{gisTable},addEventListener(){},classList:{toggle(){}}}));
const layerIds = new Set(['layerBoundary','layerRegion','layerBorough','layerRiver','layerThames','layerTributary','layerAir','layerLaqn','layerBreathe','layerBikes','layerRoads','layerRoadPoints','layerRoadAreas']);
function element(id) {
  if (!elements.has(id)) elements.set(id, {
    value:({timezone:'Europe/London',cityRange:'48',cityMode:'latest',cityMetric:'PM25',cityPeriod:'900',basemap:'dark',mapColor:'concentration'})[id] || '',
    checked:layerIds.has(id), hidden:false, innerHTML:'', textContent:'', options:[], style:{},
    addEventListener(name,fn){this[name]=fn;}, classList:{toggle(){}}, setAttribute(){}, removeAttribute(){},
    append(){}, querySelector(){return null;},
  });
  return elements.get(id);
}
const map = {
  zoom:10, center:null, bounds:null,
  addControl(){}, addImage(){}, on(name,layer,handler){handlers.set(name + ':' + (typeof layer === 'string' ? layer : ''), handler || layer);},
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
  '/data/london_thames_rivers.geojson':JSON.parse(fs.readFileSync(path.join(root,'data/london_thames_rivers.geojson'))),
};
const context = {
  window:{AirWatchData:{api(url){if(staticFiles[url]) return Promise.resolve(staticFiles[url]); if(url==='/api/map' && mapReply) return Promise.resolve(mapReply); return new Promise(()=>{});},time:v=>v || '—',calendarDate:d=>d.toISOString().slice(0,10),dateRange(){throw Error('unused');}},maplibregl:library,Supercluster:require('../assets/vendor/supercluster-8.0.1.min.js'),addEventListener(){}},
  maplibregl:library,
  document:{getElementById:element,querySelectorAll(selector){return selector==='[data-page]' ? tabs : selector==='[data-gis-table]' ? tableButtons : [];},querySelector(){return null;},createElement(name){if(name==='canvas')return {getContext(){return {beginPath(){},moveTo(){},lineTo(){},closePath(){},fill(){},getImageData(){return {data:new Uint8ClampedArray(4096)};}};}};return {style:{},setAttribute(){},addEventListener(){},append(){},textContent:'',className:''};}},
  location:{protocol:'http:'},requestAnimationFrame:fn=>fn(),console,Date,Intl,Map,Set,Promise,
};
vm.runInNewContext(source.replace(/\}\)\(\);\s*$/, 'window.gisTest={S,renderGisTable,openGisTable,chooseGisFeature,drawLayers,drawPointClusters,visibility,legend,refreshCity};})();'), context);
handlers.get('style.load:')();
const {S,renderGisTable,openGisTable,chooseGisFeature,drawLayers,drawPointClusters,visibility:applyVisibility,legend,refreshCity} = context.window.gisTest;
const point = (id,lon,props={}) => ({type:'Feature',geometry:{type:'Point',coordinates:[lon,51.5]},properties:{id,name:id,...props}});
const polygon = {type:'Feature',geometry:{type:'Polygon',coordinates:[[[-.2,51.49],[-.19,51.49],[-.19,51.51],[-.2,51.51],[-.2,51.49]]]},properties:{id:'area',name:'Road area',severity:'Moderate'}};
const fixture = {
  air:{type:'FeatureCollection',features:[point('L1',-.1,{source:'laqn',value:5,unit:'µg/m³',observed_at:'2026-11-18T10:00:00Z'}),point('L2',-.1001,{source:'laqn',value:null}),point('B1',-.11,{source:'breathe',value:7}),point('W1',-.1101,{source:'breathe_wcc',value:8})]},
  bikes:{type:'FeatureCollection',features:[point('bike1',-.15,{bikes:0}),point('bike2',-.1501,{bikes:2})]},
  roads:{type:'FeatureCollection',features:[point('road1',-.18,{severity:'Serious'}),point('road2',-.1801,{severity:'Minimal'}),polygon]},
  breaks:[6],start:'2026-11-16T10:00:00Z',end:'2026-11-18T10:00:00Z',coverage:{total:4,valid:3,fresh:3},counts:{bikes:2,empty:1,full:0,roads:3},bike_info:{captured_at:null},road_info:{captured_at:null},source_stats:{},
};
const represented = markers => markers.reduce((sum,item)=>sum+(item.feature.properties.point_count || 1),0);
(async () => {
  S.data=fixture;
  drawLayers();
  assert.equal(represented(S.pointGroups.laqn.markers),2);
  assert.equal(represented(S.pointGroups.breathe.markers),2);
  assert.equal(represented(S.pointGroups.roadPoint.markers),2);
  assert.equal(represented(S.bikeMarkers),2);
  assert.equal(visibility.get('road-point'),'none');
  assert.equal(visibility.get('road-fill'),'visible');
  assert.equal(sources.get('city-roads').data.features.length,3);
  element('layerRoadPoints').checked=false; applyVisibility(); legend();
  assert.ok(S.pointGroups.roadPoint.markers.every(item=>item.element.hidden));
  assert.equal(visibility.get('road-fill'),'visible');
  assert.doesNotMatch(element('mapLegend').innerHTML,/道路点事件/);
  element('layerRoadPoints').checked=true;
  map.zoom=13; drawPointClusters(); applyVisibility();
  assert.equal(S.pointGroups.roadPoint.markers.length,0);
  assert.equal(visibility.get('road-point'),'visible');
  map.zoom=10; drawPointClusters(); applyVisibility();

  for (const [layer,expected] of [['laqn','L1'],['breathe','W1'],['bikes','bike1'],['roadPoint','road1'],['roadArea','Road area']]) {
    await openGisTable(layer);
    assert.match(element('gisTable').innerHTML,new RegExp(expected));
  }
  await openGisTable('breathe');
  assert.match(element('gisTable').innerHTML,/Breathe London · Westminster 导入/);
  await openGisTable('region'); assert.match(element('gisTable').innerHTML,/>London</);
  await openGisTable('borough'); assert.match(element('gisTableCount').textContent,/33/);
  assert.match(element('gisTable').innerHTML,/面积 km²/);
  await openGisTable('thames'); assert.match(element('gisTableCount').textContent,/86/);
  await openGisTable('tributary'); assert.match(element('gisTableCount').textContent,/701/);
  await chooseGisFeature('tributary',staticFiles['/data/london_thames_rivers.geojson'].features.filter(f=>f.properties.class==='tributary')[80],false,true);
  assert.equal(S.gisPage,2);
  assert.match(element('gisTable').innerHTML,/class='selected'/);
  await openGisTable('borough');
  handlers.get('click:london-borough-fill')({features:[staticFiles['/data/london_boroughs_2025.geojson'].features[0]],originalEvent:{}});
  await new Promise(setImmediate);
  assert.equal(S.gisSelected.key,'borough:E09000001');
  assert.match(element('gisTable').innerHTML,/class='selected'/);
  await openGisTable('tributary');
  element('gisTableSearch').value='River Lea'; renderGisTable();
  assert.match(element('gisTableCount').textContent,/匹配/);

  await openGisTable('laqn');
  await chooseGisFeature('laqn',fixture.air.features[0]);
  assert.equal(map.zoom,13);
  assert.match(element('gisTable').innerHTML,/class='selected'/);
  assert.equal(sources.get('city-selected').data.features[0].properties.id,'L1');
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
  mapReply={...fixture,air:{type:'FeatureCollection',features:[]},bikes:{type:'FeatureCollection',features:[]},roads:{type:'FeatureCollection',features:[]},coverage:{total:0,valid:0,fresh:0},counts:{bikes:0,empty:0,full:0,roads:0}};
  await refreshCity();
  assert.equal(S.gisSelected,null);
  assert.equal(sources.get('city-selected').data.features.length,0);
  assert.equal(S.pointGroups.roadPoint.markers.length,0);
  console.log('PASS GIS attribute tables, selection, visibility, point clustering, polygons, refresh and style reload');
})().catch(error=>{console.error(error);process.exitCode=1;});
