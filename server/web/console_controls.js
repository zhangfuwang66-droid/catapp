'use strict';

let voiceState = null;
let voiceControlBusy = false;
let voiceIntent = null;
let voiceSyncPromise = null;
let voiceStateStale = false;
let performanceModeBusy = false;
let cameraState = 'idle';
let uiReconnectTimer = null;

function feedback(id, kind, message) {
  const element = document.getElementById(id);
  element.hidden = !message;
  element.dataset.kind = kind;
  element.textContent = message || '';
}

function renderVoiceState() {
  const state = voiceState;
  const paused = !!state?.paused;
  const busy = voiceControlBusy;
  document.getElementById('pauseVoiceBtn').disabled = !consoleReady || (paused && !voiceStateStale) || busy;
  document.getElementById('resumeVoiceBtn').disabled = !consoleReady || !state || busy ||
    (!paused && (!state.connected || state.active || state.performance_mode));
  document.getElementById('refreshVoiceBtn').disabled = !consoleReady || busy;
  document.getElementById('pauseVoiceText').textContent = busy && voiceIntent === true ? '正在暂停…' : '暂停语音';
  document.getElementById('resumeVoiceText').textContent = busy && voiceIntent === false ? '正在恢复…' : '恢复语音';
  document.getElementById('voiceControls').setAttribute('aria-busy', String(busy));
  ['pauseVoiceBtn', 'resumeVoiceBtn'].forEach((id, index) => {
    document.getElementById(id).classList.toggle('is-loading', busy && voiceIntent === (index === 0));
  });
  let label = !state ? (voiceStateStale ? '语音状态暂不可用' : '正在同步语音状态…') :
    paused ? (state.connected ? '语音已暂停' : '设备离线 · 已设置暂停') :
    state.performance_mode ? '极速画面中 · 语音暂停' :
    !state.connected ? '设备离线' : state.active ? '语音已连接' : '语音正在准备';
  if(voiceStateStale && state) label = '语音状态待确认';
  if(busy) label = voiceIntent ? '正在暂停收音和播放…' : '正在恢复语音…';
  document.getElementById('voiceState').textContent = label;
  const detail = !state ? '暂时读不到状态时，仍可尝试发送暂停指令。' :
    paused ? '已暂停小猫收音和播放，摄像头仍可使用。' :
    state.performance_mode ? '退出极速画面后才能进行语音对话。' :
    !state.connected ? '请确认小猫已开机，并已连接可上网的 Wi-Fi。' :
    state.active ? '可以直接和小猫说话，关闭网页也能继续使用。' : '设备已连上服务器，等待语音服务就绪。';
  document.getElementById('voiceDetail').textContent = voiceStateStale && state ? '状态暂未刷新，以下显示上次确认的结果。' : detail;
  setStatusPill(document.getElementById('voiceOverview'), !busy && !voiceStateStale && state?.active && !paused ? 'on' : '', label);
  document.getElementById('voiceControls').dataset.state = busy ? 'pending' : voiceStateStale ? 'waiting' : paused ? 'paused' : state?.active ? 'active' : 'waiting';
}

function applyVoiceState(state) {
  if(state && voiceState && state.revision < voiceState.revision) return;
  voiceState = state || null;
  voiceStateStale = false;
  if(state?.paused) stopLocalTtsAudio();
  renderVoiceState();
}

async function readVoiceState() {
  const epoch = consoleEpoch;
  const state = await deviceJson('/voice', {timeoutMs:6000});
  if(epoch !== consoleEpoch) throw new Error('设备连接已结束');
  if(!completeVoiceState(state))
    throw new Error('服务器返回的语音状态不完整，请刷新重试');
  applyVoiceState(state);
  return voiceState;
}

function completeVoiceState(state) {
  return state && typeof state.paused === 'boolean' && typeof state.connected === 'boolean' &&
    typeof state.active === 'boolean' && typeof state.performance_mode === 'boolean' && Number.isFinite(state.revision);
}

async function refreshVoiceState(notify=false, fresh=false) {
  if(!consoleReady) return null;
  // 写请求结果未知时，先等旧查询结束，再发起新的只读查询；不重复发送写请求。
  if(fresh && voiceSyncPromise) await voiceSyncPromise;
  if(voiceSyncPromise) return voiceSyncPromise;
  const epoch = consoleEpoch;
  const pending = (async () => {
    try {
      const state = await readVoiceState();
      if(notify && epoch === consoleEpoch) {
        feedback('voiceFeedback', 'success', '已从服务器刷新语音状态。');
      }
      return state;
    } catch(error) {
      if(epoch === consoleEpoch) {
        voiceStateStale = true; renderVoiceState();
        if(notify || !voiceState) feedback('voiceFeedback', 'error', error.message);
      }
      return null;
    }
  })();
  voiceSyncPromise = pending;
  try { return await pending; }
  finally { if(voiceSyncPromise === pending) voiceSyncPromise = null; }
}

