/* Set NODE_PATH to an installed Playwright directory before running this local UI test. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const {once} = require('node:events');
const {chromium} = require('playwright');

const root = path.resolve(__dirname, '..');
const files = {
  '/admin': ['web/admin/index.html','text/html; charset=utf-8'],
  '/web/admin/admin.css': ['web/admin/admin.css','text/css; charset=utf-8'],
  '/web/admin/admin.js': ['web/admin/admin.js','text/javascript; charset=utf-8'],
};
const actions = [];
const user = {id:1,username:'admin',created_at:1730000000,disabled:false,is_admin:true,device_count:1,key_configured:true};
const device = {id:'cat-one',display_name:'小喵一号',device_type:'cat',owner_user_id:1,owner_username:'admin',
  active_user_id:1,active_username:'admin',loan_active:false,loan_pending:false,revoked:false,
  audio_online:false,camera_online:false,online:false,voice_paused:true};

const server = http.createServer(async (req,res) => {
  const url = new URL(req.url,'http://localhost');
  const file = files[url.pathname];
  if(file) { res.writeHead(200,{'Content-Type':file[1],'Cache-Control':'no-store'}); res.end(fs.readFileSync(path.join(root,file[0]))); return; }
  if(req.method === 'POST') {
    let body = ''; for await(const chunk of req) body += chunk;
    actions.push({path:url.pathname,csrf:req.headers['x-csrf-token'],body:JSON.parse(body)});
    if(url.pathname.endsWith('/unbind')) { res.writeHead(409,{'Content-Type':'application/json'}); res.end(JSON.stringify({detail:'设备状态已变化'})); return; }
    res.writeHead(200,{'Content-Type':'application/json'}); res.end(JSON.stringify({ok:true})); return;
  }
  const data = {
    '/api/admin/session':{user:{id:1,username:'admin'},csrf_token:'test-csrf'},
    '/api/auth/session':{user:{id:1,username:'admin'},csrf_token:'test-csrf'},
    '/api/admin/overview':{users:{total:1,enabled:1,disabled:0,admins:1},devices:{total:1,bound:1,online:0},uptime_seconds:3600,
      storage:{database_bytes:1024,wal_bytes:0,knowledge_bytes:0,archive_bytes:0},backups:{last_success_at:null}},
    '/api/admin/users':{items:[user],total:1,page:1,page_size:20},
    '/api/admin/devices':{items:[device],total:1,page:1,page_size:20},
    '/api/admin/storage':{documents:{count:0,bytes:0,statuses:{}},memory:{memories:0,content_bytes:0,archive_bytes:0,conversations:0,messages:0,summaries:0,archived_messages:0}},
    '/api/admin/backups':{enabled:true,running:false,last_success_at:null,last_error_code:null},
    '/api/admin/audit':{items:[],total:0,page:1,page_size:20},
  }[url.pathname];
  res.writeHead(data ? 200 : 404,{'Content-Type':'application/json','Cache-Control':'no-store'});
  res.end(JSON.stringify(data || {detail:'not found'}));
});

(async () => {
  server.listen(0,'127.0.0.1'); await once(server,'listening');
  let browser;
  try {
    browser = await chromium.launch({headless:true,...(process.env.BROWSER_EXECUTABLE ? {executablePath:process.env.BROWSER_EXECUTABLE} : {})});
    const context = await browser.newContext({viewport:{width:1440,height:900}});
    const page = await context.newPage(), errors = [];
    page.on('pageerror',error => errors.push(error.message));
    const url = `http://127.0.0.1:${server.address().port}/admin`;
    await page.goto(url); await page.locator('#metricUsers').getByText('1').waitFor();
    assert.match(await page.locator('#overviewConnections').innerText(),/0 台可核实连接/);
    assert.equal(actions.length,0,'页面加载不能触发写请求');
    const output = path.resolve(root,'../artifacts/admin-ui'); fs.mkdirSync(output,{recursive:true});
    await page.screenshot({path:path.join(output,'desktop.png'),fullPage:true});
    await page.getByRole('button',{name:'用户',exact:true}).click();
    await page.locator('#usersBody tr').waitFor();
    assert.doesNotMatch(await page.locator('#usersBody').innerText(),/test-csrf/);
    await page.getByRole('button',{name:'设备',exact:true}).click();
    await page.getByRole('button',{name:'恢复语音'}).click();
    await page.locator('#confirmDescription').getByText(/模型连接/).waitFor();
    assert.equal(actions.length,0,'确认前不能恢复语音');
    await page.fill('#confirmInput','cat-one'); await page.click('#confirmSubmit');
    await page.waitForFunction(() => document.getElementById('toast').textContent.includes('操作已完成'));
    assert.deepEqual(actions[0],{path:'/api/admin/devices/cat-one/voice',csrf:'test-csrf',body:{paused:false,confirm_device_id:'cat-one'}});
    await page.getByRole('button',{name:'解绑'}).click(); await page.fill('#confirmInput','cat-one'); await page.click('#confirmSubmit');
    await page.locator('#confirmError').getByText('设备状态已变化').waitFor();
    await page.click('#confirmCancel');
    for(const view of ['资料与记忆','备份','操作审计']) await page.getByRole('button',{name:view,exact:true}).click();
    await page.waitForFunction(() => document.getElementById('view-audit').hidden === false);
    const mobile = await browser.newContext({viewport:{width:390,height:844},isMobile:true,deviceScaleFactor:1});
    const mobilePage = await mobile.newPage(); mobilePage.on('pageerror',error => errors.push(error.message));
    await mobilePage.goto(url); await mobilePage.locator('#metricUsers').getByText('1').waitFor();
    await mobilePage.click('#menuToggle');
    assert.equal(await mobilePage.locator('#menuToggle').getAttribute('aria-expanded'),'true');
    await mobilePage.getByRole('button',{name:'设备',exact:true}).click();
    assert.equal(await mobilePage.locator('#menuToggle').getAttribute('aria-expanded'),'false');
    await mobilePage.waitForTimeout(250);
    assert.equal(await mobilePage.evaluate(() => document.documentElement.scrollWidth <= innerWidth),true,'移动端不应有页面横向溢出');
    assert.deepEqual(errors,[]);
    await mobilePage.screenshot({path:path.join(output,'mobile.png'),fullPage:true});
    console.log('admin UI: desktop, mobile, actions and errors passed');
  } finally { await browser?.close(); server.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
