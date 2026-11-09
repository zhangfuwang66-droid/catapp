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
    await consolePage.evaluate(() => connectCamera());
    await consolePage.waitForFunction(() => document.getElementById('videoCanvas').getContext('2d').getImageData(0,0,1,1).data[0] > 150);
    const write = await consolePage.evaluate(async () => {
      disconnectUI();
      const response = await deviceFetch('/screen_mode?mode=1', {method:'POST'});
      connectUI(); return response.ok;
    });
    assert.equal(write,true);
    assert(sockets.some(url => url.endsWith('/ws/devices/cat-alice/ui')));
    assert(sockets.some(url => url.endsWith('/ws/devices/cat-alice/viewer')));
    assert(sockets.every(url => !url.includes('token=')));
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
    await page.click('#logoutBtn');
    await consolePage.waitForURL(info.accounts_url + '/?notice=changed');
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
    const localPage = await context.newPage(); observe(localPage);
    await localPage.goto(info.local_url);
    await localPage.waitForFunction(() => document.getElementById('uiStatus').textContent.includes('控制已连'));
    assert.equal(await localPage.locator('#consoleAccount').isVisible(),false);
    const secureUrl = vm.runInNewContext(fs.readFileSync(path.join(root,'web/console_access.js'),'utf8') + '\nconsoleSocketUrl("ui")', {
      CONSOLE_CONTEXT:{mode:'accounts',device_id:'cat-alice'}, location:{protocol:'https:',origin:'https://cat.example'}, AbortController, URL,
    });
    assert.equal(secureUrl,'wss://cat.example/ws/devices/cat-alice/ui');
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({ok:true,checks:['登录与错误提示','设备配对与账号隔离','记忆查看与确认清除','设备停用','认证控制与摄像头','退出与跨页清理','移动布局','本地模式兼容','HTTPS使用WSS路径'],screenshots:output}));
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
