'use strict';

const accountConsole = CONSOLE_CONTEXT.mode === 'accounts';
const deviceApiPrefix = accountConsole ? '/api/devices/' + encodeURIComponent(CONSOLE_CONTEXT.device_id) : '';
let consoleReady = false;
let consoleCsrf = '';
let consoleUserId = null;
let consoleEpoch = 0;
let consoleAbort = new AbortController();
let sessionCheckRunning = false;
let consoleLeaving = false;
let consoleChannel = null;

function devicePath(path) { return deviceApiPrefix + path; }

function consoleSocketUrl(kind) {
  const path = accountConsole ? '/ws/devices/' + encodeURIComponent(CONSOLE_CONTEXT.device_id) + '/' + kind : (kind === 'ui' ? '/ws_ui' : '/ws/viewer');
  const url = new URL(path, location.origin);
  url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  return url.href;
}

function consoleMediaUrl(source) {
  const url = new URL(source, location.origin);
  if(url.origin !== location.origin || (accountConsole && !url.pathname.startsWith(deviceApiPrefix + '/')))
    throw new Error('媒体地址不属于当前设备');
  return url.href;
}

async function deviceRequest(path, options={}, json=false) {
  if(!consoleReady) throw new Error('请先登录并连接设备');
  const epoch = consoleEpoch;
  const {timeoutMs=10000, signal, ...requestOptions} = options;
  const headers = new Headers(options.headers || {});
  if(accountConsole && options.method && !['GET','HEAD'].includes(options.method.toUpperCase()))
    headers.set('X-CSRF-Token', consoleCsrf);
  const controller = new AbortController();
  const abort = () => controller.abort();
  const sources = [consoleAbort.signal, signal].filter(Boolean);
  sources.forEach(source => {
    if(source.aborted) abort();
    else source.addEventListener('abort', abort, {once:true});
  });
  let timedOut = false;
  const timeout = setTimeout(() => { timedOut = true; abort(); }, timeoutMs);
  try {
    const response = await fetch(devicePath(path), {...requestOptions, headers, credentials:'same-origin', cache:'no-store', signal:controller.signal});
    if(epoch !== consoleEpoch) throw new Error('设备连接已结束');
    if(!response.ok) {
      if(accountConsole && (response.status === 401 || response.status === 404)) leaveConsole();
      const result = await response.json().catch(() => ({}));
      throw new Error(typeof result.detail === 'string' ? result.detail : '请求失败（' + response.status + '），请稍后重试');
    }
    const result = json ? await response.json() : response;
    if(epoch !== consoleEpoch) throw new Error('设备连接已结束');
    return result;
  } catch(error) {
    if(timedOut) throw new Error('请求超时，服务器可能仍在处理，请刷新状态确认');
    throw error;
  } finally {
    clearTimeout(timeout);
    sources.forEach(source => source.removeEventListener('abort', abort));
  }
}

function deviceFetch(path, options={}) { return deviceRequest(path, options); }
// JSON 请求的超时与账号校验也覆盖响应体读取，避免按钮一直停在处理中。
function deviceJson(path, options={}) { return deviceRequest(path, options, true); }

function clearConsoleData() {
  if(typeof modelInput !== 'undefined') {
    modelInput.value = ''; modelDialog.close(); modelFeedback.textContent = '';
    document.getElementById('modelStatus').textContent = '';
  }
  if(typeof speechDialog !== 'undefined') {
    speechDialog.close(); document.getElementById('speechForm').reset();
    document.getElementById('speechFeedback').textContent = '';
    document.getElementById('transcriptStatus').textContent = '';
  }
  consoleReady = false; consoleEpoch++; consoleAbort.abort();
  consoleCsrf = ''; consoleUserId = null;
  const viewer = wsViewer, ui = wsUi;
  wsViewer = null; wsUi = null;
  if(viewer) { viewer.onmessage = null; viewer.onclose = null; viewer.close(); }
  if(ui) { ui.onmessage = null; ui.onclose = null; ui.close(); }
  stopLocalTtsAudio();
  resetConsoleControls(); applyVoiceState(null);
  ctx.clearRect(0,0,canvas.width,canvas.height); lastFrameAt = 0; setCameraState('idle');
  chatBox.querySelectorAll('.chat-line').forEach(row => row.remove()); partialText.textContent = '';
  entriesCache = []; renderTimeline();
  document.getElementById('lightbox').classList.remove('on');
  document.getElementById('lightboxImg').removeAttribute('src');
  ['promptText','repeatText','askInput'].forEach(id => { document.getElementById(id).value = ''; });
  analysisWrap.style.display = 'none';
  document.getElementById('analysisSummary').textContent = '';
  document.getElementById('ganttWrap').replaceChildren();
  document.querySelector('#activityTable tbody').replaceChildren();
  if(categoryChart) { categoryChart.destroy(); categoryChart = null; }
  document.getElementById('categoryChart').getContext('2d').clearRect(0,0,10000,10000);
  applyRecordState({}); applyPerformanceMode({}); applyVisionScreenState({}); applyTranslationState({});
  toastsEl.replaceChildren();
  document.getElementById('consoleAccountName').textContent = '';
  setConn(camStatus, footerCam, false, '画面未连'); setConn(uiStatus, footerUi, false, '控制未连');
  if(accountConsole) document.body.classList.add('auth-pending');
}

