/* Independent experience acceptance against this worktree's real app and temporary data. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const {chromium} = require('playwright');

const root = path.resolve(__dirname, '..');
const output = path.resolve(root, '../artifacts/admin-experience');
const fixture = spawn(process.env.PYTHON_EXECUTABLE || 'python',
  ['-u', path.join(__dirname, 'admin_experience_fixture.py')],
  {cwd: root, env: {...process.env, PYTHONPATH: root, PYTHONIOENCODING: 'utf-8', PYTHONDONTWRITEBYTECODE: '1'},
    stdio: ['pipe', 'pipe', 'pipe']});
let fixtureErrors = '';
fixture.stderr.on('data', data => { fixtureErrors += data.toString(); });
const fixtureReady = new Promise((resolve, reject) => {
  let buffer = '';
  const timer = setTimeout(() => reject(new Error('独立体验夹具未就绪: ' + fixtureErrors)), 30000);
  fixture.on('error', error => { clearTimeout(timer); reject(error); });
  fixture.on('exit', code => { clearTimeout(timer); reject(new Error('夹具退出 ' + code + ': ' + fixtureErrors)); });
  fixture.stdout.on('data', data => {
    buffer += data.toString();
    const end = buffer.indexOf('\n');
    if (end >= 0) {
      clearTimeout(timer);
      try { resolve(JSON.parse(buffer.slice(0, end))); } catch (error) { reject(error); }
    }
  });
});

function elapsedSeconds(text) {
  const value = text.trim();
  const colon = value.match(/^(\d+):(\d{2}):(\d{2})$/);
  if (colon) return Number(colon[1]) * 3600 + Number(colon[2]) * 60 + Number(colon[3]);
  if (!/\d+\s*秒/.test(value)) return null;
  const scale = {'天': 86400, '小时': 3600, '时': 3600, '分钟': 60, '分': 60, '秒': 1};
  return [...value.matchAll(/(\d+)\s*(天|小时|时|分钟|分|秒)/g)]
    .reduce((total, match) => total + Number(match[1]) * scale[match[2]], 0);
}

(async () => {
  let browser;
  const pageErrors = [], adminBodies = [], checks = [];
  try {
    const {url, owner_id: ownerId} = await fixtureReady;
    fs.mkdirSync(output, {recursive: true});
    browser = await chromium.launch({headless: true,
      ...(process.env.BROWSER_EXECUTABLE ? {executablePath: process.env.BROWSER_EXECUTABLE} : {})});
    async function pageFor(context) {
      const page = await context.newPage();
      page.on('pageerror', error => pageErrors.push(error.message));
      page.on('response', response => {
        if (new URL(response.url()).pathname.startsWith('/api/admin/')) {
          // 立即接住导航取消，结束前逐项核查，避免未处理的 Promise 拒绝。
          adminBodies.push(response.text().then(body => ({body}), error => ({error, page})));
        }
      });
      return page;
    }
    async function login(page, username, password = 'temporary-experience-password') {
      await page.goto(url + '/admin/login');
      const form = page.locator('form').filter({has: page.locator('input[type=password]')});
      await form.locator('input[autocomplete=username]').fill(username);
      await form.locator('input[type=password]').fill(password);
      const response = page.waitForResponse(response => new URL(response.url()).pathname === '/api/admin/auth/login'
        && response.request().method() === 'POST');
      await form.locator('button[type=submit]').click();
      return response;
    }
    async function ready(page) {
      await page.locator('#shell').waitFor({state: 'visible'});
      await page.waitForFunction(() => document.querySelector('.view:not([hidden])')?.getAttribute('aria-busy') === 'false'
        && document.getElementById('pageFeedback').hidden);
    }
    async function noOverflow(page) {
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      const dialog = page.locator('dialog[open]');
      if (await dialog.count()) {
        const bounds = await dialog.boundingBox();
        const viewport = page.viewportSize();
        assert.ok(bounds.x >= -1 && bounds.x + bounds.width <= viewport.width + 1, JSON.stringify(bounds));
        assert.ok(bounds.y >= -1 && bounds.y + bounds.height <= viewport.height + 1, JSON.stringify(bounds));
      }
    }
    async function assertReducedMotion(page, selector) {
      const offenders = await page.locator(selector).evaluateAll(elements => {
        const seconds = value => value.split(',').map(item => parseFloat(item) * (item.trim().endsWith('ms') ? .001 : 1));
        return elements.flatMap(element => ['', '::before', '::after'].flatMap(pseudo => {
          const style = getComputedStyle(element, pseudo || null);
          return [...seconds(style.animationDuration), ...seconds(style.transitionDuration)].some(value => value > .001)
            ? [element.tagName + '#' + element.id + pseudo] : [];
        }));
      });
      assert.deepEqual(offenders, [], 'reduced-motion 下不能保留长动效');
    }
    async function finishAnimations(locator) {
      await locator.evaluate(async element => {
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        await Promise.all(element.getAnimations().map(animation => animation.finished.catch(() => {})));
      });
    }

    const ordinary = await browser.newContext();
    const ordinaryPage = await pageFor(ordinary);
    const anonymous = await ordinary.request.get(url + '/admin', {maxRedirects: 0});
    assert.equal(anonymous.status(), 303);
    assert.equal(anonymous.headers().location, '/admin/login');
    assert.equal((await login(ordinaryPage, 'experience-admin', 'incorrect-experience-password')).status(), 401);
    await ordinaryPage.locator('[role=alert]:visible').first().waitFor();
    assert.equal(new URL(ordinaryPage.url()).pathname, '/admin/login');
    assert.match(await ordinaryPage.locator('[role=alert]:visible').first().innerText(), /账号|密码/);
    const ordinaryResult = await login(ordinaryPage, 'experience-owner');
    assert.equal(ordinaryResult.status(), 403);
    assert.equal(ordinaryResult.headers()['set-cookie'], undefined);
    await ordinaryPage.locator('[role=alert]:visible').first().waitFor();
    assert.match(await ordinaryPage.locator('[role=alert]:visible').first().innerText(), /管理员|权限/);
    assert.equal(new URL(ordinaryPage.url()).pathname, '/admin/login');
    assert.equal((await ordinary.request.get(url + '/api/admin/dashboard')).status(), 401);
    checks.push('匿名跳独立登录、错误密码401、普通账号403且不签发会话');
    await ordinary.close();

    const context = await browser.newContext({viewport: {width: 1440, height: 1000}, reducedMotion: 'no-preference'});
    const page = await pageFor(context);
    const successfulLogin = await login(page, 'experience-admin');
    assert.equal(successfulLogin.status(), 200);
    assert.equal(Object.hasOwn(await successfulLogin.json(), 'redirect'), false);
    await page.waitForURL(current => current.pathname === '/admin');
    await ready(page);
    const direct = await context.request.get(url + '/admin/login', {maxRedirects: 0});
    assert.equal(direct.status(), 303);
    assert.equal(direct.headers().location, '/admin');
    const appLink = page.locator('a[href="/"]');
    assert.ok(await appLink.count() > 0);
    await appLink.first().waitFor({state: 'visible'});
    checks.push('管理员表单登录直达、已有会话直达、返回应用入口可见');

    const firstText = await page.locator('#metricUptime').innerText();
    const firstSeconds = elapsedSeconds(firstText);
    assert.notEqual(firstSeconds, null, '运行时长必须显示到秒: ' + firstText);
    const sample = await (await context.request.get(url + '/api/admin/dashboard')).json();
    assert.ok(Math.abs(firstSeconds - sample.summary.uptime_seconds) <= 4, firstText);
    let previousText = firstText, previousSeconds = firstSeconds;
    const ticks = [];
    for (let index = 0; index < 3; index++) {
      const started = Date.now();
      await page.waitForFunction(previous => document.getElementById('metricUptime').textContent !== previous,
        previousText, {timeout: 2500});
      previousText = await page.locator('#metricUptime').innerText();
      const seconds = elapsedSeconds(previousText);
      assert.ok(seconds > previousSeconds && seconds - previousSeconds <= 2, previousText);
      ticks.push(Date.now() - started);
      previousSeconds = seconds;
    }
    assert.ok(ticks.slice(1).every(milliseconds => milliseconds >= 400 && milliseconds <= 2300), JSON.stringify(ticks));
    await page.reload(); await ready(page);
    const refreshed = await (await context.request.get(url + '/api/admin/dashboard')).json();
    assert.ok(Math.abs(elapsedSeconds(await page.locator('#metricUptime').innerText())
      - refreshed.summary.uptime_seconds) <= 4);
    checks.push('运行时长来自真实服务、每秒推进、刷新重新校准');

    if (await page.locator('html').getAttribute('data-theme') !== 'light') await page.click('#themeToggle');
    await finishAnimations(page.locator('.sidebar'));
    const lightSidebar = await page.locator('.sidebar').evaluate(element => getComputedStyle(element).backgroundColor);
    const lightPage = await page.locator('.panel').first().evaluate(element => getComputedStyle(element).backgroundColor);
    await page.screenshot({path: path.join(output, 'desktop-light.png'), fullPage: true});
    await page.click('#themeToggle');
    assert.equal(await page.locator('html').getAttribute('data-theme'), 'dark');
    await finishAnimations(page.locator('.sidebar'));
    const darkSidebar = await page.locator('.sidebar').evaluate(element => getComputedStyle(element).backgroundColor);
    assert.notEqual(lightSidebar, darkSidebar, '侧栏必须随主题切换');
    assert.notEqual(lightPage, await page.locator('.panel').first().evaluate(element => getComputedStyle(element).backgroundColor),
      '页面面板必须随主题切换');
    await page.screenshot({path: path.join(output, 'desktop-dark.png'), fullPage: true});
    await page.reload(); await ready(page);
    assert.equal(await page.locator('html').getAttribute('data-theme'), 'dark');
    checks.push('浅深主题包含侧栏并在刷新后保留');

    await page.evaluate(() => {
      window.__experienceMotion = [];
      for (const type of ['animationstart', 'transitionrun']) {
        document.addEventListener(type, event => {
          const target = event.target;
          window.__experienceMotion.push({type, id: target.id, view: Boolean(target.closest('.view')),
            dialog: target.closest('dialog')?.id});
        });
      }
    });
    await page.getByRole('button', {name: '用户', exact: true}).click();
    await ready(page);
    await page.waitForFunction(() => window.__experienceMotion.some(event => event.view), null, {timeout: 3000});
    await page.evaluate(() => { window.__experienceMotion = []; });
    await page.click('#createUser');
    await page.locator('#editorDialog').waitFor({state: 'visible'});
    await page.waitForFunction(() => window.__experienceMotion.some(event => event.dialog === 'editorDialog'),
      null, {timeout: 3000});
    const darkDialog = await page.locator('#editorDialog').evaluate(element => getComputedStyle(element).backgroundColor);
    await page.click('#editorCancel');
    await page.click('#themeToggle');
    await page.click('#createUser');
    await page.locator('#editorDialog').waitFor({state: 'visible'});
    const lightDialog = await page.locator('#editorDialog').evaluate(element => getComputedStyle(element).backgroundColor);
    assert.notEqual(darkDialog, lightDialog);
    await page.click('#editorCancel');
    checks.push('正常模式切页和弹窗有动效、弹窗主题同步');

    // 只延迟真实 HTTP 请求的放行，不伪造 API 响应；检查晚返回数据不会覆盖当前页。
    await page.getByRole('button', {name: '概览', exact: true}).click(); await ready(page);
    let releaseUsers;
    const usersGate = new Promise(resolve => { releaseUsers = resolve; });
    const lateRequests = [];
    const delayUsers = async route => {
      const pending = usersGate.then(() => route.continue()).catch(error => {
        assert.ok(/aborted|closed|handled|invalid interception/i.test(error.message), error.message);
      });
      lateRequests.push(pending);
      await pending;
    };
    await page.route('**/api/admin/users?*', delayUsers);
    try {
      const observedUsers = page.waitForRequest(request => new URL(request.url()).pathname === '/api/admin/users',
        {timeout: 8000});
      await page.getByRole('button', {name: '用户', exact: true}).click();
      await observedUsers;
      for (const name of ['设备', '资料与记忆', '备份', '概览', '设备']) {
        await page.getByRole('button', {name, exact: true}).click();
      }
      await ready(page);
    } finally {
      releaseUsers();
      await page.unroute('**/api/admin/users?*', delayUsers);
    }
    await Promise.all(lateRequests);
    await page.waitForFunction(() => document.getElementById('view-users').getAttribute('aria-busy') === 'false');
    assert.equal(await page.locator('.view:not([hidden])').count(), 1);
    assert.equal(await page.locator('.view:not([hidden])').getAttribute('id'), 'view-devices');
    assert.equal(await page.locator('#pageTitle').innerText(), '设备');
    assert.equal(await page.locator('#pageFeedback').isVisible(), false);
    checks.push('快速切换页面后晚返回的真实请求不覆盖当前页面');

    await page.getByRole('button', {name: '用户', exact: true}).click(); await ready(page);

    await page.emulateMedia({reducedMotion: 'reduce'});
    await page.click('#createUser');
    await page.locator('#editorDialog').waitFor({state: 'visible'});
    await assertReducedMotion(page, '.view, .sidebar, dialog[open]');
    await page.click('#editorCancel');
    checks.push('reduced-motion 关闭页面、侧栏和弹窗长动效');

    for (const width of [320, 390]) {
      const mobile = await browser.newContext({viewport: {width, height: 844}, isMobile: true,
        hasTouch: true, reducedMotion: 'reduce'});
      const mp = await pageFor(mobile);
      await mp.goto(url + '/admin/login');
      await mp.locator('input[autocomplete=username]').waitFor(); await noOverflow(mp);
      await mp.locator('a[href="/"]').first().waitFor({state: 'visible'});
      await assertReducedMotion(mp, 'body, .login-card, .scene, .scene *, dialog');
      await mp.screenshot({path: path.join(output, 'mobile-' + width + '-login.png'), fullPage: true});
      await mp.getByRole('button', {name: '登录遇到问题？', exact: true}).click();
      await mp.locator('dialog[open]').waitFor(); await noOverflow(mp);
      await mp.getByRole('button', {name: '知道了', exact: true}).click();
      await mobile.addCookies(await context.cookies());
      await mp.goto(url + '/admin'); await ready(mp); await noOverflow(mp);
      await mp.click('#menuToggle');
      await mp.getByRole('button', {name: '用户', exact: true}).click();
      await ready(mp); await mp.click('#createUser');
      await mp.locator('#editorDialog').waitFor({state: 'visible'});
      await noOverflow(mp);
      await mp.locator('#editorCancel').scrollIntoViewIfNeeded();
      assert.equal(await mp.locator('#editorCancel').isEnabled(), true);
      await mp.screenshot({path: path.join(output, 'mobile-' + width + '-dialog.png')});
      await mp.click('#editorCancel');
      await mobile.close();
    }
    checks.push('320/390手机登录、后台和弹窗无水平溢出，减少动态偏好生效、操作可达');

    for (const endpoint of ['session', 'dashboard', 'overview', 'users', 'users/' + ownerId, 'devices',
      'devices/cat-experience', 'storage', 'backups', 'backups/history', 'audit']) {
      const response = await context.request.get(url + '/api/admin/' + endpoint);
      assert.equal(response.status(), 200, endpoint + ': ' + await response.text());
      assert.ok(!(await response.text()).includes('PRIVATE_EXPERIENCE_'), endpoint);
    }
    const html = await page.content();
    assert.ok(!html.includes('PRIVATE_EXPERIENCE_'));
    for (const result of await Promise.all(adminBodies)) {
      if (result.error) assert.ok(result.page.isClosed() || /aborted|closed|No resource/i.test(result.error.message), result.error.message);
      else assert.ok(!result.body.includes('PRIVATE_EXPERIENCE_'));
    }
    assert.deepEqual(pageErrors, []);
    checks.push('管理页面与全部读取响应无私人正文、运行时和模型访问受阻');

    const sessionCookie = (await context.cookies()).find(cookie => cookie.name === 'cybermiao_session');
    assert.ok(sessionCookie?.httpOnly && sessionCookie.sameSite === 'Strict');
    assert.equal(await page.evaluate(token => JSON.stringify({local: {...localStorage}, session: {...sessionStorage}}).includes(token),
      sessionCookie.value), false, '会话令牌不能进入网页存储');
    const logout = page.waitForResponse(response => new URL(response.url()).pathname === '/api/admin/auth/logout'
      && response.request().method() === 'POST');
    await page.getByRole('button', {name: /退出/}).first().click();
    assert.equal((await logout).status(), 200);
    await page.waitForURL(current => current.pathname === '/admin/login');
    assert.equal((await context.request.get(url + '/api/admin/session')).status(), 401);
    assert.ok(!(await context.cookies()).some(cookie => cookie.name === 'cybermiao_session'));
    const expiredCookie = await context.request.get(url + '/api/admin/session', {
      headers: {Cookie: 'cybermiao_session=' + sessionCookie.value}});
    assert.equal(expiredCookie.status(), 401);
    checks.push('退出返回独立登录、清除Cookie且旧令牌失效，令牌未进入网页存储');
    console.log(JSON.stringify({ok: true, checks, screenshots: output}));
    await context.close();
  } finally {
    await browser?.close();
    fixture.stdin.end();
    if (fixture.exitCode === null) await once(fixture, 'exit').catch(() => {});
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
