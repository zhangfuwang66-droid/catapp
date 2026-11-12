'use strict';

const $ = id => document.getElementById(id);
const state = {user:null, csrf:'', epoch:0, devices:[], memoryId:null, revokeId:null};
let pairInFlight = false, pairSubmitted = false, revokeInFlight = false, revokeSubmitted = false;
let shareDevice = null;
let noticeTimer = null;
let refreshRequest = null;
let loginInFlight = false;
const accountChannel = typeof BroadcastChannel === 'function' ? new BroadcastChannel('cybermiao-account') : null;

class RequestError extends Error {
  constructor(message, status=0) { super(message); this.status = status; }
}
class StaleRequest extends Error {}

function errorText(error) { return error.status === 429 ? error.message + '，请稍后再试。' : error.message; }
function report(id, error) { if(!(error instanceof StaleRequest)) $(id).textContent = errorText(error); }
function notice(text) {
  clearTimeout(noticeTimer);
  $('notice').textContent = text;
  $('notice').hidden = false;
  noticeTimer = setTimeout(() => { $('notice').hidden = true; }, 4000);
}

async function api(path, options={}, allowUnauthorized=false) {
  const epoch = state.epoch;
  const headers = new Headers(options.headers || {});
  if(options.body) headers.set('Content-Type', 'application/json');
  if(options.method && !['GET','HEAD'].includes(options.method.toUpperCase()) && state.csrf)
    headers.set('X-CSRF-Token', state.csrf);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  let response, data;
  try {
    response = await fetch(path, {...options, headers, signal:controller.signal, credentials:'same-origin', cache:'no-store'});
    try { data = await response.json(); }
    catch(error) { if(response.ok || error.name === 'AbortError') throw error; data = {}; }
  } catch(error) {
    if(epoch !== state.epoch) throw new StaleRequest();
    throw new RequestError(error.name === 'AbortError' ? '请求超时，请刷新核对结果；提交操作请勿直接重复发送。' : '网络连接中断，请刷新核对结果。');
  } finally { clearTimeout(timeout); }
  if(epoch !== state.epoch) throw new StaleRequest();
  if(!response.ok) {
    if(response.status === 401 && !allowUnauthorized) {
      showLogin('登录已失效，请重新登录。');
      throw new StaleRequest();
    }
    throw new RequestError(typeof data.detail === 'string' ? data.detail : '操作未完成，请检查输入后重试。', response.status);
  }
  return data;
}

function resetPrivateView() {
  state.epoch++;
  state.user = null; state.csrf = ''; state.devices = []; state.memoryId = null; state.revokeId = null;
  refreshRequest = null;
  document.querySelectorAll('dialog[open]').forEach(dialog => dialog.close());
  $('deviceGrid').replaceChildren(); $('memoryContent').replaceChildren(); $('historyDevices').replaceChildren();
  $('loanCode').textContent = ''; $('shareConfirm').value = ''; shareDevice = null;
  ['username','avatar','memoryTitle','pageError','pairError','memoryError','revokeError','retentionStatus','retentionFeedback'].forEach(id => { $(id).textContent = ''; });
  ['loginUsername','loginPassword','pairCode','clearDeviceId','revokeDeviceId'].forEach(id => { $(id).value = ''; });
  $('loginPassword').type = 'password';
  $('loginPasswordToggle').setAttribute('aria-label', '显示密码');
  $('loginPasswordToggle').setAttribute('aria-pressed', 'false');
  $('deviceCount').textContent = '0'; $('sideCount').textContent = '0'; $('onlineCount').textContent = '0';
  $('clearDetails').open = false; $('clearBtn').disabled = true; $('revokeBtn').disabled = true;
  $('accountBar').hidden = true; $('devicesView').hidden = true;
  $('adminEntry').hidden = true;
  $('notice').hidden = true; clearTimeout(noticeTimer);
}

function showLogin(message='') {
  resetPrivateView();
  $('loading').hidden = true; $('topNote').hidden = false; $('loginView').hidden = false;
  $('loginError').textContent = message;
}

async function adoptSession(grant) {
  resetPrivateView();
  state.user = grant.user; state.csrf = grant.csrf_token;
  $('adminEntry').hidden = grant.is_admin !== true;
  $('username').textContent = grant.user.username;
  $('avatar').textContent = grant.user.username.slice(0,1).toUpperCase();
  $('loginView').hidden = true; $('loading').hidden = true; $('topNote').hidden = true;
  $('accountBar').hidden = false; $('devicesView').hidden = false;
  await refreshDevices();
}

