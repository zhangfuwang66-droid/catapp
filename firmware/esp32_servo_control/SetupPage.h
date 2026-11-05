#pragma once

#include <Arduino.h>

static const char DEVICE_SETUP_HTML[] PROGMEM = R"CMIAOHTML(
<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>小喵 · 设备设置</title>
<style>
:root{color-scheme:dark;font-family:system-ui,-apple-system,"Segoe UI",sans-serif;background:#0c1019;color:#edf3fc}
*{box-sizing:border-box}body{margin:0;padding:32px 18px}main{max-width:480px;margin:auto}.eyebrow{font-size:11px;letter-spacing:3px;color:#88d7d2}h1{font-size:30px;margin:12px 0}p,.meta{font-size:13px;line-height:1.7;color:#99a7bf}.card{background:#151c29;border:1px solid #253148;border-radius:20px;padding:24px;margin:20px 0}.code{font-size:32px;letter-spacing:4px;color:#a4e4cf;font-weight:700;margin:10px 0}label{display:block;font-size:13px;margin:18px 0 7px}input,select,button{font:inherit;width:100%;padding:12px 14px;border-radius:11px}input,select{background:#0e1521;color:#edf3fc;border:1px solid #35405a}button{border:0;background:#a4e4cf;color:#142924;font-weight:650;cursor:pointer;margin-top:20px}button.secondary{background:#273347;color:#d8e3f5;margin-top:10px}button:disabled{opacity:.5;cursor:wait}#hint{white-space:pre-wrap;color:#e5c88b;min-height:22px}#nets button{font-size:13px;text-align:left;margin-top:6px}.badge{display:inline-block;background:#233a39;color:#a4e4cf;border-radius:20px;padding:4px 10px;font-size:12px}input:focus,select:focus,button:focus-visible{outline:2px solid #a4e4cf;outline-offset:2px}@media(max-width:380px){.card{padding:18px}.code{font-size:26px}}
</style></head><body><main>
<div class="eyebrow">CYBER MIAO / DEVICE</div><h1>让小喵连接你的世界</h1>
<p>连接设备屏幕上显示的热点后，在这里设置网络与服务器。</p>
<section class="card" aria-live="polite"><span id="state" class="badge">正在读取设备</span>
<div id="pair" class="code">—</div><div id="ttl" class="meta"></div><div id="device" class="meta"></div>
<p id="guide">账号模式下，登录服务器网站并输入配对码，即可把这只小喵添加到自己的账号。</p>
<button id="refresh" class="secondary" type="button" hidden>换一个配对码</button></section>
<form id="form" class="card"><label for="ssid">Wi-Fi 名称</label><input id="ssid" maxlength="32" required autocomplete="off">
<label for="password">Wi-Fi 密码</label><input id="password" type="password" maxlength="64" autocomplete="new-password" placeholder="留空表示开放 Wi-Fi；原密码不会显示">
<button id="scan" class="secondary" type="button">查找附近 Wi-Fi</button><div id="nets"></div>
<label for="server">服务器地址</label><input id="server" required maxlength="300" placeholder="https://cat.example.com" autocomplete="off" autocapitalize="off" spellcheck="false">
<label for="mode">连接方式</label><select id="mode"><option value="accounts">账号模式 · 独立设备与记忆</option><option value="local">本地调试 · 原版接口</option></select>
<p>云端使用 HTTPS；内网测试请明确填写 HTTP 地址与端口，例如 http://192.168.2.105:8081。</p>
<button id="save" type="submit">保存设置并重启</button><p id="hint" role="status"></p></form>
<p>长期设备凭证通过 USB 配发，本页面不会显示。保存时请重新输入 Wi-Fi 密码。</p>
</main><script>
'use strict';
const $=id=>document.getElementById(id);
let csrf='',initialized=false,saved=false,scanning=false;
const labels={local:'本地调试模式',wifi_offline:'等待 Wi-Fi 连接',need_credential:'请通过 USB 配置设备身份',waiting_clock:'正在同步时间',server_offline:'暂时无法连接服务器',unbound:'等待账号配对',bound:'已绑定账号',credential_invalid:'设备凭证已失效'};
async function api(path,options={}){const r=await fetch(path,{cache:'no-store',...options});const d=await r.json();if(!r.ok)throw Error(d.error||'请求失败');return d;}
function hint(text){$('hint').textContent=text;}
async function status(){if(saved)return;try{const d=await api('/status');csrf=d.csrf_token;$('state').textContent=labels[d.status]||'读取设备状态';$('device').textContent=d.device_id;$('pair').textContent=d.pairing_code||'—';$('ttl').textContent=d.expires_in?'配对码剩余 '+d.expires_in+' 秒':'';$('refresh').hidden=d.mode!=='accounts'||!d.credential_configured||d.bound;$('guide').textContent=d.bound?'已完成绑定，可以回服务器网站打开控制台。':d.mode==='local'?'当前使用本地兼容接口，无需账号配对。':'登录服务器网站，在“添加设备”中输入这里的配对码。';if(!initialized){$('ssid').value=d.wifi_ssid;$('server').value=d.server_url;$('mode').value=d.mode;initialized=true;}}catch(e){$('state').textContent='连接设备热点后刷新页面';}}
function post(path,body){return api(path,{method:'POST',headers:{'X-CyberMiao-CSRF':csrf,'Content-Type':'application/x-www-form-urlencoded'},body:body||''});}
$('form').addEventListener('submit',async e=>{e.preventDefault();$('save').disabled=true;hint('正在保存…');try{const d=await post('/configure',new URLSearchParams({ssid:$('ssid').value,password:$('password').value,server_url:$('server').value,mode:$('mode').value}));saved=true;$('password').value='';hint(d.message);}catch(e){hint(e.message);$('save').disabled=false;}});
$('refresh').addEventListener('click',async()=>{try{await post('/pairing');hint('已申请刷新，稍后自动显示新码。');}catch(e){hint(e.message);}});
async function scan(){if(saved)return;try{const d=await post('/scan');if(d.scanning){setTimeout(scan,1200);return;}const box=$('nets');box.replaceChildren();for(const n of d.networks){const b=document.createElement('button');b.type='button';b.className='secondary';b.textContent=n.ssid+'  '+n.rssi+' dBm';b.addEventListener('click',()=>{$('ssid').value=n.ssid;});box.appendChild(b);}if(!d.networks.length)hint('暂未发现 Wi-Fi，可手动填写名称。');}catch(e){hint(e.message);}scanning=false;$('scan').disabled=false;}
$('scan').addEventListener('click',()=>{if(scanning)return;scanning=true;$('scan').disabled=true;scan();});
status();setInterval(status,2000);
</script></body></html>
)CMIAOHTML";
