const fs=require('fs'),{JSDOM}=require('../web/tests/node_modules/jsdom'),{Resvg}=require('./svg-preview/node_modules/@resvg/resvg-js');
const dom=new JSDOM(fs.readFileSync('web/index.html','utf8'),{url:'http://fixture.invalid/app',runScripts:'outside-only'}),w=dom.window;w.HTMLDialogElement.prototype.showModal=function(){};w.HTMLDialogElement.prototype.close=function(){};
w.eval(fs.readFileSync('web/metric-status.js','utf8'));w.eval(fs.readFileSync('web/app.js','utf8').replace("page(location.hash.slice(1) || 'today'); action(load);","page(location.hash.slice(1) || 'today');")+"\nwindow.__weeklyChart=weeklyChart;");
for(const [key,values,reference,unit] of [['hours',[4.6,3.2,6.2,8.3,8.3],5.4,'ore'],['load',[272,191,300,434,391],263,'punti']]){
 const data=values.map((v,i)=>({start:['2026-09-14','2026-09-21','2026-09-28','2026-10-05','2026-10-12'][i],end:['2026-09-20','2026-09-27','2026-10-04','2026-10-11','2026-10-18'][i],[key]:v}));w.__chart=w.__weeklyChart(data,key,{reference,tone:'critical'});
 const svg=w.__chart.querySelector('svg');svg.setAttribute('xmlns','http://www.w3.org/2000/svg');const style=w.document.createElementNS('http://www.w3.org/2000/svg','style');style.textContent='text{font-family:Segoe UI,Arial,sans-serif}.axis-label{fill:#758c98;font-size:12px}.chart-value{font-size:15px;fill:#244554;font-weight:650}.chart-current{font-size:9px;fill:#385f70;letter-spacing:1.6px}';svg.prepend(style);
 fs.writeFileSync(`.qa/chart-${key}.svg`,svg.outerHTML);fs.writeFileSync(`.qa/chart-${key}.png`,new Resvg(svg.outerHTML,{background:'white',fitTo:{mode:'width',value:1100}}).render().asPng());
}
dom.window.close();
