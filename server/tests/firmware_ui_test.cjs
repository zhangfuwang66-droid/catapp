/* OTA 控制台浏览器回归：真实临时登录，固件状态接口模拟，不触碰设备或云端。 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {chromium} = require('playwright');

async function main() {
  const root = path.resolve(__dirname, '..');
  const output = path.resolve(root, '../artifacts/ota/web'); fs.mkdirSync(output, {recursive:true});
  const fixture = spawn(process.env.PYTHON_EXECUTABLE || 'python', ['-u', path.join(__dirname,'web_fixture.py')], {
    cwd:root, env:{...process.env,PYTHONPATH:root,PYTHONIOENCODING:'utf-8'}, stdio:['pipe','pipe','pipe'],
  });
  let browser, stderr = '';
  fixture.stderr.on('data',data => { stderr += data; });
  try {
    const info = await new Promise((resolve,reject) => {
      let buffer = ''; const timeout = setTimeout(() => reject(Error('测试网页启动超时')),25000);
      fixture.on('exit',code => { clearTimeout(timeout); reject(Error('测试进程退出 '+code+': '+stderr)); });
      fixture.stdout.on('data',data => {
        buffer += data;
        const line = buffer.split('\n').find(value => value.trim().startsWith('{'));
        if(line) { clearTimeout(timeout); resolve(JSON.parse(line)); }
      });
    });
    browser = await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE});
    const context = await browser.newContext({viewport:{width:1440,height:980},reducedMotion:'reduce'});
    const page = await context.newPage(); const errors=[];
    page.on('pageerror',error => errors.push(error.message));
    page.on('dialog',dialog => dialog.accept());
    await page.route(/https:\/\/(fonts\.|cdn\.jsdelivr)/,route => route.abort());
    await page.goto(info.accounts_url);
    await page.fill('#loginUsername','alice'); await page.fill('#loginPassword','alice-test-password');
    await page.click('#loginBtn'); await page.locator('#devicesView').waitFor({state:'visible'});
    let state = {supported:true,online:false,reported_at:Date.now()/1000-80,borrowed:false,current:{version:'0.3.0-ota.1',sequence:1,rollback_available:false},
      available:{id:'deer-0.3.0-ota.2',version:'0.3.0-ota.2',notes:'远程升级、完整语音与回退保护\n<img src=x onerror=alert(1)>'},
      auto_update:false,automatic_blocked:false,command:null};
    let mutations=0, delayNextRead=false, releaseDelayedRead, delayedReadStarted, rejectPolicy=false, failReads=false;
    await page.route('**/api/devices/cat-alice/firmware**',async route => {
      const request = route.request();
      if(request.method() === 'GET' && failReads) { await route.fulfill({status:503,json:{detail:'模拟状态查询失败'}}); return; }
      if(request.method() === 'GET' && delayNextRead) {
        delayNextRead=false; const snapshot=structuredClone(state);
        const gate=new Promise(resolve=>{releaseDelayedRead=resolve;}); delayedReadStarted();
        await gate; await route.fulfill({json:snapshot}); return;
      }
      if(request.method() !== 'GET') {
        mutations++; assert.ok(request.headers()['x-csrf-token']);
        const body=request.postDataJSON();
        if(request.url().endsWith('/policy') && rejectPolicy) { await route.fulfill({status:503,json:{detail:'模拟设置保存失败'}}); return; }
        if(request.url().endsWith('/policy')) state.auto_update=body.auto_update;
        else state.command={id:'a'.repeat(32),action:body.action,state:'queued',progress:0};
        await new Promise(resolve=>setTimeout(resolve,120));
        if(request.url().endsWith('/update')) { await route.fulfill({json:state.command}); return; }
      }
      await route.fulfill({json:state});
    });
    await page.goto(info.accounts_url+'/console/cat-alice#firmware');
    await page.waitForFunction(()=>document.getElementById('firmwareCurrent').textContent==='0.3.0-ota.1');
    assert.equal(await page.locator('#firmwareAuto').isChecked(),false);
    assert.equal(await page.locator('#firmwareInstall').isEnabled(),true);
    assert.equal(await page.locator('#firmwareRollback').isDisabled(),true);
    assert.match(await page.locator('#firmwareDetail').innerText(),/离线/);
    assert.match(await page.locator('#firmwareCurrentLabel').innerText(),/上次上报/);
    assert.match(await page.locator('#firmwareReported').innerText(),/上次上报/);
    assert.equal(await page.locator('#firmwareNotes img').count(),0);
    assert.equal(await page.locator('#firmwareFeedback').isHidden(),true);
    await page.screenshot({path:path.join(output,'update-desktop.png'),fullPage:true});
    // 延迟的旧 GET 不能覆盖刚保存的策略；不访问真实设备或付费接口。
    const oldRead=new Promise(resolve=>{delayedReadStarted=resolve;}); delayNextRead=true;
    await page.evaluate(()=>{void refreshFirmware();}); await oldRead;
    await page.check('#firmwareAuto');
    await page.waitForFunction(()=>firmwareState.auto_update===true);
    await page.waitForFunction(()=>!firmwareBusy);
    const releasedRead=page.waitForResponse(response=>response.url().endsWith('/firmware') && response.request().method()==='GET');
    releaseDelayedRead(); await releasedRead;
    assert.equal(await page.locator('#firmwareAuto').isChecked(),true);
    // 保存和补查同时失败时，复选框必须恢复上次已确认值。
    rejectPolicy=true; failReads=true;
    await page.uncheck('#firmwareAuto');
    await page.waitForFunction(()=>!firmwareBusy && firmwareReadFailed);
    assert.equal(await page.locator('#firmwareAuto').isChecked(),true);
    rejectPolicy=false; failReads=false;
    await page.click('#firmwareRefresh');
    await page.waitForFunction(()=>!firmwareReadFailed);
    await page.click('#firmwareInstall');
    await page.waitForFunction(()=>firmwareState.command?.state==='queued');
    assert.equal(mutations,3); assert.equal(await page.locator('#firmwareInstall').isDisabled(),true);
    assert.match(await page.locator('#firmwareHeadline').innerText(),/等待设备上线/);
    assert.equal(await page.locator('.firmware-orbit').evaluate(el=>el.classList.contains('is-updating')),false);
    state.online=true; state.command.state='downloading'; state.command.progress=54;
    await page.click('#firmwareRefresh');
    await page.waitForFunction(()=>document.getElementById('firmwareProgress').value===54);
    assert.match(await page.locator('#firmwarePhase').innerText(),/下载/);
    state.online=false;
    await page.click('#firmwareRefresh');
    await page.waitForFunction(()=>!firmwareState.online);
    assert.match(await page.locator('#firmwareHeadline').innerText(),/等待设备重新连接/);
    assert.match(await page.locator('#firmwarePhase').innerText(),/上次进度/);
    assert.equal(await page.locator('.firmware-orbit').evaluate(el=>el.classList.contains('is-updating')),false);
    state.online=true; state.command.state='pending_validation'; state.command.progress=100;
    await page.click('#firmwareRefresh');
    await page.waitForFunction(()=>firmwareState.command?.state==='pending_validation');
    assert.equal(await page.locator('#firmwarePercent').innerText(),'99%');
    state.command.state='downloading'; state.command.progress=54;
    await page.click('#firmwareRefresh');
    await page.waitForFunction(()=>document.getElementById('firmwareProgress').value===54);
    await page.screenshot({path:path.join(output,'update-progress.png'),fullPage:true});
    state.command.state='updated'; state.command.progress=100;
    state.current={version:'0.3.0-ota.2',sequence:2,rollback_available:true,previous_version:'0.3.0-ota.1'};
    state.available=null;
    await page.click('#firmwareRefresh');
    await page.waitForFunction(()=>document.getElementById('firmwareCurrent').textContent==='0.3.0-ota.2');
    assert.equal(await page.locator('#firmwareRollback').isEnabled(),true);
    await page.click('#firmwareRollback');
    await page.waitForFunction(()=>firmwareState.command?.action==='rollback');
    assert.doesNotMatch(await page.locator('#firmwareFeedback').innerText(),/新版已通过启动自检/);
    state.command.state='failed'; state.command.error_code='authorization_lost';
    await page.click('#firmwareRefresh');
    await page.waitForFunction(()=>document.getElementById('firmwareFeedback').textContent.includes('授权'));
    state.command={id:'b'.repeat(32),action:'install',state:'queued',progress:0};
    await page.click('#firmwareRefresh');
    await page.waitForFunction(()=>firmwareState.command?.id==='b'.repeat(32));
    assert.doesNotMatch(await page.locator('#firmwareFeedback').innerText(),/更新未完成/);
    state.borrowed=true;
    await page.click('#firmwareRefresh');
    await page.waitForFunction(()=>document.getElementById('firmwareDetail').textContent.includes('借用'));
    assert.equal(await page.locator('#firmwareRollback').isDisabled(),true);
    state.borrowed=false; state.online=false;
    await page.click('#firmwareRefresh');
    await page.waitForFunction(()=>!firmwareState.borrowed && !firmwareState.online);
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    await page.screenshot({path:path.join(output,'update-mobile.png'),fullPage:true});
    state.current=null; state.supported=false; state.reported_at=null; state.command=null;
    await page.click('#firmwareRefresh');
    await page.waitForFunction(()=>firmwareState.current===null);
    assert.equal(await page.locator('#firmwareCurrent').innerText(),'等待设备首次上报');
    assert.equal(await page.locator('#firmwareInstall').isDisabled(),true);
    await page.evaluate(()=>clearConsoleData());
    assert.equal(await page.locator('#firmwareCurrent').textContent(),'等待设备上报');
    assert.equal(await page.locator('#firmwareNotes').textContent(),'');
    assert.equal(await page.locator('#firmwareReported').textContent(),'');
    assert.equal(await page.locator('.firmware-orbit').evaluate(el=>el.classList.contains('is-updating')),false);
    assert.equal(await page.locator('#firmwareInstall').isDisabled(),true);
    assert.deepEqual(errors,[]);
    console.log('OTA 页面：离线与历史进度、策略竞态和失败恢复、版本、回退、错误、借用、移动端与会话清理通过。');
  } finally {
    await browser?.close(); fixture.stdin.end('\n');
  }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