function confirmVoiceIntent(paused) {
  if(!voiceState || voiceState.paused !== paused) {
    feedback('voiceFeedback', 'info', '状态已被另一处操作更新，请以当前状态为准。');
    return;
  }
  const message = paused ? (voiceState.connected ? '已暂停小猫收音和播放。' : '已设置暂停，小猫重新上线后也会保持安静。') :
    voiceState.performance_mode ? '已取消手动暂停，退出极速画面后恢复语音。' :
    voiceState.active ? '语音已恢复，可以和小猫说话了。' :
    voiceState.connected ? '已取消暂停，正在等待语音服务就绪。' : '已取消暂停，等待小猫连接。';
  feedback('voiceFeedback', 'success', message);
  toast('success', message);
}

async function setVoicePaused(paused) {
  if(!consoleReady || voiceControlBusy) return;
  const epoch = consoleEpoch;
  voiceControlBusy = true; voiceIntent = paused;
  renderVoiceState();
  feedback('voiceFeedback', 'pending', paused ? '正在请求服务器停止收音与播放…' : '正在请求服务器恢复语音…');
  if(paused) stopLocalTtsAudio();
  try {
    const state = await deviceJson('/voice', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({paused}), timeoutMs:12000});
    if(epoch !== consoleEpoch) return;
    if(!completeVoiceState(state)) throw new Error('语音指令结果不完整');
    applyVoiceState(state);
    confirmVoiceIntent(paused);
  } catch(error) {
    if(epoch !== consoleEpoch) return;
    feedback('voiceFeedback', 'pending', '指令结果尚未确认，正在重新查询实际状态…');
    const state = await refreshVoiceState(false, true);
    if(epoch !== consoleEpoch) return;
    if(state && state.paused === paused) confirmVoiceIntent(paused);
    else {
      const message = error.message + '。' + (state ? '当前状态未达到本次设置，请检查后重试。' : '暂时无法核对结果，请稍后刷新状态。');
      feedback('voiceFeedback', 'error', message); toast('error', message);
    }
  } finally {
    if(epoch === consoleEpoch) {
      voiceControlBusy = false; voiceIntent = null; renderVoiceState();
    }
  }
}

function renderPerformanceControl() {
  document.querySelectorAll('[data-performance-control]').forEach(button => {
    button.disabled = performanceModeBusy || !consoleReady;
    button.classList.toggle('is-loading', performanceModeBusy);
  });
  document.getElementById('perfBtnText').textContent = performanceModeBusy ? '正在切换…' : performanceModeActive ? '退出极速画面' : '极速画面';
}

async function setPerformanceMode(on) {
  if(!consoleReady || performanceModeBusy) return;
  const epoch = consoleEpoch;
  performanceModeBusy = true; renderPerformanceControl();
  feedback('performanceFeedback', 'pending', '正在切换画面模式…');
  try {
    const state = await deviceJson('/performance_mode?active=' + (on ? 1 : 0), {method:'POST', timeoutMs:12000});
    if(epoch !== consoleEpoch) return;
    if(state.active !== on) throw new Error('服务器未确认本次画面模式');
    applyPerformanceMode(state);
    const message = on ? '极速画面已开启，语音和记录等功能已暂停。' : '已退出极速画面。';
    feedback('performanceFeedback', 'success', message); toast('success', message);
    refreshVoiceState();
  } catch(error) {
    if(epoch !== consoleEpoch) return;
    feedback('performanceFeedback', 'error', error.message + '，请核对当前模式。');
    toast('error', error.message);
    try {
      const state = await deviceJson('/performance_mode');
      if(epoch === consoleEpoch) applyPerformanceMode(state);
    } catch { /* 保留上次确认的状态，下一次控制连接会重新同步。 */ }
  } finally {
    if(epoch === consoleEpoch) { performanceModeBusy = false; renderPerformanceControl(); }
  }
}

