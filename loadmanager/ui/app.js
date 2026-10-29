/* No remote assets, telemetry, or network API. Native data uses the pywebview bridge. */
document.addEventListener('DOMContentLoaded', () => {
  'use strict';
  const $ = s => document.querySelector(s);
  const $$ = s => [...document.querySelectorAll(s)];
  const paths = {
    grid:'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    sliders:'<path d="M4 7h8m4 0h4M4 17h3m4 0h9"/><circle cx="14" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
    layers:'<path d="m12 3 10 5-10 5L2 8Zm-9 9 9 5 9-5M3 17l9 5 9-5"/>',
    clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    settings:'<path d="m9 3-1 3-3 1 1 4-1 4 3 1 1 4h6l1-4 3-1-1-4 1-4-3-1-1-3Z"/><circle cx="12" cy="11" r="3"/>',
    shield:'<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z"/><path d="m8 12 3 3 5-6"/>',
    laptop:'<rect x="4" y="4" width="16" height="12" rx="2"/><path d="M2 20h20M9 16l-1 4m7-4 1 4"/>',
    info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-11v1"/>',
    refresh:'<path d="M20 8a8 8 0 1 0 0 8M20 3v5h-5"/>',
    play:'<path d="m8 4 12 8-12 8Z"/>',
    cpu:'<rect x="6" y="6" width="12" height="12" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M9 2v4m6-4v4M9 18v4m6-4v4M2 9h4m-4 6h4m12-6h4m-4 6h4"/>',
    memory:'<rect x="3" y="6" width="18" height="12" rx="2"/><path d="M7 10v4m5-4v4m5-4v4M7 18v3m5-3v3m5-3v3"/>',
    disk:'<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 14h18m-5 3h2"/>',
    battery:'<rect x="2" y="6" width="17" height="12" rx="2"/><path d="M22 10v4M6 10v4m4-4v4m4-4v4"/>',
    bolt:'<path d="m13 2-9 12h7l-1 8 10-13h-8Z"/>',
    sparkles:'<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5ZM20 2v4m-2-2h4"/>',
    code:'<path d="m8 5-6 7 6 7m8-14 6 7-6 7m-3-16-2 20"/>',
    film:'<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M7 3v18M17 3v18M3 8h4m-4 8h4m10-8h4m-4 8h4"/>',
    search:'<circle cx="10" cy="10" r="7"/><path d="m15 15 6 6"/>',
    undo:'<path d="M3 4v6h6M3 10a9 9 0 1 1 0 7"/>',
    check:'<path d="m4 12 5 5L20 6"/>',
    pause:'<path d="M8 4v16m8-16v16"/>',
    video:'<rect x="3" y="6" width="13" height="12" rx="2"/><path d="m16 10 5-3v10l-5-3"/>',
    folder:'<path d="M3 6a2 2 0 0 1 2-2h4l2 3h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>',
    stop:'<rect x="6" y="6" width="12" height="12" rx="1.5"/>',
    plus:'<path d="M12 5v14M5 12h14"/>'
  };
  const icon = name => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name]||paths.info}</svg>`;
  $$('[data-icon]').forEach(el=>{el.innerHTML=icon(el.dataset.icon);});
  const escape = value => String(value).replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const native = document.documentElement.dataset.native === 'true';
  const names={overview:'Overview',profiles:'Profiles',apps:'Apps',render:'Render',video:'Video',spikes:'Spikes',power:'Power',activity:'Session log',settings:'About'};
  const profileNames={auto:'Auto',focus:'Focus',render:'Render'};
  let data=null, page='overview', profile='auto', filter='all', sort='cpu', busy=false, polling=false, pollAgain=false, pending=[], toastTimer, benchTimer;
  const selected=new Map(), selectedGroups=new Set();
  const selectedKey=p=>`${p.pid}:${p.created}`;
  let demoPlatform='Windows', demoActive=false, demoProfile=null, demoStart=null, demoTick=0, demoChanged=[];
  const demoEvents=[{time:Date.now()/1000,text:'Demo started. No access to this computer.',kind:'info'}];
  const demoHistory=Array.from({length:60},(_,i)=>({cpu:Math.round(31+13*Math.sin(i*.24)+5*Math.sin(i*.81)),memory:58+3*Math.sin(i*.12)}));

  const groupInfo={chrome:{title:'Google Chrome',actions:['priority','eco','trim']},claude:{title:'Claude',actions:['trim']},telegram:{title:'Telegram',actions:['priority','eco','trim']},gdrive:{title:'Google Drive (sync)',actions:['priority','eco','trim']}};
  const actionNames={priority:'priority ↓',eco:'efficiency mode',trim:'memory trim',suspend:'pause'};
  const demoSpec=[['Adobe Premiere Pro',null,25.1,3820],['Claude','claude',3.4,2410],['Claude','claude',1.1,980],['Chrome','chrome',4.2,640],['Chrome','chrome',2.9,520],['Chrome','chrome',1.7,480],['Telegram','telegram',.9,640],['GoogleDriveFS','gdrive',5.2,310],['Code',null,1.2,580],['Spotify','spotify',2.4,320],['Discord','discord',1.8,442],['Steam','steam',.6,260],['Load Manager',null,.3,55]];
  const demoBench={running:false,progress:0,results:[],error:null};
  let demoStats={};
  const demoNow=Date.now()/1000;
  const demoRec={enabled:true,on:40,off:25,spikes:[
    {started:Math.round(demoNow-5400),seconds:412,avg_cpu:58.2,peak_cpu:91,plan:'Turbo',quiet_mode:false,on_ac:true,session:true,culprits:[
      {name:'ffmpeg.exe',via:null,processes:1,cpu_seconds:2210.4,share:62.1,peak:48.3},{name:'Google Chrome',via:null,processes:14,cpu_seconds:380.2,share:10.7,peak:12.1},{name:'bdservicehost.exe',via:null,processes:2,cpu_seconds:150,share:4.2,peak:5}]},
    {started:Math.round(demoNow-2400),seconds:96,avg_cpu:44.7,peak_cpu:77,plan:'Turbo',quiet_mode:false,on_ac:true,session:false,culprits:[
      {name:'python.exe',via:'Claude',processes:3,cpu_seconds:540,share:53.8,peak:30},{name:'node.exe',via:'Claude',processes:4,cpu_seconds:210,share:20.9,peak:12},{name:'bdservicehost.exe',via:null,processes:2,cpu_seconds:96,share:9.6,peak:6}]}]};
  function demoRecorderView(){
    const totals={};
    demoRec.spikes.forEach(s=>s.culprits.forEach(c=>{const k=c.name+'|'+c.via;(totals[k]=totals[k]||{name:c.name,via:c.via,cpu_seconds:0,spikes:0}).cpu_seconds+=c.cpu_seconds;totals[k].spikes++;}));
    const sum=Object.values(totals).reduce((a,t)=>a+t.cpu_seconds,0)||1;
    const top=Object.values(totals).sort((a,b)=>b.cpu_seconds-a.cpu_seconds).slice(0,6).map(t=>({...t,cpu_seconds:Math.round(t.cpu_seconds*10)/10,share:Math.round(t.cpu_seconds/sum*1000)/10}));
    return {enabled:demoRec.enabled,on:demoRec.on,off:demoRec.off,current:null,spikes:[...demoRec.spikes].reverse(),top,count:demoRec.spikes.length};
  }
  const demoPlans=[{guid:'27fa6203-3987-4dcc-918d-748559d549ec',name:'Performance'},{guid:'381b4222-f694-41f0-9685-ff5bb260df2e',name:'Balanced'},{guid:'64a64f24-65b9-4b56-befd-5ec1eaced9b3',name:'Silent'},{guid:'6fecc5ae-f350-48a5-b669-b472cb895ccf',name:'Turbo'}];
  const demoQuiet={entry:null,scheme:null,render:null,auto:false,note:null,mode:'plan',cap:100,boost:true,keep:true};
  function demoLimits(){
    const out=[];
    if(demoQuiet.boost)out.push({label:'Turbo boost',before:'Aggressive',now:'Off'});
    if(demoQuiet.cap<100)out.push({label:'Maximum processor state',before:'100%',now:demoQuiet.cap+'%'});
    return out;
  }
  function demoQuietView(){
    const win=demoPlatform==='Windows',e=demoQuiet.entry,render=!!(e&&demoActive&&demoProfile==='render'),limitOn=!!(e&&e.mode==='limit');
    const find=g=>demoPlans.find(p=>p.guid===g),target=find(demoQuiet.scheme)||demoPlans.find(p=>p.name==='Silent'),turbo=demoPlans[3];
    const nowGuid=e&&!limitOn?(render?(demoQuiet.render||e.before):e.after):turbo.guid;
    return {available:win,active:!!e,mode:demoQuiet.mode,cap:demoQuiet.cap,boost_off:demoQuiet.boost,state:e?(render?'render':'quiet'):'off',scheme:demoQuiet.scheme,render_scheme:demoQuiet.render,quiet_target:win?target:null,
      original:e?{guid:limitOn?turbo.guid:e.before,name:limitOn?turbo.name:find(e.before).name}:null,
      limits:limitOn?e.limits.map(l=>render?{...l,now:l.before}:l):null,
      current:win?find(nowGuid):null,schemes:win?demoPlans.map(p=>({...p,active:p.guid===nowGuid})):[],auto_enter:demoQuiet.auto,keep_awake:demoQuiet.keep,
      awake:!!(e&&demoQuiet.keep&&(demoActive||demoTimed.running)),since:e?e.since:null,note:demoQuiet.note,error:null};
  }
  const demoAuto={enabled:false,groups:['chrome','claude','telegram','gdrive'],grace:20,jobs:[{started:Date.now()/1000-3600,seconds:372.4,tools:['ffmpeg.exe'],auto_session:false},{started:Date.now()/1000-1800,seconds:281,tools:['ffmpeg.exe'],auto_session:true}]};
  const demoAutoView=()=>({enabled:demoAuto.enabled,state:demoAuto.enabled?(demoActive?'manual':'waiting'):'off',groups:[...demoAuto.groups],available_groups:Object.keys(groupInfo).map(key=>({key,title:groupInfo[key].title})),grace:demoAuto.grace,start_after:3,error:null,owned:false,paused:false,jobs:[...demoAuto.jobs].reverse()});
  const demoTimed={running:false,phase:null,phase_started:null,results:[],error:null,command:'',cwd:'',order:'baseline_first'};
  function demoComparison(){
    const last=m=>[...demoTimed.results].reverse().find(r=>r.mode===m&&r.exit_code===0),b=last('baseline'),s=last('session'),both=b&&s;
    return {command:demoTimed.command,baseline:b?b.seconds:null,session:s?s.seconds:null,saved_seconds:both?Math.round((b.seconds-s.seconds)*10)/10:null,saved_percent:both?Math.round((b.seconds-s.seconds)/b.seconds*1000)/10:null};
  }
  const demoTimedView=()=>({...demoTimed,now:Date.now()/1000,results:[...demoTimed.results].reverse().slice(0,10),comparisons:demoTimed.results.length?[demoComparison()]:[]});
  function demoTimedRun(command,mode,order){
    if(demoTimed.running)return;
    const phases=mode==='ab'?(order==='session_first'?['session','baseline']:['baseline','session']):[mode];
    demoTimed.running=true;demoTimed.error=null;demoTimed.command=command;
    let i=0;
    const next=()=>{
      if(!demoTimed.running)return;
      if(i>=phases.length){demoTimed.running=false;demoTimed.phase=null;demoTimed.phase_started=null;poll();return;}
      const phase=phases[i++];demoTimed.phase=phase;demoTimed.phase_started=Date.now()/1000;poll();
      setTimeout(()=>{if(!demoTimed.running)return;demoTimed.results.push({id:Date.now(),command,cwd:'',mode:phase,seconds:phase==='baseline'?372:281,exit_code:0,error:null,started:Date.now()/1000});poll();next();},1500);
    };
    next();
  }
  function demoBenchView(){
    const last=a=>[...demoBench.results].reverse().find(r=>r.active===a)||null,baseline=last(false),now=last(true);
    return {...demoBench,results:[...demoBench.results],baseline,with_session:now,delta_percent:baseline&&now?Math.round((now.score/baseline.score-1)*1000)/10:null};
  }
  function demoBenchRun(){
    if(demoBench.running)return;
    demoBench.running=true;demoBench.progress=0;
    const timer=setInterval(()=>{
      demoBench.progress=Math.min(1,demoBench.progress+.25);
      if(demoBench.progress>=1){clearInterval(timer);demoBench.running=false;demoBench.results.push({time:Date.now()/1000,active:demoActive,profile:demoProfile,score:demoActive?30100:12800,background_cpu:demoActive?100:96,threads:12});}
      poll();
    },300);
  }
  function makeDemo(){
    demoTick++;
    const win=demoPlatform==='Windows',cpu=Math.round(38+9*Math.sin(demoTick*.4));
    demoHistory.push({cpu,memory:78}); if(demoHistory.length>60)demoHistory.shift();
    const processes=demoSpec.map(([name,app,cpuUse,mem],i)=>{
      const kind=!app?null:groupInfo[app]?'heavy':'light',eligible=kind==='light'||(kind==='heavy'&&win),pid=3200+i*17;
      return {pid,created:1700000000+i,name:win?name+'.exe':name,exe:'/demo/'+name,owner:'demo',cpu:cpuUse,memory:mem,eligible,protected:!eligible,status:'running',managed:demoChanged.includes(pid),app,kind};
    });
    const groups=Object.keys(groupInfo).map(key=>{const m=processes.filter(p=>p.app===key);return {key,title:groupInfo[key].title,actions:groupInfo[key].actions,available:win,count:m.length,eligible:m.filter(p=>p.eligible).length,managed:m.filter(p=>p.managed).length,cpu:Math.round(m.reduce((a,p)=>a+p.cpu,0)*10)/10,memory:Math.round(m.reduce((a,p)=>a+p.memory,0))};});
    return {demo:true,platform:demoPlatform,machine:win?'AMD64':'arm64',cores:12,cpu_name:'Demo CPU',cpu,memory_percent:78,memory_total:32,memory_used:25,memory_available:7,trim_threshold:70,disk_rate:Math.round(24+10*Math.sin(demoTick*.3)),disk_free:186.4,disk_percent:63,battery:{percent:84,plugged:true},processes,groups,history:demoHistory,active:demoActive,profile:demoProfile,changed:demoChanged.length,events:demoEvents,session_started:demoStart,recommendation:'render',guardian_ready:true,timestamp:Date.now()/1000,benchmark:demoBenchView(),stats:demoStats,auto:demoAutoView(),timed:demoTimedView(),quiet:demoQuietView(),recorder:demoRecorderView(),video:{running:!!(demoV.job&&demoV.job.status==='running'),title:null,status:null,progress:null,close_asked:0,queued:demoV.queue.length,paused:demoV.paused,after:demoV.after}};
  }
  function showToast(text,error=false){const el=$('#toast');el.textContent=text;el.classList.toggle('error',error);el.hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.hidden=true,5500);}
  function navigate(next){if(!names[next])next='overview';page=next;$$('.page').forEach(el=>el.classList.toggle('active',el.id==='page-'+next));$$('.nav-item').forEach(el=>el.classList.toggle('active',el.dataset.page===next));$('#page-label').textContent=names[next];if(location.hash!=='#'+next)history.replaceState(null,'','#'+next);if(data){render();if(next==='video')videoSync();}}
  $$('[data-page]').forEach(el=>el.onclick=()=>navigate(el.dataset.page));
  $$('[data-go]').forEach(el=>el.onclick=()=>navigate(el.dataset.go));
  $('.brand').onclick=e=>{e.preventDefault();navigate('overview');};
  window.addEventListener('hashchange',()=>navigate(location.hash.slice(1)));
  $$('[data-profile]').forEach(el=>el.onclick=()=>{if(data?.active)return showToast('End the current session first.');profile=el.dataset.profile;$$('[data-profile]').forEach(b=>b.classList.toggle('selected',b===el));$('#selected-profile').textContent=profileNames[profile];showToast('Profile "'+profileNames[profile]+'" selected. Nothing has been changed yet.');});
  $$('[data-filter]').forEach(el=>el.onclick=()=>{filter=el.dataset.filter;$$('[data-filter]').forEach(b=>b.classList.toggle('active',b===el));renderProcesses();});
  $('#process-search').oninput=renderProcesses;
  $('#sort-cpu').onclick=()=>{sort='cpu';$('#sort-cpu').textContent='CPU ↓';$('#sort-memory').textContent='Memory';renderProcesses();};
  $('#sort-memory').onclick=()=>{sort='memory';$('#sort-cpu').textContent='CPU';$('#sort-memory').textContent='Memory ↓';renderProcesses();};
  $('#clear-selection').onclick=()=>{selected.clear();renderProcesses();};

  const countSelected=()=>{$('#selected-count').textContent=selected.size+selectedGroups.size;};
  const procWord=n=>n+(n===1?' process':' processes');
  const memText=mb=>mb>=1024?(mb/1024).toFixed(1)+' GB':Math.round(mb)+' MB';
  function renderGroups(){
    if(!data)return;
    const groups=data.groups||[];
    for(const key of [...selectedGroups])if(!groups.some(g=>g.key===key&&g.available&&g.eligible>0))selectedGroups.delete(key);
    $('#group-list').innerHTML=groups.map(g=>{
      const ok=g.available&&g.eligible>0&&!data.active&&!busy,sel=selectedGroups.has(g.key);
      const state=!g.available?'Unavailable on this system':g.managed?'In session':g.count?'Available':'Not running';
      return `<label class="group-card panel${sel?' selected':''}${ok||g.managed?'':' disabled'}"><input type="checkbox" data-group="${escape(g.key)}" aria-label="Select ${escape(g.title)}" ${sel?'checked':''} ${ok?'':'disabled'}><div><strong>${escape(g.title)}</strong><small>${g.count?`${procWord(g.count)} · CPU ${g.cpu.toFixed(1)}% · ≈${memText(g.memory)}`:'Not running'}</small><div class="chips">${g.actions.map(a=>`<span class="chip">${actionNames[a]}</span>`).join('')}</div></div><span class="badge ${g.managed?'managed':ok?'':'protected'}">${icon(g.managed?'check':ok?'clock':'shield')}${state}</span></label>`;
    }).join('');
    $$('#group-list input').forEach(el=>el.onchange=()=>{el.checked?selectedGroups.add(el.dataset.group):selectedGroups.delete(el.dataset.group);renderGroups();countSelected();});
    countSelected();
  }
  function renderProcesses(){
    if(!data)return;
    const query=$('#process-search').value.toLowerCase();
    const processes=data.processes.filter(p=>p.name.toLowerCase().includes(query)&&(filter==='all'||(filter==='eligible'?p.eligible:p.protected))).sort((a,b)=>b[sort]-a[sort]);
    const live=new Set(data.processes.filter(p=>p.eligible&&p.kind!=='heavy').map(selectedKey));
    for(const key of selected.keys())if(!live.has(key))selected.delete(key);
    $('#process-body').innerHTML=processes.map(p=>{
      const key=selectedKey(p),grouped=p.eligible&&p.kind==='heavy',label=p.managed?(data.platform==='Windows'?'Modified':'Paused'):grouped?'In group':p.eligible?'Available':'Protected';
      return `<tr><td>${p.eligible&&!grouped?`<input type="checkbox" aria-label="Select ${escape(p.name)} PID ${p.pid}" data-key="${key}" ${selected.has(key)?'checked':''} ${data.active||busy?'disabled':''}>`:icon(grouped?'layers':'shield')}</td><td><div class="process-name"><span class="app-avatar">${escape(p.name[0].toUpperCase())}</span><div><strong>${escape(p.name)}</strong><small>PID ${p.pid}</small></div></div></td><td>${p.cpu.toFixed(1)}%</td><td>${memText(p.memory)}</td><td><span class="badge ${p.managed?'managed':p.protected?'protected':''}">${icon(p.managed?'check':p.protected?'shield':'clock')}${label}</span></td></tr>`;
    }).join('');
    $$('#process-body input').forEach(el=>el.onchange=()=>{const p=data.processes.find(p=>selectedKey(p)===el.dataset.key);if(el.checked){if(selected.size>=40){el.checked=false;return showToast('At most 40 processes per session.');}selected.set(el.dataset.key,{...p});}else selected.delete(el.dataset.key);countSelected();});
    $('#empty-processes').hidden=processes.length>0;
    countSelected();
    $('#selection-hint').textContent=data.active?'Session active · changes recorded':'Nothing applied yet';
  }
  const gbs=r=>(r.score/1024).toFixed(1)+' GB/s',clock=r=>new Date(r.time*1000).toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit'});
  function renderBench(){
    const b=data.benchmark;if(!b)return;
    $('#bench-run').disabled=b.running||busy;$('#bench-run-label').textContent=b.running?'Running…':'Run (≈8 s)';
    $('#bench-meter').hidden=!b.running;$('#bench-progress').style.width=Math.round(b.progress*100)+'%';
    $('#bench-base').textContent=b.baseline?gbs(b.baseline):'—';$('#bench-base-note').textContent=b.baseline?` ${clock(b.baseline)} · background ${Math.round(b.baseline.background_cpu)}% CPU`:'';
    $('#bench-now').textContent=b.with_session?gbs(b.with_session):'—';$('#bench-now-note').textContent=b.with_session?` ${clock(b.with_session)} · background ${Math.round(b.with_session.background_cpu)}% CPU`:'';
    const d=b.delta_percent;$('#bench-delta').textContent=d===null?'—':(d>0?'+':'')+d+'%';$('.bench-delta').classList.toggle('negative',d!==null&&d<0);
    let note;
    if(b.error)note='Benchmark failed: '+b.error+'.';
    else if(!b.baseline&&!b.with_session)note='Measure without a session first.';
    else if(d===null)note=data.active?'Session running: measure again.':b.baseline.background_cpu<10?'Background apps barely use the CPU, so there is nothing to gain. Measure during normal work, when Chrome and Drive are busy.':'Now start a session and measure again.';
    else note=d>3?`A normal-priority job gets ${d}% more CPU.`:d<-3?'Slower with the session: check whether something else is interfering.':'Almost no difference: the CPU was not the main limit.';
    $('#bench-note').textContent=note+' This probes the CPU only; disk, network and GPU are not measured.';
    const st=data.stats||{};
    $('#session-stats').hidden=!st.processes;
    if(st.processes)$('#session-stats').textContent=`Last session: ${st.processes} processes changed${st.freed_mb?`, ${st.freed_mb} MB RAM freed`:''}${st.boosted?`, ${st.boosted} render tools boosted`:''}${st.skipped?`, ${st.skipped} skipped`:''}.`;
  }
  const autoText={off:'Auto-start is off.',waiting:'Waiting for a render.',pending:'Render detected; the session starts in a few seconds.',active:'Session running, started automatically. It ends shortly after the render stops.',ending:'Render finished; ending the session.',manual:'A session you started is running; auto-start leaves it alone.',held:'You ended the session during this render; it will not restart until the render finishes.',retry:'Could not start yet; retrying in a minute.'};
  const modeLabel={baseline:'without session',session:'with session'};
  const stamp=t=>new Date(t*1000).toLocaleString('en-GB',{day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'});
  function fmtDuration(sec){if(sec<60)return sec.toFixed(1)+' s';const s=Math.round(sec),h=Math.floor(s/3600),m=Math.floor(s%3600/60),r=s%60,p=n=>String(n).padStart(2,'0');return h?`${h}:${p(m)}:${p(r)}`:`${m}:${p(r)}`;}
  let timedInit=false, timedSkew=0, recInit=false, quietSig='';
  const planOf=g=>(data.quiet.schemes.find(s=>s.guid===g)||{}).name||'';
  function renderSpikes(){
    const r=data.recorder;if(!r)return;
    $('#rec-enabled').checked=r.enabled;
    if(!recInit){$('#rec-on-input').value=r.on;$('#rec-off-input').value=r.off;recInit=true;}
    $('#rec-status').textContent=!r.enabled?'Recording is off.':r.current?`Busy now: ${Math.round(r.current.avg_cpu)}% CPU on average for ${fmtDuration(r.current.seconds)}, peak ${Math.round(r.current.peak_cpu)}%.`:`No busy period right now. ${r.count} recorded so far.`;
    const row=c=>`<div class="job-row culprit"><span>${escape(c.name)}${c.via?`<em>via ${escape(c.via)}</em>`:''}</span><span>${c.processes} process${c.processes===1?'':'es'}</span><span>${Math.round(c.cpu_seconds)} core-s</span><span>${c.share}%</span></div>`;
    $('#rec-current').hidden=!r.current;
    $('#rec-current').innerHTML=r.current?r.current.culprits.map(row).join(''):'';
    $('#rec-top').innerHTML=r.top.length?r.top.map(t=>`<div class="job-row culprit"><span>${escape(t.name)}${t.via?`<em>via ${escape(t.via)}</em>`:''}</span><span>${t.spikes} period${t.spikes===1?'':'s'}</span><span>${Math.round(t.cpu_seconds)} core-s</span><span>${t.share}%</span></div>`).join(''):'<div class="empty">Busy periods will be listed here with the programs behind them.</div>';
    const open=new Set($$('#rec-list details[open]').map(d=>d.dataset.started));
    $('#rec-list').innerHTML=r.spikes.map(s=>{
      const ctx=[s.plan?'Power plan: '+s.plan:'',s.on_ac===true?'plugged in':s.on_ac===false?'on battery':'',s.quiet_mode?'quiet mode on':'',s.session?'a Load Manager session was active':''].filter(Boolean).join(' · ');
      const top=s.culprits[0];
      return `<details class="spike" data-started="${s.started}" ${open.has(String(s.started))?'open':''}><summary><span>${stamp(s.started)}</span><span>${fmtDuration(s.seconds)}</span><span>avg ${s.avg_cpu}% · peak ${s.peak_cpu}%</span><span>${top?escape(top.name)+(top.via?' via '+escape(top.via):'')+' '+top.share+'%':'—'}</span></summary><p class="spike-context">${escape(ctx||'No extra context')}</p><div class="job-list">${s.culprits.map(row).join('')}</div></details>`;
    }).join('');
  }
  function fillPlans(select,q,firstLabel,current){
    const sig=JSON.stringify([q.schemes.map(s=>s.guid+s.name),firstLabel,current]);
    if(select.dataset.sig!==sig&&document.activeElement!==select){
      select.innerHTML=`<option value="">${escape(firstLabel)}</option>`+q.schemes.map(s=>`<option value="${escape(s.guid)}">${escape(s.name)}</option>`).join('');
      select.value=current||'';select.dataset.sig=sig;
    }
  }
  function renderPower(){
    const q=data.quiet;if(!q)return;
    const cur=q.current?q.current.name:'unknown',limitOn=q.active&&!!q.limits,limit=q.mode==='limit';
    $('#quiet-badge').textContent=q.active?(q.state==='render'?'Render':'Quiet'):'Off';
    $('#quiet-badge').className='badge'+(q.active?' managed':' protected');
    const awake=q.awake?' The laptop is being kept awake while work runs.':'';
    const limits=limitOn?q.limits.map(l=>`${l.label}: ${l.before} → ${l.now}`).join('; '):'';
    $('#quiet-status').textContent=!q.available?'Quiet mode needs Windows power plans.':limitOn?`Quiet mode is on since ${stamp(q.since)}. In ${q.original.name}: ${limits}.${q.state==='render'?' A render is running, so the original settings are in use.':''}${awake}`:q.active?`Quiet mode is on since ${stamp(q.since)}. Active plan: ${cur}. Original plan: ${q.original.name}.${q.state==='render'?' A render is running, so its plan is in use.':''}${awake}`:`Quiet mode is off. Active plan: ${cur}.`;
    if(document.activeElement!==$('#quiet-mode'))$('#quiet-mode').value=q.mode;
    if(document.activeElement!==$('#quiet-cap'))$('#quiet-cap').value=q.cap;
    if(document.activeElement!==$('#quiet-boost'))$('#quiet-boost').value=q.boost_off?'off':'keep';
    $('#quiet-cap-field').hidden=$('#quiet-boost-field').hidden=!limit;$('#quiet-scheme-field').hidden=limit;$('#quiet-render-field').hidden=limit;
    $('#quiet-method-note').textContent=limit?'Your power plan, sleep and display settings stay as they are; only these processor settings change, and only while quiet mode is on. Renders and timed runs use the original values. On the ROG Zephyrus G14 turning boost off cut the busiest core from about 250% to 100% of the base clock (and one busy thread runs about 60% slower); the maximum state had no effect there, so some processors ignore it.':'The quiet plan replaces your plan for a while, including its sleep and display timeouts. Renders and timed runs use the plan you had before (or the one chosen above).';
    fillPlans($('#quiet-scheme'),q,'Automatic'+(q.quiet_target?` (now: ${q.quiet_target.name})`:''),q.scheme);
    fillPlans($('#quiet-render'),q,'The plan I had before',q.render_scheme);
    const locked=q.active||!q.available;
    $('#quiet-scheme').disabled=$('#quiet-render').disabled=$('#quiet-mode').disabled=$('#quiet-cap').disabled=$('#quiet-boost').disabled=locked;
    $('#quiet-auto').checked=q.auto_enter;$('#quiet-auto').disabled=!q.available;
    $('#quiet-awake').checked=q.keep_awake;$('#quiet-awake').disabled=!q.available;
    $('#quiet-enter').disabled=locked||busy;$('#quiet-leave').disabled=!q.active||busy;
    $('#quiet-leave').textContent=limitOn?'Remove the limits':q.original?'Return to '+q.original.name:'Return to the original plan';
    $('#quiet-note').textContent=q.error||q.note||'';
    $('#quiet-list').innerHTML=q.schemes.length?q.schemes.map(s=>`<div class="job-row"><span>${escape(s.name)}</span><span class="muted">${escape(s.guid.slice(0,8))}…</span><span>${q.current&&q.current.guid===s.guid?'active':''}</span><span>${!limit&&q.quiet_target&&q.quiet_target.guid===s.guid?'quiet plan':''}</span></div>`).join(''):'<div class="empty">No power plans were found.</div>';
  }
  function tickClock(){
    const t=data?.timed,live=!!(t&&t.running);
    $('#timed-live').hidden=!live;$('#timed-cancel').hidden=!live;
    if(!live)return;
    $('#timed-phase').textContent=t.phase?'Running '+modeLabel[t.phase]:'Preparing…';
    $('#timed-clock').textContent=t.phase_started?fmtDuration(Math.max(0,Date.now()/1000+timedSkew-t.phase_started)):'—';
  }
  function renderRender(){
    const a=data.auto,t=data.timed;if(!a||!t)return;
    $('#auto-enabled').checked=a.enabled;$('#auto-enabled').disabled=busy;
    $('#auto-grace').textContent=Math.round(a.grace);
    $('#auto-groups').innerHTML=a.available_groups.map(g=>{const on=a.groups.includes(g.key);return `<label class="group-card panel${on?' selected':''}"><input type="checkbox" data-autogroup="${escape(g.key)}" aria-label="Limit ${escape(g.title)}" ${on?'checked':''}><div><strong>${escape(g.title)}</strong></div></label>`;}).join('');
    $$('#auto-groups input').forEach(el=>el.onchange=()=>{
      const keys=$$('#auto-groups input:checked').map(i=>i.dataset.autogroup);
      if(!keys.length){el.checked=true;return showToast('Keep at least one app selected.',true);}
      autoConfigure({groups:keys});
    });
    $('#auto-status').textContent=(autoText[a.state]||a.state)+(a.error&&a.state==='retry'?' '+a.error:'');
    $('#job-list').innerHTML=a.jobs.length?a.jobs.map(j=>`<div class="job-row"><span>${stamp(j.started)}</span><span>${escape(j.tools.join(', '))}</span><span>${fmtDuration(j.seconds)}</span><span>${j.auto_session?'auto session':'no session'}</span></div>`).join(''):'<div class="empty">Renders you run are listed here, even with auto-start off.</div>';
    if(!timedInit){$('#timed-command').value=t.command;$('#timed-cwd').value=t.cwd;$('#timed-order').value=t.order;timedInit=true;}
    timedSkew=t.now-Date.now()/1000;
    const idle=!t.running&&!busy&&!data.benchmark?.running;
    $('#timed-ab').disabled=!idle||data.active;$('#timed-baseline').disabled=!idle||data.active;$('#timed-session').disabled=!idle;
    const c=t.comparisons[0]||null;
    $('#timed-base').textContent=c&&c.baseline!==null?fmtDuration(c.baseline):'—';
    $('#timed-now').textContent=c&&c.session!==null?fmtDuration(c.session):'—';
    const both=c&&c.saved_seconds!==null;
    $('#timed-saved').textContent=both?(c.saved_seconds<0?'−':'')+fmtDuration(Math.abs(c.saved_seconds))+' ('+c.saved_percent+'%)':'—';
    $('#timed-saved').parentElement.classList.toggle('negative',both&&c.saved_seconds<0);
    let note;
    if(data.active&&!t.running)note='A session is running. End it to time a run without one; "With session" runs under it.';
    else if(!c)note='Enter a command and press Run A/B.';
    else if(!both)note=c.baseline!==null?'Now run it with a session.':'Now run it without a session.';
    else if(c.saved_seconds>0)note='The run that goes second may benefit from files already in the cache; repeat with the order swapped to confirm.';
    else note='No gain: this command is not limited by what the session changes (disk, GPU or the encoder itself).';
    $('#timed-note').textContent=note;
    $('#timed-error').hidden=!t.error||t.running;$('#timed-error').textContent=t.error||'';
    $('#timed-history').innerHTML=t.results.map(r=>`<div class="job-row"><span>${stamp(r.started)}</span><span>${modeLabel[r.mode]}</span><span>${r.exit_code===0?fmtDuration(r.seconds):'failed'}</span><span class="muted">${escape(r.command.slice(0,40))}</span></div>`).join('');
    tickClock();
  }
  function chartPoints(history,key,w,h){return history.map((v,i)=>`${(i/Math.max(1,history.length-1)*w).toFixed(1)},${(h-Math.max(0,Math.min(100,v[key]))/100*h).toFixed(1)}`).join(' ');}
  function render(){
    if(!data||data.loading)return;
    const win=data.platform==='Windows';
    $('#connection').innerHTML=`<i></i>${native?'On device':'Demo mode'}`;
    $('#device-name').textContent=(data.platform==='Darwin'?'macOS':data.platform)+' · '+data.machine;
    $('#device-detail').textContent=`${data.cores} threads · ${data.memory_total} GB RAM`;
    $('#app-count').textContent=data.processes.length;
    $('#cpu-value').textContent=Math.round(data.cpu);
    $('#cpu-meter').style.width=data.cpu+'%';
    $('#cpu-caption').textContent=data.cpu>85?'High load':data.cpu>50?'Busy':'Plenty of headroom';
    $('#cpu-spark').innerHTML=`<polyline points="${chartPoints(data.history.slice(-20),'cpu',120,38)}"/>`;
    $('#ram-value').textContent=data.memory_used.toFixed(1);$('#ram-total').textContent='of '+data.memory_total+' GB';
    $('#ram-meter').style.width=data.memory_percent+'%';$('#ram-caption').textContent='Available: '+data.memory_available.toFixed(1)+' GB';
    $('#disk-value').textContent=data.disk_rate===null?'—':data.disk_rate.toFixed(1);$('#disk-caption').textContent=`${data.disk_free.toFixed(0)} GB free on the system drive`;
    $('#battery-value').textContent=data.battery?Math.round(data.battery.percent):'AC';$('#battery-unit').textContent=data.battery?'%':'';
    $('#power-kind').textContent=data.battery?(data.battery.plugged?'AC':'BAT'):'AC';$('#power-caption').textContent=data.battery?(data.battery.plugged?'Plugged in':'On battery'):'No battery detected';
    const h=data.history;
    $('#history-chart').innerHTML=`<defs><linearGradient id="chart-fill" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#d4f88a" stop-opacity=".12"/><stop offset="1" stop-color="#d4f88a" stop-opacity="0"/></linearGradient></defs><path d="M0 1H620M0 75H620M0 149H620" stroke="#313a30" stroke-dasharray="3 5" fill="none"/><polygon points="0,150 ${chartPoints(h,'cpu',620,150)} 620,150" fill="url(#chart-fill)"/><polyline points="${chartPoints(h,'memory',620,150)}" fill="none" stroke="#9cbbff" stroke-width="1.5"/><polyline points="${chartPoints(h,'cpu',620,150)}" fill="none" stroke="#d4f88a" stroke-width="2"/>`;
    $('#hero-status').textContent=data.active?'SESSION ACTIVE':'MONITORING';
    $('#hero-title').innerHTML=data.active?'Session active.<br><em>Background limited.</em>':'Limit background<br><em>CPU and memory use.</em>';
    $('#hero-description').textContent=data.active?`Profile: ${profileNames[data.profile]||'Focus'}. Changes recorded: ${data.changed}. End the session to restore the original settings.`:'Pick a profile and the apps that can wait. Their priority and memory use are reduced until you end the session.';
    $('#main-action-label').textContent=data.active?'End session':'Set up session';
    $('#hero-footnote').textContent=data.active?'One click restores everything.':data.auto?.enabled?'Auto-start on render is on.':'Nothing changes until you apply a plan.';
    $('#orb-label').textContent=data.active?'Session active':'Idle';
    $('#footer-state').textContent=(data.demo?'Demo · ':'')+(data.active?'Session active':'Monitoring only');
    $('#focus-detail').textContent=win?'Background: below-normal priority + efficiency mode':'Pauses the selected background processes';
    $('#render-detail').textContent=win?'Background: lowest priority · ffmpeg and editors above normal · memory trim when short':'Pauses the selected background processes';
    $('#platform-explanation').textContent=win?'On Windows, the selected apps get a lower priority and CPU efficiency mode. In Render the priority is lower still, ffmpeg and video editors are raised above normal, and when 70% or more of RAM is in use the selected apps\' memory is trimmed. Everything is restored when the session ends.':'On this platform both profiles pause the selected processes. They resume when the session ends. Pausing does not free RAM.';
    $('#apps-description').textContent=win?'Select whole apps or individual light processes. The original settings are saved and come back when the session ends.':'The selected processes will be paused. Music, messages and calls in them stop too.';
    $('#restore-action').disabled=!data.active||busy;$('#review-action').disabled=data.active||busy;$('#quick-render').disabled=data.active||busy;
    if(data.memory_percent>85){$('#insight-title').textContent='Memory is almost full';$('#insight-text').textContent=win?'Select Chrome, Claude and Telegram under Apps: their memory is moved out of RAM so your job has room. Save your work.':'Pausing processes does not free RAM. Save your work and close unneeded apps yourself.';}
    else if(data.cpu>85){$('#insight-title').textContent='The CPU is busy';$('#insight-text').textContent='See which background processes compete with your work. You can select them for a session.';}
    else if(data.disk_free<15){$('#insight-title').textContent='Low disk space';$('#insight-text').textContent='End the session and check for old files. Keep the cache of the active project.';}
    else{$('#insight-title').textContent='Plenty of room';$('#insight-text').textContent='The load is moderate. Leave everything as it is, or select apps for a session in advance.';}
    renderGroups();renderBench();renderRender();renderSpikes();renderPower();renderProcesses();renderVideo();
    $('#event-list').innerHTML=data.events.length?data.events.map(e=>`<div class="event"><span class="event-icon ${escape(e.kind)}">${icon(e.kind==='error'?'info':e.kind==='success'?'check':'clock')}</span><span>${escape(e.text)}</span><time>${new Date(e.time*1000).toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit',second:'2-digit'})}</time></div>`).join(''):'<div class="empty">Actions of the current session will appear here.</div>';
  }
  async function poll(){
    if(polling){pollAgain=true;return;}polling=true;
    try{
      if(native){if(!window.pywebview?.api)return;data=await window.pywebview.api.snapshot();}
      else data=makeDemo();
      render();
      await videoSync();
      if((data.benchmark?.running||data.timed?.running||data.video?.running)&&!benchTimer)benchTimer=setTimeout(()=>{benchTimer=null;poll();},500);
    }catch(e){$('#connection').textContent='Connection lost';showToast('Could not get data. '+e.message,true);}
    finally{polling=false;if(pollAgain){pollAgain=false;poll();}}
  }
  function demoPlan(resolved){
    const lines=[];
    for(const key of selectedGroups){const g=data.groups.find(g=>g.key===key);lines.push({title:g.title,count:g.eligible,actions:g.actions.filter(a=>a!=='trim'||data.memory_percent>=data.trim_threshold)});}
    const light={};
    for(const p of selected.values())(light[p.name]=light[p.name]||{title:p.name.replace(/\.exe$/i,''),count:0,actions:[data.platform==='Windows'?'priority':'suspend']}).count++;
    return lines.concat(Object.values(light));
  }
  async function review(){
    if(!data||data.loading)return showToast('Wait for the metrics to load.');
    if(!data.guardian_ready)return showToast('The recovery guardian is unavailable. See the session log.',true);
    if(data.active)return showToast('End the current session first.');
    if(!selected.size&&!selectedGroups.size){navigate('apps');return showToast('Select at least one app or process.');}
    pending=[...[...selectedGroups].map(group=>({group})),...[...selected.values()].map(p=>({pid:p.pid,created:p.created,name:p.name,exe:p.exe,owner:p.owner}))];
    const resolved=profile==='auto'?data.recommendation:profile,win=data.platform==='Windows';
    let lines;
    if(native){
      const plan=await window.pywebview.api.preview(pending,profile);
      if(!plan.ok)return showToast(plan.error,true);
      lines=plan.apps.map(a=>({title:a.title,count:a.processes,actions:Object.keys(a.actions)}));
    }else lines=demoPlan(resolved);
    $('#plan-description').textContent=`${data.demo?'Simulation · ':''}Profile: ${profileNames[resolved]}. ${win?'The selected apps get a lower priority and less CPU load.':'The selected processes will be paused.'}`;
    $('#plan-list').innerHTML=lines.map(l=>`<li><span>${escape(l.title)}<small> · ${procWord(l.count)}</small></span><small>${l.actions.map(a=>actionNames[a]||a).join(' · ')}</small></li>`).join('');
    $('#plan-note').textContent=win?'Windows stay open, but the selected apps run slower: Chrome tabs, calls and video may stutter while the session runs. The original settings come back when it ends. Trimming memory deletes nothing: data is loaded back on demand.':'These processes will stop responding for a while. Do not select an app with an active call or an important background operation. Pausing does not free memory.';
    $('#confirm-plan').textContent=data.demo?'Test in demo':'Apply plan';
    $('#plan-dialog').showModal();
  }
  $('#plan-form').onsubmit=e=>e.preventDefault();
  $('#review-action').onclick=review;$('#main-action').onclick=()=>data?.active?restore():navigate('apps');
  $('#close-dialog').onclick=$('#cancel-plan').onclick=()=>$('#plan-dialog').close();
  $('#confirm-plan').onclick=async()=>{
    if(busy)return;busy=true;$('#confirm-plan').disabled=true;
    try{
      let result;
      if(native)result=await window.pywebview.api.apply(pending,profile);
      else{
        demoActive=true;demoProfile=profile==='auto'?data.recommendation:profile;demoStart=Date.now()/1000;
        const ids=[];
        for(const item of pending){
          if(item.group){const g=data.groups.find(g=>g.key===item.group);data.processes.filter(p=>p.app===item.group&&p.eligible).forEach(p=>ids.push(p.pid));demoEvents.unshift({time:Date.now()/1000,text:`Demo: ${g.title} — ${g.actions.map(a=>actionNames[a]).join(', ')}`,kind:'action'});}
          else{ids.push(item.pid);demoEvents.unshift({time:Date.now()/1000,text:`Demo: ${item.name} — ${demoPlatform==='Windows'?'priority lowered':'suspended'}`,kind:'action'});}
        }
        demoChanged=ids;
        const freed=selectedGroups.size?412:0;
        demoStats={processes:ids.length,freed_mb:freed,boosted:0,skipped:0};
        result={ok:true,applied:ids.length,freed_mb:freed,session:true};
      }
      if(!result.ok)throw new Error(result.error);
      $('#plan-dialog').close();selected.clear();selectedGroups.clear();await poll();navigate('overview');
      showToast(result.session===false?`Memory freed: ${result.freed_mb} MB. No session started: nothing to restore.`:(native?'Session started. ':'Simulation started. ')+`Processes changed: ${result.applied}.`+(result.freed_mb?` Memory freed: ${result.freed_mb} MB.`:''));
    }catch(e){showToast(e.message,true);}
    finally{busy=false;$('#confirm-plan').disabled=false;render();}
  };
  async function restore(){
    if(busy)return;busy=true;
    try{
      let result;
      if(native)result=await window.pywebview.api.restore();
      else{result={ok:true,restored:demoChanged.length};demoActive=false;demoChanged=[];demoProfile=null;demoStart=null;demoEvents.unshift({time:Date.now()/1000,text:'Demo: original settings restored.',kind:'success'});}
      if(!result.ok)throw new Error(result.error);
      await poll();showToast((native?'Processes restored: ':'Demo: processes restored: ')+result.restored);
    }catch(e){showToast(e.message,true);}
    finally{busy=false;render();}
  }
  $('#restore-action').onclick=restore;
  const usableGroups=()=>(data?.groups||[]).filter(g=>g.available&&g.eligible>0);
  $('#select-heavy').onclick=()=>{
    if(!data||data.loading)return;
    if(data.active)return showToast('End the current session first.');
    const usable=usableGroups();
    if(!usable.length)return showToast(data.platform==='Windows'?'Chrome, Claude, Telegram and Drive are not running.':'These apps are protected on macOS: pausing does not free memory.');
    usable.forEach(g=>selectedGroups.add(g.key));renderGroups();
  };
  $('#quick-render').onclick=()=>{
    if(!data||data.loading)return showToast('Wait for the metrics to load.');
    if(data.active)return showToast('End the current session first.');
    const usable=usableGroups();
    if(!usable.length){navigate('apps');return showToast(data.platform==='Windows'?'Chrome, Claude, Telegram and Drive are not running.':'Render mode works on Windows only: pausing does not free memory on Mac.');}
    profile='render';$$('[data-profile]').forEach(b=>b.classList.toggle('selected',b.dataset.profile==='render'));$('#selected-profile').textContent=profileNames.render;
    selected.clear();selectedGroups.clear();usable.forEach(g=>selectedGroups.add(g.key));renderGroups();review();
  };
  $('#bench-run').onclick=async()=>{
    if(!data||data.loading)return;
    try{if(native){const r=await window.pywebview.api.benchmark();if(!r.ok)return showToast('A benchmark is already running.');}else demoBenchRun();await poll();}
    catch(e){showToast('Could not start the benchmark. '+e.message,true);}
  };
  async function autoConfigure(patch){
    try{
      if(native){const r=await window.pywebview.api.auto_configure(patch.enabled??null,patch.groups??null,patch.grace??null);if(!r.ok)throw new Error(r.error);}
      else{if(patch.enabled!==undefined)demoAuto.enabled=patch.enabled;if(patch.groups)demoAuto.groups=patch.groups;}
    }catch(e){showToast(e.message,true);}
    await poll();
  }
  $('#auto-enabled').onchange=e=>autoConfigure({enabled:e.target.checked});
  async function startTimed(mode){
    if(!data||data.loading)return;
    const command=$('#timed-command').value,cwd=$('#timed-cwd').value,order=$('#timed-order').value;
    if(!command.trim())return showToast('Enter a command first.',true);
    try{
      if(native){const r=await window.pywebview.api.timed_start(command,cwd,mode,order);if(!r.ok)return showToast(r.error,true);}
      else demoTimedRun(command,mode,order);
      await poll();
    }catch(e){showToast(e.message,true);}
  }
  [['#timed-ab','ab'],['#timed-baseline','baseline'],['#timed-session','session']].forEach(([id,mode])=>{$(id).onclick=()=>startTimed(mode);});
  $('#timed-cancel').onclick=async()=>{
    if(native)await window.pywebview.api.timed_cancel();
    else{demoTimed.running=false;demoTimed.phase=null;demoTimed.phase_started=null;demoTimed.error='Cancelled';}
    await poll();
  };
  async function quiet(call,demo){
    try{
      if(native){const r=await call(window.pywebview.api);if(!r.ok)throw new Error(r.error);}
      else demo();
    }catch(e){showToast(e.message,true);}
    await poll();
  }
  $('#quiet-enter').onclick=()=>quiet(a=>a.quiet_enter(),()=>{
    if(data.platform!=='Windows')throw new Error('Quiet mode needs Windows power plans');
    if(demoQuiet.mode==='limit'){
      const limits=demoLimits();
      if(!limits.length)throw new Error('Nothing to limit: turn turbo boost off or set a maximum state below 100%');
      demoQuiet.entry={mode:'limit',limits,since:Date.now()/1000};demoQuiet.note=null;
      demoEvents.unshift({time:Date.now()/1000,text:'Demo: quiet mode on, '+limits.map(l=>l.label+' '+l.before+' → '+l.now).join('; ')+' in Turbo',kind:'action'});
      return;
    }
    const target=demoPlans.find(p=>p.guid===demoQuiet.scheme)||demoPlans.find(p=>p.name==='Silent');
    demoQuiet.entry={mode:'plan',before:demoPlans[3].guid,after:target.guid,since:Date.now()/1000};demoQuiet.note=null;
    demoEvents.unshift({time:Date.now()/1000,text:'Demo: quiet mode on, power plan Turbo → '+target.name,kind:'action'});
  });
  $('#quiet-leave').onclick=()=>quiet(a=>a.quiet_leave(),()=>{
    const limit=demoQuiet.entry&&demoQuiet.entry.mode==='limit';demoQuiet.entry=null;
    demoEvents.unshift({time:Date.now()/1000,text:limit?'Demo: quiet mode ended, processor settings restored':'Demo: quiet mode ended, power plan Turbo restored',kind:'success'});
  });
  $('#quiet-scheme').onchange=e=>quiet(a=>a.quiet_configure(e.target.value,null,null,null,null,null,null),()=>{demoQuiet.scheme=e.target.value||null;});
  $('#quiet-render').onchange=e=>quiet(a=>a.quiet_configure(null,e.target.value,null,null,null,null,null),()=>{demoQuiet.render=e.target.value||null;});
  $('#quiet-auto').onchange=e=>quiet(a=>a.quiet_configure(null,null,e.target.checked,null,null,null,null),()=>{demoQuiet.auto=e.target.checked;});
  $('#quiet-mode').onchange=e=>quiet(a=>a.quiet_configure(null,null,null,e.target.value,null,null,null),()=>{demoQuiet.mode=e.target.value;});
  $('#quiet-boost').onchange=e=>quiet(a=>a.quiet_configure(null,null,null,null,null,null,e.target.value==='off'),()=>{demoQuiet.boost=e.target.value==='off';});
  $('#quiet-cap').onchange=e=>quiet(a=>a.quiet_configure(null,null,null,null,Number(e.target.value),null,null),()=>{
    const n=Number(e.target.value);
    if(!Number.isInteger(n)||n<30||n>100)throw new Error('The maximum processor state must be a whole number from 30 to 100 percent');
    demoQuiet.cap=n;
  });
  $('#quiet-awake').onchange=e=>quiet(a=>a.quiet_configure(null,null,null,null,null,e.target.checked,null),()=>{demoQuiet.keep=e.target.checked;});
  const recCall=(native_call,demo)=>quiet(native_call,demo);
  $('#rec-enabled').onchange=e=>recCall(a=>a.recorder_configure(e.target.checked,null,null),()=>{demoRec.enabled=e.target.checked;});
  $('#rec-save').onclick=()=>recCall(a=>a.recorder_configure(null,Number($('#rec-on-input').value),Number($('#rec-off-input').value)),()=>{
    const on=Number($('#rec-on-input').value),off=Number($('#rec-off-input').value);
    if(!(off>=10&&off<on&&on<=95))throw new Error('The end level must be at least 10% and lower than the start level (at most 95%)');
    demoRec.on=on;demoRec.off=off;showToast('Levels saved.');
  });
  $('#rec-clear').onclick=()=>recCall(a=>a.recorder_clear(),()=>{demoRec.spikes=[];});
  /* ---------- Video page: the lm commands as forms ---------- */
  const V={tab:'fetch',st:null,overview:null,kit:null,known:null,sources:[],items:[],outEdited:false,preview:null,previewKey:null,skew:0,sig:{},settingsInit:false,closeSeen:0,inspected:{},doctorRows:null,loading:false,countUntil:null,countShown:false,syncing:false,syncAgain:false};
  const sepOf=()=>(V.overview?.system||data?.platform)==='Windows'?'\\':'/';
  const fmtBytes=n=>{n=Number(n)||0;const u=['B','KB','MB','GB','TB'];let i=0;while(n>=1024&&i<4){n/=1024;i++;}return i?n.toFixed(1)+' '+u[i]:Math.round(n)+' B';};
  const clockText=sec=>{const s=Math.max(0,Math.round(sec)),h=Math.floor(s/3600),m=Math.floor(s%3600/60),r=s%60,p=n=>String(n).padStart(2,'0');return h?`${h}:${p(m)}:${p(r)}`:`${m}:${p(r)}`;};
  const jobWord={running:'Running',done:'Done',failed:'Failed',cancelled:'Cancelled'};
  async function vcall(method,...args){
    const r=native?await window.pywebview.api[method](...args):await demoVideo(method,args);
    if(!r||!r.ok)throw new Error((r&&r.error)||'Something went wrong');
    return r;
  }
  async function loadOverview(){try{V.overview=await vcall('video_overview');}catch(e){V.overview=null;showToast(e.message,true);}renderVideo();}
  async function loadKit(){try{V.kit=await vcall('video_kit');}catch(e){V.kit=null;}renderVideo();}
  async function videoSync(){                       // one request at a time, so that an older answer can never overwrite a newer one
    if(V.syncing){V.syncAgain=true;return;}
    V.syncing=true;
    try{await videoSyncOnce();}
    finally{V.syncing=false;if(V.syncAgain){V.syncAgain=false;videoSync();}}
  }
  async function videoSyncOnce(){
    if(!data||!(page==='video'||data.video?.running||data.video?.queued||V.st?.running))return;
    if(!V.overview&&!V.loading){V.loading=true;loadOverview();loadKit();}
    try{
      const r=await vcall('video_state',V.known);
      $('#v-offline').hidden=true;
      V.skew=r.now-Date.now()/1000;
      const prev=V.st;
      if(r.job&&r.job.lines===null&&prev?.job?.id===r.job.id)r.job.lines=prev.job.lines;
      V.st=r;V.known=r.job?[r.job.id,r.job.lines_total]:null;
      if(prev?.job&&prev.job.status==='running'&&r.job){
        if(r.job.id===prev.job.id){if(r.job.status!=='running')jobFinished(r.job);}
        else{const h=(r.history||[]).find(x=>x.id===prev.job.id);jobFinished({...prev.job,status:h?h.status:'done',title:h?h.title:prev.job.title,summary:h?h.summary:null});}
      }
    }catch(e){$('#v-offline').hidden=false;$('#v-offline').textContent=e.message;return;}
    const asked=data.video?.close_asked||0;
    if(asked>V.closeSeen&&Date.now()/1000-asked<15){V.closeSeen=asked;showToast('A video job is still running. Close the window again within 15 seconds to cancel it and quit.',true);}
    renderVideo();
  }
  function jobFinished(job){
    loadOverview();
    if(job.kind==='install_claude'||job.kind==='uninstall_claude')loadKit();
    if(job.kind==='doctor')V.doctorRows=job.rows;
    if(job.kind==='clean'&&job.status==='done'){
      const c=job.result?.clean;
      V.preview=c&&!c.applied?{key:V.previewKey,count:c.count,size:c.size}:null;
    }
    const word=job.status==='done'?'Finished':job.status==='cancelled'?'Cancelled':'Failed';
    showToast(`${word}: ${job.title}`,job.status==='failed');
  }
  async function startJob(kind,params){
    try{
      const r=await vcall('video_start',kind,params);
      V.known=null;
      if(r.queued)showToast(`Added to the queue (number ${r.position}).`);
      await videoSync();poll();
      const panel=$(r.queued?'#v-queue':'#v-job');if(panel.scrollIntoView)panel.scrollIntoView({block:'nearest',behavior:'smooth'});
    }catch(e){showToast(e.message,true);}
  }
  function projectOptions(select,keepEmpty){
    const names=(V.overview?.projects||[]).map(p=>p.name),sig=names.join('|');
    if(select.dataset.sig===sig)return;
    const old=select.value;
    select.innerHTML=names.length?names.map(n=>`<option value="${escape(n)}">${escape(n)}</option>`).join(''):`<option value="">${keepEmpty||'No projects yet'}</option>`;
    select.value=names.includes(old)?old:names[0]||'';select.dataset.sig=sig;
  }
  function pathRows(list,target,empty){
    $(target).innerHTML=list.length?list.map((s,i)=>`<div class="v-path"><span>${escape(s.path)}</span>${s.cloud?'<span class="badge">cloud</span>':''}${s.size!=null?`<span class="small muted">${fmtBytes(s.size)}</span>`:s.dir?'<span class="small muted">folder</span>':''}<button class="icon-button" data-remove="${i}" aria-label="Remove ${escape(s.name)}">×</button></div>`).join(''):`<p class="small muted">${empty}</p>`;
  }
  function logHtml(lines){
    return lines.map(t=>{const bad=/^lm: (?!warning)/.test(t)||/^\[warn\]/.test(t),warn=/^lm: warning/.test(t);return `<span class="${bad?'err':warn?'warn':''}">${escape(t)}</span>`;}).join('\n');
  }
  function diagRows(rows,target,empty){
    $(target).innerHTML=rows&&rows.length?rows.map(r=>`<div class="v-diag ${escape(r.tag)}"><span class="badge">${r.tag==='ok'?'OK':r.tag==='warn'?'Check':'Info'}</span><span>${escape(r.text)}</span></div>`).join(''):`<p class="small muted">${empty}</p>`;
  }
  const afterText={nothing:'Do nothing',sleep:'Put the computer to sleep',hibernate:'Hibernate',shutdown:'Shut down'};
  const afterVerb={sleep:'go to sleep',hibernate:'hibernate',shutdown:'shut down'};
  const pauseNote={cancelled:'Paused because you cancelled a job. What is left waits; press Resume to carry on.',
    failed:'Paused because a job failed. What is left waits; press Resume to carry on, or tick "Keep going when a job fails".',
    you:'Paused. Whatever you add waits until you press Resume.',restored:'Restored from the last time Load Manager ran. Nothing starts until you press Resume.'};
  function renderQueue(st,running){
    const q=st?.queue;
    if(!q){$('#v-queue').hidden=true;return;}
    $('#v-queue').hidden=!(running||q.items.length||q.paused||q.after!=='nothing'||q.countdown);
    $('#v-queue-note').textContent=q.paused?(pauseNote[q.reason]||'Paused.'):q.items.length?`${q.items.length} waiting. Each one starts when the one before it has finished.`:running?'Whatever you add with the buttons above runs after this job.':'Nothing is waiting.';
    $('#v-queue-list').innerHTML=q.items.map((it,i)=>`<div class="v-qrow"><span class="v-qn">${i+1}</span><div><strong>${escape(it.title)}</strong><small>${escape(it.command)}</small></div><span class="v-qbtns"><button class="icon-button" data-qmove="-1" data-qid="${it.id}" aria-label="Move up" ${i===0?'disabled':''}>↑</button><button class="icon-button" data-qmove="1" data-qid="${it.id}" aria-label="Move down" ${i===q.items.length-1?'disabled':''}>↓</button><button class="icon-button" data-qremove="${it.id}" aria-label="Remove from the queue">×</button></span></div>`).join('');
    $('#v-queue-pause').textContent=q.paused?'Resume':'Pause queue';
    $('#v-queue-clear').disabled=!q.items.length;
    const sel=$('#v-after'),sig=q.after_choices.join('|');
    if(sel.dataset.sig!==sig){sel.innerHTML=q.after_choices.map(a=>`<option value="${a}">${afterText[a]}</option>`).join('');sel.dataset.sig=sig;}
    if(document.activeElement!==sel)sel.value=q.after;
    $('#v-keep-going').checked=q.keep_going;
    $('#v-countdown').hidden=!q.countdown;
    if(q.countdown&&!V.countShown){
      V.countShown=true;V.countUntil=Date.now()/1000+q.countdown.seconds;
      showToast(`All done. The computer will ${afterVerb[q.countdown.action]} in ${Math.round(q.countdown.seconds)} seconds unless you cancel.`,true);
    }
    if(!q.countdown){V.countShown=false;V.countUntil=null;}
    $('#v-after-error').hidden=!q.error;$('#v-after-error').textContent=q.error||'';
  }
  function renderVideo(){
    if(!data)return;
    $('#video-dot').hidden=!(data.video?.running||(data.video?.queued>0&&!data.video?.paused));
    if(page!=='video')return;
    const st=V.st,job=st?.job,running=!!st?.running,ov=V.overview,mac=data.platform==='Darwin';
    $$('[data-mac]').forEach(el=>el.hidden=!mac);
    $$('[data-vtab]').forEach(b=>{const on=b.dataset.vtab===V.tab;b.classList.toggle('active',on);b.setAttribute('aria-selected',String(on));});
    $$('.v-tab').forEach(t=>t.hidden=t.id!=='v-tab-'+V.tab);
    // work folder
    $('#v-free').textContent=ov?fmtBytes(ov.free):'—';
    $('#v-free-note').textContent=ov&&ov.free<50*1024**3?' under 50 GB: big renders may fail halfway':'';
    $('#v-ffmpeg').textContent=ov?(ov.ffmpeg?'Found':'Not found'):'—';
    $('#v-ffmpeg-note').textContent=ov?(ov.ffmpeg?' '+ov.ffmpeg:(ov.system==='Darwin'?' install it with: brew install ffmpeg':' install it with: winget install ffmpeg, or choose it in Settings')):'';
    $('#v-count').textContent=ov?ov.projects.length:'—';
    $('#v-count-note').textContent=ov?' in '+ov.work+(ov.exists?'':' (not created yet)'):'';
    const sig=JSON.stringify(ov?.projects||[]);
    if(V.sig.projects!==sig){
      V.sig.projects=sig;
      $('#v-projects').innerHTML=ov&&ov.projects.length?ov.projects.map(p=>`<div class="v-project"><div><strong>${escape(p.name)}</strong></div><div>${fmtBytes(p.src.bytes)}<small>${p.src.files} in src</small></div><div>${fmtBytes(p.proxy.bytes)}<small>${p.proxy.files} proxies</small></div><div>${fmtBytes(p.out.bytes)}<small>${p.out.files} in out</small></div><div class="v-project-actions"><button class="text-button" data-act="open" data-name="${escape(p.name)}">Open</button> <button class="text-button" data-act="proxies" data-name="${escape(p.name)}">Proxies</button> <button class="text-button" data-act="clean" data-name="${escape(p.name)}">Free space</button></div></div>`).join(''):`<div class="empty">${ov?'No projects yet. Copy something from the cloud to start one.':'Reading the work folder…'}</div>`;
    }
    ['#v-proxy-project','#v-clean-project','#v-pub-project'].forEach(id=>projectOptions($(id)));
    const set=st?.settings;
    if(set&&!V.settingsInit){
      $('#v-set-hw').innerHTML=set.hw_choices.map(h=>`<option value="${h}">${h==='auto'?'Automatic (the best that works)':h==='none'?'None (software only)':h}</option>`).join('');
      $('#v-set-work').value=set.work;$('#v-set-hw').value=set.hw;$('#v-set-free').value=set.min_free_gb;$('#v-set-ffmpeg').value=set.ffmpeg;V.settingsInit=true;
    }
    // lists and warnings
    pathRows(V.sources,'#v-fetch-list','Nothing chosen yet.');
    $('#v-fetch-project').placeholder=V.sources[0]?V.sources[0].project:'taken from the folder name';
    pathRows(V.items,'#v-pub-list','Nothing chosen yet.');
    const encIn=$('#v-enc-in').value.trim(),info=V.inspected[encIn];
    $('#v-enc-warn').hidden=!(info&&info.cloud);
    if(info&&info.cloud)$('#v-enc-warn').textContent='This file is in a cloud folder, so encoding it there is refused. Copy it to the work folder first (step 1).';
    // buttons
    const lock=running?'Another job is running':'';
    const need={'#v-fetch-plan':V.sources.length,'#v-fetch-go':V.sources.length,'#v-proxy-go':$('#v-proxy-project').value,'#v-enc-plan':encIn,'#v-enc-go':encIn&&$('#v-enc-out').value.trim(),
      '#v-pub-go':V.items.length&&$('#v-pub-dest').value.trim(),'#v-clean-preview':$('#v-clean-project').value,'#v-run-go':$('#v-run-cmd').value.trim()};
    const queueing=!!(st&&(running||st.queue.items.length||st.queue.paused)),waits=['#v-fetch-go','#v-proxy-go','#v-enc-go','#v-pub-go','#v-run-go'];
    Object.keys(need).forEach(id=>{
      const b=$(id);
      if(waits.includes(id)){
        if(!b.dataset.label)b.dataset.label=b.innerHTML;
        const mode=queueing?'queue':'now';
        if(b.dataset.mode!==mode){b.dataset.mode=mode;b.innerHTML=queueing?icon('plus')+'Add to queue':b.dataset.label;}
        b.disabled=!need[id];b.title=queueing?'Runs after what is already planned':'';
      }else{b.disabled=running||!need[id];b.title=lock;}
    });
    renderQueue(st,running);
    ['#v-doctor-go','#v-kit-install','#v-kit-remove','#v-setup'].forEach(id=>{$(id).disabled=running;$(id).title=lock;});
    // clean preview
    const key=[$('#v-clean-project').value,$('#v-clean-src').checked,$('#v-clean-proxy').checked].join('|');
    const pv=V.preview&&V.preview.key===key?V.preview:null;
    $('#v-clean-go').hidden=!(pv&&pv.count>0);$('#v-clean-go').disabled=running;
    if(pv)$('#v-clean-go').textContent=`Delete ${pv.count} file${pv.count===1?'':'s'} (${pv.size})`;
    $('#v-clean-note').textContent=pv?(pv.count?`This would free ${pv.size}. The files are listed in the output below. Press the red button to delete them.`:'Nothing to remove: there are no copies that are safe to delete.'):'Press Preview to see what would be deleted. Nothing is deleted yet.';
    // doctor and kit
    diagRows(job&&job.kind==='doctor'?job.rows:V.doctorRows,'#v-doctor-rows','Press Run the check.');
    const kit=V.kit;
    diagRows(kit?[{tag:kit.hook?'ok':'warn',text:kit.hook?'The guard is registered in Claude\'s settings.json.':'The guard is not registered: Claude can still run ffmpeg inside a cloud folder.'},{tag:kit.skill?'ok':'warn',text:kit.skill?'The skill is installed: Claude knows the routine.':'The skill is not installed.'},{tag:kit.launcher?'ok':'warn',text:kit.launcher?'The lm command for Claude is in place.':'The lm command for Claude is not installed.'},{tag:'info',text:'Claude folder: '+kit.dir}]:[],'#v-kit-rows','Checking…');
    $('#v-kit-error').hidden=!(kit&&kit.error);$('#v-kit-error').textContent=kit?.error||'';
    $('#v-kit-install').textContent=kit&&kit.installed?'Reinstall':'Install';
    $('#v-kit-remove').disabled=running||!(kit&&(kit.hook||kit.skill||kit.launcher));
    // the job
    $('#v-job').hidden=!job;
    if(job){
      $('#v-job-title').textContent=job.title;$('#v-job-command').textContent=job.command;
      const badge=$('#v-job-badge');badge.textContent=job.cancelling&&job.status==='running'?'Cancelling…':jobWord[job.status];badge.className='badge'+(job.status==='running'||job.status==='done'?' managed':' protected');
      $('#v-job-cancel').hidden=job.status!=='running';$('#v-job-cancel').disabled=!!job.cancelling;
      $('#v-job-meter').hidden=!(job.status==='running'&&job.progress!=null);$('#v-job-bar').style.width=Math.round((job.progress||0)*100)+'%';
      const bits=[];
      if(job.status==='running'){
        if(job.count)bits.push(`Item ${job.index} of ${job.count}`);
        if(job.item)bits.push(job.item);
        if(job.progress!=null)bits.push(Math.round(job.progress*100)+'%');
        if(job.speed)bits.push(job.speed+' speed');
        if(job.eta)bits.push('about '+clockText(job.eta)+' left');
      }
      $('#v-job-detail').textContent=bits.join(' · ');
      $('#v-job-summary').textContent=job.status==='running'?'':job.summary||(job.status==='done'?'Finished.':'');
      $('#v-job-summary').className='small'+(job.status==='failed'?' error-text':'');
      $('#v-job-show').hidden=!(job.output&&job.status==='done');
      const logSig=job.id+':'+job.lines_total+':'+job.status;
      if(V.sig.log!==logSig&&job.lines){
        const box=$('#v-log'),stick=box.scrollTop+box.clientHeight>=box.scrollHeight-30;
        box.innerHTML=logHtml(job.lines);V.sig.log=logSig;
        if(stick)box.scrollTop=box.scrollHeight;
      }
    }
    $('#v-recent').hidden=!(st&&st.history.length);
    $('#v-history').innerHTML=st&&st.history.length?st.history.map(h=>`<div class="job-row"><span>${stamp(h.started)}</span><span>${escape(h.title)}</span><span>${jobWord[h.status]||h.status}</span><span>${clockText((h.ended||h.started)-h.started)}</span></div>`).join(''):'<div class="empty">Jobs you run here are listed with how they ended.</div>';
    tickVideoClock();
  }
  function tickVideoClock(){
    if(V.countUntil!==null&&!$('#v-countdown').hidden){
      const left=Math.max(0,Math.ceil(V.countUntil-Date.now()/1000)),a=V.st?.queue?.countdown?.action;
      $('#v-countdown-text').textContent=`The computer will ${afterVerb[a]||'switch off'} in ${left} s unless you cancel.`;
    }
    const job=V.st?.job;if(!job||$('#v-job').hidden)return;
    const end=job.ended||Date.now()/1000+V.skew;
    $('#v-job-clock').textContent=clockText(end-job.started);
  }
  setInterval(tickVideoClock,500);
  async function addPath(kind,value){
    const list=kind==='sources'?V.sources:V.items;
    const path=String(value||'').trim().replace(/^"(.*)"$/,'$1');
    if(!path)return;
    if(list.some(s=>s.path===path))return;
    try{
      const r=await vcall('video_inspect',path);
      list.push({path:r.path,name:r.name,dir:r.dir,cloud:r.cloud,size:r.size,project:r.project});
    }catch(e){showToast(e.message,true);}
    renderVideo();
  }
  async function inspectInput(){
    const value=$('#v-enc-in').value.trim();
    if(!value){renderVideo();return;}
    try{V.inspected[value]=await vcall('video_inspect',value);}catch(e){showToast(e.message,true);}
    if(!V.outEdited||!$('#v-enc-out').value.trim())await suggestOut();
    renderVideo();
  }
  async function suggestOut(){
    const value=$('#v-enc-in').value.trim();if(!value)return;
    try{const r=await vcall('video_suggest',value,$('#v-enc-preset').value,$('#v-enc-codec').value);$('#v-enc-out').value=r.output;}catch(e){/* the input is not valid yet */}
    renderVideo();
  }
  async function pickInto(button){
    const kind=button.dataset.pick,target=button.dataset.target,list=button.dataset.list;
    try{
      const start=target?$('#'+target).value:'';
      const r=await vcall('pick',kind,start);
      if(!r.paths.length)return;
      if(target){
        $('#'+target).value=r.paths[0];
        if(target==='v-enc-out')V.outEdited=true;
        $('#'+target).dispatchEvent(new Event('change',{bubbles:true}));
      }else for(const p of r.paths)await addPath(list,p);
    }catch(e){showToast(e.message,true);}
    renderVideo();
  }
  $$('[data-pick]').forEach(b=>b.onclick=()=>pickInto(b));
  $$('[data-vtab]').forEach(b=>b.onclick=()=>{V.tab=b.dataset.vtab;if(V.tab==='kit')loadKit();renderVideo();});
  $('#v-refresh').onclick=()=>{V.sig.projects=null;loadOverview();loadKit();poll();};
  $('#v-open-work').onclick=()=>vcall('video_reveal','work').catch(e=>showToast(e.message,true));
  $('#v-setup').onclick=()=>startJob('setup',{});
  $('#v-set-save').onclick=async()=>{
    try{
      const r=await vcall('video_configure',{work:$('#v-set-work').value,hw:$('#v-set-hw').value,min_free_gb:Number($('#v-set-free').value),ffmpeg:$('#v-set-ffmpeg').value});
      V.st={...(V.st||{}),settings:r.settings};showToast('Settings saved.');await loadOverview();
    }catch(e){showToast(e.message,true);}
  };
  $('#v-fetch-path').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();addPath('sources',e.target.value);e.target.value='';}};
  [['#v-fetch-list','sources'],['#v-pub-list','items']].forEach(([id,kind])=>{
    $(id).onclick=e=>{const b=e.target.closest('[data-remove]');if(!b)return;(kind==='sources'?V.sources:V.items).splice(Number(b.dataset.remove),1);renderVideo();};
  });
  const fetchParams=dry=>({sources:V.sources.map(s=>s.path),project:$('#v-fetch-project').value.trim(),verify:$('#v-fetch-verify').checked,dry_run:dry});
  $('#v-fetch-plan').onclick=()=>startJob('fetch',fetchParams(true));
  $('#v-fetch-go').onclick=()=>startJob('fetch',fetchParams(false));
  $('#v-proxy-go').onclick=()=>startJob('proxies',{project:$('#v-proxy-project').value,preset:$('#v-proxy-preset').value,low:$('#v-proxy-low').checked,sw:$('#v-proxy-sw').checked,force:$('#v-proxy-force').checked,no_hwdecode:$('#v-proxy-nohw').checked});
  $('#v-enc-in').onchange=inspectInput;
  $('#v-enc-in').oninput=()=>renderVideo();
  $('#v-enc-out').oninput=()=>{V.outEdited=true;renderVideo();};
  $('#v-enc-preset').onchange=$('#v-enc-codec').onchange=()=>{if(!V.outEdited)suggestOut();};
  const encodeParams=dry=>({input:$('#v-enc-in').value.trim(),output:$('#v-enc-out').value.trim(),preset:$('#v-enc-preset').value,codec:$('#v-enc-codec').value,bitrate:$('#v-enc-bitrate').value.trim(),extra:$('#v-enc-extra').value.trim(),
    low:$('#v-enc-low').checked,sw:$('#v-enc-sw').checked,force:$('#v-enc-force').checked,no_hwdecode:$('#v-enc-nohw').checked,dry_run:dry});
  $('#v-enc-plan').onclick=()=>startJob('encode',encodeParams(true));
  $('#v-enc-go').onclick=()=>startJob('encode',encodeParams(false));
  $('#v-pub-add-out').onclick=()=>{const name=$('#v-pub-project').value;if(name&&V.overview)addPath('items',V.overview.work+sepOf()+name+sepOf()+'out');};
  $('#v-pub-dest').oninput=()=>renderVideo();
  $('#v-pub-go').onclick=()=>startJob('publish',{items:V.items.map(s=>s.path),dest:$('#v-pub-dest').value.trim(),force:$('#v-pub-force').checked});
  const cleanParams=yes=>({project:$('#v-clean-project').value,src:$('#v-clean-src').checked,proxy:$('#v-clean-proxy').checked,yes});
  ['#v-clean-project','#v-clean-src','#v-clean-proxy'].forEach(id=>$(id).onchange=()=>renderVideo());
  $('#v-clean-preview').onclick=()=>{const p=cleanParams(false);V.previewKey=[p.project,p.src,p.proxy].join('|');V.preview=null;startJob('clean',p);};
  $('#v-clean-go').onclick=()=>{V.preview=null;startJob('clean',cleanParams(true));};
  $('#v-run-cmd').oninput=()=>renderVideo();
  $('#v-run-go').onclick=()=>startJob('run',{command:$('#v-run-cmd').value.trim(),name:$('#v-run-name').value.trim(),cwd:$('#v-run-cwd').value.trim(),low:$('#v-run-low').checked,no_notify:!$('#v-run-notify').checked});
  $('#v-doctor-go').onclick=()=>startJob('doctor',{});
  $('#v-kit-install').onclick=()=>startJob('install_claude',{});
  $('#v-kit-remove').onclick=()=>startJob('uninstall_claude',{});
  const queueCall=async(method,...args)=>{try{await vcall(method,...args);V.known=null;await videoSync();}catch(e){showToast(e.message,true);}};
  $('#v-queue-pause').onclick=()=>queueCall('video_queue_pause',!(V.st?.queue?.paused));
  $('#v-queue-clear').onclick=()=>queueCall('video_queue_clear');
  $('#v-queue-list').onclick=e=>{
    const move=e.target.closest('[data-qmove]'),remove=e.target.closest('[data-qremove]');
    if(move)queueCall('video_queue_move',Number(move.dataset.qid),Number(move.dataset.qmove));
    else if(remove)queueCall('video_queue_remove',Number(remove.dataset.qremove));
  };
  $('#v-after').onchange=e=>queueCall('video_queue_configure',null,e.target.value);
  $('#v-keep-going').onchange=e=>queueCall('video_queue_configure',e.target.checked,null);
  $('#v-countdown-cancel').onclick=()=>queueCall('video_cancel_countdown');
  $('#v-job-cancel').onclick=async()=>{try{await vcall('video_cancel');await videoSync();}catch(e){showToast(e.message,true);}};
  $('#v-job-copy').onclick=async()=>{
    const text=V.st?.job?.command||'';
    try{await navigator.clipboard.writeText(text);showToast('Command copied.');}catch(e){showToast('Select the command text and copy it.',true);}
  };
  $('#v-job-show').onclick=()=>vcall('video_reveal','path','',V.st.job.output).catch(e=>showToast(e.message,true));
  $('#v-projects').onclick=e=>{
    const b=e.target.closest('[data-act]');if(!b)return;
    const name=b.dataset.name;
    if(b.dataset.act==='open')return vcall('video_reveal','project',name).catch(err=>showToast(err.message,true));
    const select=$(b.dataset.act==='proxies'?'#v-proxy-project':'#v-clean-project');
    select.value=name;V.tab=b.dataset.act;V.preview=null;renderVideo();
  };

  // A stand-in for the real thing, so that the page can be tried in a browser (nothing here touches the computer).
  const demoV={settings:{work:'',hw:'auto',min_free_gb:5,ffmpeg:''},kit:{installed:false,hook:false,skill:false,launcher:false,error:null},job:null,history:[],counter:0,timer:null,hasWork:true,queue:[],paused:false,reason:null,keepGoing:false,after:'nothing',countdown:null,qcounter:0,countTimer:null,
    projects:[{name:'Wedding-2026',src:{bytes:41.2e9,files:14},proxy:{bytes:2.2e9,files:14},out:{bytes:6.5e9,files:2}},{name:'Lesson-12',src:{bytes:3.1e9,files:3},proxy:{bytes:0,files:0},out:{bytes:0,files:0}}]};
  const demoSep=()=>demoPlatform==='Windows'?'\\':'/';
  const demoWork=()=>demoPlatform==='Windows'?'C:\\Users\\demo\\VideoWork':'/Users/demo/VideoWork';
  function demoScript(kind,p){
    const w=demoWork(),s=demoSep(),name=(p.sources&&p.sources[0]||p.input||p.items&&p.items[0]||'clip.mov').split(/[\\/]/).pop();
    const batch=n=>Array.from({length:n},(_,i)=>`[${i+1}/${n}] clip-${String(i+1).padStart(2,'0')}.mov (${(1.2+i*.3).toFixed(1)} GB)`);
    switch(kind){
      case 'setup':return {title:'Create the work folder',cmd:'setup',lines:['Work folder ready: '+w],summary:'Work folder ready: '+w};
      case 'doctor':return {title:'Health check',cmd:'doctor',lines:['Load Manager 0.8.0 - video workflow report (read-only)','[info]    '+(demoPlatform==='Windows'?'Windows 11, AMD64':'macOS 15, arm64'),'[info]    free disk space at '+w+': 186.4 GB','[info]    cloud folder: '+(demoPlatform==='Windows'?'G:\\My Drive':'~/Library/CloudStorage/GoogleDrive-demo'),'[ok]      ffmpeg version 6.1.1',demoPlatform==='Windows'?'[ok]      self-test: h264_nvenc encoded 1 s of 720p':'[ok]      self-test: h264_videotoolbox encoded 1 s of 720p',demoPlatform==='Windows'?'[warn]    self-test: h264_qsv FAILED (no such hardware, or its driver)':'[ok]      caffeinate is available','[ok]      libx264 available'],summary:null};
      case 'fetch':return {title:(p.dry_run?'Check the plan for ':'Copy ')+name+' from the cloud',cmd:'fetch',lines:p.dry_run?[`Plan: 3 file(s), 4.1 GB -> ${w}${s}wedding${s}src`,'Free on that disk: 186.4 GB (keeping at least 5 GB spare)','(dry run, nothing copied)']:['Plan: 3 file(s), 4.1 GB -> '+w+s+'wedding'+s+'src',...batch(3),'Copied 3, skipped 0, failed 0.','Local copy: '+w+s+'wedding'+s+'src'],summary:p.dry_run?'(dry run, nothing copied)':'Copied 3, skipped 0, failed 0.',project:'wedding'};
      case 'proxies':return {title:'Make proxies'+(p.project?' for '+p.project:''),cmd:'proxies',lines:[...batch(4)],summary:`Proxies for ${p.project||'project'}: 4 made, 0 already existed, 0 failed (25s).`,project:p.project};
      case 'encode':return {title:(p.dry_run?'Check the command for ':'Encode ')+name+' ('+p.preset+')',cmd:'encode',lines:['Input duration: 183.0 s','Encoder: '+(demoPlatform==='Windows'?'h264_nvenc':'h264_videotoolbox')+' (hardware)   preset: '+p.preset,...(p.dry_run?['(dry run, nothing encoded)']:['Done: '+p.output+' (212.4 MB, source was 1.2 GB)'])],summary:p.dry_run?'(dry run, nothing encoded)':'Done: '+p.output+' (212.4 MB, source was 1.2 GB)',output:p.dry_run?null:p.output};
      case 'publish':return {title:`Copy ${p.items.length} item${p.items.length===1?'':'s'} to ${p.dest.split(/[\\/]/).pop()||p.dest}`,cmd:'publish',lines:[...p.items.map(i=>i.split(/[\\/]/).pop()+' (212.4 MB)'),'Published '+p.items.length+', skipped 0, failed 0. Local files were left untouched.','The cloud client now uploads in the background; check its icon for progress.'],summary:'Published '+p.items.length+', skipped 0, failed 0. Local files were left untouched.'};
      case 'run':return {title:'Run '+(p.name||p.command.split(' ')[0]),cmd:'run',lines:['(output of your command appears here)'],summary:null};
      case 'clean':return p.yes?{title:'Delete files for '+p.project,cmd:'clean',lines:['Removing 3 file(s), 4.1 GB:','  src/clip-01.mov','  src/clip-02.mov','  src/clip-03.mov'],summary:'Removing 3 file(s), 4.1 GB:',clean:{applied:true,count:3,size:'4.1 GB'}}
        :{title:'Preview what to delete for '+p.project,cmd:'clean',lines:['Would remove 3 file(s), 4.1 GB:','  src/clip-01.mov','  src/clip-02.mov','  src/clip-03.mov','Nothing was removed. Add --yes to do it. (out/ is never touched.)'],summary:'Nothing was removed. Add --yes to do it. (out/ is never touched.)',clean:{applied:false,count:3,size:'4.1 GB'}};
      case 'install_claude':return {title:'Install the Claude kit',cmd:'install-claude',lines:['Installing the Load Manager Claude kit into ~/.claude','  skill and launcher written','  guard hook registered in settings.json','Done. Restart Claude so it picks the changes up.'],summary:'Done. Restart Claude so it picks the changes up.'};
      default:return {title:'Remove the Claude kit',cmd:'uninstall-claude',lines:['Removing the Load Manager Claude kit from ~/.claude','  guard hook removed from settings.json','  skill and launcher removed. The work folder was left as it is.'],summary:null};
    }
  }
  function demoCommand(kind,p,cmd){
    const q=v=>/\s/.test(v)?`"${v}"`:v;
    const flags=Object.entries({'--verify':p.verify,'--dry-run':p.dry_run,'--low':p.low,'--sw':p.sw,'--force':p.force,'--yes':p.yes,'--src':p.src,'--proxy':p.proxy}).filter(e=>e[1]).map(e=>e[0]);
    const args=[p.project,p.input,p.output,...(p.sources||[]),...(p.items||[]),p.dest,p.command].filter(Boolean).map(q);
    return `lm ${cmd} ${[...flags,...args].join(' ')}`.trim();
  }
  function demoJobView(withLines){
    const j=demoV.job;if(!j)return null;
    const running=j.status==='running',total=j.script.lines.length;
    return {id:j.id,kind:j.kind,title:j.script.title,command:j.command,status:j.status,code:running?null:j.status==='done'?0:j.status==='cancelled'?130:1,started:j.started,ended:j.ended,cancelling:false,
      progress:running?(j.shown/Math.max(1,total))*.95:null,speed:running&&j.kind==='encode'?'2.1x':null,eta:running?Math.round((total-j.shown)*.5):null,index:null,count:null,item:'',summary:j.status==='cancelled'?'interrupted':j.script.summary,rows:j.kind==='doctor'?j.lines.filter(l=>/^\[/.test(l)).map(l=>({tag:l.match(/^\[(\w+)\]/)[1],text:l.replace(/^\[\w+\]\s*/,'')})):[],
      output:j.script.output||null,project:j.script.project||null,lines_total:j.lines.length,lines:withLines?[...j.lines]:null,result:j.script.clean?{clean:j.script.clean}:null};
  }
  const demoAfterChoices=()=>demoPlatform==='Darwin'?['nothing','sleep','shutdown']:['nothing','sleep','hibernate','shutdown'];
  function demoQueueView(){
    const c=demoV.countdown;
    return {items:demoV.queue.map(i=>({id:i.id,kind:i.kind,title:i.title,command:i.command})),paused:demoV.paused,reason:demoV.reason,keep_going:demoV.keepGoing,after:demoV.after,after_choices:demoAfterChoices(),
      countdown:c?{action:c.action,seconds:Math.max(0,c.until-Date.now()/1000)}:null,error:null};
  }
  function demoStartJob(kind,params){
    const script=demoScript(kind,params||{});
    const job={id:++demoV.counter,kind,params:params||{},script,command:demoCommand(kind,params||{},script.cmd),status:'running',started:Date.now()/1000,ended:null,lines:[],shown:0};
    demoV.job=job;demoV.countdown=null;clearTimeout(demoV.countTimer);
    demoV.timer=setInterval(()=>{
      if(job.shown<script.lines.length){job.lines.push(script.lines[job.shown++]);poll();}
      else demoFinish(job,'done');
    },window.__lmDemoTick||450);
    return job;
  }
  function demoAdvance(){
    if(demoV.paused||!demoV.queue.length||(demoV.job&&demoV.job.status==='running'))return;
    const item=demoV.queue.shift();demoStartJob(item.kind,item.params);
  }
  function demoCountdown(){
    if(demoV.after==='nothing'||demoV.countdown||demoV.queue.length||(demoV.job&&demoV.job.status==='running'))return;
    const action=demoV.after;demoV.countdown={action,until:Date.now()/1000+60};
    demoV.countTimer=setTimeout(()=>{demoV.countdown=null;demoV.after='nothing';showToast('Demo: the computer would '+afterVerb[action]+' now.');poll();},60000);
  }
  function demoFinish(j,status){
    clearInterval(demoV.timer);j.status=status;j.ended=Date.now()/1000;
    if(status==='cancelled')j.lines.push('lm: interrupted');
    demoV.history.unshift({id:j.id,kind:j.kind,title:j.script.title,command:j.command,status,code:status==='done'?0:130,started:j.started,ended:j.ended,summary:j.script.summary||''});
    demoV.history=demoV.history.slice(0,12);
    if(status==='done'){
      if(j.kind==='install_claude')Object.assign(demoV.kit,{installed:true,hook:true,skill:true,launcher:true});
      if(j.kind==='uninstall_claude')Object.assign(demoV.kit,{installed:false,hook:false,skill:false,launcher:false});
      if(j.kind==='fetch'&&!j.params.dry_run&&!demoV.projects.some(p=>p.name==='wedding'))demoV.projects.push({name:'wedding',src:{bytes:4.1e9,files:3},proxy:{bytes:0,files:0},out:{bytes:0,files:0}});
      if(j.kind==='clean'&&j.params.yes){const p=demoV.projects.find(p=>p.name===j.params.project);if(p){if(j.params.src)p.src={bytes:0,files:0};if(j.params.proxy)p.proxy={bytes:0,files:0};}}
      if(j.kind==='proxies'){const p=demoV.projects.find(p=>p.name===j.params.project);if(p)p.proxy={bytes:p.src.bytes/18,files:p.src.files};}
    }
    if(status==='cancelled'){if(demoV.queue.length){demoV.paused=true;demoV.reason='cancelled';}}
    else{demoAdvance();demoCountdown();}
    poll();
  }
  async function demoVideo(method,args){
    const ok=extra=>({ok:true,...extra});
    switch(method){
      case 'video_state':{
        const j=demoJobView(!(demoV.job&&args[0]&&args[0][0]===demoV.job.id&&args[0][1]===demoV.job.lines.length));
        return ok({running:!!(demoV.job&&demoV.job.status==='running'),job:j,history:demoV.history,queue:demoQueueView(),now:Date.now()/1000,settings:{...demoV.settings,work_effective:demoWork(),ffmpeg_found:'ffmpeg',hw_choices:['auto','none',demoPlatform==='Windows'?'nvenc':'videotoolbox',...(demoPlatform==='Windows'?['amf','qsv']:[])],platform:demoPlatform}});
      }
      case 'video_overview':return ok({work:demoWork(),exists:demoV.hasWork,free:186.4e9,projects:demoV.projects,ffmpeg:'ffmpeg',system:demoPlatform,time:Date.now()/1000});
      case 'video_kit':return ok({dir:demoPlatform==='Windows'?'C:\\Users\\demo\\.claude':'/Users/demo/.claude',...demoV.kit});
      case 'video_configure':Object.assign(demoV.settings,args[0]);return ok({settings:{...demoV.settings,work_effective:demoWork(),ffmpeg_found:'ffmpeg',hw_choices:['auto','none'],platform:demoPlatform}});
      case 'video_inspect':{const path=String(args[0]),cloud=/drive|dropbox|onedrive|icloud|cloudstorage/i.test(path),name=path.split(/[\\/]/).pop();
        return ok({path,name,dir:!/\.\w{2,4}$/.test(name),cloud,size:/\.\w{2,4}$/.test(name)?1.2e9:null,project:name.replace(/\.\w+$/,'').replace(/[^A-Za-z0-9._]+/g,'-')||'project',inside_work:path.startsWith(demoWork())});}
      case 'video_suggest':{const base=String(args[0]).split(/[\\/]/).pop().replace(/\.\w+$/,'');return ok({output:demoWork()+demoSep()+'wedding'+demoSep()+'out'+demoSep()+base+'-'+args[1]+'.mp4'});}
      case 'video_reveal':return ok({shown:demoWork()});
      case 'video_cancel':{const j=demoV.job;if(j&&j.status==='running'){demoFinish(j,'cancelled');return ok({cancelled:true});}return ok({cancelled:false});}
      case 'pick':{const kind=args[0],w=demoSep()==='\\'?'G:\\My Drive\\Wedding 2026\\':'/Users/demo/Library/CloudStorage/GoogleDrive-demo/My Drive/Wedding 2026/';
        const paths=kind==='videos'?[w+'A001.mov',w+'A002.mov']:kind==='files'?[demoWork()+demoSep()+'wedding'+demoSep()+'out'+demoSep()+'final.mp4']:kind==='save'?[demoWork()+demoSep()+'wedding'+demoSep()+'out'+demoSep()+'picked.mp4']:kind==='program'?['C:\\ffmpeg\\bin\\ffmpeg.exe']:[w.slice(0,-1)];
        return ok({paths});}
      case 'video_start':{
        const [kind,params]=args,running=!!(demoV.job&&demoV.job.status==='running');
        if(kind==='clean'&&!params.src&&!params.proxy)throw new Error('Choose what to clean: the copies from the cloud, the proxies, or both');
        const waits=['fetch','encode','proxies','publish','run'].includes(kind);
        if(waits&&(running||demoV.queue.length||demoV.paused)){
          const script=demoScript(kind,params||{});
          demoV.queue.push({id:++demoV.qcounter,kind,params:params||{},title:script.title,command:demoCommand(kind,params||{},script.cmd)});
          return ok({queued:true,id:demoV.qcounter,position:demoV.queue.length});
        }
        if(running)throw new Error('Another video job is still running. Wait for it or cancel it first.');
        return ok({queued:false,id:demoStartJob(kind,params).id});
      }
      case 'video_queue_remove':demoV.queue=demoV.queue.filter(i=>i.id!==args[0]);if(!demoV.queue.length&&demoV.reason!=='you'){demoV.paused=false;demoV.reason=null;}return ok({queue:demoQueueView()});
      case 'video_queue_move':{const i=demoV.queue.findIndex(x=>x.id===args[0]),t=i+(args[1]<0?-1:1);if(i>=0&&t>=0&&t<demoV.queue.length)[demoV.queue[i],demoV.queue[t]]=[demoV.queue[t],demoV.queue[i]];return ok({queue:demoQueueView()});}
      case 'video_queue_clear':demoV.queue=[];if(demoV.reason!=='you'){demoV.paused=false;demoV.reason=null;}return ok({queue:demoQueueView()});
      case 'video_queue_pause':demoV.paused=!!args[0];demoV.reason=args[0]?'you':null;if(!args[0])demoAdvance();return ok({queue:demoQueueView()});
      case 'video_queue_configure':{
        if(args[1]!==null&&args[1]!==undefined){if(!demoAfterChoices().includes(args[1]))throw new Error('This computer cannot do that');demoV.after=args[1];if(args[1]==='nothing'){demoV.countdown=null;clearTimeout(demoV.countTimer);}}
        if(args[0]!==null&&args[0]!==undefined)demoV.keepGoing=!!args[0];
        return ok({queue:demoQueueView()});}
      case 'video_cancel_countdown':demoV.countdown=null;demoV.after='nothing';clearTimeout(demoV.countTimer);return ok({queue:demoQueueView()});
      default:throw new Error('Not available in the demo');
    }
  }
  setInterval(tickClock,250);
  $('#refresh').onclick=()=>poll();
  $('#demo-platform').onclick=()=>{if(demoActive)return showToast('End the demo session first.');demoPlatform=demoPlatform==='Windows'?'Darwin':'Windows';selected.clear();selectedGroups.clear();poll();};
  $('#demo-banner').hidden=native;
  window.addEventListener('pywebviewready',poll);
  navigate(location.hash.slice(1));poll();
  setInterval(()=>{if(!document.hidden)poll();},2000);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)poll();});
});
