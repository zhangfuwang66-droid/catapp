/* Offline browser regression for the administrator's explicit unbind recovery flow. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const {once} = require('node:events');
const {chromium} = require('playwright');

const root = path.resolve(__dirname,'..');
const output = path.resolve(root,'../artifacts/admin-ui');
const id = 'cat-recovery';
const files = {'/admin':['web/admin/index.html','text/html; charset=utf-8'],
  '/web/admin/admin.js':['web/admin/admin.js','text/javascript; charset=utf-8'],
  '/web/admin/admin.css':['web/admin/admin.css','text/css; charset=utf-8']};
let scenario;
function reset() {
  scenario = {device:{id,display_name:'待核对小喵',device_type:'cat',owner_user_id:2,owner_username:'原主人',
    active_user_id:2,active_username:'原主人',voice_paused:true,revoked:false},audit:[],posts:[],denied:false,readFailure:false};
}
function reply(res,status,data) {
  res.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'});
  res.end(JSON.stringify(data));
}
function unbind() {
  Object.assign(scenario.device,{owner_user_id:null,owner_username:null,active_user_id:null,active_username:null,voice_paused:null});
}
function log(result='started') {
  const row = {id:Math.max(0,...scenario.audit.map(item => item.id))+1,actor_id:1,actor_username:'admin',target_type:'device',target_id:id,
    action:'unbind-device',result,created_at:1730000000};
  scenario.audit.unshift(row); return row;
}
function partial() {
  scenario.onWrite = async(action,res) => {
    unbind(); log(); reply(res,503,{detail:'状态变更已生效，连接、任务或设备状态同步未完成；请刷新后重试，审计保留未完成状态'});
  };
}
function deferred() { let resolve; const promise = new Promise(done => { resolve=done; }); return {promise,resolve}; }
const server = http.createServer(async(req,res) => {
  const url = new URL(req.url,'http://localhost'), file = files[url.pathname];
  if(file) { res.writeHead(200,{'Content-Type':file[1]}); res.end(fs.readFileSync(path.join(root,file[0]))); return; }
  if(req.method === 'POST') {
    let body = ''; for await(const chunk of req) body += chunk;
    const action = {path:url.pathname,body:JSON.parse(body),csrf:req.headers['x-csrf-token']};
    scenario.posts.push(action);
    if(scenario.onWrite) return scenario.onWrite(action,res);
    unbind(); log('success'); return reply(res,200,{ok:true});
  }
  if(scenario.denied) return reply(res,403,{detail:'管理员权限已撤销'});
  if(scenario.onRead && await scenario.onRead(url,res)) return;
  if(url.pathname === '/api/admin/devices') {
    if(scenario.readFailure) return reply(res,503,{detail:'暂时无法核对设备'});
    let items = [scenario.device];
    if(url.searchParams.get('status') === 'bound' && scenario.device.owner_user_id == null) items = [];
    return reply(res,200,{items,total:items.length,page:1,page_size:20});
  }
  const data = {
    '/api/admin/session':{user:{id:1,username:'admin'},csrf_token:'test-csrf'},
    '/api/auth/session':{user:{id:1,username:'admin'},csrf_token:'test-csrf'},
    '/api/admin/audit':{items:scenario.audit,total:scenario.audit.length,page:1,page_size:100},
  }[url.pathname];
  reply(res,data ? 200 : 404,data || {detail:'不存在'});
});

(async() => {
  server.listen(0,'127.0.0.1'); await once(server,'listening');
  const base = `http://127.0.0.1:${server.address().port}`;
  let browser;
  try {
    browser = await chromium.launch({headless:true,...(process.env.BROWSER_EXECUTABLE ? {executablePath:process.env.BROWSER_EXECUTABLE} : {})});
    fs.mkdirSync(output,{recursive:true});
    const errors = [];
    async function open(mobile=false) {
      const context = await browser.newContext({viewport:mobile ? {width:390,height:844} : {width:1440,height:900},
        ...(mobile ? {isMobile:true,deviceScaleFactor:1} : {})});
      const page = await context.newPage(); page.on('pageerror',error => errors.push(error.message));
      await page.goto(base+'/admin#devices'); await page.locator('#devicesBody tr').waitFor();
      return {context,page};
    }
    async function submit(page) {
      await page.getByRole('button',{name:'解绑',exact:true}).click();
      await page.fill('#confirmInput',id); await page.click('#confirmSubmit');
    }
    async function returnedPartial(page) {
      await submit(page); await page.locator('#confirmError').getByText(/已刷新/).waitFor(); await page.click('#confirmCancel');
    }
    async function inspect(page) {
      await page.locator('#unbindFollowups').getByRole('button',{name:'核对解绑结果',exact:true}).click();
      await page.locator('#unbindStatus').getByText(/当前设备未绑定|后台可能仍在处理中/).waitFor();
    }

    // 200 成功直接结束，不产生重复写入入口；首次 POST 保持原契约。
    reset();
    let {context,page} = await open();
    await submit(page); await page.locator('#toast').getByText('操作已完成',{exact:true}).waitFor();
    assert.deepEqual(scenario.posts,[{path:`/api/admin/devices/${id}/unbind`,csrf:'test-csrf',body:{confirm_device_id:id}}]);
    assert.equal(await page.locator('#unbindFollowups').isVisible(),false);
    await page.getByRole('button',{name:'核对收尾',exact:true}).click();
    await page.locator('#unbindStatus').getByText(/审计已记录成功/).waitFor();
    assert.equal(await page.locator('#unbindRetry').isVisible(),false);
    await context.close();

    // 503 后，列表过滤/审计搜索都不能遮蔽原目标；刷新保持跟进但不会写入。
    reset(); partial(); ({context,page} = await open());
    await page.selectOption('#deviceStatus','bound'); await page.locator('#deviceSearchForm button').click();
    await page.waitForFunction(() => document.getElementById('pageFeedback').hidden);
    await returnedPartial(page);
    assert.equal(await page.locator('#devicesBody tr').count(),0,'已解绑目标已不符合已绑定筛选');
    await page.screenshot({path:path.join(output,'recovery-desktop-followup.png'),fullPage:true});
    assert.equal(await page.locator('#unbindFollowups').getByRole('button',{name:'核对解绑结果',exact:true}).count(),1,
      '503 解绑生效后，未绑定设备仍必须保留只读核对和手动收尾入口');
    await page.reload(); await page.locator('#unbindFollowups').waitFor();
    assert.equal(scenario.posts.length,1,'重新加载页面不能自动重试');
    await inspect(page);
    assert.match(await page.locator('#unbindStatus').innerText(),/不代表.*已清理/);
    assert.equal(await page.locator('#unbindRetry').isDisabled(),true,'必须重新输入设备编号');
    await page.fill('#unbindConfirm','wrong'); assert.equal(await page.locator('#unbindRetry').isDisabled(),true);
    await page.fill('#unbindConfirm',id);
    await page.click('#unbindCheck');
    await page.locator('#unbindStatus').getByText(/请求已返回 503/).waitFor();
    assert.equal(await page.locator('#unbindConfirm').inputValue(),'','每次重新核对必须清空旧确认');
    assert.equal(scenario.posts.length,1,'核对为只读');
    await page.fill('#unbindConfirm',id);
    await page.screenshot({path:path.join(output,'recovery-desktop-confirm.png')});
    const running = deferred(), sent = deferred();
    scenario.onWrite = async(action,res) => { sent.resolve(); await running.promise; log('success'); reply(res,200,{ok:true}); };
    await page.click('#unbindRetry'); await sent.promise;
    assert.deepEqual(scenario.posts[1].body,{confirm_device_id:id,cleanup_only:true},'重试必须使用后端锁内保护');
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#unbindDialog').isVisible(),true,'收尾执行时 Escape 不应关闭');
    assert.equal(await page.locator('#unbindClose').isDisabled(),true);
    await page.evaluate(() => document.getElementById('unbindForm').requestSubmit());
    assert.equal(scenario.posts.length,2,'收尾执行时不重复写入');
    running.resolve(); await page.locator('#unbindStatus').getByText(/服务已确认本次收尾成功/).waitFor();
    assert.equal(await page.locator('#unbindRetry').isVisible(),false);
    assert.equal(await page.locator('#unbindFollowups').isVisible(),false);
    await page.click('#unbindClose'); await context.close();

    // 收尾再次返回 503 时仍保留入口，但旧输入失效，不会连续自动写入。
    reset(); partial(); ({context,page} = await open()); await returnedPartial(page); await inspect(page);
    await page.fill('#unbindConfirm',id); await page.click('#unbindRetry');
    await page.locator('#unbindStatus').getByText(/尚未确认本次收尾结果/).waitFor();
    assert.equal(scenario.posts.length,2);
    assert.equal(await page.locator('#unbindConfirm').inputValue(),'');
    assert.equal(await page.locator('#unbindRetry').isDisabled(),true);
    scenario.onWrite=null; await page.fill('#unbindConfirm',id); await page.click('#unbindRetry');
    await page.locator('#unbindStatus').getByText(/服务已确认本次收尾成功/).waitFor();
    assert.equal(scenario.posts.length,3); await context.close();

    // 无法准确对应同一目标的多个未完成尝试时，不根据列表刷新放开写入。
    reset(); partial(); ({context,page} = await open()); await returnedPartial(page);
    const another=log(); another.actor_id=9; another.actor_username='另一管理员';
    await page.locator('#unbindFollowups').getByRole('button',{name:'核对解绑结果',exact:true}).click();
    await page.locator('#unbindStatus').getByText(/审计无法与上次请求准确对应/).waitFor();
    assert.equal(await page.locator('#unbindRetry').isVisible(),false);
    assert.equal(scenario.posts.length,1); await context.close();

    // 重新绑定发生在确认后、POST 前：锁内 cleanup_only 返回 409，不能误报成功或自动重试。
    reset(); partial(); ({context,page} = await open()); await returnedPartial(page); await inspect(page);
    scenario.onWrite = async(action,res) => {
      Object.assign(scenario.device,{owner_user_id:3,active_user_id:3,owner_username:'新主人'}); log('failed');
      reply(res,409,{detail:'设备已重新绑定，不能重试旧解绑收尾'});
    };
    await page.fill('#unbindConfirm',id); await page.click('#unbindRetry');
    await page.locator('#unbindStatus').getByText(/本次重试未完成/).waitFor();
    await page.locator('#unbindCheck:enabled').waitFor();
    assert.match(await page.locator('#unbindStatus').innerText(),/新主人/);
    assert.doesNotMatch(await page.locator('#unbindStatus').innerText(),/本次收尾成功/);
    assert.equal(await page.locator('#unbindRetry').isVisible(),false);
    assert.equal(scenario.posts.length,2); assert.equal(scenario.device.owner_user_id,3);
    await page.click('#unbindCheck'); await page.locator('#unbindStatus').getByText(/归属已变化/).waitFor();
    assert.equal(scenario.posts.length,2); await context.close();

    // 在下一次预检前已经变更归属：前端直接阻止 POST。
    reset(); partial(); ({context,page} = await open()); await returnedPartial(page); await inspect(page);
    Object.assign(scenario.device,{owner_user_id:4,active_user_id:4,owner_username:'另一位主人'});
    await page.fill('#unbindConfirm',id); await page.click('#unbindRetry');
    await page.locator('#unbindStatus').getByText(/本次未提交/).waitFor();
    assert.equal(scenario.posts.length,1); await context.close();

    // 读取失败不得沿用旧检查结果或旧输入，恢复后仍需再次确认。
    reset(); partial(); ({context,page} = await open()); await returnedPartial(page); await inspect(page);
    await page.fill('#unbindConfirm',id); scenario.readFailure=true; await page.click('#unbindCheck');
    await page.locator('#unbindStatus').getByText(/核对未完成/).waitFor();
    assert.equal(await page.locator('#unbindRetry').isVisible(),false);
    scenario.readFailure=false; await page.click('#unbindCheck');
    await page.locator('#unbindStatus').getByText(/请求已返回 503/).waitFor();
    assert.equal(await page.locator('#unbindConfirm').inputValue(),'');
    assert.equal(scenario.posts.length,1); await context.close();

    // 超时后后台仍未结束：刷新/核对都不能解锁重试，最终以成功审计为准。
    reset(); ({context,page} = await open());
    const finish = deferred(), started = deferred(), finished = deferred();
    scenario.onWrite = async(action,res) => {
      unbind(); const entry=log(); started.resolve(); await finish.promise; entry.result='success'; reply(res,200,{ok:true}); finished.resolve();
    };
    await page.clock.install(); await submit(page); await started.promise; await page.clock.runFor(12050);
    await page.locator('#confirmError').getByText(/超时/).waitFor();
    await page.locator('#confirmError').getByText(/已刷新/).waitFor(); await page.click('#confirmFollowup');
    await page.locator('#unbindStatus').getByText(/后台可能仍在处理中/).waitFor();
    assert.equal(await page.locator('#unbindRetry').isVisible(),false);
    await page.click('#unbindClose'); await page.reload(); await inspect(page);
    assert.equal(await page.locator('#unbindRetry').isVisible(),false);
    assert.equal(scenario.posts.length,1);
    finish.resolve(); await finished.promise; await page.click('#unbindCheck');
    await page.locator('#unbindStatus').getByText(/审计已记录成功/).waitFor();
    assert.equal(await page.locator('#unbindRetry').isVisible(),false);
    assert.equal(scenario.posts.length,1); await context.close();

    // 权限在核对之后撤销：预检 403 关闭后台，不能继续发出写请求。
    reset(); partial(); ({context,page} = await open()); await returnedPartial(page); await inspect(page);
    scenario.denied=true; await page.fill('#unbindConfirm',id); await page.click('#unbindRetry');
    await page.locator('#gate').getByText(/已无权访问/).waitFor();
    assert.equal(await page.locator('#shell').isVisible(),false); assert.equal(await page.locator('#unbindDialog').isVisible(),false);
    assert.equal(scenario.posts.length,1); await context.close();

    // 权限在 POST 中被撤销时仍由服务端拒绝，读取 403 后关闭所有操作入口。
    reset(); partial(); ({context,page} = await open()); await returnedPartial(page); await inspect(page);
    scenario.onWrite=async(action,res) => { scenario.denied=true; reply(res,403,{detail:'管理员权限已撤销'}); };
    await page.fill('#unbindConfirm',id); await page.click('#unbindRetry');
    await page.locator('#gate').getByText(/已无权访问/).waitFor(); assert.equal(scenario.posts.length,2);
    await context.close();

    // 手机上完整执行核对与明确确认，不溢出，不自动重试。
    reset(); partial(); ({context,page} = await open(true)); await returnedPartial(page); await inspect(page);
    await page.fill('#unbindConfirm',id);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),true);
    assert.equal(await page.locator('#unbindDialog').evaluate(element => element.scrollWidth <= element.clientWidth),true);
    await page.screenshot({path:path.join(output,'recovery-mobile-confirm.png')});
    scenario.onWrite=null; await page.click('#unbindRetry');
    await page.locator('#unbindStatus').getByText(/服务已确认本次收尾成功/).waitFor();
    assert.equal(scenario.posts.length,2);
    await context.close();
    assert.deepEqual(errors,[]);
    console.log('admin recovery UI: success, partial 503, explicit cleanup_only retry, conflict, pending timeout, permissions and mobile passed');
  } finally { await browser?.close(); server.closeAllConnections(); server.close(); }
})().catch(error => { console.error(error); process.exitCode=1; });