async function restoreSession() {
  try {
    const grant = await api('/api/auth/session', {}, true);
    await adoptSession(grant);
    if(new URLSearchParams(location.search).has('notice')) {
      notice('登录信息或设备状态已更新，请重新选择小喵。');
      history.replaceState(null, '', '/');
    }
  } catch(error) {
    if(error instanceof StaleRequest) return;
    const needsLogin = new URLSearchParams(location.search).has('notice');
    showLogin(error.status === 401 ? (needsLogin ? '请登录后继续使用你的小喵。' : '') : '暂时连接不到服务，请稍后重试。');
  }
}

async function refreshDevices() {
  if(!state.user || refreshRequest) return;
  const request = {};
  refreshRequest = request;
  $('refreshBtn').disabled = true;
  try {
    const data = await api('/api/devices');
    state.devices = data.devices || [];
    renderDevices(); $('pageError').textContent = '';
    await refreshHistory();
  } catch(error) { report('pageError', error); }
  finally {
    if(refreshRequest === request) { refreshRequest = null; $('refreshBtn').disabled = false; }
  }
}

function element(tag, className='', text='') {
  const node = document.createElement(tag);
  node.className = className; node.textContent = text;
  return node;
}

function renderDevices() {
  $('deviceCount').textContent = state.devices.length;
  $('sideCount').textContent = state.devices.length;
  $('onlineCount').textContent = state.devices.filter(d => d.revoked_at == null && (d.audio_online || d.camera_online)).length;
  $('emptyState').hidden = state.devices.length > 0;
  $('deviceGrid').replaceChildren();
  state.devices.forEach(device => {
    const disabled = device.revoked_at != null;
    const accessible = !disabled && device.control_allowed !== false;
    const online = !disabled && (device.audio_online || device.camera_online);
    const card = element('article', 'device-card');
    const top = element('div', 'device-top');
    const portrait = element('div', 'device-portrait');
    portrait.innerHTML = '<svg aria-hidden="true"><use href="#cat"/></svg>';
    top.append(portrait, element('span', 'state' + (disabled ? ' disabled' : online ? ' online' : ''), disabled ? '凭证已撤销' : online ? '● 在线' : '离线'));
    const channels = element('div', 'channels');
    channels.append(element('span', 'channel' + (device.audio_online ? ' active' : ''), device.audio_online ? '语音已连接' : '语音未连接'),
                    element('span', 'channel' + (device.camera_online ? ' active' : ''), device.camera_online ? '画面已连接' : '画面未连接'));
    const actions = element('div', 'card-actions');
    const consoleLink = element('a', 'primary', '进入控制台 ↗');
    if(accessible) consoleLink.href = '/console/' + encodeURIComponent(device.id);
    else { consoleLink.setAttribute('aria-disabled', 'true'); if(!disabled) consoleLink.textContent = '借用中，控制暂交对方'; }
    const memory = element('button', 'secondary', '查看记忆');
    memory.type = 'button'; memory.disabled = false; memory.addEventListener('click', () => openMemory(device));
    actions.append(consoleLink, memory);
    const bottom = element('div', 'card-bottom');
    const boundDate = device.bound_at ? new Date(device.bound_at * 1000).toLocaleDateString('zh-CN') + ' 已相识' : '已绑定到你的账号';
    bottom.append(element('span', '', boundDate));
    const share = element('button','quiet',device.role === 'guest' ? '归还设备' : '解绑 / 转交');
    share.type = 'button'; share.disabled = disabled;
    share.addEventListener('click',() => openShare(device, 'unbind')); bottom.append(share);
    if(device.role !== 'guest' && !disabled) {
      const loan = element('button','quiet','临时借用'); loan.type = 'button';
      loan.addEventListener('click',() => openShare(device, 'loan')); bottom.append(loan);
    }
    card.append(top, element('h2', '', device.display_name), element('div', 'device-id', device.id), channels, actions, bottom);
    if(disabled) card.append(element('p','muted','设备凭证已撤销，不能重新领配对码。请联系管理员签发新凭证，经 USB 恢复；Wi-Fi 和你的历史记忆保留。'));
    else if(device.role !== 'guest') {
      const security = element('details', 'guide'); security.append(element('summary','','安全操作'));
      security.append(element('p','muted','交给别人使用请选择“解绑 / 转交”。仅在设备遗失或凭证泄露时撤销凭证，恢复需要 USB。'));
      const revoke = element('button', 'quiet', '撤销凭证（遗失设备）'); revoke.type = 'button';
      revoke.addEventListener('click', () => openRevoke(device)); security.append(revoke); card.append(security);
    }
    $('deviceGrid').append(card);
  });
}

