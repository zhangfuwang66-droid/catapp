/* Focused browser checks with fictional data; no preview, cloud, models or hardware. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require('playwright');
const {createFixture}=require('./admin_console_fixture.cjs');
const output=path.resolve(__dirname,'../../artifacts/admin-ui');
const fixture=createFixture();
const deferred=() => { let resolve; const promise=new Promise(done => { resolve=done; }); return {promise,resolve}; };
const channels=color => color.match(/[\d.]+/g).slice(0,3).map(Number);
const luminance=color => channels(color).map(part => { const value=part/255; return value<=.04045 ? value/12.92 : ((value+.055)/1.055)**2.4; }).reduce((sum,value,i) => sum+value*[.2126,.7152,.0722][i],0);
const contrast=(a,b) => (Math.max(luminance(a),luminance(b))+.05)/(Math.min(luminance(a),luminance(b))+.05);
const ready=page => page.waitForFunction(() => !document.getElementById('shell').hidden && document.getElementById('pageFeedback').hidden && document.querySelector('.view:not([hidden])')?.getAttribute('aria-busy') === 'false');
const nav=async(page,name) => { await page.getByRole('button',{name,exact:true}).click(); await ready(page); };
const colors=page => page.evaluate(() => {
  const sidebar=getComputedStyle(document.getElementById('sidebar')), active=getComputedStyle(document.querySelector('.nav-item.active'));
  return {background:sidebar.backgroundColor,ink:sidebar.color,activeBackground:active.backgroundColor,activeInk:active.color};
});

(async() => {
  let browser;
  try {
    fs.mkdirSync(output,{recursive:true});
    const url=await fixture.start(), state=fixture.state, errors=[];
    browser=await chromium.launch({headless:true,...(process.env.BROWSER_EXECUTABLE ? {executablePath:process.env.BROWSER_EXECUTABLE} : {})});
    const context=await browser.newContext({viewport:{width:1440,height:1000},colorScheme:'light',reducedMotion:'no-preference'});
    await context.addInitScript(() => { localStorage.setItem('admin-theme','dark'); });
    await context.route('**/admin',async route => {
      const response=await route.fetch();
      await route.fulfill({response,headers:{...response.headers(),'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'"}});
    });
    await context.route('**/admin/login?*',route => route.fulfill({contentType:'text/html',body:'<!doctype html><html lang="zh-CN"><h1>后台登录</h1></html>'}));
    const page=await context.newPage(); page.on('pageerror',error => errors.push(error.message));
    // 暂缓CSS响应，证明保存的主题在首屏样式到达前已恢复，且无内联脚本。
    const cssRequested=deferred(), cssRelease=deferred();
    await page.route('**/web/admin/admin.css',async route => { cssRequested.resolve(); await cssRelease.promise; await route.continue(); });
    const opened=page.goto(url+'/admin'); opened.catch(() => {}); await cssRequested.promise;
    await page.waitForFunction(() => document.documentElement.dataset.theme === 'dark',null,{polling:20});
    assert.equal(await page.locator('html').getAttribute('data-theme'),'dark','首次样式加载前恢复保存的深色主题');
    cssRelease.resolve(); await opened; await ready(page);
    await page.unroute('**/web/admin/admin.css');
    assert.equal(await page.locator('script:not([src])').count(),0);
    assert.equal(state.reads.includes('/api/auth/session'),false,'管理CSRF仅取后台会话');
    const dark=await colors(page);
    assert.ok(luminance(dark.background)<.04,'深色侧栏跟随深色主题');
    assert.ok(contrast(dark.ink,dark.background)>=4.5);
    assert.ok(contrast(dark.activeInk,dark.activeBackground)>=4.5);
    await page.waitForTimeout(230);
    await page.screenshot({path:path.join(output,'polish-desktop-dark.png'),fullPage:true});
    await page.click('#themeToggle'); await page.waitForTimeout(230);
    const light=await colors(page);
    assert.ok(luminance(light.background)>.85,'浅色侧栏跟随浅色主题');
    assert.ok(contrast(light.ink,light.background)>=4.5);
    assert.ok(contrast(light.activeInk,light.activeBackground)>=4.5);
    await page.keyboard.press('Tab'); await page.locator('[data-view=users]').focus();
    assert.equal(await page.locator('[data-view=users]').evaluate(el => el.matches(':focus-visible')),true);
    assert.ok(await page.locator('[data-view=users]').evaluate(el => parseFloat(getComputedStyle(el).outlineWidth)>=2));
    await page.screenshot({path:path.join(output,'polish-desktop-light.png'),fullPage:true});

    await nav(page,'用户'); await nav(page,'设备');
    let requestNumber=0;
    const received=[deferred(),deferred()], releases=[deferred(),deferred()], sent=[deferred(),deferred()];
    state.onRead=async(request,res,reply) => {
      if(request.pathname !== '/api/admin/users') return false;
      const number=requestNumber++;
      received[number].resolve(); await releases[number].promise;
      reply(res,200,{items:[{...state.users[1],username:number ? '最新账号快照' : '过期账号快照'}],total:1,page:1,page_size:20});
      sent[number].resolve(); return true;
    };
    const during=await page.evaluate(() => {
      document.querySelector('[data-view=users]').click();
      return {hidden:document.getElementById('view-users').hidden,loading:!document.getElementById('viewLoading').hidden};
    });
    assert.deepEqual(during,{hidden:true,loading:true},'切页同步隐藏旧快照');
    await received[0].promise;
    await page.waitForTimeout(150);
    assert.equal(await page.locator('#usersBody').isVisible(),false);
    await page.screenshot({path:path.join(output,'polish-loading.png'),fullPage:true});
    await nav(page,'设备');
    await page.getByRole('button',{name:'用户',exact:true}).click(); await received[1].promise;
    releases[1].resolve(); await ready(page);
    assert.match(await page.locator('#usersBody').innerText(),/最新账号快照/);
    releases[0].resolve(); await sent[0].promise; await sent[1].promise;
    assert.doesNotMatch(await page.locator('#usersBody').innerText(),/过期账号快照|example-member/);
    assert.equal(await page.locator('.view:not([hidden])').getAttribute('id'),'view-users');
    state.onRead=null;
    state.fail={path:'/api/admin/users',status:503}; await page.click('#refreshButton');
    await page.locator('#pageFeedback.error').waitFor();
    assert.equal(await page.locator('#usersBody').isVisible(),false,'读取失败也不把旧内容当成新结果');
    await page.getByRole('button',{name:'重新加载',exact:true}).click(); await ready(page);

    await nav(page,'存储概况');
    assert.match(await page.locator('.privacy-note').innerText(),/仅展示资料与记忆的数量、容量和处理状态/);
    assert.match(await page.locator('.privacy-note').innerText(),/不提供私人资料、对话或记忆正文/);
    assert.equal(await page.locator('#view-storage input,#view-storage textarea,#view-storage a[download]').count(),0);
    await page.waitForTimeout(230);
    await page.screenshot({path:path.join(output,'polish-storage-privacy.png'),fullPage:true});
    await nav(page,'设备');
    await page.locator('#devicesBody tr').filter({hasText:'cat-history'}).getByRole('button',{name:'详情',exact:true}).click();
    await page.locator('#detailContent .detail-section').first().waitFor();
    assert.match(await page.locator('#detailDialog').evaluate(el => getComputedStyle(el).transitionProperty),/opacity/);
    await page.waitForTimeout(230);
    await page.screenshot({path:path.join(output,'polish-detail-drawer.png'),fullPage:true});
    await page.click('#detailClose'); await page.locator('#detailDialog').waitFor({state:'hidden'});

    // 定时器被后台节流时从performance.now重新取差值，而非seconds++。
    const clockContext=await browser.newContext({viewport:{width:390,height:844},reducedMotion:'reduce'});
    const clockPage=await clockContext.newPage(); clockPage.on('pageerror',error => errors.push(error.message));
    await clockPage.clock.install({time:new Date('2026-11-11T00:00:00Z')});
    await clockPage.goto(url+'/admin'); await ready(clockPage);
    assert.equal(await clockPage.locator('#metricUptime').innerText(),'01:00:00');
    await clockPage.clock.runFor(2100);
    assert.equal(await clockPage.locator('#metricUptime').innerText(),'01:00:02');
    await clockPage.clock.fastForward(15*60*1000);
    assert.equal(await clockPage.locator('#metricUptime').innerText(),'01:15:02','后台恢复一次跳到真实经过时间');
    await clockPage.clock.setSystemTime(new Date('2027-01-01T00:00:00Z'));
    await clockPage.clock.runFor(1000);
    assert.equal(await clockPage.locator('#metricUptime').innerText(),'01:15:03','修改系统时间不影响运行时长');
    assert.equal(await clockPage.locator('#sidebar').evaluate(el => el.inert),true,'移动端收起导航不进入键盘顺序');
    await clockPage.click('#menuToggle');
    assert.equal(await clockPage.locator('.main-wrap').evaluate(el => el.inert),true,'展开导航时背景不接受键盘输入');
    await clockPage.keyboard.press('Escape');
    assert.equal(await clockPage.locator('#menuToggle').getAttribute('aria-expanded'),'false');
    assert.equal(await clockPage.evaluate(() => document.activeElement.id),'menuToggle');
    // 模拟服务长时间运行并刷新基准；长天数与秒钟在手机上独立换行。
    await clockPage.route('**/api/admin/dashboard',route => route.fulfill({json:{summary:{uptime_seconds:12345*86400+4*3600+3*60+2}}}));
    await clockPage.click('#refreshButton'); await ready(clockPage);
    assert.match(await clockPage.locator('#metricUptime').innerText(),/12345天/);
    assert.match(await clockPage.locator('#metricUptime').innerText(),/04:03:02/);
    assert.equal(await clockPage.locator('#metricUptime').getAttribute('aria-label'),'12345天4小时3分2秒');
    assert.equal(await clockPage.locator('#metricUptime').evaluate(el => el.scrollWidth<=el.clientWidth),true);
    assert.equal(await clockPage.evaluate(() => document.documentElement.scrollWidth<=innerWidth),true);
    await clockPage.screenshot({path:path.join(output,'polish-mobile-long-uptime-light.png'),fullPage:true});
    await clockPage.click('#themeToggle');
    await clockPage.screenshot({path:path.join(output,'polish-mobile-long-uptime-dark.png'),fullPage:true});
    await clockPage.click('#menuToggle');
    assert.deepEqual(await clockPage.locator('#sidebar').evaluate(el => ({animation:getComputedStyle(el).animationName,transition:getComputedStyle(el).transitionDuration})),{animation:'none',transition:'0s'});
    await clockPage.screenshot({path:path.join(output,'polish-mobile-navigation-dark.png'),fullPage:true});
    await clockPage.getByRole('button',{name:'设备',exact:true}).click(); await ready(clockPage);
    await clockPage.locator('#devicesBody tr').first().getByRole('button',{name:'详情',exact:true}).click();
    await clockPage.locator('#detailContent .detail-section').first().waitFor();
    assert.equal(await clockPage.locator('#detailDialog').evaluate(el => getComputedStyle(el).transitionDuration),'0s');
    assert.equal(await clockPage.locator('#detailDialog').evaluate(el => el.scrollWidth<=el.clientWidth),true);
    await clockPage.click('#detailClose');
    await clockContext.close();

    // 成功、失效、撤权和网络失败均只使用固定后台入口，不依赖普通登录CSRF。
    let logoutStatus=503, logoutReceived=[];
    await page.route('**/api/admin/auth/logout',async route => {
      logoutReceived.push({method:route.request().method(),body:route.request().postData(),headers:await route.request().allHeaders()});
      await route.fulfill({status:logoutStatus,json:logoutStatus===200 ? {ok:true} : {detail:'模拟退出响应'}});
    });
    await page.click('#logoutButton'); await page.locator('#toast.error').waitFor();
    assert.equal(await page.locator('#logoutButton').isEnabled(),true);
    assert.equal(page.url().includes('/admin/login'),false);
    logoutStatus=200; await page.click('#logoutButton'); await page.waitForURL('**/admin/login?notice=logout');
    assert.equal(logoutReceived.length,2);
    for(const request of logoutReceived) {
      assert.equal(request.method,'POST'); assert.equal(request.body,null);
      assert.equal(request.headers['x-csrf-token'],'fixture-csrf'); assert.equal(request.headers.origin,url);
    }
    await page.goto(url+'/admin'); await ready(page);
    state.fail={path:'/api/admin/dashboard',status:403}; await page.click('#refreshButton');
    await page.locator('#gateLogout').waitFor();
    await page.click('#gateLogout'); await page.waitForURL('**/admin/login?notice=logout');
    assert.equal(logoutReceived.length,3,'撤权后仍能退出有效会话');
    await page.goto(url+'/admin'); await ready(page);
    logoutStatus=401; await page.click('#logoutButton'); await page.waitForURL('**/admin/login?notice=expired');
    await page.goto(url+'/admin'); await ready(page);
    state.fail={path:'/api/admin/dashboard',status:401}; await page.click('#refreshButton');
    await page.waitForURL('**/admin/login?notice=expired');
    assert.deepEqual(errors,[]);
    console.log('admin polish UI: first-paint themes/CSP, sidebar contrast/focus, rapid navigation races, private metadata, drawers/reduced motion, monotonic seconds/mobile and logout contract passed');
  } finally { if(browser) await browser.close(); fixture.close(); }
})().catch(error => { console.error(error); process.exitCode=1; });