function leaveConsole() {
  if(consoleLeaving) return;
  consoleLeaving = true; clearConsoleData();
  location.replace('/?notice=changed');
}

function consoleSocketClosed(event) {
  if(!accountConsole || consoleLeaving) return;
  if(event.code === 4401 || event.code === 4403) leaveConsole();
  else verifyConsoleSession();
}

async function verifyConsoleSession() {
  if(!accountConsole || consoleLeaving || sessionCheckRunning) return;
  sessionCheckRunning = true;
  const epoch = consoleEpoch;
  try {
    const response = await fetch('/api/auth/session', {credentials:'same-origin', cache:'no-store', signal:consoleAbort.signal});
    if(epoch !== consoleEpoch) return;
    if(response.status === 401) { leaveConsole(); return; }
    if(!response.ok) throw new Error('无法确认登录状态');
    const grant = await response.json();
    if(epoch !== consoleEpoch) return;
    if(consoleUserId && grant.user.id !== consoleUserId) { leaveConsole(); return; }
    const deviceResponse = await fetch('/api/devices', {credentials:'same-origin', cache:'no-store', signal:consoleAbort.signal});
    if(epoch !== consoleEpoch) return;
    if(deviceResponse.status === 401) { leaveConsole(); return; }
    if(!deviceResponse.ok) throw new Error('无法读取设备状态');
    const result = await deviceResponse.json();
    if(epoch !== consoleEpoch) return;
    const device = result.devices.find(item => item.id === CONSOLE_CONTEXT.device_id && item.revoked_at == null && item.control_allowed !== false);
    if(!device) { leaveConsole(); return; }
    consoleCsrf = grant.csrf_token; consoleUserId = grant.user.id;
    document.getElementById('consoleAccountName').textContent = grant.user.username + ' · ' + device.display_name;
    if(!consoleReady) {
      consoleReady = true; document.body.classList.remove('auth-pending');
      connectUI(); fetchInitialRecordState();
    }
    document.getElementById('consoleAccessError').hidden = true;
  } catch(error) {
    if(!consoleLeaving && !consoleReady) {
      document.getElementById('consoleAccessError').hidden = false;
      document.getElementById('consoleAccessError').textContent = '暂时连接不到服务，请刷新重试，或返回我的设备。';
    }
  } finally { sessionCheckRunning = false; }
}

async function logoutConsole() {
  const button = document.getElementById('consoleLogout'); button.disabled = true;
  try {
    const response = await fetch('/api/auth/logout', {method:'POST', headers:{'X-CSRF-Token':consoleCsrf}, credentials:'same-origin', cache:'no-store'});
    if(!response.ok && response.status !== 401) throw new Error('退出失败，请稍后重试');
    consoleChannel?.postMessage({type:'changed'}); leaveConsole();
  } catch(error) { toast('error', error.message); }
  finally { button.disabled = false; }
}

function initializeConsole() {
  if(accountConsole) {
    document.getElementById('consoleAccount').hidden = false;
    if(typeof BroadcastChannel === 'function') {
      consoleChannel = new BroadcastChannel('cybermiao-account');
      consoleChannel.addEventListener('message', leaveConsole);
    }
    verifyConsoleSession();
    setInterval(() => { if(!document.hidden) verifyConsoleSession(); }, 10000);
  } else {
    consoleReady = true; connectUI(); fetchInitialRecordState();
  }
  window.addEventListener('pagehide', clearConsoleData);
  window.addEventListener('pageshow', event => { if(event.persisted) location.reload(); });
}
