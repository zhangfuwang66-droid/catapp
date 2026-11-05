/* 设置 NODE_PATH（playwright）与 BROWSER_EXECUTABLE 后运行；只模拟页面 API，不访问设备。 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');

async function main() {
  const source = fs.readFileSync(path.resolve(__dirname, '../esp32_servo_control/SetupPage.h'), 'utf8');
  const html = source.match(/R"CMIAOHTML\(([\s\S]*?)\)CMIAOHTML"/)[1];
  const output = path.resolve(__dirname, '../../artifacts/web-tests');
  fs.mkdirSync(output, {recursive:true});
  const browser = await chromium.launch({headless:true, executablePath:process.env.BROWSER_EXECUTABLE});
  try {
    const context = await browser.newContext({viewport:{width:390,height:844}});
    const failures = [], writes = [];
    const maliciousSsid = '<img onerror="window.ssidAttack=1">';
    let scans = 0, rejectSave = true, code = 'ABCD-EFGH', variant = 'unbound';
    await context.route('http://setup.test/**', async route => {
      const request = route.request(), url = new URL(request.url());
      let status = 200, data;
      if (url.pathname === '/') {
        await route.fulfill({contentType:'text/html',body:html,
          headers:{'Content-Security-Policy':"default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'none'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"}});
        return;
      }
      if (request.method() === 'POST') {
        assert.equal(request.headers()['x-cybermiao-csrf'], 'test-only-csrf');
        assert(!url.search, '密码与设置不得进入网址');
        writes.push({path:url.pathname,body:request.postData()});
      }
      if (url.pathname === '/status') data = {csrf_token:'test-only-csrf',device_id:'cat-test',
        wifi_ssid:'Test WiFi',server_url:'https://cat.example:443',mode:variant === 'local' ? 'local' : 'accounts',
        credential_configured:true,status:variant,pairing_code:variant === 'unbound' ? code : '',
        expires_in:variant === 'unbound' ? 150 : 0,bound:variant === 'bound'};
      else if (url.pathname === '/scan') data = ++scans === 1 ? {scanning:true,networks:[]} :
        {scanning:false,networks:[{ssid:maliciousSsid,rssi:-55}]};
      else if (url.pathname === '/pairing') { code = 'JKLM-NPQR'; data = {ok:true}; }
      else if (url.pathname === '/configure') {
        if (rejectSave) { status = 400; data = {error:'请先通过 USB 配置设备身份'}; }
        else data = {message:'设置已保存，即将重启。'};
      } else throw Error('未知页面请求 ' + url.pathname);
      await route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
    });
    const page = await context.newPage();
    page.on('pageerror', error => failures.push(error.message));
    await page.goto('http://setup.test/');
    await page.waitForFunction(() => document.querySelector('#pair').textContent === 'ABCD-EFGH');
    assert.equal(await page.inputValue('#ssid'), 'Test WiFi');
    assert.equal(await page.inputValue('#password'), '');
    assert.match(await page.locator('#ttl').innerText(), /150/);
    await page.click('#scan');
    await page.locator('#nets button').waitFor();
    assert.equal(await page.locator('#nets img').count(), 0);
    await page.click('#nets button');
    assert.equal(await page.inputValue('#ssid'), maliciousSsid);
    assert.equal(await page.evaluate(() => window.ssidAttack), undefined);
    await page.click('#refresh');
    await page.waitForFunction(() => document.querySelector('#pair').textContent === 'JKLM-NPQR');
    await page.fill('#ssid', 'My Test WiFi');
    await page.fill('#password', 'test-only-wifi-password');
    await page.fill('#server', 'https://cat.example:8443');
    await page.click('#save');
    await page.waitForFunction(() => document.querySelector('#hint').textContent.includes('USB'));
    assert.equal(await page.isEnabled('#save'), true);
    const form = new URLSearchParams(writes.find(item => item.path === '/configure').body);
    assert.equal(form.get('password'), 'test-only-wifi-password');
    assert.equal(form.get('mode'), 'accounts');
    assert.equal(form.get('server_url'), 'https://cat.example:8443');
    rejectSave = false;
    await page.click('#save');
    await page.waitForFunction(() => document.querySelector('#hint').textContent.includes('已保存'));
    assert.equal(await page.inputValue('#password'), '');
    assert.equal(await page.isDisabled('#save'), true);
    variant = 'bound';
    await page.reload();
    await page.waitForFunction(() => document.querySelector('#state').textContent === '已绑定账号');
    assert.equal(await page.isHidden('#refresh'), true);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({path:path.join(output,'device-setup-mobile.png'),fullPage:true});
    variant = 'local';
    await page.reload();
    await page.waitForFunction(() => document.querySelector('#state').textContent === '本地调试模式');
    assert.equal(await page.inputValue('#mode'), 'local');
    assert.equal(await page.isHidden('#refresh'), true);
    assert.deepEqual(failures, []);
    console.log('设备设置页通过：状态/倒计时、异步扫描与 SSID 转义、CSRF/POST、保存失败与重试、绑定/本地模式、手机布局。使用模拟 API，未连接硬件。');
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
