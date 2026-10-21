/** Stable names for inspection, documentation and reusable factory calls. */
export const componentCatalog=Object.freeze([
  ['button','玻璃按钮','Glass button',"{label:'Save',onPress:()=>console.log('Saved')}"],
  ['icon-button','图标按钮','Icon button',"{label:'Favorite',icon:'heart',pressed:false,onChange:console.log}"],
  ['switch','滑动开关','Sliding switch',"{label:'Focus',checked:false,onChange:console.log}"],
  ['toggle-tile','连接开关','Connection tile',"{label:'Wi-Fi',icon:'wifi',checked:true,onLabel:'Connected',offLabel:'Off'}"],
  ['choices','选项胶囊','Choice capsules',"{label:'Scene',options:[{value:'day',label:'Day'},{value:'night',label:'Night'}]}"],
  ['tabs','分段标签','Segmented tabs',"{label:'View',options:[{value:'a',label:'Overview',content:'Overview content'},{value:'b',label:'Details',content:'Detail content'}]}"],
  ['dock','浮动导航','Floating dock',"{label:'Navigation',options:[{value:'home',label:'Home',icon:'home'},{value:'saved',label:'Saved',icon:'heart'}],onChange:console.log}"],
  ['slider','光学滑杆','Optical slider',"{label:'Brightness',min:0,max:100,value:70,onChange:console.log}"],
  ['stepper','数值步进器','Numeric stepper',"{label:'Duration',min:5,max:60,step:5,value:25,unit:'min'}"],
  ['notification','玻璃通知','Glass notification',"{title:'Ready to focus',description:'Make room for an idea.',onDismiss:()=>console.log('Dismissed')}"],
  ['menu','操作菜单','Action menu',"{label:'More actions',options:[{value:'save',label:'Save',icon:'heart'},{value:'reset',label:'Reset',icon:'reset'}],onAction:console.log}"],
  ['search','折射搜索框','Refractive search',"{label:'Search',placeholder:'Find a record',onChange:console.log}"],
  ['panel','弹性面板','Elastic panel',"{label:'Drag panel',children:['Your content']}"],
  ['lens','自由透镜','Free lens',"{label:'Move the lens',caption:'Hold and drag'}"],
  ['card-list','透镜卡片列表','Lens card list',"{label:'Featured tools',items:[{id:'notes',label:'Notes',description:'Capture a thought'},{id:'focus',label:'Focus',children:['25 min']}]}"],
  ['progress','光导进度条','Light-guided progress',"{label:'Progress',value:38,completionColor:'green'}"],
  ['drop-select','凹槽吸附选择','Magnetic well selector',"{label:'Choose a scene',variant:'lens',options:[{value:'day',label:'Day',icon:'sun'},{value:'night',label:'Night',icon:'moon'}]}"],
  ['xy-slider','二维滤镜滑块','Two-dimensional filter',"{label:'Filter',xLabel:'Warmth',yLabel:'Tone',variant:'lens',value:{x:0,y:0}}"],
].map(([name,zh,en,props])=>Object.freeze({name,zh,en,props,kind:'component'})));

export const showcasePatterns=Object.freeze([
  {name:'line-chart',zh:'玻璃光导折线',en:'Glass line chart'},
  {name:'bar-chart',zh:'液体柱状图',en:'Liquid bar chart'},
  {name:'ring-chart',zh:'彩色玻璃环图',en:'Colored glass ring'},
  {name:'records-table',zh:'光学记录表',en:'Optical records table'},
].map(item=>Object.freeze({...item,kind:'composition'})));

/** Visible names belong to the examples, never inside an application control. */
export function labelComponentSample(host,name,language='zh',{heading=true}={}){
  const entry=[...componentCatalog,...showcasePatterns].find(item=>item.name===name);if(!entry)throw new TypeError(`Unknown sample: ${name}`);
  const en=language==='en',anchor=`component-${name}`;host.dataset.componentName=name;
  const label=host.querySelector(':scope > h2, :scope > h3')||document.createElement(heading?'h3':'p');label.classList.add('component-label');label.replaceChildren();
  if(!host.id)host.id=anchor;else if(host.id!==anchor)label.id=anchor;
  const text=document.createElement('span'),code=document.createElement('code');text.textContent=en?entry.en:entry.zh;code.textContent=name;label.append(text,code);host.prepend(label);
  const details=document.createElement('details'),summary=document.createElement('summary'),pre=document.createElement('pre'),snippet=document.createElement('code');details.className='component-usage';summary.textContent=en?'Usage example':'使用示例';
  snippet.textContent=entry.kind==='component'?`import {createComponent, createGlassSystem} from './design-system/index.js';\n\n// Load tokens.css and components.css; provide <div id="stage" class="lg-stage" style="min-height:280px;padding:28px">.\nconst stage = document.querySelector('#stage');\nconst component = createComponent('${name}', ${entry.props});\nstage.append(component.element);\nconst glass = createGlassSystem(stage);\n\n// Call on unmount, not immediately:\n// glass.destroy();\n// component.destroy();`:`import {mountShowcaseCharts} from './design-system/showcase-charts.js';\n\n// ${en?'This example belongs to the shared chart and records group.':'此示例属于共享数据的图表与记录表组合。'}\n// Load tokens.css, components.css, showcase.css and showcase-records.css.\nconst host = document.querySelector('#charts');\nconst charts = mountShowcaseCharts(host, {language:'${language}'});\n// charts.snapshot();\n// charts.destroy();`;
  pre.append(snippet);details.append(summary,pre);host.append(details);return entry;
}
