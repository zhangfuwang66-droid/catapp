/* 仅模拟设备 API，不访问真实 ESP32、云端或 Wi-Fi。 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');

const origin = 'http://192.168.4.1';
const output = path.resolve(__dirname, '../../artifacts/web-tests');
const source = fs.readFileSync(path.resolve(__dirname, '../esp32_servo_control/SetupPage.h'), 'utf8');
// 解析整个 C++ 相邻字面量表达式；不能只提取第一段 raw string 而漏测页面。
function renderSetupHtml(profile = 'cat') {
  const macros = profile === 'deer'
    ? {CYBERMIAO_SHORT_NAME:'小鹿',CYBERMIAO_BRAND:'CYBER DEER',CYBERMIAO_SETUP_AVATAR:'cat deer'}
    : {CYBERMIAO_SHORT_NAME:'小喵',CYBERMIAO_BRAND:'CYBER MIAO',CYBERMIAO_SETUP_AVATAR:'cat'};
  const expression = source.split('static const char DEVICE_SETUP_HTML[] PROGMEM = ')[1].trim();
  const token = /R"CMIAOHTML\(([\s\S]*?)\)CMIAOHTML"|CYBERMIAO_SHORT_NAME|CYBERMIAO_BRAND|CYBERMIAO_SETUP_AVATAR/g;
  assert.equal(expression.replace(token, '').trim(), ';', '设置页包含未解析的 C++ 字面量');
  return Array.from(expression.matchAll(token), match => match[1] ?? macros[match[0]]).join('');
}
const csp = "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'none'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'";
const maliciousSsid = '<img onerror="ssidAttack=1">';
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
let browser, completed = 0;

async function fixture(options = {}) {
  const context = await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
  const state = {csrf_token:'test-only-csrf',device_id:'cat-test',wifi_ssid:'Home Wi-Fi',
    server_url:'https://cat.example:443',mode:'accounts',password_saved:true,credential_configured:true,
    status:'wifi_offline',wifi_connected:false,bound:true,pairing_code:'',expires_in:0,
    reboot_pending:false,save_request_id:'',...options.state};
  const behavior = {scan:'ok',save:'success',status:'ok',...options.behavior};
  const writes = [], failures = [], pending = [];
  let scanPolls = 0, statusCalls = 0;
  await context.addInitScript(() => {
    const original = window.fetch;
    let active = 0;
    window.testMaxStatus = 0;
    window.fetch = async (...args) => {
      if (args[0] !== '/status') return original(...args);
      ++active; window.testMaxStatus = Math.max(window.testMaxStatus, active);
      try { return await original(...args); } finally { --active; }
    };
  });
  if (options.fastDeadline) await context.addInitScript(() => {
    let ticks = 0;
    Object.defineProperty(performance,'now',{value:() => ++ticks * 10000});
  });
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    assert.equal(url.origin, origin, '离线页面不得请求外部资源');
    if (url.pathname === '/') {
      return route.fulfill({contentType:'text/html',body:renderSetupHtml(options.profile),headers:{'Content-Security-Policy':csp}});
    }
    if (url.pathname === '/favicon.ico') return route.fulfill({status:204});
    if (request.method() === 'POST') {
      assert.equal(request.headers()['x-cybermiao-csrf'], state.csrf_token);
      assert.equal(request.headers().origin, origin);
      assert.equal(request.headers()['content-type'], 'application/x-www-form-urlencoded');
      assert.equal(url.search, '', '密码不得进入 URL');
      writes.push({path:url.pathname,form:new URLSearchParams(request.postData())});
    }
    let status = 200, data;
    if (url.pathname === '/status') {
      ++statusCalls;
      if (behavior.status === 'hang') { pending.push(route); return; }
      if (behavior.status === 'slow') await delay(2800);
      data = {...state};
    } else if (url.pathname === '/scan') {
      const action = writes.at(-1).form.get('action');
      if (action === 'start') scanPolls = 0;
      else assert.equal(action, 'poll');
      if (behavior.scan === 'hang') { pending.push(route); return; }
      if (behavior.scan === 'error') { status = 503; data = {error:'暂时无法扫描，请稍后重试'}; }
      else if (behavior.scan === 'empty') data = {scanning:false,networks:[]};
      else if (behavior.scan === 'forever' || ++scanPolls === 1) data = {scanning:true,networks:[]};
      else data = {scanning:false,networks:[
        {ssid:'Miao Home',rssi:-52,secure:true},{ssid:maliciousSsid,rssi:-61,secure:true},
        {ssid:'Miao Home',rssi:-79,secure:true},{ssid:'Guest',rssi:-77,secure:false}]};
    } else if (url.pathname === '/configure') {
      if (behavior.save === 'reject') { status = 400; data = {error:'请先通过 USB 配置设备身份'}; }
      else if (behavior.save === 'ambiguous') { pending.push(route); return; }
      else {
        state.reboot_pending = true;
        state.save_request_id = writes.at(-1).form.get('request_id');
        if (behavior.save === 'lost') return route.abort('failed');
        data = {saved:true,save_request_id:state.save_request_id,restart_in_ms:8000};
      }
    } else if (url.pathname === '/pairing') {
      state.pairing_code = 'JKLM-NPQR'; data = {ok:true};
    } else throw Error('未知页面请求 ' + url.pathname);
    try { await route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)}); }
    catch (error) { if (!/closed|handled|cancel/i.test(error.message)) throw error; }
  });
  const page = await context.newPage();
  page.on('pageerror',error => failures.push(error.message));
  await page.goto(origin);
  return {page,state,behavior,writes,failures,pending,get statusCalls(){return statusCalls;},
    async ready(){await page.waitForFunction(() => !document.querySelector('#fields').disabled);},
    async close(){
      assert.deepEqual(failures, []);
      for (const route of pending) await route.abort().catch(() => {});
      await context.close();
      ++completed;
    }};
}
async function text(page, selector, value) {
  await page.waitForFunction(({selector,value}) => document.querySelector(selector).textContent.includes(value), {selector,value});
}
async function manual(page, ssid, password) {
  await page.click('#choose'); await page.click('#manual');
  await page.fill('#ssid',ssid); if (password !== undefined) await page.fill('#password',password);
}

async function main() {
  fs.mkdirSync(output,{recursive:true});
  browser = await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE});
  try {
    // 鹿使用独立品牌；保存、扫描等配网逻辑与猫共用，动态回执也必须使用鹿的名称。
    {
      const f = await fixture({profile:'deer',state:{device_id:'deer-C8381AAD4F7C',device_type:'deer'}});
      const {page} = f; await f.ready();
      assert.equal(await page.title(), '小鹿 · 连接 Wi-Fi');
      assert.equal(await page.locator('.cat.deer').count(), 1);
      assert((await page.innerText('header')).includes('CYBER DEER'));
      assert(!(await page.innerText('body')).includes('小喵'));
      await page.screenshot({path:path.join(output,'device-setup-deer-mobile.png'),fullPage:true});
      await page.fill('#password','test-only-password'); await page.click('#save');
      await text(page,'#resultTitle','已保存');
      assert((await page.innerText('#resultCopy')).includes('小鹿'));
      assert(!(await page.innerText('#resultCopy')).includes('小喵'));
      await f.close(); ++completed;
    }
    // 主路径：默认隐藏高级设置、可见选网弹层、SSID 转义与去重、失败可重试。
    {
      const f = await fixture(); const {page} = f; await f.ready();
      assert.equal(await page.locator('#advanced').getAttribute('open'), null);
      assert.equal(await page.isHidden('#pairCard'), true);
      assert.equal(await page.inputValue('#password'), '');
      assert(await page.locator('#password').evaluate(input => parseFloat(getComputedStyle(input).fontSize) >= 16), 'iPhone 输入框字号应避免触发自动放大');
      await page.screenshot({path:path.join(output,'device-setup-mobile.png'),fullPage:true});
      await page.click('#choose');
      await text(page,'#scanStatus','正在查找');
      await page.waitForFunction(() => document.querySelectorAll('#nets button').length === 3);
      assert.equal(await page.locator('#nets img').count(),0);
      assert.equal(await page.evaluate(() => window.ssidAttack),undefined);
      assert.equal(await page.locator('#nets .net-name').first().textContent(),'Miao Home');
      await page.screenshot({path:path.join(output,'device-setup-networks-mobile.png'),fullPage:true});
      await page.locator('#nets button').filter({hasText:'Miao Home'}).click();
      assert.equal(await page.inputValue('#ssid'),'Miao Home');
      assert.equal(await page.isHidden('#wifiSheet'),true);
      await page.fill('#password','test-only-password');
      await page.click('#showPassword'); assert.equal(await page.getAttribute('#password','type'),'text');
      await page.click('#showPassword'); assert.equal(await page.getAttribute('#password','type'),'password');
      f.behavior.save = 'reject'; await page.click('#save'); await text(page,'#hint','USB');
      assert.equal(await page.isEnabled('#save'),true);
      let configure = f.writes.find(item => item.path === '/configure').form;
      assert.equal(configure.get('ssid'),'Miao Home'); assert.equal(configure.get('password'),'test-only-password');
      assert.equal(configure.get('keep_password'),'0'); assert.equal(configure.get('mode'),'accounts');
      assert.equal(configure.get('server_url'),'https://cat.example:443');
      assert.match(configure.get('request_id'),/^[0-9a-f]{32}$/);
      f.behavior.save = 'success'; await page.click('#save'); await text(page,'#resultTitle','设置已保存');
      assert.equal(await page.inputValue('#password'),'');
      assert.equal(await page.isHidden('#form'),true);
      assert.match(await page.innerText('#resultCopy'),/尝试连接/);
      assert(!/联网成功/.test(await page.innerText('#result')));
      assert.equal(await page.getAttribute('#consoleLink','href'),'https://cat.example/');
      await page.screenshot({path:path.join(output,'device-setup-saved-mobile.png'),fullPage:true});
      const count = f.writes.filter(item => item.path === '/configure').length;
      await page.waitForTimeout(300);
      assert.equal(f.writes.filter(item => item.path === '/configure').length,count);
      await f.close();
    }
    // 保留旧网络密码，避免只调整高级设置时误清空。
    {
      const f = await fixture(); await f.ready(); await f.page.click('#save');
      await text(f.page,'#resultTitle','已保存');
      const form = f.writes.find(item => item.path === '/configure').form;
      assert.equal(form.get('password'),''); assert.equal(form.get('keep_password'),'1');
      await f.close();
    }
    // 开放网络须通过扫描识别或手动明确勾选。
    {
      const f = await fixture(); await f.ready(); const {page} = f;
      await page.click('#choose'); await page.locator('#nets button').filter({hasText:'Guest'}).click();
      assert.equal(await page.isDisabled('#password'),true);
      await page.click('#save'); await text(page,'#resultTitle','已保存');
      const form = f.writes.find(item => item.path === '/configure').form;
      assert.equal(form.get('ssid'),'Guest'); assert.equal(form.get('password'),''); assert.equal(form.get('keep_password'),'0');
      await f.close();
    }
    // 已保存的开放网络再次提交设置时，不要求虚构密码。
    {
      const f = await fixture({state:{wifi_ssid:'Saved Guest',password_saved:false}}); await f.ready();
      assert.equal(await f.page.isDisabled('#password'),true);
      await f.page.click('#save'); await text(f.page,'#resultTitle','已保存');
      assert.equal(f.writes.find(item => item.path === '/configure').form.get('password'),'');
      await f.close();
    }
    // 扫描错误、空结果和重试；弹层关闭后仍能手动输入并保存。
    {
      const f = await fixture({behavior:{scan:'error'}}); await f.ready(); const {page} = f;
      await page.click('#choose'); await text(page,'#scanStatus','暂时无法');
      assert.equal(await page.isEnabled('#scan'),true);
      f.behavior.scan = 'empty'; await page.click('#scan'); await text(page,'#scanStatus','没有发现');
      f.behavior.scan = 'ok'; await page.click('#scan'); await page.locator('#nets button').first().waitFor();
      await page.keyboard.press('Escape'); assert.equal(await page.isHidden('#wifiSheet'),true);
      f.behavior.scan = 'hang'; await manual(page,'Hidden Wi-Fi','new-test-password');
      assert.equal(await page.isEnabled('#save'),true);
      await page.click('#save'); await text(page,'#resultTitle','已保存');
      assert.equal(f.writes.find(item => item.path === '/configure').form.get('ssid'),'Hidden Wi-Fi');
      await f.close();
    }
    // 真正挂起的扫描请求也会超时并恢复按钮。
    {
      const f = await fixture({behavior:{scan:'hang'}}); await f.ready();
      await f.page.click('#choose'); await text(f.page,'#scanStatus','超时');
      assert.equal(await f.page.isEnabled('#scan'),true);
      f.behavior.scan = 'ok'; await f.page.click('#scan'); await f.page.locator('#nets button').first().waitFor();
      await f.close();
    }
    // 后端反复返回 scanning:true 时，前端也有总截止时间。
    {
      const f = await fixture({behavior:{scan:'forever'},fastDeadline:true}); await f.ready();
      await f.page.click('#choose'); await text(f.page,'#scanStatus','扫描超时');
      assert.equal(await f.page.isEnabled('#scan'),true);
      await f.close();
    }
    // 保存响应丢失，但状态中的同一请求回执可确认已写入。
    {
      const f = await fixture({behavior:{save:'lost'}}); await f.ready();
      await f.page.click('#save'); await text(f.page,'#resultTitle','已保存');
      assert.equal(f.writes.filter(item => item.path === '/configure').length,1);
      await f.close();
    }
    // 无法确认结果时不谎报成功，也不自动再次提交密码；可重新检查并恢复表单。
    {
      const f = await fixture({behavior:{save:'ambiguous'}}); await f.ready(); const {page} = f;
      await page.fill('#password','test-only-new-password');
      await page.click('#showPassword');
      await page.click('#save'); await text(page,'#resultTitle','还未确认');
      assert.equal(await page.isHidden('#verify'),false);
      assert.equal(await page.inputValue('#password'),'');
      assert.equal(await page.getAttribute('#password','type'),'password');
      assert.equal(await page.getAttribute('#showPassword','aria-pressed'),'false');
      assert.equal(f.writes.filter(item => item.path === '/configure').length,1);
      await page.screenshot({path:path.join(output,'device-setup-uncertain-mobile.png'),fullPage:true});
      await page.click('#verify'); await text(page,'#hint','请核对');
      assert.equal(await page.isEnabled('#save'),true);
      f.behavior.save = 'success'; await page.fill('#password','another-test-password');
      await page.click('#save'); await text(page,'#resultTitle','已保存');
      await f.close();
    }
    // 配对信息只在真正联网且未绑定时显示；没有丢失原配对刷新功能。
    {
      const f = await fixture({state:{status:'unbound',wifi_connected:true,bound:false,pairing_code:'ABCD-EFGH',expires_in:150}});
      await f.ready(); await text(f.page,'#pair','ABCD-EFGH'); assert.equal(await f.page.isVisible('#pairCard'),true);
      await text(f.page,'#ttl','150'); await f.page.click('#refresh'); await text(f.page,'#pair','JKLM-NPQR');
      await f.close();
    }
    // 离线、身份失效、服务错误、已绑定或过期时不能显示上次缓存的配对码。
    for (const state of [
      {status:'wifi_offline',wifi_connected:false},
      {status:'credential_invalid'},
      {status:'server_offline'},
      {status:'bound',bound:true,binding_verified:false},
      {status:'unbound',expires_in:0}
    ]) {
      const f = await fixture({state:{wifi_connected:true,bound:false,expires_in:150,pairing_code:'ABCD-EFGH',...state}});
      await f.ready(); assert.equal(await f.page.isHidden('#pairCard'),true);
      if (state.status === 'credential_invalid') await text(f.page,'#state','通过 USB 更新身份');
      if (state.status === 'bound') await text(f.page,'#state','重新确认');
      assert.equal(f.writes.length,0,'状态变化不能自动清除网络或写入身份');
      await f.close();
    }
    // 字节数校验、本地模式保留、高级字段可修改；320px/桌面无横向溢出。
    {
      const f = await fixture({state:{status:'local',mode:'local',password_saved:false,wifi_ssid:''}});
      await f.ready(); const {page} = f;
      await manual(page,'中文名称中文名称中文名称','test-only-password');
      await page.click('#save'); await text(page,'#hint','32 字节');
      assert.equal(f.writes.filter(item => item.path === '/configure').length,0);
      await page.fill('#ssid','Lab Wi-Fi'); await page.fill('#password','short'); await page.click('#save');
      await text(page,'#hint','至少 8 位');
      await page.fill('#password','test-only-password'); await page.locator('#advanced summary').click();
      await page.fill('#server','http://192.168.2.105:8081');
      for (const width of [320,390,960]) {
        await page.setViewportSize({width,height:844});
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      }
      await page.click('#save'); await text(page,'#resultTitle','已保存');
      const form = f.writes.find(item => item.path === '/configure').form;
      assert.equal(form.get('mode'),'local'); assert.equal(form.get('server_url'),'http://192.168.2.105:8081');
      await f.close();
    }
    // 初次读取中断可恢复，未初始化前不允许提交空设置。
    {
      const f = await fixture({behavior:{status:'hang'}});
      await f.page.locator('#offline').waitFor(); assert.equal(await f.page.isDisabled('#save'),true);
      f.behavior.status = 'ok'; await f.page.click('#reconnect'); await f.ready();
      assert.equal(await f.page.isHidden('#offline'),true);
      await f.close();
    }
    // 慢状态请求不会与下一个轮询重叠，且不会覆盖用户正在编辑的字段。
    {
      const f = await fixture({behavior:{status:'slow'}}); await f.ready();
      await f.page.locator('#advanced summary').click();
      await f.page.fill('#server','https://edited.example:8443');
      await f.page.waitForTimeout(6000);
      assert(f.statusCalls >= 2); assert.equal(await f.page.evaluate(() => window.testMaxStatus),1);
      assert.equal(await f.page.inputValue('#server'),'https://edited.example:8443');
      await f.close();
    }
    console.log(completed + ' 组配网页场景通过：移动布局、选网与去重/XSS、CSRF/POST、扫描失败/挂起/截止时间、密码保留、保存回执/丢失/未确认、配对与本地模式、轮询互斥。仅使用模拟 API，未验收手机与实物。');
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