function openPair() {
  if(!state.user || pairInFlight) return;
  pairSubmitted = false;
  $('pairError').textContent = ''; $('pairCode').value = ''; $('pairKind').value = 'pair'; $('pairBtn').disabled = false;
  $('pairDialog').showModal(); $('pairCode').focus();
}

function memorySection(title, items, formatter) {
  const section = element('section', 'memory-section');
  section.append(element('h3', '', title));
  if(!items.length) section.append(element('p', 'memory-empty', '还没有这类记忆。'));
  items.forEach(item => {
    section.append(element('div', 'memory-item', formatter(item)));
    if(Array.isArray(item.open_topics) && item.open_topics.length)
      section.append(element('p', 'memory-topics', '可以继续的话题：' + item.open_topics.join(' · ')));
  });
  return section;
}

function renderMemory(data) {
  renderRetention(data.retention || {});
  $('memoryContent').replaceChildren(
    memorySection('长期记忆', data.memories || [], item => item.content),
    memorySection('近期对话摘要', data.summaries || [], item => item.summary),
    memorySection('尚未总结的近期消息', data.recent_messages || [], item => (item.role === 'user' ? '你：' : '小喵：') + item.content),
  );
}

async function openMemory(device) {
  state.memoryId = device.id;
  $('memoryTitle').textContent = device.display_name + '的记忆';
  $('memoryError').textContent = ''; $('memoryContent').textContent = '正在读取记忆…';
  $('clearDetails').open = false; $('clearDeviceId').value = ''; $('clearBtn').disabled = true;
  $('clearLabel').textContent = '输入 ' + device.id + ' 以确认清除';
  $('memoryDialog').showModal();
  try {
    const data = await api('/api/account/memories/' + encodeURIComponent(device.id));
    if(state.memoryId === device.id) renderMemory(data);
  } catch(error) { if(state.memoryId === device.id) report('memoryError', error); }
}

function openRevoke(device) {
  if(revokeInFlight) return;
  revokeSubmitted = false;
  state.revokeId = device.id;
  $('revokeDeviceId').value = ''; $('revokeBtn').disabled = true; $('revokeError').textContent = '';
  $('revokeLabel').textContent = '输入 ' + device.id + ' 以确认撤销凭证';
  $('revokeDialog').showModal();
}

$('loginPasswordToggle').addEventListener('click', () => {
  const visible = $('loginPassword').type === 'password';
  $('loginPassword').type = visible ? 'text' : 'password';
  $('loginPasswordToggle').setAttribute('aria-label', visible ? '隐藏密码' : '显示密码');
  $('loginPasswordToggle').setAttribute('aria-pressed', String(visible));
});

$('loginForm').addEventListener('submit', async event => {
  event.preventDefault();
  if(loginInFlight) return;
  loginInFlight = true;
  $('loginError').textContent = ''; $('loginBtn').disabled = true;
  $('loginBtn').setAttribute('aria-busy', 'true');
  $('loginButtonLabel').textContent = '正在登录';
  $('loginStatus').textContent = '正在打开你的小喵空间…';
  try {
    const grant = await api('/api/auth/login', {method:'POST', body:JSON.stringify({username:$('loginUsername').value.trim(), password:$('loginPassword').value})}, true);
    $('loginPassword').value = '';
    accountChannel?.postMessage({type:'changed'});
    await adoptSession(grant);
  } catch(error) { report('loginError', error); }
  finally {
    loginInFlight = false; $('loginBtn').disabled = false;
    $('loginBtn').removeAttribute('aria-busy');
    $('loginButtonLabel').textContent = '登录我的小喵'; $('loginStatus').textContent = '';
  }
});

$('logoutBtn').addEventListener('click', async () => {
  $('logoutBtn').disabled = true;
  try {
    await api('/api/auth/logout', {method:'POST'});
    showLogin('已退出登录。'); accountChannel?.postMessage({type:'changed'});
  } catch(error) { report('pageError', error); }
  finally { $('logoutBtn').disabled = false; }
});

$('pairForm').addEventListener('submit', async event => {
  event.preventDefault();
  if(pairInFlight || pairSubmitted || !state.user) return;
  const epoch = state.epoch;
  pairInFlight = true; pairSubmitted = true; $('pairDialog').dataset.busy = 'true';
  $('pairError').textContent = '正在绑定…'; $('pairBtn').disabled = true;
  try {
    const result = await api('/api/devices/' + $('pairKind').value, {method:'POST', body:JSON.stringify({code:$('pairCode').value.trim().toUpperCase()})});
    if(epoch !== state.epoch) return;
    $('pairDialog').close(); notice(result.message || '配对成功。请在控制台确认自己的 Key，再开启语音。'); await refreshDevices();
  } catch(error) {
    if(epoch === state.epoch && !(error instanceof StaleRequest)) {
      pairSubmitted = ![400,404,409,422,429].includes(error.status);
      report('pairError', error);
      if(pairSubmitted) { $('pairError').textContent += ' 请关闭后刷新设备列表核对，不会自动重试。'; await refreshDevices(); }
    }
  }
  finally { pairInFlight = false; $('pairDialog').dataset.busy = 'false'; $('pairBtn').disabled = pairSubmitted; }
});

