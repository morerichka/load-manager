const assert=require('node:assert/strict');
const path=require('node:path');
const fs=require('node:fs');
const {pathToFileURL}=require('node:url');
const {chromium}=require('playwright');

(async()=>{
  const browser=await chromium.launch({headless:true,channel:process.env.LM_UI_CHANNEL||undefined});
  const page=await browser.newPage({viewport:{width:1440,height:1050}});
  const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const entry=path.resolve(__dirname,'../loadmanager/ui/index.html');
  await page.goto(pathToFileURL(entry).href);
  await page.waitForFunction(()=>document.querySelector('#cpu-value').textContent!=='—');
  assert(await page.locator('#demo-banner').isVisible(),'Demo must be explicit');
  assert.equal(await page.locator('#connection').innerText(),'Demo mode');
  await page.locator('[data-page="profiles"]').click();
  await page.locator('[data-profile="focus"]').click();
  await page.locator('[data-page="apps"]').click();
  assert.equal(await page.locator('#process-body input').count(),3);
  assert.equal(await page.locator('#group-list .group-card').count(),4);
  assert.equal(await page.locator('#process-body tr').filter({hasText:'Claude'}).locator('input').count(),0,'Heavy-app helpers are chosen as a group, not one by one');
  assert.equal(await page.locator('#process-body tr').filter({hasText:'Premiere'}).locator('input').count(),0,'Work apps must not be selectable');
  await page.locator('#process-search').fill('spotify');
  assert.equal(await page.locator('#process-body tr').count(),1);
  await page.locator('#process-body input').check();
  await page.locator('#review-action').click();
  assert(await page.locator('#plan-dialog').isVisible());
  assert.match(await page.locator('#plan-description').innerText(),/Simulation.*Focus/);
  await page.locator('#confirm-plan').click();
  await page.waitForFunction(()=>document.querySelector('#main-action-label').textContent==='End session');
  await page.locator('#main-action').click();
  await page.waitForFunction(()=>document.querySelector('#main-action-label').textContent==='Set up session');
  await page.locator('[data-page="apps"]').click();
  await page.locator('#select-heavy').click();
  await page.locator('#review-action').click();
  assert.match(await page.locator('#plan-list').innerText(),/Google Chrome · 3 processes/);
  await page.locator('#cancel-plan').click();
  await page.locator('#demo-platform').click();
  await page.locator('[data-page="apps"]').click();
  assert.equal(await page.locator('#group-list input:not([disabled])').count(),0,'No heavy group on macOS');
  await page.locator('#process-body input').check();
  await page.locator('#review-action').click();
  assert.match(await page.locator('#plan-note').innerText(),/does not free memory/);
  await page.locator('#cancel-plan').click();
  await page.locator('[data-page="activity"]').click();
  assert.match(await page.locator('#event-list').innerText(),/restored/);
  await page.locator('[data-page="overview"]').click();
  const output=path.resolve(__dirname,'../test-results');fs.mkdirSync(output,{recursive:true});
  await page.screenshot({path:path.join(output,'desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Overview must fit a phone viewport');
  await page.screenshot({path:path.join(output,'mobile.png'),fullPage:true});
  for(const name of ['profiles','apps','render','video','spikes','power','activity','settings']){
    await page.locator(`[data-page="${name}"]`).click();
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),name+' must not overflow the viewport');
  }

  // Video page: every task fits every width, and a demo job runs from a click to the end.
  for(const width of [390,760,1120,1440]){
    await page.setViewportSize({width,height:900});
    await page.locator('[data-page="video"]').click();
    for(const tab of ['fetch','proxies','encode','publish','clean','run','doctor','kit']){
      await page.locator(`[data-vtab="${tab}"]`).click();
      assert(await page.locator('#v-tab-'+tab).isVisible(),tab+' tab must show');
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`video/${tab} must not overflow at ${width}px`);
    }
  }
  await page.setViewportSize({width:1440,height:1050});
  await page.locator('[data-vtab="doctor"]').click();
  await page.locator('#v-doctor-go').click();
  await page.waitForFunction(()=>document.querySelector('#v-job-badge').textContent==='Done',null,{timeout:15000});
  assert(await page.locator('#v-job').isVisible());
  assert(await page.locator('#v-doctor-rows .v-diag').count()>=6);
  await page.screenshot({path:path.join(output,'video.png'),fullPage:true});
  // the queue: a second job waits, the choice for the end is made, the countdown is shown and cancelled
  await page.locator('[data-vtab="proxies"]').click();
  await page.locator('#v-proxy-go').click();
  await page.waitForFunction(()=>document.querySelector('#v-job-title').textContent.startsWith('Make proxies')&&document.querySelector('#v-job-badge').textContent==='Running');
  await page.locator('[data-vtab="run"]').click();
  await page.locator('#v-run-cmd').fill('python render.py');
  await page.locator('#v-run-go').click();
  await page.waitForFunction(()=>document.querySelectorAll('#v-queue-list .v-qrow').length===1);
  await page.locator('#v-after').selectOption('sleep');
  await page.screenshot({path:path.join(output,'video-queue.png'),fullPage:true});
  for(const width of [390,760,1440]){
    await page.setViewportSize({width,height:900});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`the queue panel must fit ${width}px`);
  }
  await page.waitForFunction(()=>!document.querySelector('#v-countdown').hidden,null,{timeout:30000});
  assert.match(await page.locator('#v-countdown-text').innerText(),/go to sleep in \d+ s/);
  await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'the countdown must fit a phone');
  await page.screenshot({path:path.join(output,'video-countdown-mobile.png'),fullPage:true});
  await page.locator('#v-countdown-cancel').click();
  await page.waitForFunction(()=>document.querySelector('#v-countdown').hidden);
  await page.setViewportSize({width:1440,height:1050});
  await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'the finished job must fit a phone');
  await page.screenshot({path:path.join(output,'video-mobile.png'),fullPage:true});
  for(const nav of await page.locator('.sidebar nav .nav-item').all()){
    const box=await nav.boundingBox();
    assert(box.x>=0&&box.x+box.width<=390,'every menu item stays on a phone screen');
  }

  // Native shell: no bridge means no fallback to fake metrics or fake success.
  const nativePage=await browser.newPage();
  let html=fs.readFileSync(entry,'utf8').replace('data-native="false"','data-native="true"');
  html=html.replace('<link rel="stylesheet" href="styles.css">','<style>'+fs.readFileSync(path.resolve(__dirname,'../loadmanager/ui/styles.css'),'utf8')+'</style>');
  html=html.replace('<script src="app.js" defer></script>',()=>'<script>'+fs.readFileSync(path.resolve(__dirname,'../loadmanager/ui/app.js'),'utf8')+'</script>');
  await nativePage.setContent(html);
  assert.equal(await nativePage.locator('#cpu-value').innerText(),'—');
  assert.equal(await nativePage.locator('#demo-banner').isVisible(),false);
  assert.equal(await nativePage.locator('#process-body input').count(),0);
  assert.deepEqual(errors,[],'No page errors');
  await browser.close();
  console.log('UI checks passed: demo labeling, protected apps, filters, plan, apply/restore, macOS copy, responsive pages, the Video page at four widths, the queue and the countdown, native bridge absence.');
})().catch(e=>{console.error(e);process.exit(1);});
