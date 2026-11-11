/* Browser trust boundaries with temporary data and synthetic payloads; no model or hardware. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {spawn}=require('node:child_process');
const {once}=require('node:events');
const {chromium}=require('playwright');
const {createFixture}=require('./admin_console_fixture.cjs');
const root=path.resolve(__dirname,'..'), output=path.resolve(root,'../artifacts/browser-content-security');
const payload='<img src="/__browser_content_probe__" onerror="window.__contentExecuted = (window.__contentExecuted || 0) + 1">';
const fixture=spawn(process.env.PYTHON_EXECUTABLE || 'python',['-u',path.join(__dirname,'web_fixture.py')],
  {cwd:root,env:{...process.env,PYTHONPATH:root,PYTHONIOENCODING:'utf-8',PYTHONDONTWRITEBYTECODE:'1'},stdio:['pipe','pipe','pipe']});
let stderr=''; fixture.stderr.on('data',data => stderr+=data.toString());
const fixtureReady=new Promise((resolve,reject) => {
  let buffer=''; const timer=setTimeout(() => reject(new Error('内容边界夹具未就绪: '+stderr)),30000);
  fixture.on('error',error => { clearTimeout(timer); reject(error); });
  fixture.on('exit',code => { clearTimeout(timer); reject(new Error(`内容边界夹具退出 ${code}: ${stderr}`)); });
  fixture.stdout.on('data',data => {
    buffer+=data.toString(); const end=buffer.indexOf('\n');
    if(end>=0) { clearTimeout(timer); try { resolve(JSON.parse(buffer.slice(0,end))); } catch(error) { reject(error); } }
  });
});
const admin=createFixture(), checks=[], errors=[];
const noInjection=async(page,selector,label) => {
  const observed=await page.locator(selector).evaluate(element => ({
    executions:window.__contentExecuted || 0, injectedNodes:element.querySelectorAll('img,script,iframe,object,embed').length,
  }));
  assert.deepEqual(observed,{executions:0,injectedNodes:0},label);
};
const ready=page => page.waitForFunction(() => document.querySelector('.view:not([hidden])')?.getAttribute('aria-busy') === 'false' && document.getElementById('pageFeedback').hidden);
(async() => {
  let browser;
  try {
    fs.mkdirSync(output,{recursive:true});
    const info=await fixtureReady;
    browser=await chromium.launch({headless:true,...(process.env.BROWSER_EXECUTABLE ? {executablePath:process.env.BROWSER_EXECUTABLE} : {})});
    const context=await browser.newContext({viewport:{width:1440,height:980},reducedMotion:'reduce'});
    await context.route(/https:\/\/(fonts\.|cdn\.jsdelivr)/,route => route.abort());
    await context.route('**/__browser_content_probe__',route => route.fulfill({status:404,body:''}));
    const login=await context.request.post(info.accounts_url+'/api/auth/login',{
      headers:{Origin:info.accounts_url},data:{username:'alice',password:'alice-test-password'},
    });
    assert.equal(login.status(),200);
    const page=await context.newPage(); page.on('pageerror',error => errors.push(error.message));
    await page.route('**/api/devices',async route => {
      const response=await route.fetch(), result=await response.json();
      result.devices.forEach(device => { device.display_name=payload; }); await route.fulfill({response,json:result});
    });
    await page.goto(info.accounts_url);
    await page.locator('.device-card h2').getByText(payload,{exact:true}).waitFor();
    await noInjection(page,'#deviceGrid','设备名称作为文本渲染');
    const consoleHref=await page.locator('.device-card a.primary').first().getAttribute('href');
    assert.equal(new URL(consoleHref,info.accounts_url).origin,info.accounts_url);
    assert.match(consoleHref,/^\/console\/cat-alice$/);
    checks.push('设备名称文本与固定站内入口');

    const consolePage=await context.newPage(); consolePage.on('pageerror',error => errors.push(error.message));
    const commands=[]; let commandReceived;
    const commandReady=new Promise(resolve => { commandReceived=resolve; });
    await consolePage.routeWebSocket('**/ws/devices/cat-alice/ui',socket => {
      const server=socket.connectToServer();
      socket.onMessage(message => { if(typeof message === 'string' && message.startsWith('TRANSLATE_')) { commands.push(message); commandReceived(); } else server.send(message); });
      server.onMessage(message => {
        if(typeof message === 'string' && message.startsWith('INIT:')) {
          const init=JSON.parse(message.slice(5));
          init.finals=['[AI] '+payload]; init.partial=payload;
          init.translation_pairs=[{pair:'zh-en',name:payload,display:payload},{pair:'en-zh',name:'EN ⇌ 中',display:'英中互译'}];
          socket.send('INIT:'+JSON.stringify(init));
          socket.send('KNOWLEDGE:'+JSON.stringify({sources:[{filename:payload,ordinal:1}]}));
        } else if(typeof message === 'string' && message.startsWith('TRANSLATE:')) {
          socket.send('TRANSLATE:'+JSON.stringify({active:false,pair:'zh-en',current:payload,source:payload,finalized:true}));
        } else server.send(message);
      });
    });
    const entry={time_label:payload,description:payload,image_url:'/api/devices/cat-alice/record/probe.jpg'};
    await consolePage.route('**/api/devices/cat-alice/record/state',route => route.fulfill({json:{state:{active:false,count:1},entries:[entry]}}));
    await consolePage.route('**/api/devices/cat-alice/record/probe.jpg',route => route.fulfill({status:404,body:''}));
    await consolePage.goto(info.accounts_url+'/console/cat-alice');
    await consolePage.waitForFunction(() => document.getElementById('uiStatus').textContent.includes('控制已连'));
    await consolePage.click('.nav-links a[href="#translate"]');
    await consolePage.locator('#trPairs .tr-pair').first().waitFor();
    await consolePage.waitForTimeout(100);
    await noInjection(consolePage,'#trPairs','WebSocket语言标签不能创建HTML或执行事件处理器');
    assert.match(await consolePage.locator('#trPairs').innerText(),/<img src=/);
    await noInjection(consolePage,'#chatBox','WebSocket聊天、增量内容和资料来源作为文本');
    assert.match(await consolePage.locator('#chatBox').innerText(),/<img src=/);
    await noInjection(consolePage,'#trTranslated','译文作为文本');
    await noInjection(consolePage,'#trSource','原文作为文本');
    await consolePage.locator('#trPairs .tr-pair').nth(1).click();
    assert.match(await consolePage.locator('#trPairs .tr-pair').nth(1).getAttribute('class'),/active/);
    await consolePage.evaluate(() => applyVoiceState({paused:false,connected:true,active:true,performance_mode:false,revision:999999}));
    await consolePage.click('#trStartBtn');
    let commandTimer;
    try { await Promise.race([commandReady,new Promise((_,reject) => { commandTimer=setTimeout(() => reject(new Error('语言选择未发出同传指令')),5000); })]); }
    finally { clearTimeout(commandTimer); }
    assert.equal(commands.at(-1),'TRANSLATE_START:en-zh');
    await consolePage.screenshot({path:path.join(output,'websocket-labels-as-text.png'),fullPage:true});
    checks.push('WebSocket语言标签、聊天、资料来源、原文译文文本边界与语言选择');

    const urls=await consolePage.evaluate(() => {
      const invalid=['javascript:alert(1)','data:image/svg+xml,<svg onload=alert(1)>','https://collector.invalid/secret',
        '//collector.invalid/secret','/api/devices/cat-bob/stream.wav','/api/devices/cat-alice/%2e%2e/cat-bob/stream.wav'];
      return {blocked:invalid.map(source => { try { consoleMediaUrl(source); return false; } catch { return true; } }),
        valid:consoleMediaUrl('/api/devices/cat-alice/stream.wav')};
    });
    assert.deepEqual(urls.blocked,[true,true,true,true,true,true]);
    assert.equal(urls.valid,info.accounts_url+'/api/devices/cat-alice/stream.wav');
    checks.push('媒体地址拒绝脚本、data、外站与其他设备路径');

    await consolePage.route('**/api/devices/cat-alice/record/analyze',route => route.fulfill({json:{result:{summary:payload,
      activities:[{start:'09:00',end:'10:00',title:payload,category:payload,detail:payload,duration_minutes:60}],categories:[{name:payload,minutes:60}]}}}));
    await consolePage.click('.nav-links a[href="#record"]');
    await consolePage.locator('#timeline .desc').getByText(payload,{exact:true}).waitFor();
    assert.equal(await consolePage.locator('#timeline img').count(),1,'记录只包含既有缩略图');
    assert.equal(await consolePage.locator('#timeline [onerror],#timeline script').count(),0);
    await consolePage.click('#recordAnalyzeBtn');
    await consolePage.locator('#analysisSummary').getByText(payload,{exact:true}).waitFor();
    await noInjection(consolePage,'#analysis','记录分析、分类与甘特图标签作为文本');
    checks.push('记录描述、分析总结、分类和甘特图文本');

    await consolePage.route('**/api/account/model',route => route.fulfill({json:{configured:false}}));
    await consolePage.getByRole('button',{name:'模型设置',exact:true}).click();
    await consolePage.fill('#modelKey','sk-synthetic-browser-boundary'); await consolePage.click('#closeModel');
    await consolePage.locator('#modelDialog').waitFor({state:'hidden'});
    assert.equal(await consolePage.locator('#modelKey').inputValue(),'');
    assert.doesNotMatch(await consolePage.evaluate(() => JSON.stringify({local:{...localStorage},session:{...sessionStorage}})),/sk-synthetic-browser-boundary|alice-test-password/);
    checks.push('模型Key关闭清理且不写浏览器存储');

    const knowledge=await context.newPage(); knowledge.on('pageerror',error => errors.push(error.message));
    const docId='javascript:probe/../"<x>', document={id:docId,filename:payload,size_bytes:12,chunk_count:1,status:'keyword',message:payload,summary:payload,keywords:[payload]};
    await knowledge.route('**/api/knowledge',route => route.fulfill({json:{documents:[document],usage:{bytes:12,documents:1},limits:{bytes:1024,documents:10}}}));
    await knowledge.route('**/api/knowledge/search',route => route.fulfill({json:{hits:[{filename:payload,document_id:docId,ordinal:1,mode:'keyword',text:payload}]}}));
    await knowledge.goto(info.accounts_url+'/knowledge');
    await knowledge.locator('.document-name').getByText(payload,{exact:true}).waitFor();
    await noInjection(knowledge,'#documentList','资料文件名、摘要、话题和错误作为文本');
    await knowledge.fill('#query','boundary'); await knowledge.click('#searchBtn');
    await knowledge.locator('.search-hit p').getByText(payload,{exact:true}).waitFor();
    await noInjection(knowledge,'#searchResults','检索正文与引用作为文本');
    for(const href of await knowledge.locator('#documentList a,#searchResults a').evaluateAll(links => links.map(link => link.href))) {
      assert.equal(new URL(href).origin,info.accounts_url);
      assert.equal(new URL(href).pathname,'/api/knowledge/documents/'+encodeURIComponent(docId)+'/download');
    }
    await knowledge.screenshot({path:path.join(output,'knowledge-content-as-text.png'),fullPage:true});
    checks.push('资料与检索文本、转义编号和固定站内下载地址');

    const adminUrl=await admin.start();
    admin.state.audit.push({id:1,actor_username:payload,action:payload,target_id:payload,result:'failed',created_at:1730000000});
    const adminPage=await context.newPage(); adminPage.on('pageerror',error => errors.push(error.message));
    await adminPage.goto(adminUrl+'/admin#audit'); await ready(adminPage);
    await noInjection(adminPage,'#auditBody','审计人员、未知动作与对象作为文本');
    assert.match(await adminPage.locator('#auditBody').innerText(),/<img src=/);
    await adminPage.screenshot({path:path.join(output,'audit-content-as-text.png'),fullPage:true});
    await adminPage.getByRole('button',{name:'设备',exact:true}).click(); await ready(adminPage);
    await adminPage.click('#createDevice'); await adminPage.fill('#editor-id','cat-content-boundary');
    await adminPage.fill('#editor-display_name','安全边界临时设备'); await adminPage.fill('#editor-confirm','cat-content-boundary');
    await adminPage.click('#editorSubmit'); await adminPage.locator('#credentialDialog').waitFor();
    const credential=await adminPage.locator('#credentialValue').inputValue(); assert.ok(credential.startsWith('fictional-device-token-'));
    assert.equal(await adminPage.evaluate(value => JSON.stringify({local:{...localStorage},session:{...sessionStorage}}).includes(value),credential),false);
    await adminPage.click('#credentialClose'); await adminPage.locator('#credentialDialog').waitFor({state:'hidden'});
    assert.equal(await adminPage.locator('#credentialValue').inputValue(),'');
    checks.push('审计文本与一次性设备凭证关闭清理、不写存储');
    assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(output,'summary.json'),JSON.stringify({ok:true,checks},null,2));
    console.log(JSON.stringify({ok:true,checks,evidence:output}));
  } finally {
    await browser?.close(); admin.close();
    if(fixture.exitCode === null) {
      const exited=once(fixture,'exit').catch(() => {}), timer=setTimeout(() => fixture.kill(),15000);
      fixture.stdin.end(); await exited; clearTimeout(timer);
    }
  }
})().catch(error => { console.error(error); process.exitCode=1; });
