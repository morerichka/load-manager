// The Video page in jsdom: the demo (every task end to end) and the native bridge with answers shaped like the real
// backend's (tests/video_contract.json is checked against the real backend by tests/test_video_bridge.py).
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require('jsdom');
const nativeSnapshot=require('./snapshot.cjs');
const contract=JSON.parse(fs.readFileSync(path.join(__dirname,'video_contract.json'),'utf8'));
const ui=path.resolve(__dirname,'../loadmanager/ui');
let html=fs.readFileSync(path.join(ui,'index.html'),'utf8');
html=html.replace('<script src="app.js" defer></script>',()=>'<script>'+fs.readFileSync(path.join(ui,'app.js'),'utf8')+'</script>');
const tick=(ms=30)=>new Promise(resolve=>setTimeout(resolve,ms));
const clone=x=>JSON.parse(JSON.stringify(x));
function create(native=false,api=null){
  return new JSDOM(native?html.replace('data-native="false"','data-native="true"'):html,{
    runScripts:'dangerously',url:'https://loadmanager.test',pretendToBeVisual:true,
    beforeParse(window){
      window.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
      window.HTMLDialogElement.prototype.close=function(){this.open=false;};
      if(api)window.pywebview={api};
      const every=window.setInterval;                  // the demo's pretend jobs take a line every 450 ms: run them faster
      window.setInterval=(fn,ms,...rest)=>every.call(window,fn,ms===450?12:ms,...rest);
    }
  });
}
async function waitFor(check,limit=8000,what='condition'){
  const end=Date.now()+limit;
  while(Date.now()<end){if(check())return;await tick(40);}
  throw new Error('timed out waiting for '+what);
}
const set=(dom,el,value)=>{el.value=value;el.dispatchEvent(new dom.window.Event('input',{bubbles:true}));el.dispatchEvent(new dom.window.Event('change',{bubbles:true}));};

