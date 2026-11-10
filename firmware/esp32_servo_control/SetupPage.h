#pragma once

#include <Arduino.h>

// 全离线配网页，不依赖手机的互联网连接。
static const char DEVICE_SETUP_HTML[] PROGMEM = R"CMIAOHTML(
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light">
<title>小喵 · 连接 Wi-Fi</title>
<style>
:root{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif;color:#302d42;background:#f5f4f8;font-synthesis:none;--accent:#7760d5;--muted:#848095;--line:#eeecf3}
*{box-sizing:border-box}body{margin:0;padding:26px 20px max(26px,env(safe-area-inset-bottom));-webkit-tap-highlight-color:transparent}button,input,select{font:inherit}button,a,input,select,summary{touch-action:manipulation}button{cursor:pointer}button:disabled{cursor:default;opacity:.52}button:focus-visible,a:focus-visible,summary:focus-visible{outline:3px solid #b7a8ed;outline-offset:4px}input:focus,select:focus{outline:0;border-color:var(--accent);box-shadow:0 0 0 3px #7760d512}[hidden]{display:none!important}main{max-width:460px;margin:auto}.top{display:flex;align-items:center;justify-content:space-between;gap:12px;font-size:12px}.brand{font-weight:750;letter-spacing:1.3px}.connection{display:flex;align-items:center;gap:6px;font-size:11px;color:var(--muted)}.dot{height:6px;width:6px;background:#b4afc1;border-radius:50%}.connection.ready .dot{background:#6da28c}.hero{text-align:center;padding:30px 0 24px}.cat{width:55px;height:42px;position:relative;margin:8px auto 19px;background:#e4ddf7;border-radius:15px 15px 18px 18px;isolation:isolate}.cat:before,.cat:after{content:"";position:absolute;top:-9px;width:20px;height:23px;background:#e4ddf7;border-radius:5px;transform:rotate(16deg);z-index:-1}.cat:before{left:2px;transform:rotate(-16deg)}.cat:after{right:2px}.eyes{position:absolute;left:16px;top:18px;width:5px;height:7px;background:var(--accent);border-radius:4px;box-shadow:19px 0 var(--accent)}.nose{position:absolute;left:26px;top:28px;width:4px;height:3px;border-radius:0 0 5px 5px;background:#9b86dc}h1{font-size:29px;letter-spacing:-1px;margin:0 0 9px;font-weight:750}.intro{margin:0;color:var(--muted);font-size:13px;line-height:1.7}.steps{display:flex;align-items:center;justify-content:center;gap:10px;margin-top:22px;color:#a7a2b5;font-size:11px}.step{display:flex;align-items:center;gap:6px}.step b{display:grid;place-items:center;width:19px;height:19px;border-radius:50%;font-size:10px;background:#ebe8f1;font-weight:650}.step.active{color:var(--accent)}.step.active b{background:#e7e0f8}.step-line{width:24px;height:1px;background:#ded9e9}.card{background:#fff;border:1px solid #ebe8f1;border-radius:24px;padding:23px;box-shadow:0 8px 36px #33234f04}h2{font-size:17px;margin:0;font-weight:700}.section-top{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:20px}.tag{font-size:10px;color:#8a70c1;border:1px solid #ece5f8;background:#faf7ff;border-radius:6px;padding:4px 7px;white-space:nowrap}.field-label{display:block;font-size:12px;font-weight:600;margin:20px 0 9px}.choose{display:flex;align-items:center;gap:12px;background:#f9f8fc;border:1px solid #e9e5f2;border-radius:13px;padding:14px;width:100%;text-align:left;color:#383247}.choose-copy{flex:1;min-width:0}.network-name{display:block;font-size:14px;font-weight:650;overflow-wrap:anywhere}.network-note{display:block;font-size:10px;color:var(--muted);margin-top:4px;font-weight:400}.chevron{font-size:22px;color:#b6adc9;line-height:1}.wifi-icon{width:23px;height:18px;position:relative;overflow:hidden;flex-shrink:0;clip-path:polygon(0 0,100% 0,50% 100%)}.wifi-icon:before{content:"";position:absolute;width:22px;height:22px;left:0;top:1px;border:2px solid var(--accent);border-radius:50%}.wifi-icon:after{content:"";position:absolute;width:11px;height:11px;left:5.5px;top:7px;border:2px solid var(--accent);border-radius:50%;box-shadow:0 0 0 3px #f9f8fc}input,select{width:100%;padding:13px 14px;color:#383247;background:#fff;border:1px solid #e4e0eb;border-radius:12px;font-size:14px;min-height:47px}input::placeholder{color:#b0a9bc;font-size:12px}.password-wrap{display:flex;position:relative}.password-wrap input{padding-right:60px}.show-pass{position:absolute;right:4px;top:4px;bottom:4px;border:0;background:transparent;color:var(--muted);padding:0 11px;font-size:11px}.small{font-size:11px;line-height:1.7;color:var(--muted);margin:9px 0 0}.check{display:flex;align-items:center;gap:7px;color:var(--muted);font-size:12px;margin:12px 0}.check input{width:16px;min-height:16px;height:16px;accent-color:var(--accent);margin:0}.primary{width:100%;min-height:48px;border:0;border-radius:12px;background:var(--accent);color:#fff;font-size:14px;font-weight:650;padding:13px;margin-top:21px;box-shadow:0 4px 12px #7760d51a}.primary:hover:enabled{background:#6951c8}.primary.busy:before{content:"";display:inline-block;width:11px;height:11px;border:2px solid #ffffff60;border-top-color:#fff;border-radius:50%;margin-right:9px;vertical-align:-2px;animation:spin .8s linear infinite}#hint{font-size:12px;line-height:1.75;color:#a1654d;margin:12px 0 0;white-space:pre-wrap;overflow-wrap:anywhere}#hint:empty{display:none}fieldset{border:0;padding:0;margin:0;min-width:0}details{border-top:1px solid var(--line);margin-top:22px;padding-top:17px}summary{cursor:pointer;color:#92899f;font-size:11px;list-style:none;display:flex;align-items:center;justify-content:space-between;gap:9px;min-height:22px}summary::-webkit-details-marker{display:none}summary:after{content:"+";font-size:16px}details[open]>summary:after{content:"−"}.advanced-note{font-size:11px;color:var(--muted);line-height:1.8;margin:12px 0 0}.footer{text-align:center;margin:20px 10px 0;color:#a19aaa;font-size:10px;line-height:1.8}.footer strong{font-weight:500;color:#81758e}.device-status{display:flex;justify-content:center;gap:6px;align-items:center;margin-top:16px;font-size:10px;color:#958b9e}.help{margin-top:17px}.help summary{font-size:11px}.help p{font-size:11px;line-height:1.9;color:var(--muted)}.banner{margin:0 0 14px;background:#fff5ec;color:#986543;border:1px solid #f3e3d4;border-radius:12px;padding:12px;font-size:12px;line-height:1.7}.text-button{padding:8px 0;border:0;background:transparent;color:var(--accent);font-size:12px}.banner .text-button{display:block}.overlay{position:fixed;inset:0;background:#251b3b55;z-index:10;display:flex;align-items:flex-end;justify-content:center;padding:18px 12px 0}.sheet{display:flex;flex-direction:column;max-height:85vh;max-height:85dvh;width:100%;max-width:460px;background:#fff;border-radius:25px 25px 0 0;padding:22px 22px max(22px,env(safe-area-inset-bottom));box-shadow:0 -8px 50px #21132a1c}.sheet-top{display:flex;align-items:center;justify-content:space-between;gap:10px}.close{background:#f5f2f8;border:0;border-radius:50%;width:30px;height:30px;color:#93879f;font-size:20px}.scan-status{min-height:32px;font-size:11px;color:var(--muted);line-height:1.7;margin:12px 0 7px}.scan-status.loading:before{content:"";display:inline-block;width:9px;height:9px;border:2px solid #e1d9f4;border-top-color:var(--accent);border-radius:50%;margin-right:8px;vertical-align:-2px;animation:spin .8s linear infinite}#nets{overflow-y:auto;min-height:80px;overscroll-behavior:contain}#nets button{display:flex;align-items:center;gap:13px;width:100%;text-align:left;background:#fff;border:0;border-bottom:1px solid #f0edf4;padding:15px 2px;color:#393046;min-height:65px}#nets button:hover{background:#fbf9fe}.net-copy{min-width:0;flex:1}.net-name{display:block;font-size:14px;overflow-wrap:anywhere}.net-detail{display:block;font-size:10px;color:#aaa0b5;margin-top:5px}.signal{display:flex;align-items:flex-end;gap:2px;width:21px;height:17px;flex-shrink:0}.signal i{width:4px;background:#e5dfef;border-radius:1px}.signal i:nth-child(1){height:6px}.signal i:nth-child(2){height:11px}.signal i:nth-child(3){height:16px}.signal.l1 i:first-child,.signal.l2 i:nth-child(-n+2),.signal.l3 i{background:#9d87ce}.sheet-actions{display:flex;justify-content:space-between;gap:12px;flex-shrink:0;padding-top:12px}.pair-card{margin-top:15px}.pair-code{font-size:28px;letter-spacing:3px;color:var(--accent);font-weight:750;margin:14px 0 5px;overflow-wrap:anywhere}.result{text-align:center;padding:30px 23px}.result-icon{display:grid;place-items:center;width:55px;height:55px;margin:0 auto 20px;background:#e7f4ec;color:#679d80;border-radius:50%;font-size:27px}.result.uncertain .result-icon{background:#fff0df;color:#ba8a51}.result h2{font-size:23px}.result-copy{font-size:12px;line-height:1.9;color:var(--muted);white-space:pre-line;margin:14px 0 0}.next-steps{list-style:none;counter-reset:next;padding:0;margin:24px 0 0;text-align:left}.next-steps li{counter-increment:next;position:relative;padding:0 0 19px 29px;font-size:12px;color:#80758c;line-height:1.8}.next-steps li:before{content:counter(next);position:absolute;left:0;top:1px;width:19px;height:19px;display:grid;place-items:center;font-size:10px;background:#f1edf8;border-radius:50%;color:#9b86ba}.result a{color:var(--accent);overflow-wrap:anywhere}.device-id{font-size:10px;color:#b1a8ba;margin:14px 0 0;text-align:center;overflow-wrap:anywhere}@keyframes spin{to{transform:rotate(360deg)}}@media(min-width:600px){body{padding-top:44px}.overlay{align-items:center;padding:24px}.sheet{border-radius:25px}}@media(max-width:350px){body{padding:20px 14px}.card{padding:20px}h1{font-size:26px}.top{gap:6px}.connection{font-size:10px}}@media(prefers-reduced-motion:reduce){*,*:before{animation:none!important}}
</style>
</head>
<body>
<main>
<header class="top"><span class="brand">CYBER MIAO<span style="color:#b9a6e2"> ·</span></span><span id="connection" class="connection"><i class="dot" aria-hidden="true"></i><span id="connectionText">正在连接小喵</span></span></header>
<div class="hero"><div class="cat" aria-hidden="true"><i class="eyes"></i><i class="nose"></i></div><h1>给小喵一个家</h1><p class="intro">连上 Wi-Fi，就可以和你说话了。</p><nav class="steps" aria-label="配网步骤"><span id="step1" class="step active"><b>1</b>选网络</span><i class="step-line"></i><span id="step2" class="step"><b>2</b>输密码</span><i class="step-line"></i><span id="step3" class="step"><b>3</b>完成</span></nav></div>
<div id="offline" class="banner" role="status" hidden>没有连上小喵。请保持连接它的 Wi-Fi 热点，选择“保持连接”或“使用此网络”，再试一次。<button id="reconnect" class="text-button" type="button">重新连接页面</button></div>
<form id="form" class="card" novalidate>
<fieldset id="fields" disabled>
<div class="section-top"><h2>连接 Wi-Fi</h2><span class="tag">2.4 GHz</span></div>
<button id="choose" class="choose" type="button" aria-haspopup="dialog" aria-controls="wifiSheet"><i class="wifi-icon" aria-hidden="true"></i><span class="choose-copy"><span id="selected" class="network-name">选择你的 Wi-Fi</span><span id="selectedNote" class="network-note">点这里，查看附近的网络</span></span><span class="chevron" aria-hidden="true">›</span></button>
<div id="manualFields" hidden><label class="field-label" for="ssid">Wi-Fi 名称</label><input id="ssid" maxlength="32" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="请输入完整的 Wi-Fi 名称"></div>
<label class="field-label" for="password">Wi-Fi 密码</label>
<div class="password-wrap"><input id="password" type="password" maxlength="64" autocomplete="new-password" autocapitalize="off" spellcheck="false" placeholder="输入这个 Wi-Fi 的密码"><button id="showPassword" class="show-pass" type="button" aria-pressed="false">显示</button></div>
<p id="passwordNote" class="small">密码只保存在小喵里。</p>
<label id="openNetworkLabel" class="check" hidden><input id="openNetwork" type="checkbox">这是无需密码的开放网络</label>
<button id="save" class="primary" type="submit">连接这个 Wi-Fi</button>
<p id="hint" role="status" aria-live="polite"></p>
<details id="advanced"><summary>高级设置<span style="font-size:10px;margin-left:auto">一般无需修改</span></summary><p class="advanced-note">服务器地址与账号模式会沿用设备现有设置。更换 Wi-Fi 不会清除账号绑定、聊天记忆或资料。</p><label class="field-label" for="server">服务器地址</label><input id="server" maxlength="300" placeholder="https://cat.example.com" autocomplete="off" autocapitalize="off" spellcheck="false"><label class="field-label" for="mode">连接方式</label><select id="mode"><option value="accounts">账号模式 · 云端记忆</option><option value="local">本地调试</option></select><p class="advanced-note">仅自行部署服务时修改。局域网测试需填写完整的 HTTP 地址和端口。</p></details>
</fieldset>
</form>
<section id="result" class="card result" role="status" aria-live="polite" hidden><div id="resultIcon" class="result-icon" aria-hidden="true">✓</div><h2 id="resultTitle"></h2><p id="resultCopy" class="result-copy"></p><ol id="nextSteps" class="next-steps" hidden><li>等待小喵重启，留意屏幕上的连接状态。</li><li id="bindingStep"></li><li>手机切回日常网络，然后<a id="consoleLink" target="_blank" rel="noopener noreferrer">打开小喵控制台</a>。</li></ol><button id="verify" class="primary" type="button" hidden>重新检查设备</button><p id="verifyHint" class="small"></p></section>
<section id="pairCard" class="card pair-card" hidden><h2>把小喵添加到账号</h2><div id="pair" class="pair-code"></div><div id="ttl" class="small"></div><p id="guide" class="small">手机切回可上网的 Wi-Fi，登录服务器网站，在“添加设备”中输入这个配对码。也可以查看小喵屏幕上的配对码。</p><button id="refresh" class="text-button" type="button" hidden>换一个配对码</button></section>
<div class="device-status"><span id="state">正在读取设备</span></div><p id="device" class="device-id"></p>
<details class="help"><summary>换了地方，每次都要这样连接吗？</summary><p>不用。同一个 Wi-Fi 下，小喵开机会自动重连。首次使用或更换 Wi-Fi 时才需要设置；当前只保存一组网络。</p><p>优先使用无需网页登录的 2.4 GHz 家庭 Wi-Fi 或手机热点。iPhone 热点请开启“最大兼容性”。页面没有自动弹出时，在浏览器打开 http://192.168.4.1。</p><p>若密码填错，重启后等待约 30 秒，再连接屏幕上显示的小喵热点即可修改。这里的“已保存”只代表设置写入设备，联网结果请看小喵屏幕。</p></details>
<p class="footer">设置一次，下一次见面就会自动连接。<br><strong>你的账号与记忆，都会好好保留。</strong></p>
</main>
<div id="wifiSheet" class="overlay" hidden><section class="sheet" role="dialog" aria-modal="true" aria-labelledby="sheetTitle" tabindex="-1"><div class="sheet-top"><h2 id="sheetTitle">选择 Wi-Fi</h2><button id="closeSheet" class="close" type="button" aria-label="关闭网络列表">×</button></div><p id="scanStatus" class="scan-status" role="status" aria-live="polite"></p><div id="nets"></div><div class="sheet-actions"><button id="manual" class="text-button" type="button">手动输入名称</button><button id="scan" class="text-button" type="button">重新扫描</button></div><p class="small">这里只显示小喵能发现的 2.4 GHz 网络。</p></section></div>
<script>
'use strict';
const $ = id => document.getElementById(id);
const REQUEST_TIMEOUT_MS = 4500;
const SCAN_DEADLINE_MS = 18000;
const labels = {local:'本地调试模式',wifi_offline:'等待连接 Wi-Fi',need_credential:'设备身份尚未配置，请联系设备提供者',waiting_clock:'正在同步时间',server_offline:'Wi-Fi 已连接，暂时无法连接服务器',unbound:'等待账号配对',bound:'已绑定账号',credential_invalid:'设备凭证已失效，请联系设备提供者'};
let csrf = '', initialized = false, operation = 'idle', phase = 'editing';
let initial = {}, statusTimer = 0, statusController = null, statusRunning = false, scanController = null, scanTask = null, lastRequestId = '';

async function api(path, options = {}, timeout = REQUEST_TIMEOUT_MS, signal) {
  const controller = new AbortController();
  let timedOut = false;
  const cancel = () => controller.abort();
  if (signal) { signal.addEventListener('abort', cancel, {once:true}); if (signal.aborted) cancel(); }
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeout);
  try {
    const response = await fetch(path, {...options,cache:'no-store',signal:controller.signal});
    let data;
    try { data = await response.json(); } catch (error) { if (controller.signal.aborted) throw error; throw Error('设备响应不完整，请重新连接小喵热点后重试。'); }
    if (!response.ok) { const error = Error(data.error || '请求未完成，请重试。'); error.status = response.status; throw error; }
    return data;
  } catch (error) {
    if (timedOut) throw Error('连接超时。请保持连接小喵热点，再试一次。');
    if (signal && signal.aborted) throw error;
    if (error instanceof TypeError) throw Error('与小喵的连接中断了，请重新连接它的热点。');
    throw error;
  } finally {
    clearTimeout(timer);
    if (signal) signal.removeEventListener('abort', cancel);
  }
}
function post(path, body, signal) {
  if (!csrf) return Promise.reject(Error('请等待读取设备，或重新连接页面。'));
  return api(path, {method:'POST',headers:{'X-CyberMiao-CSRF':csrf,'Content-Type':'application/x-www-form-urlencoded'},body:body || ''}, REQUEST_TIMEOUT_MS, signal);
}
function stopStatus() { clearTimeout(statusTimer); if (statusController) statusController.abort(); }
function scheduleStatus() { clearTimeout(statusTimer); if (operation === 'idle' && phase === 'editing' && !document.hidden) statusTimer = setTimeout(status, 2500); }
function updateStatus(data) {
  csrf = data.csrf_token;
  $('connection').classList.add('ready'); $('connectionText').textContent = '已连接小喵热点'; $('offline').hidden = true;
  $('state').textContent = labels[data.status] || '设备已连接'; $('device').textContent = data.device_id || '';
  $('pairCard').hidden = !(data.wifi_connected && data.mode === 'accounts' && data.credential_configured && !data.bound && data.pairing_code);
  $('pair').textContent = data.pairing_code || ''; $('ttl').textContent = data.expires_in ? '配对码剩余 ' + data.expires_in + ' 秒' : '';
  $('refresh').hidden = $('pairCard').hidden;
  if (!initialized) {
    initial = {...data}; $('ssid').value = data.wifi_ssid || ''; $('server').value = data.server_url || ''; $('mode').value = data.mode;
    initialized = true;
    if (!data.server_url) $('advanced').open = true;
    updateNetwork();
  }
  $('fields').disabled = operation !== 'idle';
}
async function status() {
  if (statusRunning || operation !== 'idle' || phase !== 'editing' || document.hidden) return;
  statusRunning = true; const controller = new AbortController(); statusController = controller;
  try {
    const data = await api('/status', {}, REQUEST_TIMEOUT_MS, controller.signal);
    if (!controller.signal.aborted && operation === 'idle' && phase === 'editing') updateStatus(data);
  } catch (error) {
    if (!controller.signal.aborted && phase === 'editing') { $('connection').classList.remove('ready'); $('connectionText').textContent = '连接暂时中断'; $('offline').hidden = false; }
  } finally {
    if (statusController === controller) statusController = null;
    statusRunning = false; scheduleStatus();
  }
}
function updateNetwork() {
  const ssid = $('ssid').value, open = $('openNetwork').checked;
  const keep = ssid === initial.wifi_ssid && initial.password_saved && !open;
  $('selected').textContent = ssid || '选择你的 Wi-Fi';
  $('selectedNote').textContent = ssid ? '已选择 · 点此切换网络' : '点这里，查看附近的网络';
  $('password').disabled = open;
  $('password').placeholder = open ? '这个网络不需要密码' : keep ? '已保存，留空即可沿用' : '输入这个 Wi-Fi 的密码';
  $('passwordNote').textContent = open ? '开放网络没有密码，请确认是你信任的网络。' : keep ? '留空沿用这个网络的已保存密码，也可以输入新密码。' : '密码只保存在小喵里。';
  $('step1').classList.toggle('active', !ssid); $('step2').classList.toggle('active', !!ssid);
}
function closeSheet() {
  if (scanController) scanController.abort();
  scanController = null; scanTask = null; $('scan').disabled = false; $('scanStatus').classList.remove('loading');
  $('wifiSheet').hidden = true; document.body.style.overflow = '';
  document.querySelector('main').inert = false;
  if (operation === 'scan') { operation = 'idle'; $('fields').disabled = !initialized; }
  $('choose').focus(); scheduleStatus();
}
function selectNetwork(network) {
  if ($('ssid').value !== network.ssid) $('password').value = '';
  $('ssid').value = network.ssid;
  $('openNetwork').checked = network.secure === false; $('openNetworkLabel').hidden = true;
  $('manualFields').hidden = true;
  closeSheet(); updateNetwork(); if (!$('password').disabled) $('password').focus();
}
function renderNetworks(networks) {
  const unique = new Map();
  for (const network of networks || []) {
    if (typeof network.ssid !== 'string' || !network.ssid) continue;
    const key = JSON.stringify([network.ssid,network.secure]);
    if (!unique.has(key) || network.rssi > unique.get(key).rssi) unique.set(key, network);
  }
  const list = [...unique.values()].sort((a,b) => b.rssi - a.rssi);
  $('nets').replaceChildren();
  for (const network of list) {
    const button = document.createElement('button'); button.type = 'button';
    const level = network.rssi >= -60 ? 3 : network.rssi >= -75 ? 2 : 1;
    const signal = document.createElement('span'); signal.className = 'signal l' + level; signal.setAttribute('aria-hidden','true');
    for (let i = 0; i < 3; i++) signal.appendChild(document.createElement('i'));
    const copy = document.createElement('span'); copy.className = 'net-copy';
    const name = document.createElement('span'); name.className = 'net-name'; name.textContent = network.ssid;
    const detail = document.createElement('span'); detail.className = 'net-detail';
    detail.textContent = (level === 3 ? '信号很好' : level === 2 ? '信号良好' : '信号较弱') + ' · ' + (network.secure === false ? '无需密码' : '需要密码');
    copy.append(name,detail); button.append(signal,copy); button.addEventListener('click', () => selectNetwork(network)); $('nets').appendChild(button);
  }
  $('scanStatus').textContent = list.length ? '找到 ' + list.length + ' 个网络，点选你要连接的 Wi-Fi。' : '没有发现网络。请靠近路由器重新扫描，也可以手动输入名称。';
}
function pause(ms, signal) {
  return new Promise((resolve,reject) => {
    const cancel = () => { clearTimeout(timer); reject(Error('cancelled')); };
    const timer = setTimeout(() => { signal.removeEventListener('abort',cancel); resolve(); }, ms);
    signal.addEventListener('abort',cancel,{once:true}); if (signal.aborted) cancel();
  });
}
async function scan() {
  if (scanTask || phase !== 'editing') return;
  stopStatus(); operation = 'scan'; $('fields').disabled = true;
  const controller = new AbortController(); scanController = controller;
  $('scan').disabled = true; $('scanStatus').classList.add('loading'); $('scanStatus').textContent = '正在查找附近的 2.4 GHz Wi-Fi…';
  const task = (async () => {
    const deadline = performance.now() + SCAN_DEADLINE_MS;
    let action = 'start';
    try {
      while (!controller.signal.aborted) {
        const data = await post('/scan', new URLSearchParams({action}), controller.signal);
        if (controller.signal.aborted) return;
        if (!data.scanning) { renderNetworks(data.networks); return; }
        if (performance.now() >= deadline) throw Error('扫描超时，请重新扫描，或手动输入 Wi-Fi 名称。');
        action = 'poll'; await pause(900,controller.signal);
      }
    } catch (error) {
      if (!controller.signal.aborted) $('scanStatus').textContent = error.message + ' 也可以点“手动输入名称”。';
    } finally {
      if (scanController === controller) { scanController = null; $('scan').disabled = false; $('scanStatus').classList.remove('loading'); }
    }
  })();
  scanTask = task;
  try { await task; } finally { if (scanTask === task) scanTask = null; }
}
function newRequestId() { const bytes = new Uint8Array(16); crypto.getRandomValues(bytes); return Array.from(bytes, value => value.toString(16).padStart(2,'0')).join(''); }
function clearPassword() { $('password').value = ''; $('password').type = 'password'; $('showPassword').textContent = '显示'; $('showPassword').setAttribute('aria-pressed','false'); }
function showSaved() {
  phase = 'saved'; operation = 'idle'; stopStatus(); clearPassword();
  $('form').hidden = true; $('pairCard').hidden = true; $('result').hidden = false; $('result').classList.remove('uncertain');
  $('resultIcon').textContent = '✓'; $('resultTitle').textContent = '网络设置已保存';
  $('resultCopy').textContent = '小喵将在几秒内重启，尝试连接你选择的 Wi-Fi。\n重启时手机与小喵热点断开，属于正常现象。';
  $('nextSteps').hidden = false; $('verify').hidden = true; $('verifyHint').textContent = '';
  $('bindingStep').textContent = $('mode').value === 'local' ? '联网结果以小喵屏幕为准；若仍提示配网，重新加入热点更正设置。' : initial.bound ? '账号绑定与记忆已保留。若仍提示配网，重新加入热点更正设置。' : '联网后在屏幕查看配对码，登录账号并在“添加设备”中填写。';
  $('step1').classList.remove('active'); $('step2').classList.remove('active'); $('step3').classList.add('active');
  try { const url = new URL($('server').value); if (!['http:','https:'].includes(url.protocol) || url.username || url.password) throw Error(); $('consoleLink').href = url.href; } catch (_) { $('consoleLink').removeAttribute('href'); }
}
function showUncertain() {
  phase = 'uncertain'; operation = 'idle'; stopStatus(); clearPassword();
  $('form').hidden = true; $('pairCard').hidden = true; $('result').hidden = false; $('result').classList.add('uncertain');
  $('resultIcon').textContent = '?'; $('resultTitle').textContent = '还未确认保存结果';
  $('resultCopy').textContent = '连接中断，设备可能已保存并重启。\n请先看小喵屏幕：恢复猫脸后可直接使用。\n若仍显示配网，请重新加入小喵热点，再检查设备。';
  $('nextSteps').hidden = true; $('verify').hidden = false; $('verifyHint').textContent = '不会自动重复提交你的密码。';
}
async function verifySaved() {
  const data = await api('/status', {}, 2500);
  if (data.reboot_pending && data.save_request_id === lastRequestId) { showSaved(); return true; }
  if (data.reboot_pending) throw Error('设备正在处理另一次保存，请等待重启后再检查。');
  return data;
}
$('form').addEventListener('submit', async event => {
  event.preventDefault(); if (!initialized || operation !== 'idle' || phase !== 'editing') return;
  const ssid = $('ssid').value, password = $('password').value, open = $('openNetwork').checked;
  const keep = !password && !open && ssid === initial.wifi_ssid && initial.password_saved;
  if (!ssid || new TextEncoder().encode(ssid).length > 32) { $('hint').textContent = '请先选择 Wi-Fi；名称不能超过 32 字节。'; return; }
  if (!open && !keep && (new TextEncoder().encode(password).length < 8 || (password.length === 64 && !/^[0-9a-f]{64}$/i.test(password)))) { $('hint').textContent = '请输入 Wi-Fi 密码（至少 8 位）。无需密码的网络请在手动输入中勾选开放网络。'; $('password').focus(); return; }
  if (new TextEncoder().encode(password).length > 64) { $('hint').textContent = 'Wi-Fi 密码不能超过 64 字节。'; return; }
  if (!/^https?:\/\/[^\s]+$/.test($('server').value)) { $('advanced').open = true; $('hint').textContent = '请在高级设置中填写完整的服务器地址。'; $('server').focus(); return; }
  const body = new URLSearchParams({ssid,password:open ? '' : password,keep_password:keep ? '1' : '0',server_url:$('server').value,mode:$('mode').value,request_id:newRequestId()});
  lastRequestId = body.get('request_id'); stopStatus(); operation = 'save'; $('fields').disabled = true; $('save').classList.add('busy'); $('save').textContent = '正在保存设置'; $('hint').textContent = '请保持连接小喵热点，稍等片刻…';
  try {
    const data = await post('/configure', body);
    if (!data.saved || data.save_request_id !== lastRequestId) throw Error('未收到保存确认');
    showSaved();
  } catch (error) {
    if (error.status && error.status < 500 && error.status !== 409) {
      operation = 'idle'; $('fields').disabled = false; $('hint').textContent = error.message; scheduleStatus();
    } else {
      try { if (await verifySaved() !== true) showUncertain(); } catch (_) { showUncertain(); }
    }
  } finally { $('save').classList.remove('busy'); $('save').textContent = '连接这个 Wi-Fi'; }
});
$('verify').addEventListener('click', async () => {
  if (operation !== 'idle') return; operation = 'check'; $('verify').disabled = true; $('verifyHint').textContent = '正在检查，请保持连接小喵热点…';
  try {
    const data = await verifySaved();
    if (data !== true) {
      phase = 'editing'; operation = 'idle'; $('result').hidden = true; $('form').hidden = false;
      updateStatus(data); $('hint').textContent = '设备当前没有待重启的保存确认。请核对网络与密码后重试。'; scheduleStatus();
    }
  } catch (error) { $('verifyHint').textContent = error.message; }
  finally { operation = 'idle'; $('verify').disabled = false; }
});
$('choose').addEventListener('click', () => {
  if (operation !== 'idle' || phase !== 'editing') return;
  $('wifiSheet').hidden = false; document.body.style.overflow = 'hidden'; $('closeSheet').focus(); scan();
  document.querySelector('main').inert = true;
});
$('scan').addEventListener('click', scan);
$('closeSheet').addEventListener('click', closeSheet);
$('wifiSheet').addEventListener('click', event => { if (event.target === $('wifiSheet')) closeSheet(); });
document.addEventListener('keydown', event => {
  if ($('wifiSheet').hidden) return;
  if (event.key === 'Escape') { event.preventDefault(); closeSheet(); }
  if (event.key === 'Tab') {
    const buttons = [...$('wifiSheet').querySelectorAll('button:not(:disabled)')]; const first = buttons[0], last = buttons[buttons.length - 1];
    if (!$('wifiSheet').contains(document.activeElement)) { event.preventDefault(); (event.shiftKey ? last : first).focus(); }
    else if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
});
$('manual').addEventListener('click', () => { closeSheet(); $('manualFields').hidden = false; $('openNetworkLabel').hidden = false; $('openNetwork').checked = false; updateNetwork(); $('ssid').focus(); });
$('ssid').addEventListener('input', () => { $('openNetwork').checked = false; updateNetwork(); });
$('openNetwork').addEventListener('change', () => { if ($('openNetwork').checked) $('password').value = ''; updateNetwork(); });
$('showPassword').addEventListener('click', () => { const show = $('password').type === 'password'; $('password').type = show ? 'text' : 'password'; $('showPassword').textContent = show ? '隐藏' : '显示'; $('showPassword').setAttribute('aria-pressed',String(show)); });
$('refresh').addEventListener('click', async () => {
  if (operation !== 'idle' || phase !== 'editing') return;
  stopStatus(); operation = 'refresh'; $('refresh').disabled = true; $('fields').disabled = true;
  try { await post('/pairing'); $('ttl').textContent = '正在申请新配对码，稍后自动更新。'; } catch (error) { $('ttl').textContent = error.message; }
  finally { operation = 'idle'; $('refresh').disabled = false; $('fields').disabled = !initialized; status(); }
});
$('reconnect').addEventListener('click', status);
document.addEventListener('visibilitychange', () => { if (document.hidden) stopStatus(); else status(); });
status();
</script>
</body>
</html>
)CMIAOHTML";
