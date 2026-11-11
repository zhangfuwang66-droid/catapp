/* Real FastAPI plus a temporary database and simulated model. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const path = require('node:path');
const {chromium} = require('playwright');

const root = path.resolve(__dirname, '..');
const fixture = spawn(process.env.PYTHON_EXECUTABLE || 'python', ['-u',path.join(__dirname,'admin_web_fixture.py')],
  {cwd:root,env:{...process.env,PYTHONPATH:[root,__dirname].join(path.delimiter),PYTHONIOENCODING:'utf-8'},stdio:['pipe','pipe','pipe']});
let stderr = '';
fixture.stderr.on('data',data => { stderr += data.toString(); });

async function fixtureUrl() {
  return new Promise((resolve,reject) => {
    let buffer = '';
    const timer = setTimeout(() => reject(new Error('临时管理员服务未就绪: ' + stderr)),25000);
    fixture.on('error',reject);
    fixture.on('exit',code => { clearTimeout(timer); reject(new Error(`临时管理员服务退出 ${code}: ${stderr}`)); });
    fixture.stdout.on('data',data => {
      buffer += data.toString();
      const end = buffer.indexOf('\n');
      if(end >= 0) { clearTimeout(timer); resolve(JSON.parse(buffer.slice(0,end)).url); }
    });
  });
}

(async () => {
  let browser;
  try {
    const url = await fixtureUrl();
    browser = await chromium.launch({headless:true,...(process.env.BROWSER_EXECUTABLE ? {executablePath:process.env.BROWSER_EXECUTABLE} : {})});
    const context = await browser.newContext({viewport:{width:1365,height:900}});
    const page = await context.newPage(); const errors = [];
    page.on('pageerror',error => errors.push(error.message));
    const unauthenticated = await context.request.get(url + '/admin',{maxRedirects:0});
    assert.equal(unauthenticated.status(),303);
    await page.goto(url + '/admin');
    assert.equal(new URL(page.url()).pathname,'/');
    const login = await context.request.post(url + '/api/auth/login',
      {headers:{Origin:url},data:{username:'site-admin',password:'temporary-test-password'}});
    assert.equal(login.status(),200);
    await page.goto(url + '/admin');
    await page.locator('#metricUsers').getByText('2').waitFor();
    const output = path.resolve(root,'../artifacts/admin-ui'); fs.mkdirSync(output,{recursive:true});
    await page.screenshot({path:path.join(output,'live-desktop.png'),fullPage:true});
    await page.getByRole('button',{name:'用户',exact:true}).click();
    await page.locator('#usersBody').getByText('ordinary-user').waitFor();
    await page.getByRole('button',{name:'设备',exact:true}).click();
    await page.locator('#devicesBody').getByText('cat-browser-test').waitFor();
    await page.locator('#devicesBody').getByText('deer-browser-test').waitFor();
    await page.locator('#devicesBody').getByText('mystery-browser-test').waitFor();
    const catRow = page.locator('#devicesBody tr').filter({hasText:'cat-browser-test'});
    await catRow.getByRole('button',{name:'暂停语音'}).click();
    await page.fill('#confirmInput','cat-browser-test'); await page.click('#confirmSubmit');
    await page.locator('#toast').getByText('操作已完成').waitFor();
    await page.locator('#devicesBody').getByText('已暂停').waitFor();
    await page.getByRole('button',{name:'资料与记忆',exact:true}).click();
    await page.locator('#storageDocuments').getByText('5',{exact:true}).waitFor();
    assert.equal(await page.locator('#storagePending').innerText(),'1');
    assert.equal(await page.locator('#storageProcessing').innerText(),'1');
    const metadata = await page.locator('#storageBody').innerText();
    for(const label of ['待处理','处理中','已就绪','关键词检索可用','处理失败']) assert.ok(metadata.includes(label),label);
    assert.doesNotMatch(metadata,/queued|processing|ready|keyword|failed/);
    await page.screenshot({path:path.join(output,'live-storage.png'),fullPage:true});
    await page.getByRole('button',{name:'设备',exact:true}).click();
    const firstUnbind = page.waitForResponse(response => response.url().endsWith('/unbind') && response.request().method() === 'POST');
    await catRow.getByRole('button',{name:'解绑',exact:true}).click();
    await page.fill('#confirmInput','cat-browser-test'); await page.click('#confirmSubmit');
    assert.equal((await firstUnbind).status(),503,'真实 API 先提交解绑再出现清理失败');
    await page.locator('#confirmError').getByText(/已刷新/).waitFor();
    await page.click('#confirmFollowup');
    await page.locator('#unbindStatus').getByText(/请求已返回 503/).waitFor();
    await page.fill('#unbindConfirm','cat-browser-test');
    await page.screenshot({path:path.join(output,'live-recovery-desktop.png')});
    const cleanup = page.waitForRequest(request => request.url().endsWith('/unbind') && request.method() === 'POST');
    await page.click('#unbindRetry'); assert.equal((await cleanup).postDataJSON().cleanup_only,true);
    await page.locator('#unbindStatus').getByText(/服务已确认本次收尾成功/).waitFor();
    await page.click('#unbindClose');
    await catRow.getByText('未绑定',{exact:true}).waitFor();
    const mobile = await browser.newContext({viewport:{width:390,height:844},isMobile:true,deviceScaleFactor:1});
    const mobileLogin = await mobile.request.post(url + '/api/auth/login',
      {headers:{Origin:url},data:{username:'site-admin',password:'temporary-test-password'}});
    assert.equal(mobileLogin.status(),200);
    const mobilePage = await mobile.newPage(); mobilePage.on('pageerror',error => errors.push(error.message));
    await mobilePage.goto(url + '/admin#devices');
    await mobilePage.locator('#devicesBody').getByText('cat-browser-test').waitFor();
    assert.equal(await mobilePage.evaluate(() => document.documentElement.scrollWidth <= innerWidth),true);
    await mobilePage.screenshot({path:path.join(output,'live-mobile.png'),fullPage:true});
    await mobilePage.locator('#devicesBody tr').filter({hasText:'cat-browser-test'}).getByRole('button',{name:'核对收尾',exact:true}).click();
    await mobilePage.locator('#unbindStatus').getByText(/审计已记录成功/).waitFor();
    assert.equal(await mobilePage.locator('#unbindRetry').isVisible(),false);
    await mobilePage.screenshot({path:path.join(output,'live-recovery-mobile.png')});
    await page.getByRole('button',{name:'操作审计',exact:true}).click();
    await page.locator('#auditBody').getByText('暂停语音',{exact:true}).waitFor();
    await page.locator('#auditBody').getByText('授予管理员',{exact:true}).waitFor();
    await page.locator('#auditBody').getByText('未完成',{exact:true}).waitFor();
    assert.equal(await page.locator('#auditBody').getByText('解绑设备',{exact:true}).count(),2,'保留原未完成审计与新成功收尾审计');
    const ordinary = await browser.newContext();
    const ordinaryLogin = await ordinary.request.post(url + '/api/auth/login',
      {headers:{Origin:url},data:{username:'ordinary-user',password:'temporary-test-password'}});
    assert.equal(ordinaryLogin.status(),200);
    const ordinaryPage = await ordinary.newPage();
    const denied = await ordinaryPage.goto(url + '/admin');
    assert.equal(denied.status(),403);
    assert.deepEqual(errors,[]);
    console.log('admin live UI: real API/DB, metadata states, partial unbind cleanup recovery, voice, audit and desktop/mobile access passed');
  } finally {
    await browser?.close();
    fixture.stdin.end();
    if(fixture.exitCode === null) await once(fixture,'exit').catch(() => {});
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
