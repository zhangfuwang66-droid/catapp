/* 与 web_ui_test.cjs 使用同一临时服务；不连接真实模型、数据库或硬件。 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const {chromium} = require('playwright');

async function main() {
  const root = path.resolve(__dirname, '..');
  const output = path.resolve(root, '../artifacts/web-tests');
  fs.mkdirSync(output, {recursive:true});
  const fixture = spawn(process.env.PYTHON_EXECUTABLE || 'python', ['-u', path.join(__dirname,'web_fixture.py')], {
    cwd:root, env:{...process.env, PYTHONPATH:root, PYTHONIOENCODING:'utf-8'}, stdio:['pipe','pipe','pipe'],
  });
  let stderr = '', browser;
  fixture.stderr.on('data',data => { stderr += data.toString(); });
  try {
    const info = await new Promise((resolve,reject) => {
      let buffer = '';
      const timer = setTimeout(() => reject(new Error('测试服务未就绪')),25000);
      fixture.on('exit', code => { clearTimeout(timer); reject(new Error('服务退出 ' + code + ': ' + stderr)); });
      fixture.on('error',reject);
      fixture.stdout.on('data',data => {
        buffer += data.toString();
        let next;
        while((next = buffer.indexOf('\n')) >= 0) {
          const line = buffer.slice(0,next).trim(); buffer = buffer.slice(next+1);
          if(line.startsWith('{')) { clearTimeout(timer); resolve(JSON.parse(line)); }
        }
      });
    });
    browser = await chromium.launch({headless:true, ...(process.env.BROWSER_EXECUTABLE ? {executablePath:process.env.BROWSER_EXECUTABLE} : {})});
    const context = await browser.newContext({viewport:{width:1440,height:1000}});
    const page = await context.newPage(), errors = [];
    page.on('pageerror',error => errors.push(error.message));
    async function login(target,name) {
      await target.goto(info.accounts_url);
      await target.locator('#loginView').waitFor({state:'visible'});
      await target.fill('#loginUsername',name); await target.fill('#loginPassword',name + '-test-password');
      await target.click('#loginBtn'); await target.locator('#devicesView').waitFor({state:'visible'});
    }
    async function upload(target,name,text) {
      await target.locator('#fileInput').setInputFiles({name,mimeType:'text/plain',buffer:Buffer.from(text)});
      await target.locator('.document-row').filter({hasText:name}).waitFor();
      await target.waitForFunction(() => [...document.querySelectorAll('.document-state')].every(n => n.textContent === '关键词可用'));
    }
    await page.goto(info.accounts_url + '/knowledge');
    await page.waitForURL(info.accounts_url + '/?notice=login');
    await login(page,'alice');
    await page.locator('.sidebar a[href="/knowledge"]').click();
    await page.locator('#emptyState').waitFor({state:'visible'});
    await upload(page,'蓝星项目.txt','蓝星项目的发射日期为十二月二十五日，集合地点为星港三号门。');
    await page.fill('#query','蓝星项目的发射日期是什么'); await page.click('#searchBtn');
    await page.locator('.search-hit').waitFor();
    assert.match(await page.locator('#searchResults').innerText(),/十二月二十五日/);
    const link = await page.locator('.document-actions a').getAttribute('href');
    const download = await context.request.get(info.accounts_url + link);
    assert.match(await download.text(),/星港三号门/);
    await page.locator('.document-actions button').filter({hasText:'重建索引'}).click();
    await page.waitForFunction(() => document.querySelector('.document-state')?.textContent === '关键词可用');
    await page.screenshot({path:path.join(output,'knowledge-desktop.png'),fullPage:true});
    // 错误提示不应被自动刷新静默清除。
    await page.locator('#fileInput').setInputFiles({name:'bad.exe',mimeType:'application/octet-stream',buffer:Buffer.from('test')});
    await page.waitForFunction(() => document.querySelector('#pageError').textContent.includes('不支持'));
    await page.click('#refreshBtn');
    assert.match(await page.locator('#pageError').innerText(),/不支持/);
    // 另一个标签页退出时，资料页清除私有 DOM 并跳回登录。
    const portal = await context.newPage(); await portal.goto(info.accounts_url);
    await portal.locator('#devicesView').waitFor({state:'visible'}); await portal.click('#logoutBtn');
    await page.waitForURL(info.accounts_url + '/?notice=login');
    assert.doesNotMatch(await page.locator('body').innerText(),/星港三号门|蓝星项目/);
    await login(page,'bob'); await page.goto(info.accounts_url + '/knowledge');
    await page.locator('#emptyState').waitFor({state:'visible'});
    assert.equal((await context.request.get(info.accounts_url + link)).status(),404);
    await context.close();
    const mobile = await browser.newContext({viewport:{width:390,height:844},isMobile:true});
    await mobile.route(/https:\/\/(fonts\.|cdn\.jsdelivr)/,route => route.abort());
    const phone = await mobile.newPage(); phone.on('pageerror',error => errors.push(error.message));
    await login(phone,'alice'); await phone.locator('.summary-strip a[href="/knowledge"]').click();
    await phone.locator('.document-row').waitFor();
    assert.equal(await phone.evaluate(() => document.documentElement.scrollWidth <= innerWidth),true);
    await phone.screenshot({path:path.join(output,'knowledge-mobile.png'),fullPage:true});
    await phone.getByRole('button',{name:'删除',exact:true}).click();
    await phone.locator('#deleteDialog').waitFor({state:'visible'}); await phone.click('#cancelDelete');
    assert.equal(await phone.locator('.document-row').count(),1);
    await phone.getByRole('button',{name:'删除',exact:true}).click(); await phone.click('#confirmDelete');
    await phone.locator('#emptyState').waitFor({state:'visible'});
    const localPage = await mobile.newPage(); await localPage.goto(info.local_url);
    await localPage.locator('.nav-links a[href="/knowledge"]').click();
    await localPage.locator('#privateView').waitFor({state:'visible'});
    assert.equal(await localPage.locator('#logoutBtn').isVisible(),false);
    assert.match(await localPage.locator('#privacyLabel').innerText(),/本地/);
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({ok:true,checks:['账号页面权限','上传与离线索引','检索与下载','重建索引','错误保留','跨页退出清理','跨账号隔离','手机布局与删除确认','本地入口'],screenshots:output}));
    await mobile.close();
  } finally {
    if(browser) await browser.close();
    fixture.stdin.end('stop\n');
    if(fixture.exitCode === null) await Promise.race([once(fixture,'exit'),new Promise((_,reject) => {
      const timer = setTimeout(() => { fixture.kill(); reject(new Error('测试服务关闭超时')); },15000); timer.unref();
    })]);
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
