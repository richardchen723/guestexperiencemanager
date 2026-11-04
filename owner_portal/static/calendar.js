/* Owner-only calendar. Never renders source payloads or creates booking links. */
(() => {
'use strict';
const DAY=86400000, epoch=s=>Date.parse(s+'T00:00:00Z')/DAY;
const dateAt=n=>new Date(n*DAY).toISOString().slice(0,10);
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!=null)n.textContent=text;return n;};
const label=s=>new Date(s+'T12:00:00Z').toLocaleDateString('en-US',{month:'short',day:'numeric',timeZone:'UTC'});
const reason=p=>p.reason==='outside_reporting_dates'?'Outside your reporting dates':'Calendar is being prepared';
function segments(stays,start,end){
 const lanes=[];
 return stays.map(stay=>({stay,left:Math.max(start,epoch(stay.start)+(stay.continues_before?0:.5)),right:Math.min(end,epoch(stay.end)+(stay.continues_after?0:.5))}))
  .filter(s=>s.right>s.left).sort((a,b)=>a.left-b.left||b.right-a.right).map(s=>{
   let lane=lanes.findIndex(stop=>stop<=s.left);if(lane<0)lane=lanes.length;lanes[lane]=s.right;return {...s,lane};
  });
}
function description(s){return `${s.name} · ${s.continues_before?'From previous month':label(s.start)} – ${s.continues_after?'Into next month':label(s.end)}`;}
function bar(s,left,right,count){
 const n=el('div','oc-stay oc-'+s.stay.kind,s.stay.name);
 n.style.left=(left/count*100)+'%';n.style.width=((right-left)/count*100)+'%';n.style.top=(34+s.lane*29)+'px';
 n.title=description(s.stay);n.setAttribute('aria-label',description(s.stay));
 if(left>s.left)n.classList.add('oc-continues-left');
 return n;
}
function dayCell(day,today,inMonth=true){
 const n=el('div','oc-day'+(inMonth?'':' oc-outside'));
 if(!inMonth)return n;
 const stamp=dateAt(day),num=el('span','oc-day-number',String(new Date(day*DAY).getUTCDate()));
 if(stamp===today){n.classList.add('oc-today');num.setAttribute('aria-label','Today, '+label(stamp));}
 n.append(num);return n;
}
function paintDay(cell,state){cell.classList.add('oc-state-'+state);cell.title=state==='unknown'?'Availability unknown':state==='blocked'?'Blocked':state==='booked'?'Reserved':'Open';}
function monthGrid(prop,data){
 const start=epoch(data.month+'-01'),count=prop.days.length,end=start+count,weekday=new Date(start*DAY).getUTCDay();
 const grid=el('div','oc-month');grid.setAttribute('aria-label',prop.name+' monthly calendar');
 const weekdays=el('div','oc-weekdays');['Sun','Mon','Tue','Wed','Thu','Fri','Sat'].forEach(d=>weekdays.append(el('span','',d)));grid.append(weekdays);
 const all=segments(prop.stays,start,end);
 for(let week=start-weekday;week<end;week+=7){
  const row=el('div','oc-week'),events=all.filter(s=>s.left<week+7&&s.right>week),height=Math.max(100,40+(1+Math.max(-1,...events.map(s=>s.lane)))*29);
  row.style.minHeight=height+'px';
  for(let d=week;d<week+7;d++){const inside=d>=start&&d<end,cell=dayCell(d,data.today,inside);if(inside)paintDay(cell,prop.days[d-start].state);row.append(cell);}
  events.forEach(s=>row.append(bar(s,Math.max(week,s.left)-week,Math.min(week+7,s.right)-week,7)));grid.append(row);
 }
 return grid;
}
function timeline(properties,data){
 const start=epoch(data.month+'-01'),end=epoch(dateAt(start+32).slice(0,7)+'-01'),count=end-start;
 const scroll=el('div','oc-scroll');scroll.tabIndex=0;scroll.setAttribute('role','region');scroll.setAttribute('aria-label','Property calendars. Scroll horizontally to see all dates.');
 const grid=el('div','oc-timeline');grid.style.setProperty('--oc-days',count);
 const header=el('div','oc-timeline-header');header.append(el('div','oc-property-heading','Property'));const dates=el('div','oc-dates');
 for(let d=start;d<end;d++){const cell=dayCell(d,data.today);cell.prepend(el('small','',new Date(d*DAY).toLocaleDateString('en-US',{weekday:'short',timeZone:'UTC'})));dates.append(cell);}
 header.append(dates);grid.append(header);
 properties.forEach(prop=>{
  const row=el('div','oc-timeline-row'),name=el('button','oc-property',prop.name);name.type='button';name.dataset.property=prop.id;name.title='View '+prop.name+' monthly calendar';name.append(el('span','oc-property-arrow','→'));row.append(name);
  const track=el('div','oc-track');
  if(!prop.available){track.append(el('p','oc-unavailable',reason(prop)));row.append(track);grid.append(row);return;}
  const events=segments(prop.stays,start,end),height=Math.max(84,44+(1+Math.max(-1,...events.map(s=>s.lane)))*29);track.style.height=height+'px';
  prop.days.forEach((day,i)=>{const cell=el('div','oc-track-day');paintDay(cell,day.state);if(day.date===data.today)cell.classList.add('oc-today');track.append(cell);});
  events.forEach(s=>{const n=bar(s,s.left-start,s.right-start,count);n.style.top=(18+s.lane*29)+'px';track.append(n);});row.append(track);grid.append(row);
 });scroll.append(grid);return scroll;
}
function reservationList(properties){
 const count=properties.reduce((n,p)=>n+(p.stays?.length||0),0),details=el('details','oc-reservations'),summary=el('summary','',`View stay list (${count})`);details.append(summary);
 const list=el('div','oc-stay-list');
 properties.forEach(p=>(p.stays||[]).forEach(s=>{const row=el('div','oc-stay-row');row.append(el('strong','',s.name));if(properties.length>1)row.append(el('span','',p.name));row.append(el('span','',`${s.continues_before?'From previous month':label(s.start)} – ${s.continues_after?'Into next month':label(s.end)}`));list.append(row);}));
 if(!count)list.append(el('p','oc-note','No stays shown for this month. Shaded dates may still be blocked or unknown.'));details.append(list);return details;
}
window.OwnerCalendar=class {
 constructor(root,endpoint,onData){this.root=root;this.endpoint=endpoint;this.onData=onData;this.serial=0;}
 hide(){this.controller?.abort();this.serial++;this.key=null;this.root.replaceChildren();this.root.removeAttribute('aria-busy');}
 async show(scope){
  const key=JSON.stringify(scope);if(this.key===key)return;this.hide();this.key=key;this.scope=scope;const serial=this.serial;
  this.controller=new AbortController();this.root.setAttribute('aria-busy','true');const loading=el('p','oc-empty','Loading calendar…');loading.setAttribute('role','status');this.root.append(loading);
  try{
   const response=await fetch(this.endpoint+'?'+new URLSearchParams(scope),{credentials:'same-origin',signal:this.controller.signal});
   if(!response.ok)throw new Error(response.status===401?'Your session has ended. Sign in again.':'Calendar could not be loaded.');
   const data=await response.json();if(serial!==this.serial)return;this.root.replaceChildren();this.onData(data);
   const properties=data.properties,ready=properties.filter(p=>p.available),top=el('div','oc-heading');top.append(el('h2','',properties.length>1?'All property calendars':'Monthly calendar'));
   const meta=el('p','oc-note',properties.length>1?'Select a property for its month view.':'Dates use the property’s local time.');top.append(meta);this.root.append(top);
   const legend=el('div','oc-legend');[['reservation','Guest stay'],['owner','Owner stay'],['blocked','Blocked'],['unknown','Unknown'],['open','Open']].forEach(([kind,text])=>{const item=el('span','');item.append(el('i','oc-key oc-key-'+kind),document.createTextNode(text));legend.append(item);});this.root.append(legend);
   if(ready.some(p=>p.stale))this.root.append(el('p','oc-warning','Update delayed. These stays and availability may have changed.'));
   if(!properties.length)this.root.append(el('p','oc-empty','No properties are available for this account.'));
   else if(properties.length===1)this.root.append(properties[0].available?monthGrid(properties[0],data):el('p','oc-empty',reason(properties[0])));
   else{this.root.append(el('p','oc-scroll-hint','Swipe or scroll across to see all dates →'),timeline(properties,data));}
   this.root.append(reservationList(properties));
   const updated=ready.map(p=>p.updated_at).sort()[0];
   this.root.append(el('p','oc-note oc-footnote',`${updated?'Updated '+new Date(updated).toLocaleString('en-US',{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})+' · ':''}Refreshes hourly. Guest names only; contact details and messages stay private.`));
  }catch(error){if(error.name==='AbortError'||serial!==this.serial)return;this.root.replaceChildren(el('p','oc-empty',error.message));const retry=el('button','owner-button owner-button-secondary','Try again');retry.type='button';retry.addEventListener('click',()=>{this.key=null;this.show(scope);});this.root.append(retry);}
  finally{if(serial===this.serial)this.root.removeAttribute('aria-busy');}
 }
};
})();
