'use strict';

const $ = id => document.getElementById(id);
const state = {user:null, csrf:'', epoch:0, devices:[], memoryId:null, revokeId:null};
let noticeTimer = null;
let refreshRequest = null;
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
  const response = await fetch(path, {...options, headers, credentials:'same-origin', cache:'no-store'});
  const data = await response.json().catch(() => ({}));
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
  $('deviceGrid').replaceChildren(); $('memoryContent').replaceChildren();
  ['username','avatar','memoryTitle','pageError','pairError','memoryError','revokeError'].forEach(id => { $(id).textContent = ''; });
  ['loginUsername','loginPassword','pairCode','clearDeviceId','revokeDeviceId'].forEach(id => { $(id).value = ''; });
  $('deviceCount').textContent = '0'; $('sideCount').textContent = '0'; $('onlineCount').textContent = '0';
  $('clearDetails').open = false; $('clearBtn').disabled = true; $('revokeBtn').disabled = true;
  $('accountBar').hidden = true; $('devicesView').hidden = true;
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
    const online = !disabled && (device.audio_online || device.camera_online);
    const card = element('article', 'device-card');
    const top = element('div', 'device-top');
    const portrait = element('div', 'device-portrait');
    portrait.innerHTML = '<svg aria-hidden="true"><use href="#cat"/></svg>';
    top.append(portrait, element('span', 'state' + (disabled ? ' disabled' : online ? ' online' : ''), disabled ? '已停用' : online ? '● 在线' : '离线'));
    const channels = element('div', 'channels');
    channels.append(element('span', 'channel' + (device.audio_online ? ' active' : ''), device.audio_online ? '语音已连接' : '语音未连接'),
                    element('span', 'channel' + (device.camera_online ? ' active' : ''), device.camera_online ? '画面已连接' : '画面未连接'));
    const actions = element('div', 'card-actions');
    const consoleLink = element('a', 'primary', '进入控制台 ↗');
    if(!disabled) consoleLink.href = '/console/' + encodeURIComponent(device.id);
    else consoleLink.setAttribute('aria-disabled', 'true');
    const memory = element('button', 'secondary', '查看记忆');
    memory.type = 'button'; memory.disabled = disabled; memory.addEventListener('click', () => openMemory(device));
    actions.append(consoleLink, memory);
    const bottom = element('div', 'card-bottom');
    const boundDate = device.bound_at ? new Date(device.bound_at * 1000).toLocaleDateString('zh-CN') + ' 已相识' : '已绑定到你的账号';
    bottom.append(element('span', '', boundDate));
    const revoke = element('button', 'quiet', disabled ? '已停用' : '停用设备');
    revoke.type = 'button'; revoke.disabled = disabled;
    revoke.addEventListener('click', () => openRevoke(device)); bottom.append(revoke);
    card.append(top, element('h2', '', device.display_name), element('div', 'device-id', device.id), channels, actions, bottom);
    $('deviceGrid').append(card);
  });
}

function openPair() {
  if(!state.user) return;
  $('pairError').textContent = ''; $('pairCode').value = ''; $('pairBtn').disabled = false;
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
    const data = await api('/api/devices/' + encodeURIComponent(device.id) + '/memory');
    if(state.memoryId === device.id) renderMemory(data);
  } catch(error) { if(state.memoryId === device.id) report('memoryError', error); }
}

function openRevoke(device) {
  state.revokeId = device.id;
  $('revokeDeviceId').value = ''; $('revokeBtn').disabled = true; $('revokeError').textContent = '';
  $('revokeLabel').textContent = '输入 ' + device.id + ' 以确认停用';
  $('revokeDialog').showModal();
}

$('loginForm').addEventListener('submit', async event => {
  event.preventDefault(); $('loginError').textContent = ''; $('loginBtn').disabled = true;
  try {
    const grant = await api('/api/auth/login', {method:'POST', body:JSON.stringify({username:$('loginUsername').value.trim(), password:$('loginPassword').value})}, true);
    $('loginPassword').value = '';
    accountChannel?.postMessage({type:'changed'});
    await adoptSession(grant);
  } catch(error) { report('loginError', error); }
  finally { $('loginBtn').disabled = false; }
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
  event.preventDefault(); $('pairError').textContent = ''; $('pairBtn').disabled = true;
  try {
    await api('/api/devices/pair', {method:'POST', body:JSON.stringify({code:$('pairCode').value.trim().toUpperCase()})});
    $('pairDialog').close(); notice('配对成功，欢迎你的小喵。'); await refreshDevices();
  } catch(error) { report('pairError', error); }
  finally { $('pairBtn').disabled = false; }
});

$('clearDeviceId').addEventListener('input', () => { $('clearBtn').disabled = !state.memoryId || $('clearDeviceId').value !== state.memoryId; });
$('clearForm').addEventListener('submit', async event => {
  event.preventDefault();
  const id = state.memoryId;
  if(!id || $('clearDeviceId').value !== id) return;
  $('clearBtn').disabled = true; $('clearDeviceId').disabled = true; $('memoryError').textContent = '';
  try {
    await api('/api/devices/' + encodeURIComponent(id) + '/memory', {method:'DELETE', body:JSON.stringify({confirm_device_id:id})});
    if(state.memoryId !== id) return;
    renderMemory({}); $('clearDetails').open = false; $('clearDeviceId').value = '';
    notice('这只小喵的聊天记忆已清除，可以重新开始对话。');
  } catch(error) { if(state.memoryId === id) report('memoryError', error); }
  finally { $('clearDeviceId').disabled = false; $('clearBtn').disabled = !state.memoryId || $('clearDeviceId').value !== state.memoryId; }
});

$('revokeDeviceId').addEventListener('input', () => { $('revokeBtn').disabled = !state.revokeId || $('revokeDeviceId').value !== state.revokeId; });
$('revokeForm').addEventListener('submit', async event => {
  event.preventDefault();
  const id = state.revokeId;
  if(!id || $('revokeDeviceId').value !== id) return;
  $('revokeBtn').disabled = true; $('revokeDeviceId').disabled = true;
  try {
    await api('/api/devices/' + encodeURIComponent(id) + '/revoke', {method:'POST'});
    $('revokeDialog').close(); notice('设备已停用。'); await refreshDevices();
  } catch(error) { report('revokeError', error); }
  finally { $('revokeDeviceId').disabled = false; $('revokeBtn').disabled = !state.revokeId || $('revokeDeviceId').value !== state.revokeId; }
});

['addDeviceBtn','emptyAddBtn'].forEach(id => $(id).addEventListener('click', openPair));
$('refreshBtn').addEventListener('click', refreshDevices);
document.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => $(button.dataset.close).close()));
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
    state.csrf = grant.csrf_token; await refreshDevices();
  } catch(error) { report('pageError', error); }
}, 10000);
restoreSession();
