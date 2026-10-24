const fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('../../web/tests/node_modules/jsdom');
const {Resvg}=require('../svg-preview/node_modules/@resvg/resvg-js');
const project=path.resolve(__dirname,'../..');
const dom=new JSDOM(fs.readFileSync(path.join(project,'web/index.html'),'utf8'),{url:'http://127.0.0.1:8000/app',runScripts:'outside-only'}),w=dom.window;
w.setInterval=()=>1;w.clearInterval=()=>{};
w.eval(fs.readFileSync(path.join(project,'web/metric-status.js'),'utf8'));
w.eval(fs.readFileSync(path.join(project,'web/charts.js'),'utf8'));
const app=fs.readFileSync(path.join(project,'web/app.js'),'utf8').replace("page(location.hash.slice(1) || 'today'); action(load);","page(location.hash.slice(1) || 'today');");w.eval(app);
function render(svg,name,label){const clone=svg.cloneNode(true);clone.setAttribute('xmlns','http://www.w3.org/2000/svg');clone.setAttribute('viewBox','0 0 640 310');const style=w.document.createElementNS('http://www.w3.org/2000/svg','style');style.textContent='text{font-family:Segoe UI,Arial,sans-serif}.axis-label{font-size:12px;fill:#697f8d}.axis-unit{font-size:12px;fill:#6e8390}';clone.prepend(style);const text=w.document.createElementNS('http://www.w3.org/2000/svg','text');text.setAttribute('x','60');text.setAttribute('y','280');text.setAttribute('fill','#326346');text.setAttribute('font-size','13');text.textContent=label;clone.append(text);const source=clone.outerHTML;fs.writeFileSync(path.join(__dirname,name+'.svg'),source);fs.writeFileSync(path.join(__dirname,name+'.png'),new Resvg(source,{fitTo:{mode:'width',value:1152},background:'#ffffff'}).render().asPng());}
const series={kind:'heart_rate',label:'Frequenza cardiaca',unit:'bpm',points:Array.from({length:90},(_,i)=>({time_s:i*30,value:i>33&&i<38?null:Math.round(125+Math.sin(i/10)*12+Math.sin(i*1.3)*5+(i>45?20:0))}))};
const activity=w.IronCoachCharts.activity(series,{reference:{lower:130,upper:155,label:'Fascia impostata da te'}});render(activity.querySelector('svg'),'activity-chart','Fascia obiettivo personale: 130–155 bpm · interruzioni conservate');
const points=Array.from({length:20},(_,i)=>({date:new Date(Date.UTC(2026,8,28+i)).toISOString().slice(0,10),source:'intervals',value:Math.round(48+Math.sin(i*.5)*5+(i%3))}));
const health=w.IronCoachUI.sparkline({kind:'hrv_ms',label:'HRV',unit:'ms',latest:points.at(-1),points,stale:false});render(health,'health-chart','Fascia abituale personale · riferimento descrittivo, non obiettivo');
console.log(JSON.stringify({rendered:['activity-chart.png','health-chart.png']}));dom.window.close();