$('clearDeviceId').addEventListener('input', () => { $('clearBtn').disabled = !state.memoryId || $('clearDeviceId').value !== state.memoryId; });
$('clearForm').addEventListener('submit', async event => {
  event.preventDefault();
  const id = state.memoryId;
  if(!id || $('clearDeviceId').value !== id) return;
  $('clearBtn').disabled = true; $('clearDeviceId').disabled = true; $('memoryError').textContent = '';
  try {
    await api('/api/account/memories/' + encodeURIComponent(id), {method:'DELETE', body:JSON.stringify({confirm_device_id:id})});
    if(state.memoryId !== id) return;
    renderMemory({}); $('clearDetails').open = false; $('clearDeviceId').value = '';
    notice('这只小喵的聊天记忆已清除，可以重新开始对话。');
  } catch(error) { if(state.memoryId === id) report('memoryError', error); }
  finally { $('clearDeviceId').disabled = false; $('clearBtn').disabled = !state.memoryId || $('clearDeviceId').value !== state.memoryId; }
});

$('revokeDeviceId').addEventListener('input', () => { $('revokeBtn').disabled = revokeInFlight || revokeSubmitted || !state.revokeId || $('revokeDeviceId').value !== state.revokeId; });
$('revokeForm').addEventListener('submit', async event => {
  event.preventDefault();
  const id = state.revokeId;
  if(revokeInFlight || revokeSubmitted || !id || $('revokeDeviceId').value !== id) return;
  const epoch = state.epoch;
  revokeInFlight = true; revokeSubmitted = true; $('revokeDialog').dataset.busy = 'true';
  $('revokeBtn').disabled = true; $('revokeDeviceId').disabled = true;
  try {
    await api('/api/devices/' + encodeURIComponent(id) + '/revoke', {method:'POST'});
    if(epoch !== state.epoch || state.revokeId !== id) return;
    $('revokeDialog').close(); notice('凭证已撤销。恢复使用需要管理员经 USB 配发新凭证。'); await refreshDevices();
  } catch(error) {
    if(epoch === state.epoch && state.revokeId === id && !(error instanceof StaleRequest)) {
      revokeSubmitted = ![400,422].includes(error.status);
      report('revokeError', error);
      if(revokeSubmitted) { $('revokeError').textContent += ' 请关闭后刷新核对，不会自动重试。'; await refreshDevices(); }
    }
  }
  finally { revokeInFlight = false; $('revokeDialog').dataset.busy = 'false'; $('revokeDeviceId').disabled = false; $('revokeBtn').disabled = revokeSubmitted || !state.revokeId || $('revokeDeviceId').value !== state.revokeId; }
});
for(const [id,busy] of [['pairDialog',() => pairInFlight],['revokeDialog',() => revokeInFlight]]) {
  $(id).addEventListener('cancel',event => { if(busy()) event.preventDefault(); });
}

['addDeviceBtn','emptyAddBtn'].forEach(id => $(id).addEventListener('click', openPair));
$('refreshBtn').addEventListener('click', refreshDevices);
document.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => {
  const dialog = $(button.dataset.close);
  if(dialog.dataset.busy !== 'true') dialog.close();
}));
$('memoryDialog').addEventListener('close', () => { state.memoryId = null; $('memoryContent').replaceChildren(); $('clearDeviceId').value = ''; });
$('revokeDialog').addEventListener('close', () => { state.revokeId = null; $('revokeDeviceId').value = ''; });
accountChannel?.addEventListener('message', () => { showLogin(); restoreSession(); });
window.addEventListener('pagehide', resetPrivateView);
window.addEventListener('pageshow', event => { if(event.persisted) { showLogin(); restoreSession(); } });
setInterval(async () => {
  if(!state.user || document.hidden) return;
  try {
    const grant = await api('/api/auth/session');
    if(grant.user.id !== state.user?.id) { await adoptSession(grant); return; }
    $('adminEntry').hidden = grant.is_admin !== true;
    state.csrf = grant.csrf_token; await refreshDevices();
  } catch(error) { report('pageError', error); }
}, 10000);
document.addEventListener('DOMContentLoaded', restoreSession, {once:true});