(async()=>{
  // ---------------------------------------------------------------- the demo, every task
  const dom=create();await tick();
  const doc=dom.window.document,$=s=>doc.querySelector(s),$$=s=>[...doc.querySelectorAll(s)];
  $('[data-page="video"]').click();await tick(200);
  assert.equal($('#page-video').classList.contains('active'),true);
  assert.equal($('#v-count').textContent,'2');
  assert.match($('#v-free').textContent,/GB/);
  assert.equal($('#v-ffmpeg').textContent,'Found');
  assert.equal($$('#v-projects .v-project').length,2);
  assert.deepEqual($$('#v-proxy-project option').map(o=>o.value),['Wedding-2026','Lesson-12']);
  assert.equal($('#v-job').hidden,true,'no job panel before the first job');
  assert.equal($('#video-dot').hidden,true);

  // tabs
  assert.equal($('#v-tab-fetch').hidden,false);
  for(const tab of ['proxies','encode','publish','clean','run','doctor','kit','fetch']){
    $(`[data-vtab="${tab}"]`).click();
    assert.deepEqual($$('.v-tab').filter(t=>!t.hidden).map(t=>t.id),['v-tab-'+tab]);
    assert.equal($(`[data-vtab="${tab}"]`).getAttribute('aria-selected'),'true');
  }

  // copy from the cloud: needs something chosen, picks, pastes, removes, starts, cancels
  assert.equal($('#v-fetch-go').disabled,true);assert.equal($('#v-fetch-plan').disabled,true);
  $('[data-pick="folder"][data-list="sources"]').click();await tick(100);
  assert.equal($$('#v-fetch-list .v-path').length,1);
  assert.match($('#v-fetch-list').textContent,/Wedding 2026/);assert.match($('#v-fetch-list').textContent,/cloud/);
  assert.equal($('#v-fetch-go').disabled,false);
  assert.equal($('#v-fetch-project').placeholder,'Wedding-2026');
  const paste=$('#v-fetch-path');paste.value='D:\\Local\\notes.txt';paste.dispatchEvent(new dom.window.KeyboardEvent('keydown',{key:'Enter',bubbles:true}));await tick(100);
  assert.equal($$('#v-fetch-list .v-path').length,2);assert.equal(paste.value,'');
  $('#v-fetch-list [data-remove="1"]').click();
  assert.equal($$('#v-fetch-list .v-path').length,1);
  $('#v-fetch-go').click();await tick(120);
  assert.equal($('#v-job').hidden,false);
  assert.equal($('#v-job-badge').textContent,'Running');
  assert.match($('#v-job-command').textContent,/^lm fetch /);
  assert.equal($('#video-dot').hidden,false,'the menu shows that a job runs');
  assert.equal($('#v-job-cancel').hidden,false);
  assert.equal($('#v-fetch-plan').disabled,true);assert.match($('#v-fetch-plan').title,/Another job is running/);
  assert.equal($('#v-fetch-go').disabled,false);assert.match($('#v-fetch-go').textContent,/Add to queue/);assert.match($('#v-fetch-go').title,/after what is already planned/);
  assert.equal($('#v-doctor-go').disabled,true);assert.equal($('#v-setup').disabled,true);
  $('#v-job-cancel').click();await waitFor(()=>$('#v-job-badge').textContent==='Cancelled',3000,'cancel');
  assert.equal($('#v-job-cancel').hidden,true);
  assert.match($('#v-log').textContent,/lm: interrupted/);assert.match($('#v-log').innerHTML,/class="err"/);
  assert.match($('#toast').textContent,/Cancelled: Copy Wedding 2026/);
  assert.equal($('#v-fetch-go').disabled,false);assert.match($('#v-fetch-go').textContent,/Copy/);assert.doesNotMatch($('#v-fetch-go').textContent,/queue/);assert.equal($('#video-dot').hidden,true);
  assert.match($('#v-history').textContent,/Cancelled/);

  // a whole short job, and what it leaves behind
  $('#v-setup').click();await waitFor(()=>$('#v-job-badge').textContent==='Done',4000,'setup');
  assert.match($('#v-job-summary').textContent,/Work folder ready/);
  assert.equal($$('#v-history .job-row').length,2);

  // encode: the output is suggested, a cloud input is flagged, a hand-typed output is kept
  $('[data-vtab="encode"]').click();
  assert.equal($('#v-enc-go').disabled,true);
  set(dom,$('#v-enc-in'),'G:\\My Drive\\Wedding\\A001.mov');await tick(100);
  assert.equal($('#v-enc-warn').hidden,false);assert.match($('#v-enc-warn').textContent,/cloud folder/);
  assert.match($('#v-enc-out').value,/A001-fast\.mp4$/);
  assert.equal($('#v-enc-go').disabled,false);
  set(dom,$('#v-enc-in'),'C:\\Users\\demo\\VideoWork\\wedding\\src\\A001.mov');await tick(100);
  assert.equal($('#v-enc-warn').hidden,true);
  $('#v-enc-preset').value='final';$('#v-enc-preset').dispatchEvent(new dom.window.Event('change'));await tick(100);
  assert.match($('#v-enc-out').value,/A001-final\.mp4$/);
  $('#v-enc-out').value='C:\\mine.mp4';$('#v-enc-out').dispatchEvent(new dom.window.Event('input'));
  $('#v-enc-preset').value='proxy';$('#v-enc-preset').dispatchEvent(new dom.window.Event('change'));await tick(100);
  assert.equal($('#v-enc-out').value,'C:\\mine.mp4','an output typed by hand is not replaced');
  $('#v-enc-plan').click();await waitFor(()=>/^Check the command/.test($('#v-job-title').textContent)&&$('#v-job-badge').textContent==='Done',4000,'encode dry run');
  assert.match($('#v-job-title').textContent,/^Check the command for A001\.mov \(proxy\)/);
  assert.equal($('#v-job-show').hidden,true,'a dry run made no file to show');
  $('#v-enc-go').click();await waitFor(()=>$('#v-job-badge').textContent==='Done'&&/^Encode/.test($('#v-job-title').textContent),4000,'encode');
  assert.match($('#v-job-summary').textContent,/^Done: C:\\mine\.mp4/);assert.equal($('#v-job-show').hidden,false);

  // copy back
  $('[data-vtab="publish"]').click();
  assert.equal($('#v-pub-go').disabled,true);
  $('#v-pub-project').value='Wedding-2026';$('#v-pub-add-out').click();await tick(100);
  assert.match($('#v-pub-list').textContent,/Wedding-2026\\out/);
  $('[data-pick="folder"][data-target="v-pub-dest"]').click();await tick(100);
  assert.match($('#v-pub-dest').value,/Wedding 2026/);assert.equal($('#v-pub-go').disabled,false);
  $('#v-pub-go').click();await waitFor(()=>$('#v-job-badge').textContent==='Done'&&/^Copy 1 item/.test($('#v-job-title').textContent),4000,'publish');
  assert.match($('#v-job-summary').textContent,/Published 1/);

  // free space: preview first, delete only for the same choices
  $('[data-vtab="clean"]').click();
  assert.equal($('#v-clean-go').hidden,true);
  $('#v-clean-preview').click();await waitFor(()=>/^Preview/.test($('#v-job-title').textContent)&&$('#v-job-badge').textContent==='Done',5000,'clean preview');
  assert.equal($('#v-clean-go').hidden,false);assert.equal($('#v-clean-go').textContent,'Delete 3 files (4.1 GB)');
  $('#v-clean-proxy').click();assert.equal($('#v-clean-go').hidden,true,'a changed choice needs a new preview');
  $('#v-clean-proxy').click();assert.equal($('#v-clean-go').hidden,false,'the same choice again is the same preview');
  $('#v-clean-go').click();await waitFor(()=>/^Delete files/.test($('#v-job-title').textContent)&&$('#v-job-badge').textContent==='Done',5000,'clean');
  assert.equal($('#v-clean-go').hidden,true,'nothing left to confirm');
  assert.match($('#v-projects .v-project').textContent,/0 B/,'the work folder was read again');
  $('#v-clean-src').click();$('#v-clean-proxy').checked=false;
  $('#v-clean-preview').click();await tick(150);
  assert.match($('#toast').textContent,/Choose what to clean/);assert.equal($('#toast').classList.contains('error'),true);
  $('#v-clean-src').click();

  // run, health check, Claude kit
  $('[data-vtab="run"]').click();
  assert.equal($('#v-run-go').disabled,true);
  set(dom,$('#v-run-cmd'),'python render.py');assert.equal($('#v-run-go').disabled,false);
  $('#v-run-name').value='nightly';
  $('#v-run-go').click();await waitFor(()=>$('#v-job-badge').textContent==='Done'&&/^Run nightly/.test($('#v-job-title').textContent),4000,'run');
  $('[data-vtab="doctor"]').click();assert.match($('#v-doctor-rows').textContent,/Press Run the check/);
  $('#v-doctor-go').click();await waitFor(()=>$('#v-job-title').textContent==='Health check'&&$('#v-job-badge').textContent==='Done',8000,'doctor');
  assert.ok($$('#v-doctor-rows .v-diag').length>=6);
  assert.equal($$('#v-doctor-rows .v-diag.warn .badge')[0].textContent,'Check');
  assert.match($('#v-doctor-rows').textContent,/h264_nvenc/);
  $('[data-vtab="kit"]').click();await tick(100);
  assert.match($('#v-kit-rows').textContent,/guard is not registered/);assert.equal($('#v-kit-remove').disabled,true);assert.equal($('#v-kit-install').textContent,'Install');
  $('#v-kit-install').click();await waitFor(()=>/Install the Claude kit/.test($('#v-job-title').textContent)&&$('#v-job-badge').textContent==='Done',6000,'kit install');
  await waitFor(()=>/guard is registered/.test($('#v-kit-rows').textContent),2000,'kit rows');
  assert.equal($('#v-kit-install').textContent,'Reinstall');assert.equal($('#v-kit-remove').disabled,false);
  $('#v-kit-remove').click();await waitFor(()=>/Remove the Claude kit/.test($('#v-job-title').textContent)&&$('#v-job-badge').textContent==='Done',6000,'kit remove');
  await waitFor(()=>/guard is not registered/.test($('#v-kit-rows').textContent),2000,'kit rows again');

  // the queue: a button pressed during a job waits; cancelling pauses; the action for when everything is done
  assert.equal($('#v-queue').hidden,true,'no queue panel while nothing is going on');
  dom.window.__lmDemoTick=300;                                                  // slow enough to press buttons while a job runs
  $('[data-vtab="proxies"]').click();
  $('#v-proxy-go').click();await tick(60);
  assert.equal($('#v-job-badge').textContent,'Running');
  assert.equal($('#v-queue').hidden,false,'the panel appears with a running job, for "when everything is done"');
  assert.match($('#v-proxy-go').textContent,/Add to queue/);
  $('#v-proxy-go').click();await tick(60);
  assert.match($('#toast').textContent,/Added to the queue \(number 1\)/);
  $('[data-vtab="run"]').click();set(dom,$('#v-run-cmd'),'python a.py');$('#v-run-go').click();await tick(60);
  assert.equal($$('#v-queue-list .v-qrow').length,2);assert.match($('#v-queue-note').textContent,/2 waiting/);
  assert.equal($('#v-queue-list [data-qmove="-1"]').disabled,true);
  $('#v-queue-list [data-qmove="1"]').click();await tick(60);
  assert.match($$('#v-queue-list .v-qrow')[0].textContent,/Run nightly/,'the first item moved down');
  $('#v-queue-list [data-qremove]').click();await tick(60);
  assert.equal($$('#v-queue-list .v-qrow').length,1);assert.match($('#v-queue-list').textContent,/Make proxies/);
  $('#v-job-cancel').click();await waitFor(()=>$('#v-job-badge').textContent==='Cancelled',9000,'cancel');
  await waitFor(()=>/Paused because you cancelled/.test($('#v-queue-note').textContent),9000,'pause note');
  assert.equal($('#v-queue-pause').textContent,'Resume');assert.equal($$('#v-queue-list .v-qrow').length,1,'what was left is kept');
  assert.equal($('#video-dot').hidden,true,'a paused queue is not activity');
  assert.deepEqual($$('#v-after option').map(o=>o.value),['nothing','sleep','hibernate','shutdown']);
  $('#v-after').value='sleep';$('#v-after').dispatchEvent(new dom.window.Event('change'));await tick(60);
  assert.equal($('#v-after').value,'sleep');
  $('#v-keep-going').click();await tick(60);assert.equal($('#v-keep-going').checked,true);
  $('#v-queue-pause').click();
  await waitFor(()=>!$('#v-countdown').hidden,9000,'the countdown after the last job');
  assert.match($('#v-countdown-text').textContent,/The computer will go to sleep in \d+ s unless you cancel/);
  assert.match($('#toast').textContent,/All done\. The computer will go to sleep/);assert.equal($('#toast').classList.contains('error'),true);
  assert.match($('#v-job-title').textContent,/^Make proxies/);assert.equal($$('#v-queue-list .v-qrow').length,0);
  $('#v-countdown-cancel').click();await tick(120);
  assert.equal($('#v-countdown').hidden,true);assert.equal($('#v-after').value,'nothing');
  assert.equal($('#v-queue').hidden,true,'nothing runs, nothing waits, nothing is armed: the panel goes away');
  $('#v-queue-pause').click;                                                  // (hidden now; pausing is only offered with the panel)
  $('[data-vtab="proxies"]').click();$('#v-proxy-go').click();await tick(60);$('#v-after').value='shutdown';$('#v-after').dispatchEvent(new dom.window.Event('change'));await tick(60);
  assert.equal($('#v-after').value,'shutdown');
  $('#v-after').value='nothing';$('#v-after').dispatchEvent(new dom.window.Event('change'));
  await waitFor(()=>$('#v-job-badge').textContent==='Done',9000,'last job');
  delete dom.window.__lmDemoTick;

  // settings, project shortcuts, the Mac-only options
  $('#v-set-hw').value='none';$('#v-set-free').value='20';$('#v-set-save').click();await tick(150);
  assert.match($('#toast').textContent,/Settings saved/);
  $('[data-vtab="fetch"]').click();
  $('#v-projects [data-act="proxies"]').click();
  assert.equal($('#v-tab-proxies').hidden,false);assert.equal($('#v-proxy-project').value,'Wedding-2026');
  $('#v-projects [data-act="clean"][data-name="Lesson-12"]').click();
  assert.equal($('#v-tab-clean').hidden,false);assert.equal($('#v-clean-project').value,'Lesson-12');
  assert.equal($$('[data-mac]').every(el=>el.hidden),true,'Mac options stay hidden on Windows');
  $('#demo-platform').click();await tick(150);
  assert.equal($$('[data-mac]').every(el=>!el.hidden),true);
  $('#v-proxy-go').click();await tick(80);
  assert.deepEqual($$('#v-after option').map(o=>o.value),['nothing','sleep','shutdown'],'a Mac cannot be told to hibernate');
  dom.window.close();

  // ---------------------------------------------------------------- the native bridge
  const calls=[];
  let snap=nativeSnapshot(),vstate=clone(contract.state_idle),overview=clone(contract.overview),kit=clone(contract.kit),failState=null;
  const log=(name,result)=>async(...args)=>{calls.push([name,...clone(args)]);return typeof result==='function'?result(...args):clone(result);};
  const picks={videos:['C:\\Cloud\\a.mov','C:\\Cloud\\b.mov'],folder:['C:\\Cloud\\Wedding'],save:['C:\\out\\o.mp4'],files:['C:\\r\\final.mp4'],program:['C:\\ff\\ffmpeg.exe']};
  const api={
    snapshot:async()=>snap,
    video_state:async known=>{calls.push(['video_state',known]);return failState||clone(vstate);},
    video_overview:log('video_overview',()=>({...clone(overview),ok:true})),
    video_kit:log('video_kit',()=>({...clone(kit),ok:true})),
    video_start:log('video_start',{ok:true,queued:false,id:7}),
    video_queue_pause:log('video_queue_pause',{ok:true}),video_queue_clear:log('video_queue_clear',{ok:true}),video_queue_remove:log('video_queue_remove',{ok:true}),
    video_queue_move:log('video_queue_move',{ok:true}),video_queue_configure:log('video_queue_configure',{ok:true}),video_cancel_countdown:log('video_cancel_countdown',{ok:true}),
    video_cancel:log('video_cancel',{ok:true,cancelled:true}),
    video_configure:log('video_configure',()=>({ok:true,settings:clone(contract.state_idle.settings)})),
    video_inspect:async p=>{calls.push(['video_inspect',p]);return {...clone(contract.inspect),path:p,name:p.split('\\').pop(),project:'Wedding'};},
    video_suggest:log('video_suggest',{ok:true,output:'C:\\VW\\demo\\out\\clip-fast.mp4'}),
    video_reveal:log('video_reveal',{ok:true,shown:'x'}),
    pick:async(kind,start)=>{calls.push(['pick',kind,start]);return {ok:true,paths:picks[kind]||[]};}
  };
  const nat=create(true,api);await tick(100);
  const n=nat.window.document,N=s=>n.querySelector(s),NN=s=>[...n.querySelectorAll(s)];
  const started=()=>calls.filter(c=>c[0]==='video_start').map(c=>[c[1],c[2]]);
  const last=()=>started()[started().length-1];
  assert.equal(calls.filter(c=>c[0]==='video_state').length,0,'nothing is asked of the video backend until the page is opened');
  N('[data-page="video"]').click();await tick(150);
  assert.ok(calls.some(c=>c[0]==='video_state')&&calls.some(c=>c[0]==='video_overview')&&calls.some(c=>c[0]==='video_kit'));
  assert.equal(NN('#v-projects .v-project').length,contract.overview.projects.length);
  assert.equal(N('#v-set-hw').value,contract.state_idle.settings.hw);assert.equal(N('#v-set-free').value,String(contract.state_idle.settings.min_free_gb));

  // every form sends exactly what the backend expects
  N('[data-pick="folder"][data-list="sources"]').click();await tick(80);
  N('[data-pick="videos"][data-list="sources"]').click();await tick(80);
  assert.deepEqual(NN('#v-fetch-list .v-path span:first-child').map(e=>e.textContent),['C:\\Cloud\\Wedding','C:\\Cloud\\a.mov','C:\\Cloud\\b.mov']);
  N('#v-fetch-project').value='wed';N('#v-fetch-verify').click();N('#v-fetch-plan').click();await tick(80);
  assert.deepEqual(last(),['fetch',{sources:['C:\\Cloud\\Wedding','C:\\Cloud\\a.mov','C:\\Cloud\\b.mov'],project:'wed',verify:true,dry_run:true}]);
  N('#v-fetch-go').click();await tick(80);
  assert.equal(last()[1].dry_run,false);

  N('[data-vtab="proxies"]').click();
  N('#v-proxy-project').value='demo';N('#v-proxy-preset').value='fast';N('#v-proxy-low').click();N('#v-proxy-force').click();
  N('#v-proxy-go').click();await tick(80);
  assert.deepEqual(last(),['proxies',{project:'demo',preset:'fast',low:true,sw:false,force:true,no_hwdecode:false}]);

  N('[data-vtab="encode"]').click();
  N('[data-pick="videos"][data-target="v-enc-in"]').click();await tick(120);
  assert.equal(N('#v-enc-in').value,'C:\\Cloud\\a.mov','a single-file field takes the first path');
  assert.ok(calls.some(c=>c[0]==='video_suggest'&&c[1]==='C:\\Cloud\\a.mov'&&c[2]==='fast'));
  assert.equal(N('#v-enc-out').value,'C:\\VW\\demo\\out\\clip-fast.mp4');
  N('[data-pick="save"][data-target="v-enc-out"]').click();await tick(80);
  assert.equal(N('#v-enc-out').value,'C:\\out\\o.mp4');
  assert.deepEqual(calls.filter(c=>c[0]==='pick').pop(),['pick','save','C:\\VW\\demo\\out\\clip-fast.mp4'],'the dialog starts where the field points');
  N('#v-enc-codec').value='hevc';N('#v-enc-bitrate').value='12M';N('#v-enc-extra').value='-t 30';N('#v-enc-sw').click();N('#v-enc-nohw').click();
  N('#v-enc-go').click();await tick(80);
  assert.deepEqual(last(),['encode',{input:'C:\\Cloud\\a.mov',output:'C:\\out\\o.mp4',preset:'fast',codec:'hevc',bitrate:'12M',extra:'-t 30',low:false,sw:true,force:false,no_hwdecode:true,dry_run:false}]);
  N('#v-enc-plan').click();await tick(80);assert.equal(last()[1].dry_run,true);

  N('[data-vtab="publish"]').click();
  N('[data-pick="files"][data-list="items"]').click();await tick(80);
  N('#v-pub-dest').value=' C:\\Cloud\\Final ';N('#v-pub-dest').dispatchEvent(new nat.window.Event('input'));N('#v-pub-force').click();
  N('#v-pub-go').click();await tick(80);
  assert.deepEqual(last(),['publish',{items:['C:\\r\\final.mp4'],dest:'C:\\Cloud\\Final',force:true}]);

  N('[data-vtab="clean"]').click();
  N('#v-clean-project').value='demo';N('#v-clean-preview').click();await tick(80);
  assert.deepEqual(last(),['clean',{project:'demo',src:true,proxy:false,yes:false}]);

  N('[data-vtab="run"]').click();
  set(nat,N('#v-run-cmd'),'python x.py');N('#v-run-name').value='n';N('#v-run-cwd').value='C:\\w';N('#v-run-notify').click();
  N('#v-run-go').click();await tick(80);
  assert.deepEqual(last(),['run',{command:'python x.py',name:'n',cwd:'C:\\w',low:false,no_notify:true}]);
  N('[data-vtab="doctor"]').click();N('#v-doctor-go').click();await tick(80);assert.deepEqual(last(),['doctor',{}]);
  N('[data-vtab="kit"]').click();N('#v-kit-install').click();await tick(80);assert.deepEqual(last(),['install_claude',{}]);
  N('#v-kit-remove').disabled=false;N('#v-kit-remove').click();await tick(80);assert.deepEqual(last(),['uninstall_claude',{}]);
  N('#v-setup').click();await tick(80);assert.deepEqual(last(),['setup',{}]);

  // a running job, as the backend reports it
  const running=clone(contract.state_job);
  Object.assign(running,{running:true});
  Object.assign(running.job,{id:11,kind:'proxies',status:'running',code:null,ended:null,cancelling:false,progress:0.4,speed:'2.0x',eta:125,index:2,count:3,item:'clip 2.mov',summary:null,output:null,
    lines:['[2/3] clip 2.mov','lm: warning: odd frame','lm: something broke','plain line'],lines_total:4,started:snap.timestamp-65});
  vstate=running;snap={...nativeSnapshot(),video:{running:true,title:'Make proxies',status:'running',progress:0.4,close_asked:0}};
  N('#v-refresh').click();await tick(150);
  assert.equal(N('#v-job').hidden,false);assert.equal(N('#v-job-title').textContent,running.job.title);
  assert.equal(N('#v-job-badge').textContent,'Running');
  assert.equal(N('#v-job-detail').textContent,'Item 2 of 3 · clip 2.mov · 40% · 2.0x speed · about 2:05 left');
  assert.equal(N('#v-job-bar').style.width,'40%');assert.equal(N('#v-job-meter').hidden,false);
  assert.equal(N('#v-job-cancel').hidden,false);
  assert.equal(N('#v-doctor-go').disabled,true);assert.match(N('#v-proxy-go').textContent,/Add to queue/);assert.equal(N('#v-proxy-go').disabled,false);assert.equal(N('#video-dot').hidden,false);
  assert.equal(N('#v-queue').hidden,false,'a running job shows the queue panel, for the choice of what to do afterwards');
  assert.equal(NN('#v-log .warn').length,1);assert.equal(NN('#v-log .err').length,1);assert.match(N('#v-log').textContent,/plain line/);
  running.job.lines=null;                                                    // nothing new: the page keeps what it has
  N('#v-refresh').click();await tick(120);
  assert.match(N('#v-log').textContent,/plain line/);
  assert.ok(calls.some(c=>c[0]==='video_state'&&JSON.stringify(c[1])==='[11,4]'),'the page says which lines it already has');
  N('#v-job-cancel').click();await tick(80);assert.ok(calls.some(c=>c[0]==='video_cancel'));
  running.job.cancelling=true;N('#v-refresh').click();await tick(120);
  assert.equal(N('#v-job-badge').textContent,'Cancelling…');assert.equal(N('#v-job-cancel').disabled,true);

  // the queue as the backend reports it
  running.job.cancelling=false;
  vstate=clone(running);vstate.queue=clone(contract.state_queue.queue);vstate.queue.items.push({id:2,kind:'proxies',title:'Make proxies for demo',command:'lm proxies demo'});
  snap={...nativeSnapshot(),video:{running:true,title:'x',status:'running',progress:0.4,close_asked:0,queued:2,paused:false,after:'nothing'}};
  N('#v-refresh').click();await tick(150);
  assert.equal(N('#v-queue').hidden,false);assert.equal(NN('#v-queue-list .v-qrow').length,2);
  assert.match(N('#v-queue-note').textContent,/2 waiting\. Each one starts when the one before it has finished/);
  assert.equal(NN('#v-queue-list [data-qmove="-1"]')[0].disabled,true,'the first item cannot go up');
  assert.equal(NN('#v-queue-list [data-qmove="1"]')[1].disabled,true,'the last item cannot go down');
  assert.match(N('#v-queue-list').textContent,/Encode a\.mov \(fast\)/);
  N('#v-queue-list [data-qmove="1"]').click();await tick(60);
  assert.deepEqual(calls.filter(c=>c[0]==='video_queue_move').pop(),['video_queue_move',vstate.queue.items[0].id,1]);
  NN('#v-queue-list [data-qremove]')[1].click();await tick(60);
  assert.deepEqual(calls.filter(c=>c[0]==='video_queue_remove').pop(),['video_queue_remove',2]);
  N('#v-queue-clear').click();await tick(60);assert.ok(calls.some(c=>c[0]==='video_queue_clear'));
  assert.equal(N('#v-queue-pause').textContent,'Pause queue');
  N('#v-queue-pause').click();await tick(60);assert.deepEqual(calls.filter(c=>c[0]==='video_queue_pause').pop(),['video_queue_pause',true]);
  vstate.queue.paused=true;vstate.queue.reason='failed';N('#v-refresh').click();await tick(150);
  assert.match(N('#v-queue-note').textContent,/Paused because a job failed/);assert.equal(N('#v-queue-pause').textContent,'Resume');
  N('#v-queue-pause').click();await tick(60);assert.deepEqual(calls.filter(c=>c[0]==='video_queue_pause').pop(),['video_queue_pause',false]);
  assert.deepEqual(NN('#v-after option').map(o=>o.value),['nothing','sleep','hibernate','shutdown']);
  N('#v-after').value='sleep';N('#v-after').dispatchEvent(new nat.window.Event('change'));await tick(60);
  assert.deepEqual(calls.filter(c=>c[0]==='video_queue_configure').pop(),['video_queue_configure',null,'sleep']);
  N('#v-keep-going').click();await tick(60);
  assert.deepEqual(calls.filter(c=>c[0]==='video_queue_configure').pop(),['video_queue_configure',true,null]);
  api.video_start=log('video_start',{ok:true,queued:true,id:3,position:3});
  N('[data-vtab="run"]').click();set(nat,N('#v-run-cmd'),'python later.py');N('#v-run-go').click();await tick(80);
  assert.match(N('#toast').textContent,/Added to the queue \(number 3\)/);
  api.video_start=log('video_start',{ok:true,queued:false,id:7});

  // a job that was replaced between two looks is still announced, from the history
  vstate=clone(running);vstate.job.id=21;vstate.job.title='Encode first';vstate.queue=clone(contract.state_idle.queue);
  N('#v-refresh').click();await tick(150);
  const next=clone(running);next.job.id=22;next.job.title='Encode second';next.history=[{id:21,kind:'encode',title:'Encode first',command:'lm',status:'failed',code:1,started:1,ended:2,summary:'ffmpeg exited with 1'}];next.queue=clone(contract.state_idle.queue);
  vstate=next;N('#v-refresh').click();await tick(200);
  assert.match(N('#toast').textContent,/^Failed: Encode first/);assert.equal(N('#v-job-title').textContent,'Encode second');

  // it ends: a message, the work folder is read again, the menu dot goes
  const overviews=calls.filter(c=>c[0]==='video_overview').length;
  vstate=clone(contract.state_job);vstate.job.id=22;vstate.job.status='failed';vstate.job.code=1;vstate.job.summary='ffmpeg exited with 1';vstate.job.lines=['x'];vstate.job.lines_total=1;
  snap={...nativeSnapshot(),video:{running:false,title:'x',status:'failed',progress:null,close_asked:0}};
  N('#v-refresh').click();await tick(200);
  assert.match(N('#toast').textContent,/^Failed: /);assert.equal(N('#toast').classList.contains('error'),true);
  assert.ok(calls.filter(c=>c[0]==='video_overview').length>overviews);
  assert.equal(N('#v-job-badge').textContent,'Failed');assert.equal(N('#v-job-summary').textContent,'ffmpeg exited with 1');assert.equal(N('#v-job-summary').classList.contains('error-text'),true);
  assert.equal(N('#video-dot').hidden,true);assert.equal(N('#v-proxy-go').disabled,false);assert.match(N('#v-proxy-go').textContent,/Make proxies/);

  // a finished render can be shown, projects can be opened, the work folder too
  const done=clone(contract.state_job);done.job.status='done';done.job.output='C:\\out\\o.mp4';vstate=done;
  N('#v-refresh').click();await tick(150);
  assert.equal(N('#v-job-show').hidden,false);N('#v-job-show').click();await tick(60);
  assert.deepEqual(calls.filter(c=>c[0]==='video_reveal').pop(),['video_reveal','path','','C:\\out\\o.mp4']);
  N('#v-projects [data-act="open"]').click();await tick(60);
  assert.deepEqual(calls.filter(c=>c[0]==='video_reveal').pop(),['video_reveal','project',contract.overview.projects[0].name]);
  N('#v-open-work').click();await tick(60);
  assert.deepEqual(calls.filter(c=>c[0]==='video_reveal').pop(),['video_reveal','work']);

  // the countdown before the computer switches off, and cancelling it
  vstate=clone(contract.state_countdown);
  snap={...nativeSnapshot(),video:{running:false,title:'x',status:'done',progress:1,close_asked:0,queued:0,paused:false,after:'sleep'}};
  N('#v-refresh').click();await tick(200);
  assert.equal(N('#v-countdown').hidden,false);
  assert.match(N('#v-countdown-text').textContent,/The computer will go to sleep in \d+ s unless you cancel\./);
  assert.match(N('#toast').textContent,/All done\. The computer will go to sleep in \d+ seconds/);
  assert.equal(N('#v-queue').hidden,false);assert.equal(N('#v-after').value,'sleep');
  N('#v-countdown-cancel').click();await tick(60);assert.ok(calls.some(c=>c[0]==='video_cancel_countdown'));
  vstate=clone(contract.state_countdown);vstate.queue.countdown=null;vstate.queue.after='nothing';
  snap={...nativeSnapshot()};N('#v-refresh').click();await tick(150);
  assert.equal(N('#v-countdown').hidden,true);assert.equal(N('#v-queue').hidden,true);
  vstate.queue.error='Could not hibernate: Hibernation has not been enabled.';vstate.queue.after='hibernate';N('#v-refresh').click();await tick(150);
  assert.equal(N('#v-after-error').hidden,false);assert.match(N('#v-after-error').textContent,/Hibernation has not been enabled/);
  vstate=clone(contract.state_idle);

  // settings
  N('#v-set-work').value='D:\\VW';N('#v-set-hw').value='none';N('#v-set-free').value='9';N('#v-set-ffmpeg').value='C:\\ff\\ffmpeg.exe';N('#v-set-save').click();await tick(100);
  assert.deepEqual(calls.filter(c=>c[0]==='video_configure').pop(),['video_configure',{work:'D:\\VW',hw:'none',min_free_gb:9,ffmpeg:'C:\\ff\\ffmpeg.exe'}]);
  N('[data-pick="program"][data-target="v-set-ffmpeg"]').click();await tick(60);
  assert.equal(N('#v-set-ffmpeg').value,'C:\\ff\\ffmpeg.exe');

  // polling must not overwrite what is being typed
  set(nat,N('#v-run-cmd'),'half typed');N('#v-fetch-project').value='mine';
  N('#v-refresh').click();await tick(150);
  assert.equal(N('#v-run-cmd').value,'half typed');assert.equal(N('#v-fetch-project').value,'mine');

  // the window asked to close while a job runs
  snap={...nativeSnapshot(),video:{running:true,title:'x',status:'running',progress:0.1,close_asked:snap.timestamp?Date.now()/1000-2:0}};
  vstate=clone(running);N('#v-refresh').click();await tick(200);
  assert.match(N('#toast').textContent,/Close the window again within 15 seconds/);

  // errors from the backend are shown, not swallowed
  failState={ok:false,error:'boom'};N('#v-refresh').click();await tick(150);
  assert.equal(N('#v-offline').hidden,false);assert.equal(N('#v-offline').textContent,'boom');
  failState=null;vstate=clone(contract.state_idle);N('#v-refresh').click();await tick(150);
  assert.equal(N('#v-offline').hidden,true);
  api.video_start=async(kind,params)=>({ok:false,error:'Another video job is still running. Wait for it or cancel it first.'});
  N('[data-vtab="run"]').click();N('#v-run-go').click();await tick(100);
  assert.match(N('#toast').textContent,/Another video job is still running/);
  nat.window.close();

  // without the bridge (the page opened before pywebview is ready) nothing breaks
  const bare=create(true);await tick(100);
  bare.window.document.querySelector('[data-page="video"]').click();await tick(100);
  assert.equal(bare.window.document.querySelector('#page-video').classList.contains('active'),true);
  bare.window.close();
  console.log('Video page passed: every task in the demo, tabs, cloud warning, suggested output, cancel, preview-then-delete, kit, the queue and the countdown, native bridge contract, progress and log, finish handling, close warning, errors.');
})().catch(e=>{console.error(e);process.exit(1);});
