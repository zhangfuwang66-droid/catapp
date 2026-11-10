/* 运行：设置 PYTHON_EXECUTABLE、NODE_PATH（含 playwright），再 node server/tests/web_ui_test.cjs。 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const vm = require('node:vm');
const {chromium} = require('playwright');

async function main() {
  const root = path.resolve(__dirname, '..');
  const output = path.resolve(root, '../artifacts/web-tests');
  fs.mkdirSync(output, {recursive:true});
  const fixture = spawn(process.env.PYTHON_EXECUTABLE || 'python', ['-u', path.join(__dirname, 'web_fixture.py')], {
    cwd:root, env:{...process.env, PYTHONPATH:root, PYTHONIOENCODING:'utf-8'}, stdio:['pipe','pipe','pipe'],
  });
  let stderr = '';
  fixture.stderr.on('data', data => { stderr += data.toString(); });
  let browser;
  try {
    const info = await new Promise((resolve, reject) => {
      let buffer = '';
      const timeout = setTimeout(() => reject(new Error('网页测试服务未就绪')), 25000);
      fixture.on('exit', code => { clearTimeout(timeout); reject(new Error('测试服务退出 ' + code + ': ' + stderr)); });
      fixture.on('error', reject);
      fixture.stdout.on('data', data => {
        buffer += data.toString();
        let next;
        while((next = buffer.indexOf('\n')) >= 0) {
          const line = buffer.slice(0,next).trim(); buffer = buffer.slice(next+1);
          if(line.startsWith('{')) { clearTimeout(timeout); resolve(JSON.parse(line)); }
        }
      });
    });
    const executablePath = process.env.BROWSER_EXECUTABLE;
    browser = await chromium.launch({headless:true, ...(executablePath ? {executablePath} : {})});
    const context = await browser.newContext({viewport:{width:1440,height:980}});
    await context.route(/https:\/\/(fonts\.|cdn\.jsdelivr)/, route => route.abort());
    const page = await context.newPage();
    const errors = [];
    const observe = page => page.on('pageerror', error => errors.push(error.message));
    observe(page);
    async function login(page, name) {
      await page.locator('#loginView').waitFor({state:'visible'});
      await page.fill('#loginUsername', name);
      await page.fill('#loginPassword', name + '-test-password');
      await page.click('#loginBtn');
      await page.locator('#devicesView').waitFor({state:'visible'});
      await page.waitForFunction(() => document.querySelectorAll('.device-card').length > 0);
    }
    await page.goto(info.accounts_url);
    await page.locator('#loginView').waitFor({state:'visible'});
    await page.screenshot({path:path.join(output,'login.png'),fullPage:true});
    await page.fill('#loginUsername','alice'); await page.fill('#loginPassword','wrong-password');
    await page.click('#loginBtn');
    await page.waitForFunction(() => document.getElementById('loginError').textContent.includes('账号或密码错误'));
    await login(page,'alice');
    assert.match(await page.locator('#deviceGrid').innerText(), /Alice的小喵/);
    assert.doesNotMatch(await page.locator('#deviceGrid').innerText(), /Bob/);
    await page.click('#addDeviceBtn'); await page.fill('#pairCode', 'AAAA-BBBB'); await page.click('#pairBtn');
    await page.waitForFunction(() => !!document.getElementById('pairError').textContent);
    await page.fill('#pairCode',info.pair_code); await page.click('#pairBtn');
    await page.waitForFunction(() => document.querySelectorAll('.device-card').length === 2);
    await page.screenshot({path:path.join(output,'devices.png'),fullPage:true});
    const aliceCard = page.locator('.device-card').filter({hasText:'cat-alice'});
    await aliceCard.getByRole('button',{name:'查看记忆'}).click();
    await page.waitForFunction(() => document.getElementById('memoryContent').textContent.includes('Alice的专属记忆'));
    assert.doesNotMatch(await page.locator('#memoryContent').innerText(), /Bob/);
    await page.click('[data-close="memoryDialog"]');
    const consolePage = await context.newPage(); observe(consolePage);
    const sockets = [];
    consolePage.on('websocket', socket => sockets.push(socket.url()));
    await consolePage.goto(info.accounts_url + '/console/cat-alice');
    await consolePage.waitForFunction(() => document.getElementById('uiStatus').textContent.includes('控制已连'));
    await consolePage.waitForFunction(() => document.getElementById('chatBox').textContent.includes('Alice的私有对话'));
    assert.equal(await consolePage.locator('#dialog').isVisible(),true);
    assert.equal(await consolePage.locator('#vision').isVisible(),false);
    assert.equal(await consolePage.locator('#repeatTools').getAttribute('open'),null);
    await consolePage.click('.nav-links a[href="#vision"]');
    await consolePage.click('#openCameraBtn');
    await consolePage.waitForFunction(() => document.getElementById('videoCanvas').getContext('2d').getImageData(0,0,1,1).data[0] > 150);
    assert.equal(await consolePage.locator('#cameraLive').isVisible(),true);
    await consolePage.click('#closeCameraBtn');
    assert.match(await consolePage.locator('#cameraEmpty').innerText(),/预览未打开/);
    // 模式请求失败时不能提前宣称成功，也不能锁住语音控制。
    await consolePage.route('**/api/devices/cat-alice/performance_mode?*', route => route.fulfill({
      status:503, json:{detail:'模拟模式切换失败'},
    }));
    await consolePage.click('#perfBtn');
    await consolePage.waitForFunction(() => document.getElementById('performanceFeedback').dataset.kind === 'error');
    assert.equal(await consolePage.locator('#perfBanner').isVisible(),false);
    await consolePage.unroute('**/api/devices/cat-alice/performance_mode?*');
    await consolePage.click('.nav-links a[href="#hardware"]');
    assert.equal(await consolePage.locator('#rawServoTools').getAttribute('open'),null);
    await consolePage.click('.nav-links a[href="#dialog"]');
    assert.equal(await consolePage.locator('#hardware').isVisible(),false);
    const dialogWidth = await consolePage.locator('#dialog').evaluate(element => element.getBoundingClientRect().width);
    assert(dialogWidth > 1100,'对话工作区应使用整页宽度');
    await consolePage.evaluate(() => { location.hash = 'constructor'; });
    await consolePage.waitForFunction(() => !document.getElementById('dialog').hidden);
    await consolePage.click('.nav-links a[href="#dialog"]');
    await consolePage.locator('button[onclick="sendPrompt()"]').click();
    assert.match(await consolePage.locator('#messageFeedback').innerText(),/先输入/);
    // 慢请求有即时反馈，连续调用也只发送一个 POST。
    let releasePause;
    const pauseGate = new Promise(resolve => { releasePause = resolve; });
    let pausePosts = 0;
    await consolePage.route('**/api/devices/cat-alice/voice', async route => {
      if(route.request().method() === 'POST') { pausePosts++; await pauseGate; }
      await route.continue();
    });
    await consolePage.click('#pauseVoiceBtn');
    await consolePage.waitForFunction(() => document.getElementById('pauseVoiceText').textContent.includes('正在暂停'));
    assert.equal(await consolePage.locator('#pauseVoiceBtn').isDisabled(),true);
    assert.equal(await consolePage.locator('#resumeVoiceBtn').isDisabled(),true);
    assert.match(await consolePage.locator('#voiceFeedback').innerText(),/正在请求/);
    await consolePage.evaluate(() => { setVoicePaused(true); setVoicePaused(true); });
    releasePause();
    await consolePage.waitForFunction(() => document.getElementById('voiceState').textContent.includes('已设置暂停'));
    assert.equal(pausePosts,1);
    await consolePage.fill('#promptText','暂停时不能继续发送');
    await consolePage.locator('button[onclick="sendPrompt()"]').click();
    assert.match(await consolePage.locator('#messageFeedback').innerText(),/请先恢复语音/);
    await consolePage.unroute('**/api/devices/cat-alice/voice');
    assert.match(await consolePage.locator('#uiStatus').innerText(), /控制已连/);
    const peerConsole = await context.newPage(); observe(peerConsole);
    await peerConsole.goto(info.accounts_url + '/console/cat-alice');
    await peerConsole.waitForFunction(() => document.getElementById('voiceState').textContent.includes('已设置暂停'));
    await consolePage.reload();
    await consolePage.waitForFunction(() => document.getElementById('voiceState').textContent.includes('已设置暂停'));
    assert.equal(await consolePage.locator('#pauseVoiceBtn').isDisabled(), true);
    await consolePage.route('**/api/devices/cat-alice/voice', route => route.request().method() === 'POST' ?
      route.fulfill({status:503,json:{detail:'模拟语音控制失败'}}) : route.continue());
    await consolePage.click('#resumeVoiceBtn');
    await consolePage.waitForFunction(() => document.getElementById('voiceFeedback').dataset.kind === 'error' && !document.getElementById('resumeVoiceBtn').disabled);
    assert.match(await consolePage.locator('#voiceState').innerText(),/已设置暂停/);
    assert.match(await consolePage.locator('#voiceFeedback').innerText(),/模拟语音控制失败/);
    await consolePage.unroute('**/api/devices/cat-alice/voice');
    await consolePage.click('#resumeVoiceBtn');
    await consolePage.waitForFunction(() => document.getElementById('voiceState').textContent === '设备离线' && !document.getElementById('pauseVoiceBtn').disabled);
    assert.equal(await consolePage.locator('#pauseVoiceBtn').isDisabled(), false);
    await peerConsole.waitForFunction(() => document.getElementById('voiceState').textContent === '设备离线');
    // 服务器已执行但 HTTP 响应丢失时，用新 GET 核对，不能重发 POST。
    let lostResponsePosts = 0;
    await consolePage.route('**/api/devices/cat-alice/voice', async route => {
      if(route.request().method() === 'POST') {
        lostResponsePosts++; await route.fetch(); await route.abort('failed');
      } else await route.continue();
    });
    await consolePage.evaluate(() => disconnectUI());
    await consolePage.click('#pauseVoiceBtn');
    await consolePage.waitForFunction(() => document.getElementById('voiceFeedback').dataset.kind === 'success' && document.getElementById('voiceState').textContent.includes('已设置暂停'));
    assert.equal(lostResponsePosts,1);
    assert.match(await consolePage.locator('#uiStatus').innerText(), /控制未连/);
    await consolePage.unroute('**/api/devices/cat-alice/voice');
    await consolePage.click('#resumeVoiceBtn');
    await consolePage.waitForFunction(() => document.getElementById('voiceState').textContent === '设备离线');
    await consolePage.evaluate(() => {
      applyVoiceState({paused:true,connected:false,active:false,revision:100000});
      connectUI();
    });
    await consolePage.waitForFunction(() => document.getElementById('voiceState').textContent === '设备离线');
    // 超时能结束等待并保留页面，读请求不重试写入。
    await consolePage.route('**/api/devices/cat-alice/voice', async route => {
      await new Promise(resolve => setTimeout(resolve,250));
      await route.fulfill({status:200,json:{}}).catch(() => {});
    });
    const timeoutMessage = await consolePage.evaluate(async () => {
      try { await deviceJson('/voice',{timeoutMs:60}); return ''; } catch(error) { return error.message; }
    });
    assert.match(timeoutMessage,/请求超时/);
    await consolePage.unroute('**/api/devices/cat-alice/voice');
    await peerConsole.close();
    const write = await consolePage.evaluate(async () => {
      disconnectUI();
      const response = await deviceFetch('/screen_mode?mode=1', {method:'POST'});
      connectUI(); return response.ok;
    });
    assert.equal(write,true);
    assert(sockets.some(url => url.endsWith('/ws/devices/cat-alice/ui')));
    assert(sockets.some(url => url.endsWith('/ws/devices/cat-alice/viewer')));
    assert(sockets.every(url => !url.includes('token=')));
    await consolePage.evaluate(() => {
      toastsEl.replaceChildren(); feedback('voiceFeedback','',''); feedback('messageFeedback','','');
      document.getElementById('promptText').value = '';
    });
    await consolePage.screenshot({path:path.join(output,'console-workspace.png'),fullPage:true,animations:'disabled'});
    // 图表 CDN 不可达时，核心控制和文字分析结果仍能使用。
    await consolePage.evaluate(() => renderAnalysis({summary:'模拟活动总结',activities:[{start:'10:00',end:'10:05',title:'测试活动',detail:'测试明细'}]}));
    assert.match(await consolePage.locator('#activityTable').textContent(),/测试明细/);
    assert.match(await consolePage.locator('#chartUnavailable').textContent(),/图表组件暂未加载/);
    await consolePage.evaluate(() => handleUiMessage('KNOWLEDGE:' + JSON.stringify({sources:[{
      filename:'<img src=x onerror="window.knowledgeFileExecuted=true">.txt',ordinal:2,
    }]})));
    assert.match(await consolePage.locator('#chatBox').innerText(), /\[检索资料\].*<img src=x.*片段 2/);
    assert.equal(await consolePage.evaluate(() => !!window.knowledgeFileExecuted),false);
    await consolePage.screenshot({path:path.join(output,'console.png'),fullPage:true});
    const mediaRejected = await consolePage.evaluate(() => {
      try { consoleMediaUrl('/api/devices/cat-bob/repeat.wav'); return false; } catch { return true; }
    });
    assert.equal(mediaRejected,true);
    let releasePendingControl;
    const pendingControlGate = new Promise(resolve => { releasePendingControl = resolve; });
    await consolePage.route('**/api/devices/cat-alice/voice', async route => {
      if(route.request().method() === 'POST') await pendingControlGate;
      await route.continue().catch(() => {});
    });
    await consolePage.click('#pauseVoiceBtn');
    await consolePage.waitForFunction(() => document.getElementById('voiceState').textContent.includes('正在暂停'));
    await page.click('#logoutBtn');
    await consolePage.waitForURL(info.accounts_url + '/?notice=changed');
    releasePendingControl();
    await consolePage.unroute('**/api/devices/cat-alice/voice');
    await login(page,'bob');
    assert.doesNotMatch(await page.locator('#deviceGrid').innerText(), /Alice|cat-new/);
    await page.locator('.device-card').getByRole('button',{name:'查看记忆'}).click();
    await page.waitForFunction(() => document.getElementById('memoryContent').textContent.includes('Bob的专属记忆'));
    await page.locator('#clearDetails summary').click(); await page.fill('#clearDeviceId','cat-alice');
    assert.equal(await page.locator('#clearBtn').isDisabled(),true);
    await page.fill('#clearDeviceId','cat-bob'); await page.click('#clearBtn');
    await page.waitForFunction(() => !document.getElementById('memoryContent').textContent.includes('Bob的专属记忆'));
    await page.click('[data-close="memoryDialog"]');
    await page.locator('.device-card').getByRole('button',{name:'停用设备'}).click();
    await page.fill('#revokeDeviceId','cat-bob'); await page.click('#revokeBtn');
    await page.waitForFunction(() => document.querySelector('.device-card .state')?.textContent === '已停用');
    await page.click('#logoutBtn'); await login(page,'alice');
    assert.match(await page.locator('#deviceGrid').innerText(), /Alice/);
    await page.locator('.device-card').filter({hasText:'cat-alice'}).getByRole('button',{name:'查看记忆'}).click();
    await page.waitForFunction(() => document.getElementById('memoryContent').textContent.includes('Alice的专属记忆'));
    const mobile = await browser.newContext({viewport:{width:390,height:844},isMobile:true});
    const mobilePage = await mobile.newPage(); observe(mobilePage);
    await mobilePage.goto(info.accounts_url); await mobilePage.locator('#loginView').waitFor({state:'visible'});
    await mobilePage.screenshot({path:path.join(output,'mobile-login.png'),fullPage:true});
    await login(mobilePage,'alice');
    assert.equal(await mobilePage.evaluate(() => document.documentElement.scrollWidth <= innerWidth),true);
    await mobilePage.screenshot({path:path.join(output,'mobile-devices.png'),fullPage:true});
    await mobilePage.goto(info.accounts_url + '/console/cat-alice');
    await mobilePage.waitForFunction(() => document.getElementById('uiStatus').textContent.includes('控制已连'));
    await mobilePage.screenshot({path:path.join(output,'mobile-console.png'),fullPage:true,animations:'disabled'});
    for(const panel of ['vision','record','translate','hardware','dialog']) {
      await mobilePage.click('.nav-links a[href="#' + panel + '"]');
      assert.equal(await mobilePage.locator('#' + panel).isVisible(),true);
      assert.equal(await mobilePage.evaluate(() => document.documentElement.scrollWidth <= innerWidth),true,'手机页签不应横向溢出：' + panel);
    }
    await mobilePage.reload();
    await mobilePage.waitForFunction(() => document.getElementById('uiStatus').textContent.includes('控制已连'));
    assert.equal(await mobilePage.locator('#dialog').isVisible(),true);
    const localPage = await context.newPage(); observe(localPage);
    await localPage.goto(info.local_url);
    await localPage.waitForFunction(() => document.getElementById('uiStatus').textContent.includes('控制已连'));
    assert.equal(await localPage.locator('#consoleAccount').isVisible(),false);
    const secureUrl = vm.runInNewContext(fs.readFileSync(path.join(root,'web/console_access.js'),'utf8') + '\nconsoleSocketUrl("ui")', {
      CONSOLE_CONTEXT:{mode:'accounts',device_id:'cat-alice'}, location:{protocol:'https:',origin:'https://cat.example'}, AbortController, URL,
    });
    assert.equal(secureUrl,'wss://cat.example/ws/devices/cat-alice/ui');
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({ok:true,checks:['登录与错误提示','设备配对与账号隔离','记忆查看与确认清除','设备停用','画面首帧与关闭预览','语音处理中反馈与防重复提交','语音失败与丢失响应核对','控制离线时HTTP暂停','请求超时','模式切换失败不假报成功','暂停/恢复与刷新同步','退出取消处理中请求与跨页清理','图表不可用时保留文字结果','桌面与手机页签布局','本地模式兼容','HTTPS使用WSS路径'],screenshots:output}));
    await mobile.close(); await context.close();
  } finally {
    if(browser) await browser.close();
    fixture.stdin.end('stop\n');
    if(fixture.exitCode === null) {
      await Promise.race([once(fixture,'exit'),new Promise((_,reject) => { const timer=setTimeout(() => { fixture.kill(); reject(new Error('测试服务关闭超时')); },15000); timer.unref(); })]);
    }
  }
}
main().catch(error => { console.error(error); process.exitCode=1; });
