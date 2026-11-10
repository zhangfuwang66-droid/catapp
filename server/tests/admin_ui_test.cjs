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
const reads = new Map();
const audit = [];
let postBehavior, getBehavior;
const user = {id:1,username:'admin',created_at:1730000000,disabled:false,is_admin:true,device_count:1,key_configured:true};
const ordinary = {...user,id:2,username:'ordinary-user',is_admin:false};
const device = {id:'cat-one',display_name:'小喵一号',device_type:'cat',owner_user_id:1,owner_username:'admin',
  active_user_id:1,active_username:'admin',loan_active:false,loan_pending:false,revoked:false,
  audio_online:false,camera_online:false,online:false,voice_paused:true};

function reply(res,status,data) {
  res.writeHead(status,{'Content-Type':'application/json','Cache-Control':'no-store'});
  res.end(JSON.stringify(data));
}
function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return {promise,resolve};
}

const server = http.createServer(async (req,res) => {
  const url = new URL(req.url,'http://localhost');
  const file = files[url.pathname];
  if(file) { res.writeHead(200,{'Content-Type':file[1],'Cache-Control':'no-store'}); res.end(fs.readFileSync(path.join(root,file[0]))); return; }
  if(req.method === 'POST') {
    let body = ''; for await(const chunk of req) body += chunk;
    const action = {path:url.pathname,csrf:req.headers['x-csrf-token'],body:JSON.parse(body)};
    actions.push(action);
    if(postBehavior) { await postBehavior(action,res); return; }
    if(url.pathname.endsWith('/unbind')) { res.writeHead(409,{'Content-Type':'application/json'}); res.end(JSON.stringify({detail:'设备状态已变化'})); return; }
    if(url.pathname.endsWith('/voice')) device.voice_paused = action.body.paused;
    res.writeHead(200,{'Content-Type':'application/json'}); res.end(JSON.stringify({ok:true})); return;
  }
  reads.set(url.pathname,(reads.get(url.pathname) || 0) + 1);
  if(getBehavior && await getBehavior(url,res)) return;
  const data = {
    '/api/admin/session':{user:{id:1,username:'admin'},csrf_token:'test-csrf'},
    '/api/auth/session':{user:{id:1,username:'admin'},csrf_token:'test-csrf'},
    '/api/admin/overview':{users:{total:2,enabled:2,disabled:0,admins:1},devices:{total:1,bound:1,online:0},uptime_seconds:3600,
      storage:{database_bytes:1024,wal_bytes:0,knowledge_bytes:0,archive_bytes:0},backups:{last_success_at:null}},
    '/api/admin/users':{items:[user,ordinary],total:2,page:1,page_size:20},
    '/api/admin/devices':{items:[device],total:1,page:1,page_size:20},
    '/api/admin/storage':{documents:{count:0,bytes:0,statuses:{}},memory:{memories:0,content_bytes:0,archive_bytes:0,conversations:0,messages:0,summaries:0,archived_messages:0}},
    '/api/admin/backups':{enabled:true,running:false,last_success_at:null,last_error_code:null},
    '/api/admin/audit':{items:audit,total:audit.length,page:1,page_size:20},
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
    await page.goto(url); await page.locator('#metricUsers').getByText('2').waitFor();
    assert.match(await page.locator('#overviewConnections').innerText(),/0 台可核实连接/);
    assert.equal(actions.length,0,'页面加载不能触发写请求');
    const output = path.resolve(root,'../artifacts/admin-ui'); fs.mkdirSync(output,{recursive:true});
    await page.screenshot({path:path.join(output,'desktop.png'),fullPage:true});
    await page.getByRole('button',{name:'用户',exact:true}).click();
    await page.locator('#usersBody tr').first().waitFor();
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

    // 请求在服务端继续执行时，Escape 和取消不能关闭确认框或放开重复提交。
    const pending = deferred(), received = deferred();
    postBehavior = async (action,res) => {
      received.resolve(); await pending.promise;
      device.voice_paused = action.body.paused; reply(res,200,{ok:true});
    };
    await page.getByRole('button',{name:'暂停语音',exact:true}).click();
    await page.fill('#confirmInput','cat-one'); await page.click('#confirmSubmit');
    await received.promise;
    const pendingCount = actions.length;
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#confirmDialog').isVisible(),true,'处理中 Escape 不能关闭确认框');
    assert.equal(await page.locator('#confirmCancel').isDisabled(),true);
    await page.evaluate(() => {
      document.getElementById('confirmCancel').click();
      document.getElementById('confirmForm').requestSubmit();
    });
    await page.waitForTimeout(50);
    assert.equal(actions.length,pendingCount,'处理中不能重复写入');
    pending.resolve();
    await page.locator('#confirmDialog').waitFor({state:'hidden'});
    await page.getByRole('button',{name:'恢复语音',exact:true}).waitFor();

    // 503 也可能已经改了账号状态：自动核对目标和审计，保留未完成语义。
    await page.getByRole('button',{name:'用户',exact:true}).click();
    const ordinaryRow = page.locator('#usersBody tr').filter({hasText:'ordinary-user'});
    postBehavior = async (action,res) => {
      ordinary.disabled = true;
      audit.push({id:1,actor_username:'admin',action:'disable-user',target_type:'user',target_id:2,result:'started',created_at:1730000000});
      reply(res,503,{detail:'账号状态或会话撤销已生效，连接与任务清理未完成；请刷新后重试，审计保留未完成状态'});
    };
    const userReads = reads.get('/api/admin/users'), auditReads = reads.get('/api/admin/audit') || 0;
    await ordinaryRow.getByRole('button',{name:'停用',exact:true}).click();
    await page.fill('#confirmInput','ordinary-user'); await page.click('#confirmSubmit');
    await page.locator('#confirmError').getByText(/已刷新/).waitFor();
    assert.match(await page.locator('#confirmError').innerText(),/已生效/);
    assert.match(await page.locator('#confirmError').innerText(),/勿.*重复提交/);
    assert.equal(await page.locator('#confirmSubmit').isDisabled(),true);
    assert.ok(reads.get('/api/admin/users') > userReads,'503 后必须读取实际账号状态');
    assert.ok(reads.get('/api/admin/audit') > auditReads,'503 后必须核对审计');
    await ordinaryRow.getByText('已停用',{exact:true}).waitFor();
    const partialCount = actions.length;
    await page.evaluate(() => document.getElementById('confirmForm').requestSubmit());
    await page.waitForTimeout(50);
    assert.equal(actions.length,partialCount,'503 后不能原样重复提交');
    await page.click('#confirmCancel');
    await page.getByRole('button',{name:'操作审计',exact:true}).click();
    await page.locator('#auditBody').getByText('停用账号',{exact:true}).waitFor();
    await page.locator('#auditBody').getByText('未完成',{exact:true}).waitFor();

    // 服务端已生效但响应丢失：推进浏览器时间触发真实 AbortController 超时。
    await page.getByRole('button',{name:'设备',exact:true}).click();
    await page.getByRole('button',{name:'恢复语音',exact:true}).waitFor();
    const lost = deferred(), sent = deferred(), replied = deferred();
    postBehavior = async (action,res) => {
      device.voice_paused = action.body.paused;
      audit.push({id:2,actor_username:'admin',action:'resume-voice',target_type:'device',target_id:device.id,result:'started',created_at:1730000000});
      sent.resolve(); await lost.promise; reply(res,200,{ok:true}); replied.resolve();
    };
    await page.clock.install();
    const deviceReads = reads.get('/api/admin/devices');
    await page.getByRole('button',{name:'恢复语音',exact:true}).click();
    await page.fill('#confirmInput','cat-one'); await page.click('#confirmSubmit'); await sent.promise;
    await page.clock.runFor(12050);
    await page.locator('#confirmError').getByText(/已刷新/).waitFor();
    const timeoutMessage = await page.locator('#confirmError').innerText();
    assert.match(timeoutMessage,/超时/); assert.match(timeoutMessage,/可能已.*生效/);
    assert.doesNotMatch(timeoutMessage,/检查网络后重试/);
    assert.equal(await page.locator('#confirmSubmit').isDisabled(),true);
    assert.ok(reads.get('/api/admin/devices') > deviceReads,'超时后必须读取实际设备状态');
    await page.getByRole('button',{name:'暂停语音',exact:true}).waitFor({state:'attached'});
    const timedOutCount = actions.length;
    await page.evaluate(() => document.getElementById('confirmForm').requestSubmit());
    await page.waitForTimeout(50);
    lost.resolve(); await replied.promise;
    assert.equal(actions.length,timedOutCount,'超时及迟到响应均不能导致自动重试');
    await page.click('#confirmCancel');

    // 收到 200 响应头后读取正文超时，也不能被当作空 JSON 成功吞掉。
    const finishBody = deferred(), bodyStarted = deferred(), bodyDone = deferred();
    postBehavior = async (action,res) => {
      device.voice_paused = action.body.paused;
      res.writeHead(200,{'Content-Type':'application/json'}); res.write('{"ok":');
      bodyStarted.resolve(); await finishBody.promise; res.end('true}'); bodyDone.resolve();
    };
    await page.getByRole('button',{name:'暂停语音',exact:true}).click();
    await page.fill('#confirmInput','cat-one'); await page.click('#confirmSubmit'); await bodyStarted.promise;
    await page.clock.runFor(12050);
    await page.locator('#confirmError').getByText(/超时/).waitFor();
    await page.locator('#confirmError').getByText(/已刷新/).waitFor();
    assert.equal(await page.locator('#confirmDialog').isVisible(),true,'正文超时不能显示成功并关闭确认框');
    assert.equal(await page.locator('#confirmSubmit').isDisabled(),true);
    await page.getByRole('button',{name:'恢复语音',exact:true}).waitFor({state:'attached'});
    finishBody.resolve(); await bodyDone.promise; await page.click('#confirmCancel');

    // 核对失败时不能宣称已刷新成功，也不能恢复旧确认框中的提交按钮。
    postBehavior = async (action,res) => reply(res,503,{detail:'语音设置已生效，但连接清理未完成'});
    getBehavior = async (url,res) => {
      if(url.pathname === '/api/admin/devices') { reply(res,503,{detail:'临时无法读取设备状态'}); return true; }
      return false;
    };
    await page.getByRole('button',{name:'恢复语音',exact:true}).click();
    await page.fill('#confirmInput','cat-one'); await page.click('#confirmSubmit');
    await page.locator('#confirmError').getByText(/刷新未完成/).waitFor();
    assert.doesNotMatch(await page.locator('#confirmError').innerText(),/已刷新/);
    assert.equal(await page.locator('#confirmSubmit').isDisabled(),true);
    await page.click('#confirmCancel'); getBehavior = null;

    // 即使确认框被外部关闭，迟到的写响应也只核对原目标，不能污染新页面。
    const lateWrite = deferred(), lateSent = deferred();
    postBehavior = async (action,res) => {
      lateSent.resolve(); await lateWrite.promise;
      device.voice_paused = action.body.paused; reply(res,503,{detail:'设备设置已生效，清理未完成'});
    };
    await page.getByRole('button',{name:'恢复语音',exact:true}).click();
    await page.fill('#confirmInput','cat-one'); await page.click('#confirmSubmit'); await lateSent.promise;
    await page.evaluate(() => { document.getElementById('confirmDialog').close(); location.hash = '#users'; });
    await page.waitForFunction(() => document.getElementById('pageTitle').textContent === '用户' && document.getElementById('pageFeedback').hidden);
    const originalViewReads = reads.get('/api/admin/devices');
    lateWrite.resolve();
    await page.locator('#toast').getByText(/恢复语音/).waitFor();
    assert.ok(reads.get('/api/admin/devices') > originalViewReads,'迟到写响应仍须核对原目标页面');
    assert.equal(await page.locator('#pageFeedback').isVisible(),false,'旧写响应不能回写新页面错误');
    assert.equal(await page.locator('#refreshButton').isEnabled(),true,'后台核对不能卡住当前页刷新');
    await ordinaryRow.getByRole('button',{name:'启用',exact:true}).click();
    assert.equal(await page.locator('#confirmError').innerText(),'','新确认框不能继承旧请求错误');
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#confirmDialog').isVisible(),false,'未提交时仍能 Escape 取消');

    // 旧页面的读取失败到达时，当前页面仍保持自己的加载状态。
    const lateRead = deferred(), readStarted = deferred(), readDone = deferred();
    getBehavior = async (url,res) => {
      if(url.pathname === '/api/admin/devices') {
        readStarted.resolve(); await lateRead.promise; reply(res,503,{detail:'旧设备页错误'}); readDone.resolve(); return true;
      }
      return false;
    };
    await page.getByRole('button',{name:'设备',exact:true}).click(); await readStarted.promise;
    await page.getByRole('button',{name:'用户',exact:true}).click();
    await page.waitForFunction(() => document.getElementById('pageFeedback').hidden);
    const lateResponse = page.waitForResponse(response => response.url().includes('/api/admin/devices?') && response.status() === 503);
    lateRead.resolve(); await readDone.promise; await lateResponse;
    assert.equal(await page.locator('#pageFeedback').isVisible(),false,'旧读取错误不能覆盖当前页面');
    getBehavior = null; postBehavior = null;
    audit.push({id:3,actor_username:'admin',action:'future-action',target_type:'device',target_id:device.id,result:'success',created_at:1730000000});
    for(const view of ['资料与记忆','备份','操作审计']) await page.getByRole('button',{name:view,exact:true}).click();
    await page.waitForFunction(() => document.getElementById('view-audit').hidden === false);
    await page.locator('#auditBody').getByText('未知操作（future-action）',{exact:true}).waitFor();
    const mobile = await browser.newContext({viewport:{width:390,height:844},isMobile:true,deviceScaleFactor:1});
    const mobilePage = await mobile.newPage(); mobilePage.on('pageerror',error => errors.push(error.message));
    await mobilePage.goto(url); await mobilePage.locator('#metricUsers').getByText('2').waitFor();
    await mobilePage.click('#menuToggle');
    assert.equal(await mobilePage.locator('#menuToggle').getAttribute('aria-expanded'),'true');
    await mobilePage.getByRole('button',{name:'设备',exact:true}).click();
    assert.equal(await mobilePage.locator('#menuToggle').getAttribute('aria-expanded'),'false');
    await mobilePage.waitForTimeout(250);
    assert.equal(await mobilePage.evaluate(() => document.documentElement.scrollWidth <= innerWidth),true,'移动端不应有页面横向溢出');
    assert.deepEqual(errors,[]);
    await mobilePage.screenshot({path:path.join(output,'mobile.png'),fullPage:true});
    console.log('admin UI: desktop/mobile, pending actions, partial 503, timeout, reconciliation, stale responses and audit labels passed');
  } finally { await browser?.close(); server.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
