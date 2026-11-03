/* DOM contracts for history checks; mocked HTTP, no providers or credentials. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");
const dom = new JSDOM("<main></main>",{url:"http://127.0.0.1:8000",runScripts:"outside-only"});
const w=dom.window;
if(!w.crypto.randomUUID) w.crypto.randomUUID=require("node:crypto").randomUUID;
w.eval(fs.readFileSync(path.join(__dirname,"../app/static/vehicle-checks.js"),"utf8"));
const panel=w.VehicleChecks, main=w.document.querySelector("main");
const lot={id:1,vin:"XTA219050M0123456"};
const tick=()=>new Promise(r=>setImmediate(r));
const settle=async()=>{for(let i=0;i<6;i++)await tick();};
const check={status:"done",mode:"live",expires_at:"2026-11-03T15:00:00Z",report:{
  mode:"live",demo:false,created_at:"2026-11-03T09:00:00Z",vehicle:{vin:lot.vin,make:"Lada",model:"Vesta",year:2021},
  summary:{score:null,level:"unknown",verdict:"Недостаточно данных",headline:"Не подтверждает чистоту",factors:[{title:'<img src=x onerror="alert(1)">',detail:"Тест",source:"Тест"}]},
  coverage:{available:0,total:1,blocks:[{title:"Залоги",status:"unavailable",note:"Неизвестно"}]},sources:[],facts:[{title:"Владельцев",value:null,status:"unavailable"}],limitations:["Тест"],
}};
const config={configured:true,default_mode:"live",allow_paid:true,demo_only:false,message:"Готово"};

(async()=>{
 let calls=[], release;
 const api=async(url,options)=>{
  calls.push({url,options});
  if(url==="/api/vehicle-checks/status")return config;
  if(options?.method==="POST")return {check,reuse:"new"};
  return {check:null};
 };
 const root=()=>w.document.querySelector("#vehicleChecks");
 const mount=(fn=api)=>{main.innerHTML=panel.shell(lot);panel.mount(root(),lot,fn);};
 mount();await settle();
 const form=root().querySelector("form"), field=n=>form.elements.namedItem(n);
 const submit=async()=>{form.dispatchEvent(new w.Event("submit",{cancelable:true}));await settle();};
 assert.equal(root().querySelector("[data-start-check]").disabled,true);
 await submit();assert.equal(calls.filter(c=>c.options?.method==="POST").length,0);
 field("consent_to_external").checked=true;field("consent_to_external").dispatchEvent(new w.Event("change"));
 field("allow_paid").checked=true;await submit();
 const first=JSON.parse(calls.find(c=>c.options?.method==="POST").options.body);
 assert.equal(first.mode,"live");assert.equal(first.consent_to_external,true);assert.equal(first.allow_paid,true);
 assert.equal(root().querySelector("[data-check-report] img"),null);
 assert.match(root().querySelector("[data-check-report]").textContent,/<img/);
 assert.match(root().querySelector("[data-check-report]").textContent,/Индекс истории: —/);
 await submit();
 assert.equal(JSON.parse(calls.filter(c=>c.options?.method==="POST")[1].options.body).request_id,first.request_id);

 field("mode").value="demo";field("mode").dispatchEvent(new w.Event("change"));
 assert.equal(root().querySelector("[data-demo-note]").hidden,false);
 assert.equal(root().querySelector("[data-live-consents]").hidden,true);
 assert.equal(root().querySelector("[data-check-report]").textContent,"");
 assert.equal(field("allow_paid").checked,false);
 await submit();
 const demo=JSON.parse(calls.filter(c=>c.options?.method==="POST").at(-1).options.body);
 assert.equal(demo.mode,"demo");assert.equal(demo.allow_paid,false);assert.equal(demo.consent_to_external,false);
 assert.notEqual(demo.request_id,first.request_id);

 // An in-flight check cannot be resubmitted; closing the lot discards late responses.
 calls=[];
 mount(async(url,options)=>{
  calls.push({url,options});
  if(url==="/api/vehicle-checks/status")return {...config,default_mode:"demo",demo_only:true};
  if(options?.method==="POST")return new Promise(r=>{release=r;});
  return {check:null};
 });await settle();
 const f=root().querySelector("form");
 f.dispatchEvent(new w.Event("submit",{cancelable:true}));await settle();
 assert.equal(f.elements.namedItem("mode").disabled,true);
 f.dispatchEvent(new w.Event("submit",{cancelable:true}));await settle();
 assert.equal(calls.filter(c=>c.options?.method==="POST").length,1);
 panel.unmount();release({check,reuse:"new"});await settle();
 assert.equal(root().querySelector("[data-check-report]").textContent,"");

 // A saved demo always retains its label, even if the default selector is live.
 mount(async(url)=>url==="/api/vehicle-checks/status"?config:{check:{...check,mode:"demo",report:{...check.report,demo:true,mode:"demo"}}});
 await settle();assert.match(root().textContent,/СИНТЕТИЧЕСКИЙ ДЕМО-ОТЧЁТ/);
 panel.unmount();dom.window.close();
 console.log("Vehicle check UI: consent, paid/demo separation, escaping, idempotency, busy state and disposal PASS");
})().catch(error=>{dom.window.close();console.error(error);process.exitCode=1;});