function setCameraState(state) {
  cameraState = state;
  const live = state === 'live';
  const waiting = state === 'connecting' || state === 'waiting';
  document.getElementById('openCameraBtn').disabled = live || waiting;
  document.getElementById('closeCameraBtn').disabled = state === 'idle';
  document.getElementById('openCameraText').textContent = waiting ? '等待画面…' : live ? '预览已打开' : '打开预览';
  document.getElementById('cameraLive').hidden = !live;
  document.getElementById('cameraEmpty').hidden = live;
  document.getElementById('cameraEmptyTitle').textContent = state === 'idle' ? '画面预览未打开' : state === 'error' ? '暂时没有画面' : '正在等待小猫画面';
  document.getElementById('cameraEmptyText').textContent = state === 'idle' ? '打开预览只影响这个网页，小猫仍在正常运行。' :
    state === 'error' ? '请检查小猫网络，然后重新打开预览。' : '如果长时间没有画面，请确认小猫已开机并联网。';
  setConn(camStatus, footerCam, live, live ? '画面实时' : state === 'idle' ? '预览未开' : state === 'error' ? '画面中断' : '等待画面');
}

function setUiConnectionState(state) {
  setConn(uiStatus, footerUi, state === 'open', state === 'open' ? '控制已连' : state === 'connecting' ? '控制连接中' : '控制未连');
  const button = document.getElementById('reconnectBtn');
  button.disabled = state === 'connecting';
  button.textContent = state === 'connecting' ? '正在连接…' : '重连网页控制';
  document.getElementById('controlHint').textContent = state === 'open' ? '网页控制已连接，可发送文字与硬件指令。' :
    state === 'connecting' ? '正在连接网页控制通道…' : '网页控制已断开，语音暂停按钮仍可通过服务器操作。';
}

function resetConsoleControls() {
  clearTimeout(uiReconnectTimer); uiReconnectTimer = null;
  voiceControlBusy = false; voiceIntent = null; voiceSyncPromise = null; performanceModeBusy = false;
  recordControlBusy = false; recordAskBusy = false; analysisRequestBusy = false;
  ['recordAskBtn', 'recordAnalyzeBtn'].forEach(id => {
    const button = document.getElementById(id); button.disabled = false; button.classList.remove('is-loading');
  });
  ['voiceFeedback', 'messageFeedback', 'performanceFeedback', 'recordFeedback'].forEach(id => feedback(id, '', ''));
}

function selectConsolePanel() {
  const panels = {
    dialog:['语音与对话', '和小猫说说话，也可以用文字发起对话。'],
    vision:['实时画面', '按需打开预览，看看小猫眼前的世界。'],
    record:['记录与分析', '查看小猫观察到的片段，回顾这段时间的活动。'],
    translate:['同声传译', '开启中英互译，让小猫帮你传达。'],
    hardware:['设备设置', '调整声音、屏幕和姿态，调试工具集中在这里。'],
  };
  const requested = location.hash.slice(1);
  const key = Object.prototype.hasOwnProperty.call(panels, requested) ? requested : 'dialog';
  document.querySelectorAll('.workspace-panel').forEach(panel => { panel.hidden = panel.id !== key; });
  document.querySelector('.grid-main').hidden = key === 'hardware';
  document.querySelectorAll('.nav-links a[href^="#"]').forEach(link => {
    const active = link.hash === '#' + key;
    link.classList.toggle('active', active);
    if(active) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
  });
  document.getElementById('workspaceTitle').textContent = panels[key][0];
  document.getElementById('workspaceDescription').textContent = panels[key][1];
  document.title = panels[key][0] + ' · 小喵控制台';
}

function initializeConsoleControls() {
  document.querySelectorAll('.nav-links a[href^="#"]').forEach(link => link.addEventListener('click', event => {
    event.preventDefault();
    if(location.hash !== link.hash) location.hash = link.hash;
    selectConsolePanel(); window.scrollTo(0, 0);
  }));
  window.addEventListener('hashchange', selectConsolePanel);
  document.querySelectorAll('[data-accounts-only]').forEach(element => { element.hidden = !accountConsole; });
  document.getElementById('runModeNote').textContent = accountConsole ? '服务在线即可使用 · 无需一直开着网页' : '本机服务模式 · 使用时请保持后端运行';
  selectConsolePanel(); setCameraState('idle'); renderVoiceState();
  document.addEventListener('visibilitychange', () => { if(!document.hidden && consoleReady) refreshVoiceState(); });
  setInterval(() => { if(consoleReady && !document.hidden && !voiceControlBusy) refreshVoiceState(); }, 8000);
}
