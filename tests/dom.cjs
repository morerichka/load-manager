const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require('jsdom');
const ui=path.resolve(__dirname,'../loadmanager/ui');
let html=fs.readFileSync(path.join(ui,'index.html'),'utf8');
html=html.replace('<script src="app.js" defer></script>',()=>'<script>'+fs.readFileSync(path.join(ui,'app.js'),'utf8')+'</script>');
const tick=(ms=30)=>new Promise(resolve=>setTimeout(resolve,ms));
function create(native=false,api=null){
  return new JSDOM(native?html.replace('data-native="false"','data-native="true"'):html,{
    runScripts:'dangerously',url:'https://loadmanager.test',pretendToBeVisual:true,
    beforeParse(window){
      window.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
      window.HTMLDialogElement.prototype.close=function(){this.open=false;};
      if(api)window.pywebview={api};
    }
  });
}
const input=(dom,el,value)=>{el.value=value;el.dispatchEvent(new dom.window.Event('input'));};

const nativeSnapshot=require('./snapshot.cjs');

(async()=>{
  const dom=create();await tick();
  const doc=dom.window.document,$=selector=>doc.querySelector(selector);
  assert.equal($('#demo-banner').hidden,false);
  assert.notEqual($('#cpu-value').textContent,'—');
  $('[data-page="apps"]').click();
  assert.equal(doc.querySelectorAll('#process-body input').length,3,'only light apps are picked one by one');
  $('#process-search').value='spotify';$('#process-search').dispatchEvent(new dom.window.Event('input'));
  assert.equal(doc.querySelectorAll('#process-body tr').length,1);
  $('#process-body input').click();
  assert.equal($('#selected-count').textContent,'1');
  $('#review-action').click();await tick();
  assert.equal($('#plan-dialog').open,true);
  assert.match($('#plan-description').textContent,/Simulation/);
  $('#confirm-plan').click();await tick();
  assert.equal($('#main-action-label').textContent,'End session');
  $('#main-action').click();await tick();
  assert.equal($('#main-action-label').textContent,'Set up session');
  $('#demo-platform').click();await tick();
  $('[data-page="apps"]').click();
  $('#process-body input').click();$('#review-action').click();await tick();
  assert.match($('#plan-note').textContent,/does not free memory/);
  $('#cancel-plan').click();
  $('[data-page="activity"]').click();
  assert.match($('#event-list').textContent,/restored/);
  dom.window.close();

  // Heavy apps: whole groups on Windows, protected on macOS.
  const win=create();await tick();
  const w=win.window.document,W=selector=>w.querySelector(selector);
  W('[data-page="apps"]').click();
  assert.equal(w.querySelectorAll('#group-list .group-card').length,4);
  assert.equal(w.querySelectorAll('#group-list input:not([disabled])').length,4);
  input(win,W('#process-search'),'chrome');
  assert.equal(w.querySelectorAll('#process-body tr').length,3);
  assert.equal(w.querySelectorAll('#process-body input').length,0,'browser helpers are never picked one by one');
  input(win,W('#process-search'),'');
  W('#select-heavy').click();
  assert.equal(W('#selected-count').textContent,'4');
  W('#review-action').click();await tick();
  assert.match(W('#plan-list').textContent,/Google Chrome · 3 processes/);
  assert.match(W('#plan-list').textContent,/Claude · 2 processes.*memory trim/);
  const claudeLine=[...w.querySelectorAll('#plan-list li')].find(li=>li.textContent.startsWith('Claude'));
  assert.doesNotMatch(claudeLine.textContent,/priority|efficiency/,'Claude is trim-only');
  assert.match(W('#plan-note').textContent,/stutter/);
  W('#confirm-plan').click();await tick();
  assert.equal(W('#main-action-label').textContent,'End session');
  assert.match(W('#session-stats').textContent,/412 MB RAM freed/);
  W('#restore-action').click();await tick();
  assert.equal(W('#main-action-label').textContent,'Set up session');

  // One-click editing mode picks the render profile and every running heavy app.
  W('#quick-render').click();await tick();
  assert.equal(W('#plan-dialog').open,true);
  assert.match(W('#plan-description').textContent,/Render/);
  assert.equal(w.querySelectorAll('#plan-list li').length,4);
  W('#cancel-plan').click();

  // Benchmark: before / during / after a session, with an honest explanation.
  W('[data-page="overview"]').click();
  W('#bench-run').click();await tick(1700);
  assert.match(W('#bench-base').textContent,/12\.5 GB\/s/);
  assert.equal(W('#bench-delta').textContent,'—');
  assert.match(W('#bench-note').textContent,/start a session and measure again/);
  W('#review-action').click();await tick();
  W('#confirm-plan').click();await tick();
  W('#bench-run').click();await tick(1700);
  assert.match(W('#bench-now').textContent,/29\.4 GB\/s/);
  assert.equal(W('#bench-delta').textContent,'+135.2%');
  assert.match(W('#bench-note').textContent,/135\.2% more CPU.*not measured/);
  win.window.close();

  const mac=create();await tick();
  const m=mac.window.document,M=selector=>m.querySelector(selector);
  M('#demo-platform').click();await tick();
  M('[data-page="apps"]').click();
  assert.equal(m.querySelectorAll('#group-list input:not([disabled])').length,0,'no heavy group is selectable on macOS');
  assert.match(M('#group-list').textContent,/Unavailable on this system/);
  M('#quick-render').click();await tick();
  assert.equal(M('#plan-dialog').open,false);
  assert.match(M('#toast').textContent,/Windows only/);
  mac.window.close();

  // Render page (demo): auto-start switch, groups, timed A/B with a live clock.
  const rn=create();await tick();
  const r=rn.window.document,R=selector=>r.querySelector(selector);
  R('[data-page="render"]').click();
  assert.equal(r.querySelectorAll('#auto-groups input').length,4);
  assert.equal(r.querySelectorAll('#job-list .job-row').length,2);
  assert.match(R('#auto-status').textContent,/Auto-start is off/);
  R('#auto-enabled').click();await tick();
  assert.match(R('#auto-status').textContent,/Waiting for a render/);
  assert.equal(R('#auto-enabled').checked,true);
  ['claude','telegram','gdrive'].forEach(key=>r.querySelector(`[data-autogroup="${key}"]`).click());
  await tick();
  assert.equal(r.querySelectorAll('#auto-groups input:checked').length,1);
  r.querySelector('#auto-groups input:checked').click();await tick();
  assert.match(R('#toast').textContent,/at least one/);
  assert.equal(r.querySelectorAll('#auto-groups input:checked').length,1,'the last app cannot be unticked');
  R('#timed-ab').click();await tick();
  assert.match(R('#toast').textContent,/Enter a command first/);
  R('#timed-command').value='ffmpeg -i in.mp4 out.mp4';
  R('#timed-ab').click();await tick(120);
  assert.equal(R('#timed-live').hidden,false);
  assert.match(R('#timed-phase').textContent,/without session/);
  assert.equal(R('#timed-ab').disabled,true);
  await tick(1700);
  assert.match(R('#timed-phase').textContent,/with session/);
  await tick(1700);
  assert.equal(R('#timed-live').hidden,true);
  assert.equal(R('#timed-base').textContent,'6:12');
  assert.equal(R('#timed-now').textContent,'4:41');
  assert.match(R('#timed-saved').textContent,/1:31 \(24\.5%\)/);
  assert.match(R('#timed-note').textContent,/cache/);
  assert.equal(r.querySelectorAll('#timed-history .job-row').length,2);
  assert.equal(R('#timed-command').value,'ffmpeg -i in.mp4 out.mp4','the typed command survives polling');
  R('#timed-session').click();await tick(120);
  R('#timed-cancel').click();await tick(120);
  assert.equal(R('#timed-live').hidden,true);
  assert.match(R('#timed-error').textContent,/Cancelled/);
  rn.window.close();

  // Spike log (demo): periods, culprits, recorder switch and levels, clearing.
  const sp=create();await tick();
  const q=sp.window.document,Q=selector=>q.querySelector(selector);
  Q('[data-page="spikes"]').click();
  assert.equal(q.querySelectorAll('#rec-list details').length,2);
  assert.match(Q('#rec-list').textContent,/ffmpeg\.exe 62\.1%/);
  assert.match(Q('#rec-list').textContent,/python\.exe via Claude/);
  assert.match(Q('#rec-top .job-row').textContent,/ffmpeg\.exe/);
  assert.match(Q('#rec-status').textContent,/No busy period right now\. 2 recorded/);
  assert.equal(Q('#rec-on-input').value,'40');
  Q('#rec-enabled').click();await tick();
  assert.match(Q('#rec-status').textContent,/Recording is off/);
  Q('#rec-on-input').value='10';Q('#rec-off-input').value='20';Q('#rec-save').click();await tick();
  assert.match(Q('#toast').textContent,/end level/);
  Q('#rec-on-input').value='50';Q('#rec-off-input').value='30';Q('#rec-save').click();await tick();
  assert.match(Q('#toast').textContent,/Levels saved/);
  Q('#rec-clear').click();await tick();
  assert.equal(q.querySelectorAll('#rec-list details').length,0);
  assert.match(Q('#rec-top').textContent,/will be listed here/);

  // Power page (demo): enter, render phase on the original plan, back to quiet, leave.
  Q('[data-page="power"]').click();
  assert.equal(q.querySelectorAll('#quiet-scheme option').length,5);
  assert.match(Q('#quiet-status').textContent,/Quiet mode is off\. Active plan: Turbo/);
  assert.equal(Q('#quiet-leave').disabled,true);
  Q('#quiet-enter').click();await tick();
  assert.match(Q('#quiet-status').textContent,/Quiet mode is on .*Active plan: Silent\. Original plan: Turbo/);
  assert.equal(Q('#quiet-badge').textContent,'Quiet');
  assert.equal(Q('#quiet-enter').disabled,true);assert.equal(Q('#quiet-scheme').disabled,true);
  assert.equal(Q('#quiet-leave').textContent,'Return to Turbo');
  Q('[data-page="overview"]').click();
  Q('#quick-render').click();await tick();Q('#confirm-plan').click();await tick();
  Q('[data-page="power"]').click();
  assert.equal(Q('#quiet-badge').textContent,'Render');
  assert.match(Q('#quiet-status').textContent,/Active plan: Turbo\..*A render is running/);
  Q('#restore-action').click();await tick();
  assert.equal(Q('#quiet-badge').textContent,'Quiet');
  assert.match(Q('#quiet-status').textContent,/Active plan: Silent/);
  Q('#quiet-leave').click();await tick();
  assert.equal(Q('#quiet-badge').textContent,'Off');
  assert.match(Q('#quiet-status').textContent,/Quiet mode is off/);
  assert.equal(Q('#quiet-cap-field').hidden,true);assert.equal(Q('#quiet-boost-field').hidden,true);assert.equal(Q('#quiet-scheme-field').hidden,false);
  Q('#quiet-mode').value='limit';Q('#quiet-mode').dispatchEvent(new sp.window.Event('change'));await tick();
  assert.equal(Q('#quiet-cap-field').hidden,false);assert.equal(Q('#quiet-boost-field').hidden,false);assert.equal(Q('#quiet-scheme-field').hidden,true);
  assert.match(Q('#quiet-method-note').textContent,/sleep and display settings stay/);
  Q('#quiet-cap').value='20';Q('#quiet-cap').dispatchEvent(new sp.window.Event('change'));await tick();
  assert.match(Q('#toast').textContent,/whole number from 30 to 100/);
  Q('#quiet-cap').value='65';Q('#quiet-cap').dispatchEvent(new sp.window.Event('change'));await tick();
  Q('#quiet-enter').click();await tick();
  assert.match(Q('#quiet-status').textContent,/In Turbo: Turbo boost: Aggressive → Off; Maximum processor state: 100% → 65%/);
  assert.equal(Q('#quiet-badge').textContent,'Quiet');assert.equal(Q('#quiet-mode').disabled,true);assert.equal(Q('#quiet-cap').disabled,true);assert.equal(Q('#quiet-boost').disabled,true);
  assert.equal(Q('#quiet-leave').textContent,'Remove the limits');
  Q('#quiet-awake').click();await tick();
  assert.equal(Q('#quiet-awake').checked,false);
  Q('#quiet-awake').click();await tick();
  Q('[data-page="overview"]').click();
  Q('#quick-render').click();await tick();Q('#confirm-plan').click();await tick();
  Q('[data-page="power"]').click();
  assert.equal(Q('#quiet-badge').textContent,'Render');
  assert.match(Q('#quiet-status').textContent,/original settings are in use.*kept awake/);
  Q('#restore-action').click();await tick();
  assert.match(Q('#quiet-status').textContent,/Maximum processor state: 100% → 65%/);
  Q('#quiet-leave').click();await tick();
  assert.match(Q('#quiet-status').textContent,/Quiet mode is off/);
  Q('#demo-platform').click();await tick();
  assert.match(Q('#quiet-status').textContent,/needs Windows power plans/);
  assert.equal(Q('#quiet-enter').disabled,true);
  sp.window.close();

  // Native shell contract: the UI sends {group} selections and shows what the engine reports.
  const calls={};
  let snap=nativeSnapshot();
  const api={
    snapshot:async()=>snap,
    auto_configure:async(enabled,groups,grace)=>{calls.auto=[enabled,groups,grace];return {ok:true};},
    timed_start:async(...args)=>{calls.timed=args;return {ok:true};},
    timed_cancel:async()=>{calls.cancel=true;return {ok:true};},
    quiet_enter:async()=>{calls.quietEnter=true;return {ok:true};},
    quiet_leave:async()=>{calls.quietLeave=true;return {ok:true};},
    quiet_configure:async(...a)=>{calls.quietConfigure=a;return {ok:true};},
    recorder_configure:async(...a)=>{calls.recorderConfigure=a;return {ok:true};},
    recorder_clear:async()=>{calls.recorderClear=true;return {ok:true};},
    preview:async(selection,profile)=>{calls.preview=[selection,profile];return {ok:true,profile,apps:[{app:'chrome',title:'Google Chrome',processes:2,actions:{priority:2,eco:2,trim:2}}]};},
    apply:async(selection,profile)=>{calls.apply=[selection,profile];return {ok:true,applied:2,freed_mb:120.5,session:true,actions:{priority:2}};},
    benchmark:async()=>{calls.benchmark=true;return {ok:true};},
    restore:async()=>({ok:true,restored:2,remaining:0})
  };
  const nat=create(true,api);await tick(80);
  const n=nat.window.document,N=selector=>n.querySelector(selector);
  assert.equal(N('#demo-banner').hidden,true);
  N('[data-page="apps"]').click();
  assert.equal(n.querySelectorAll('#group-list .group-card').length,1);
  assert.equal(n.querySelectorAll('#process-body input').length,1,'only Spotify is a per-process target');
  N('[data-group="chrome"]').click();
  assert.equal(N('#selected-count').textContent,'1');
  N('#review-action').click();await tick(80);
  assert.equal(JSON.stringify(calls.preview),JSON.stringify([[{group:'chrome'}],'auto']));
  assert.match(N('#plan-list').textContent,/Google Chrome · 2 processes/);
  assert.match(N('#plan-list').textContent,/priority ↓ · efficiency mode · memory trim/);
  assert.doesNotMatch(N('#plan-description').textContent,/Simulation/);
  N('#confirm-plan').click();await tick(80);
  assert.equal(JSON.stringify(calls.apply),JSON.stringify([[{group:'chrome'}],'auto']));
  assert.match(N('#toast').textContent,/Memory freed: 120\.5 MB/);
  N('#bench-run').click();await tick(80);
  assert.equal(calls.benchmark,true);

  N('[data-page="render"]').click();
  assert.equal(N('#timed-command').value,'saved command','the last command is restored from settings');
  N('#auto-enabled').click();await tick(80);
  assert.equal(JSON.stringify(calls.auto),JSON.stringify([true,null,null]));
  N('#timed-command').value='ffmpeg -i a.mp4 b.mp4';N('#timed-cwd').value='C:\work';N('#timed-order').value='session_first';
  N('#refresh').click();await tick(80);
  assert.equal(N('#timed-command').value,'ffmpeg -i a.mp4 b.mp4','polling must not overwrite what is being typed');
  N('#timed-ab').click();await tick(80);
  assert.equal(JSON.stringify(calls.timed),JSON.stringify(['ffmpeg -i a.mp4 b.mp4','C:\work','ab','session_first']));
  snap={...nativeSnapshot(),timed:{...nativeSnapshot().timed,running:true,mode:'ab',phase:'session',phase_started:935}};
  N('#refresh').click();await tick(80);
  assert.equal(N('#timed-live').hidden,false);
  assert.match(N('#timed-phase').textContent,/with session/);
  assert.match(N('#timed-clock').textContent,/^1:0[5-9]$/);
  assert.equal(N('#timed-ab').disabled,true);
  N('#timed-cancel').click();await tick(80);
  assert.equal(calls.cancel,true);

  snap=nativeSnapshot();
  N('[data-page="power"]').click();
  assert.equal(n.querySelectorAll('#quiet-scheme option').length,5);
  assert.match(N('#quiet-scheme option').textContent,/Automatic \(now: Silent\)/);
  N('#quiet-enter').click();await tick(80);
  assert.equal(calls.quietEnter,true);
  const choose=(sel,value)=>{sel.value=value;sel.dispatchEvent(new nat.window.Event('change'));};
  choose(N('#quiet-scheme'),'381b4222-f694-41f0-9685-ff5bb260df2e');await tick(80);
  assert.equal(JSON.stringify(calls.quietConfigure),JSON.stringify(['381b4222-f694-41f0-9685-ff5bb260df2e',null,null,null,null,null,null]));
  choose(N('#quiet-render'),'27fa6203-3987-4dcc-918d-748559d549ec');await tick(80);
  assert.equal(JSON.stringify(calls.quietConfigure),JSON.stringify([null,'27fa6203-3987-4dcc-918d-748559d549ec',null,null,null,null,null]));
  N('#quiet-auto').click();await tick(80);
  assert.equal(JSON.stringify(calls.quietConfigure),JSON.stringify([null,null,true,null,null,null,null]));
  choose(N('#quiet-mode'),'limit');await tick(80);
  assert.equal(JSON.stringify(calls.quietConfigure),JSON.stringify([null,null,null,'limit',null,null,null]));
  choose(N('#quiet-boost'),'keep');await tick(80);
  assert.equal(JSON.stringify(calls.quietConfigure),JSON.stringify([null,null,null,null,null,null,false]));
  N('#quiet-cap').value='65';N('#quiet-cap').dispatchEvent(new nat.window.Event('change'));await tick(80);
  assert.equal(JSON.stringify(calls.quietConfigure),JSON.stringify([null,null,null,null,65,null,null]));
  assert.equal(N('#quiet-awake').checked,true);
  N('#quiet-awake').click();await tick(80);
  assert.equal(JSON.stringify(calls.quietConfigure),JSON.stringify([null,null,null,null,null,false,null]));
  const capSnap=nativeSnapshot();
  capSnap.quiet={...capSnap.quiet,mode:'limit',cap:65};
  snap=capSnap;N('#refresh').click();await tick(80);
  assert.equal(N('#quiet-cap-field').hidden,false);assert.equal(N('#quiet-boost-field').hidden,false);assert.equal(N('#quiet-scheme-field').hidden,true);assert.equal(N('#quiet-cap').value,'65');
  assert.equal(N('#quiet-boost').value,'off');
  const capActive=nativeSnapshot();
  capActive.quiet={...capActive.quiet,mode:'limit',cap:65,active:true,state:'quiet',original:{guid:'6fecc5ae-f350-48a5-b669-b472cb895ccf',name:'Turbo'},limits:[{label:'Turbo boost',before:'Aggressive',now:'Off'},{label:'Maximum processor state',before:'100%',now:'65%'}],since:900,awake:true};
  snap=capActive;N('#refresh').click();await tick(80);
  assert.match(N('#quiet-status').textContent,/In Turbo: Turbo boost: Aggressive → Off; Maximum processor state: 100% → 65%\..*kept awake/);
  assert.equal(N('#quiet-leave').textContent,'Remove the limits');
  snap=nativeSnapshot();N('#refresh').click();await tick(80);
  const active=nativeSnapshot();
  active.quiet={...active.quiet,active:true,state:'quiet',original:{guid:'6fecc5ae-f350-48a5-b669-b472cb895ccf',name:'Turbo'},since:900,current:{guid:'64a64f24-65b9-4b56-befd-5ec1eaced9b3',name:'Silent'}};
  snap=active;N('#refresh').click();await tick(80);
  assert.equal(N('#quiet-scheme').disabled,true);assert.equal(N('#quiet-enter').disabled,true);assert.equal(N('#quiet-leave').disabled,false);
  assert.equal(N('#quiet-leave').textContent,'Return to Turbo');
  N('#quiet-leave').click();await tick(80);
  assert.equal(calls.quietLeave,true);

  N('[data-page="spikes"]').click();
  assert.equal(N('#rec-on-input').value,'40');
  N('#rec-enabled').click();await tick(80);
  assert.equal(JSON.stringify(calls.recorderConfigure),JSON.stringify([false,null,null]));
  N('#rec-on-input').value='55';N('#rec-off-input').value='30';N('#rec-save').click();await tick(80);
  assert.equal(JSON.stringify(calls.recorderConfigure),JSON.stringify([null,55,30]));
  N('#rec-clear').click();await tick(80);
  assert.equal(calls.recorderClear,true);
  const recorded=nativeSnapshot();
  recorded.recorder={enabled:true,on:40,off:25,count:1,
    current:{started:900,seconds:30,avg_cpu:62,peak_cpu:80,culprits:[{name:'ffmpeg.exe',via:null,processes:1,cpu_seconds:100,share:60,peak:40}]},
    spikes:[{started:100,seconds:50,avg_cpu:55,peak_cpu:70,plan:'Turbo',quiet_mode:false,on_ac:true,session:false,culprits:[{name:'node.exe',via:'Claude',processes:3,cpu_seconds:80,share:70,peak:30}]}],
    top:[{name:'node.exe',via:'Claude',cpu_seconds:80,spikes:1,share:100}]};
  snap=recorded;N('#refresh').click();await tick(80);
  assert.match(N('#rec-status').textContent,/Busy now: 62% CPU/);
  assert.equal(N('#rec-current').hidden,false);assert.match(N('#rec-current').textContent,/ffmpeg\.exe/);
  assert.match(N('#rec-list').textContent,/node\.exe via Claude 70%/);
  assert.match(N('#rec-list').textContent,/Power plan: Turbo · plugged in/);
  nat.window.close();

  const bare=create(true);await tick();
  assert.equal(bare.window.document.querySelector('#demo-banner').hidden,true);
  assert.equal(bare.window.document.querySelector('#cpu-value').textContent,'—');
  assert.equal(bare.window.document.querySelectorAll('#process-body input').length,0);
  assert.equal(bare.window.document.querySelectorAll('#group-list .group-card').length,0);
  bare.window.close();
  console.log('DOM behavior passed: demo labeling, light-app selection, heavy-app groups, one-click editing mode, benchmark, auto-start and timed A/B, spike log, quiet mode, macOS protection, native bridge contract, native no-demo fallback.');
})().catch(e=>{console.error(e);process.exit(1);});
