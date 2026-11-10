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
    await page.getByRole('button',{name:'暂停语音'}).click();
    await page.fill('#confirmInput','cat-browser-test'); await page.click('#confirmSubmit');
    await page.locator('#toast').getByText('操作已完成').waitFor();
    await page.locator('#devicesBody').getByText('已暂停').waitFor();
    const mobile = await browser.newContext({viewport:{width:390,height:844},isMobile:true,deviceScaleFactor:1});
    const mobileLogin = await mobile.request.post(url + '/api/auth/login',
      {headers:{Origin:url},data:{username:'site-admin',password:'temporary-test-password'}});
    assert.equal(mobileLogin.status(),200);
    const mobilePage = await mobile.newPage(); mobilePage.on('pageerror',error => errors.push(error.message));
    await mobilePage.goto(url + '/admin#devices');
    await mobilePage.locator('#devicesBody').getByText('cat-browser-test').waitFor();
    await mobilePage.screenshot({path:path.join(output,'live-mobile.png'),fullPage:true});
    await page.getByRole('button',{name:'操作审计',exact:true}).click();
    await page.locator('#auditBody').getByText('暂停语音',{exact:true}).waitFor();
    await page.locator('#auditBody').getByText('授予管理员',{exact:true}).waitFor();
    const ordinary = await browser.newContext();
    const ordinaryLogin = await ordinary.request.post(url + '/api/auth/login',
      {headers:{Origin:url},data:{username:'ordinary-user',password:'temporary-test-password'}});
    assert.equal(ordinaryLogin.status(),200);
    const ordinaryPage = await ordinary.newPage();
    const denied = await ordinaryPage.goto(url + '/admin');
    assert.equal(denied.status(),403);
    assert.deepEqual(errors,[]);
    console.log('admin live UI: route, session, data, voice, audit and access passed');
  } finally {
    await browser?.close();
    fixture.stdin.end();
    if(fixture.exitCode === null) await once(fixture,'exit').catch(() => {});
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
