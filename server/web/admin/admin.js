'use strict';

const $ = id => document.getElementById(id);
const views = {
  overview: ['概览', '账号、设备与服务状态'], users: ['用户', '账号权限与网页登录'],
  devices: ['设备', '绑定关系与连接状态'], storage: ['资料与记忆', '用量、处理与归档元数据'],
  backups: ['备份', '备份状态与异常'], audit: ['操作审计', '管理员操作记录'],
};
const auditActions = new Map([
  ['disable-user','停用账号'], ['enable-user','启用账号'], ['revoke-sessions','撤销网页登录'],
  ['pause-voice','暂停语音'], ['resume-voice','恢复语音'], ['unbind-device','解绑设备'],
  ['grant-admin','授予管理员'], ['revoke-admin','撤销管理员'],
]);
const deviceTypes = new Map([['cat','小喵'],['deer','小鹿'],['unknown','未知类型']]);
// queued 是当前资料库值；兼容服务端曾返回的 pending，二者都计入待处理。
const documentStatuses = new Map([
  ['queued','待处理'], ['pending','待处理'], ['processing','处理中'],
  ['ready','已就绪'], ['keyword','关键词检索可用'], ['failed','处理失败'],
]);
const state = {view:'overview', csrf:'', user:null, requests:{}, serial:{}, busy:false, denied:false, unbindFollowups:new Map(),
  pages:{users:1,devices:1,audit:1}, queries:{users:'',devices:'',audit:''},
  statuses:{users:'all',devices:'all'}};
let toastTimer, recoveryContext;

class ApiError extends Error {
  constructor(message, status=0) { super(message); this.status = status; }
}

function text(id, value) { $(id).textContent = value == null || value === '' ? '—' : String(value); }
function node(tag, content='', className='') {
  const element = document.createElement(tag);
  element.textContent = content == null ? '' : String(content);
  if(className) element.className = className;
  return element;
}
function addCell(row, content, className='') { const cell = node('td', content, className); row.append(cell); return cell; }
function labelRows(body) {
  const labels = [...body.closest('table').querySelectorAll('thead th')].map(cell => cell.textContent);
  body.querySelectorAll('tr').forEach(row => [...row.cells].forEach((cell,index) => { cell.dataset.label = labels[index]; }));
}
function pill(content, kind='') { return node('span', content, `pill ${kind}`.trim()); }
function date(value) {
  if(value == null || value === '') return '暂无';
  const parsed = new Date(Number(value) * 1000);
  return Number.isNaN(parsed.getTime()) ? '暂无' : new Intl.DateTimeFormat('zh-CN', {dateStyle:'medium',timeStyle:'short'}).format(parsed);
}
function bytes(value) {
  if(value == null || !Number.isFinite(Number(value))) return '暂无';
  const amount = Number(value);
  if(amount < 1024) return `${amount} B`;
  const unit = Math.min(Math.floor(Math.log(amount) / Math.log(1024)), 4);
  return `${(amount / 1024 ** unit).toFixed(amount / 1024 ** unit >= 10 ? 0 : 1)} ${['B','KB','MB','GB','TB'][unit]}`;
}
function duration(value) {
  if(value == null || !Number.isFinite(Number(value))) return '暂无';
  const seconds = Math.max(0, Math.floor(Number(value)));
  const days = Math.floor(seconds / 86400), hours = Math.floor(seconds % 86400 / 3600);
  const minutes = Math.floor(seconds % 3600 / 60);
  return days ? `${days} 天 ${hours} 小时` : hours ? `${hours} 小时 ${minutes} 分钟` : `${minutes} 分钟`;
}
function feedback(message='', kind='') {
  $('pageFeedback').hidden = !message;
  $('pageFeedback').className = `feedback ${kind}`;
  $('pageFeedback').textContent = message;
}
function toast(message, error=false) {
  clearTimeout(toastTimer);
  $('toast').textContent = message;
  $('toast').className = error ? 'toast error' : 'toast';
  $('toast').hidden = false;
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, 4500);
}
function gate(message, denied=false) {
  $('shell').hidden = true;
  $('gate').hidden = false;
  $('gate').className = denied ? 'gate denied' : 'gate';
  $('gate').replaceChildren(node('span', message));
  if(denied) { const link = node('a', '返回我的小喵'); link.href = '/'; $('gate').append(link); }
}
function loginExpired() {
  Object.values(state.requests).forEach(controller => controller.abort());
  window.location.assign('/?notice=login');
}
async function api(path, {method='GET', body, signal, timeout=12000}={}) {
  if(state.denied) throw new ApiError('管理员权限已失效，请重新登录后核对。',403);
  const writing = method !== 'GET' && method !== 'HEAD';
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  const cancel = () => controller.abort();
  signal?.addEventListener('abort', cancel, {once:true});
  if(signal?.aborted) cancel();
  const headers = new Headers({'Accept':'application/json'});
  if(body !== undefined) headers.set('Content-Type', 'application/json');
  if(writing) {
    headers.set('X-CSRF-Token', state.csrf);
    headers.set('Origin', location.origin);
  }
  try {
    const response = await fetch(path, {method,headers,body:body === undefined ? undefined : JSON.stringify(body),
      credentials:'same-origin',cache:'no-store',signal:controller.signal});
    const payload = await response.json().catch(error => {
      if(response.ok || error.name === 'AbortError') throw error;
      return {};
    });
    if(response.status === 401) { loginExpired(); throw new ApiError('登录已过期，请重新登录。', 401); }
    if(response.status === 403 && !writing && path.startsWith('/api/admin/')) {
      state.denied = true;
      gate('当前账号已无权访问管理员后台，请重新登录后核对。',true);
      document.querySelectorAll('dialog[open]').forEach(dialog => dialog.close());
    }
    if(!response.ok) {
      const detail = typeof payload.detail === 'string' ? payload.detail : '';
      const fallback = response.status === 403 ? '无权执行此操作，或请求校验已失效。' :
        response.status === 409 ? '状态已变化，请刷新后重试。' : writing ? '服务未能确认操作结果。' : '请求失败，请稍后重试。';
      throw new ApiError(detail || fallback, response.status);
    }
    return payload;
  } catch(error) {
    if(error.name === 'AbortError') {
      if(signal?.aborted) throw error;
      throw new ApiError(writing ? '请求超时，未能确认操作结果。' : '请求超时，请检查网络后重试。');
    }
    if(error instanceof ApiError) throw error;
    throw new ApiError(writing ? '连接中断或响应异常，未能确认操作结果。' : '暂时无法连接服务，请稍后重试。');
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', cancel);
  }
}

function queryPath(view) {
  const params = new URLSearchParams();
  params.set('page', state.pages[view]); params.set('page_size', '20');
  if(state.queries[view]) params.set('q', state.queries[view]);
  if(view !== 'audit') params.set('status', state.statuses[view]);
  return `/api/admin/${view}?${params}`;
}
async function load(view=state.view) {
  if(!views[view] || state.denied) return false;
  state.requests[view]?.abort();
  const controller = new AbortController();
  const serial = (state.serial[view] || 0) + 1;
  state.requests[view] = controller; state.serial[view] = serial;
  if(view === state.view) $('refreshButton').disabled = true;
  if(['users','devices','audit'].includes(view)) {
    $(`${view}Prev`).disabled = true; $(`${view}Next`).disabled = true;
  }
  if(view === state.view) feedback('正在加载…');
  try {
    const data = await api(['users','devices','audit'].includes(view) ? queryPath(view) : `/api/admin/${view}`,
      {signal:controller.signal});
    if(serial !== state.serial[view]) return false;
    ({overview:renderOverview,users:renderUsers,devices:renderDevices,storage:renderStorage,
      backups:renderBackups,audit:renderAudit})[view](data);
    if(view === state.view) feedback();
    return true;
  } catch(error) {
    if(error.name === 'AbortError' || serial !== state.serial[view] || error.status === 401) return false;
    if(view === state.view) feedback(error.message, 'error');
    return false;
  } finally {
    if(state.requests[view] === controller) {
      delete state.requests[view];
      if(view === state.view) $('refreshButton').disabled = false;
    }
  }
}
function setView(view) {
  if(!views[view]) view = 'overview';
  state.view = view;
  document.querySelectorAll('.view').forEach(element => { element.hidden = element.id !== `view-${view}`; });
  document.querySelectorAll('.nav-item').forEach(button => {
    button.classList.toggle('active', button.dataset.view === view);
    if(button.dataset.view === view) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });
  text('pageTitle', views[view][0]); text('crumbTitle', views[view][0]); text('pageSubtitle', views[view][1]);
  $('shell').classList.remove('menu-open'); $('menuToggle').setAttribute('aria-expanded', 'false');
  history.replaceState(null, '', `#${view}`);
  load(view);
}
function renderOverview(data) {
  text('metricUsers', data.users?.total); text('metricUsersNote', data.users ? `${data.users.enabled} 启用 · ${data.users.disabled} 停用` : '未接入');
  text('metricDevices', data.devices?.total); text('metricDevicesNote', data.devices ? `${data.devices.bound} 已绑定` : '未接入');
  text('metricUptime', duration(data.uptime_seconds));
  const storage = data.storage;
  const total = storage && Number(storage.database_bytes || 0) + Number(storage.wal_bytes || 0);
  text('metricStorage', storage ? bytes(total) : '暂无');
  text('metricStorageNote', storage ? '数据库与 WAL 文件' : '未接入');
  text('overviewConnections', data.devices?.online == null ? '未接入' : `${data.devices.online} 台可核实连接`);
  text('overviewVoice', '请在设备页查看');
  text('overviewBackup', data.backups?.last_success_at ? date(data.backups.last_success_at) : '暂无成功备份');
  text('overviewTime', `更新于 ${new Intl.DateTimeFormat('zh-CN',{dateStyle:'medium',timeStyle:'short'}).format(new Date())}`);
}
function pagination(view, data) {
  const total = Number(data.total || 0), page = Number(data.page || state.pages[view]);
  const size = Number(data.page_size || 20), pages = Math.max(1, Math.ceil(total / size));
  text(`${view}Count`, `共 ${total} 条`); text(`${view}PageInfo`, `第 ${page} / ${pages} 页`);
  $(`${view}Prev`).disabled = page <= 1; $(`${view}Next`).disabled = page >= pages;
  $(`${view}Pagination`).hidden = total === 0;
}
function renderUsers(data) {
  const body = $('usersBody'); body.replaceChildren();
  const items = Array.isArray(data.items) ? data.items : [];
  items.forEach(user => {
    const row = node('tr'); const name = addCell(row); name.append(node('strong', user.username), node('small', `ID ${user.id}${user.is_admin ? ' · 管理员' : ''}`));
    addCell(row).append(pill(user.disabled ? '已停用' : '已启用', user.disabled ? 'bad' : 'good'));
    addCell(row, user.device_count ?? '暂无');
    addCell(row).append(pill(user.key_configured ? '已配置' : '未配置', user.key_configured ? 'good' : ''));
    addCell(row, date(user.created_at));
    const actions = node('div', '', 'row-actions');
    const self = user.id === state.user?.id;
    actionButton(actions, user.disabled ? '启用' : '停用', () => userAction(user, 'status'), !user.disabled, self);
    actionButton(actions, '撤销登录', () => userAction(user, 'revoke-sessions'), true, self);
    addCell(row, '', 'actions').append(actions); body.append(row);
  });
  $('usersEmpty').hidden = items.length > 0; $('usersEmpty').textContent = data.total ? '本页暂无用户记录' : '暂无用户记录';
  labelRows(body);
  pagination('users', data);
}
function renderDevices(data) {
  const body = $('devicesBody'); body.replaceChildren();
  const items = Array.isArray(data.items) ? data.items : [];
  items.forEach(device => {
    const row = node('tr'); const name = addCell(row); name.append(node('strong', device.display_name || device.id), node('small', device.id));
    addCell(row, deviceTypes.get(device.device_type) || '未知类型');
    const relation = addCell(row);
    relation.append(node('strong', device.owner_username || '未绑定'));
    if(device.loan_active) relation.append(node('small', `当前由 ${device.active_username || '未知用户'} 借用${device.loan_expires_at ? ` · 至 ${date(device.loan_expires_at)}` : ''}`));
    else if(device.loan_pending) relation.append(node('small', '借用邀请待接受'));
    addCell(row).append(pill(device.revoked ? '已撤销' : device.online ? '已连接' : '未连接', device.revoked ? 'bad' : device.online ? 'good' : ''));
    const voice = device.voice_paused == null ? '未接入' : device.voice_paused ? '已暂停' : '可用';
    const voiceCell = addCell(row); voiceCell.append(pill(voice, device.voice_paused === true ? 'warn' : device.voice_paused === false ? 'good' : ''));
    voiceCell.append(node('small', `音频 ${device.audio_online ? '已连接' : '未连接'} · 摄像头 ${device.camera_online ? '已连接' : '未连接'}`));
    const actions = node('div', '', 'row-actions');
    if(device.voice_paused != null && !device.revoked) actionButton(actions, device.voice_paused ? '恢复语音' : '暂停语音', () => deviceAction(device,'voice'), device.voice_paused);
    if(!device.revoked) {
      if(device.owner_user_id == null) actionButton(actions, '核对收尾', () => openUnbindRecovery(device.id));
      else if(state.unbindFollowups.has(device.id)) actionButton(actions, '核对解绑结果', () => openUnbindRecovery(device.id));
      else actionButton(actions, '解绑', () => deviceAction(device,'unbind'), true);
    }
    addCell(row, '', 'actions').append(actions); body.append(row);
  });
  $('devicesEmpty').hidden = items.length > 0; $('devicesEmpty').textContent = data.total ? '本页暂无设备记录' : '暂无设备记录';
  labelRows(body);
  pagination('devices', data);
}
function renderStorage(data) {
  text('storageDocuments', data.documents?.count); text('storageMemories', data.memory?.memories);
  text('storageBytes', data.documents && data.memory ? bytes(Number(data.documents.bytes || 0) + Number(data.memory.content_bytes || 0) + Number(data.memory.archive_bytes || 0)) : '暂无');
  const statuses = data.documents?.statuses;
  text('storagePending', statuses ? Number(statuses.pending || 0) + Number(statuses.queued || 0) : '暂无');
  text('storageProcessing', statuses ? Number(statuses.processing || 0) : '暂无');
  text('storageTime', `更新于 ${new Intl.DateTimeFormat('zh-CN',{dateStyle:'medium',timeStyle:'short'}).format(new Date())}`);
  const body = $('storageBody'); body.replaceChildren();
  Object.entries(statuses || {}).forEach(([status,count]) => {
    const row = node('tr'); addCell(row, '资料处理'); addCell(row, count);
    const cell = addCell(row, documentStatuses.get(status) || '未知状态'); cell.title = status;
    addCell(row, '暂无'); body.append(row);
  });
  if(data.memory) {
    for(const [label,count] of [['对话',data.memory.conversations],['消息',data.memory.messages],['摘要',data.memory.summaries],['归档消息',data.memory.archived_messages]]) {
      const row = node('tr'); addCell(row,label); addCell(row,count ?? '暂无'); addCell(row,'元数据'); addCell(row,'暂无'); body.append(row);
    }
  }
  $('storageEmpty').hidden = body.children.length > 0;
  labelRows(body);
}
function renderBackups(data) {
  const body = $('backupsBody'); body.replaceChildren();
  text('backupSummary', data.enabled === false ? '自动备份未启用' : `已保留 ${data.backup_count ?? '未知'} 份 · 跳过未验证 ${data.skipped_unverified ?? '未知'} 份 · 已清理 ${data.pruned ?? '未知'} 份`);
  const rows = [];
  if(data.last_success_at) rows.push({time:data.last_success_at,status:'最近成功',problem:'无'});
  if(data.running) rows.push({time:null,status:'运行中',problem:'无'});
  if(data.last_error_type) rows.push({time:data.last_failure_at,status:'最近异常',problem:data.last_error_type});
  rows.forEach(item => { const row = node('tr'); addCell(row,date(item.time)); addCell(row).append(pill(item.status,item.status === '最近异常' ? 'bad' : item.status === '运行中' ? 'warn' : 'good')); addCell(row,item.problem); body.append(row); });
  $('backupsEmpty').hidden = rows.length > 0;
  $('backupsEmpty').textContent = data.enabled === false ? '自动备份未启用' : '暂无备份执行记录';
  labelRows(body);
}
function renderAudit(data) {
  const body = $('auditBody'); body.replaceChildren();
  const items = Array.isArray(data.items) ? data.items : [];
  items.forEach(item => {
    const row = node('tr'); addCell(row,date(item.created_at)); addCell(row,item.actor_username || `ID ${item.actor_id}`);
    const action = addCell(row,auditActions.get(item.action) || (item.action ? `未知操作（${item.action}）` : '未知操作'));
    if(item.action) action.title = item.action;
    addCell(row,`${item.target_type || '对象'} · ${item.target_id ?? '—'}`);
    addCell(row).append(pill(item.result === 'success' ? '成功' : item.result === 'failed' ? '失败' : item.result === 'started' ? '未完成' : '未知', item.result === 'success' ? 'good' : item.result === 'failed' ? 'bad' : 'warn'));
    body.append(row);
  });
  $('auditEmpty').hidden = items.length > 0; $('auditEmpty').textContent = data.total ? '本页暂无审计记录' : '暂无审计记录';
  labelRows(body);
  pagination('audit', data);
}
function actionButton(parent, label, handler, danger=false, disabled=false) {
  const button = node('button',label,danger ? 'danger' : ''); button.type = 'button';
  button.disabled = disabled;
  if(disabled) button.title = '不能对当前账号执行此操作';
  button.addEventListener('click', handler); parent.append(button);
}
function confirmAction({title,description,expected,buttonLabel,run,followupId}) {
  const dialog = $('confirmDialog');
  if(state.busy || state.denied || dialog.open || $('unbindDialog').open) return;
  const view = state.view;
  let active = true, submitted = false;
  text('confirmTitle',title); text('confirmDescription',description);
  text('confirmLabel', `输入 ${expected} 以确认`);
  $('confirmLabel').hidden = false; $('confirmInput').hidden = false;
  $('confirmInput').disabled = false; $('confirmCancel').disabled = false; text('confirmCancel','取消');
  $('confirmInput').value = ''; $('confirmError').textContent = '';
  $('confirmFollowup').hidden = true;
  text('confirmSubmit',buttonLabel); $('confirmSubmit').disabled = true;
  dialog.showModal(); $('confirmInput').focus();
  const update = () => { $('confirmSubmit').disabled = state.busy || submitted || $('confirmInput').value !== expected; };
  const close = () => { if(state.busy) return; cleanup(); dialog.close(); };
  const cancel = event => { event.preventDefault(); close(); };
  const followup = () => { if(state.busy) return; close(); openUnbindRecovery(followupId); };
  const submit = async event => {
    event.preventDefault();
    if(!active || state.busy || submitted || $('confirmInput').value !== expected) return;
    submitted = true;
    state.busy = true; $('confirmSubmit').disabled = true; $('confirmCancel').disabled = true;
    $('confirmInput').disabled = true;
    text('confirmSubmit','处理中…'); $('confirmError').textContent = '';
    try {
      let failure;
      try { await run(); } catch(error) { failure = error; }
      if(failure?.status === 401) return;
      const notice = failure ? `${failure.message || '未能确认操作结果。'}\n${!failure.status || failure.status >= 500 ?
        '操作可能已经生效或仍在处理中，勿直接重复提交。' : '请先核对状态，勿直接重复提交。'}` : '操作已完成。';
      if(active) { text('confirmSubmit','正在核对…'); text('confirmError',`${notice}\n正在核对${views[view][0]}与操作审计…`); }
      // 写请求超时或中断后服务端可能仍在执行；只读核对，不自动重试写入。
      const checked = (await Promise.all([load(view),load('audit')])).every(Boolean);
      const message = `${notice}\n${checked ? `已刷新${views[view][0]}与操作审计，请关闭后核对。` :
        '状态或审计刷新未完成，请关闭后手动刷新核对。'}`;
      if(active && !failure && checked) { cleanup(); dialog.close(); toast('操作已完成'); }
      else if(active) text('confirmError',message);
      else toast(`${title}：${message}`,Boolean(failure) || !checked);
    } finally {
      state.busy = false;
      if(active) {
        $('confirmCancel').disabled = false; text('confirmCancel','关闭'); text('confirmSubmit','已提交'); update();
        $('confirmFollowup').hidden = !followupId || !state.unbindFollowups.has(followupId);
      }
    }
  };
  const cleanup = () => {
    active = false;
    $('confirmInput').removeEventListener('input',update); $('confirmForm').removeEventListener('submit',submit);
    $('confirmCancel').removeEventListener('click',close); dialog.removeEventListener('cancel',cancel); dialog.removeEventListener('close',cleanup);
    $('confirmFollowup').removeEventListener('click',followup);
  };
  $('confirmInput').addEventListener('input',update); $('confirmForm').addEventListener('submit',submit);
  $('confirmCancel').addEventListener('click',close); dialog.addEventListener('cancel',cancel); dialog.addEventListener('close',cleanup,{once:true});
  $('confirmFollowup').addEventListener('click',followup);
}
function userAction(user, kind) {
  const status = kind === 'status', disabled = !user.disabled;
  confirmAction({title:status ? `${disabled ? '停用' : '启用'}账号` : '撤销网页登录', expected:user.username,
    buttonLabel:status ? `确认${disabled ? '停用' : '启用'}` : '确认撤销',
    description:status ? (disabled ? `将停用 ${user.username}。该账号无法继续登录，相关连接和运行任务会结束。` : `将启用 ${user.username}，该账号可重新登录。`) :
      `将撤销 ${user.username} 的全部网页登录，并结束相关连接和运行任务。账号与设备数据会保留。`,
    run:() => api(`/api/admin/users/${encodeURIComponent(user.id)}/${kind}`,{method:'POST',body:status ? {disabled,confirm_username:user.username} : {confirm_username:user.username}})});
}
function deviceAction(device, kind) {
  const voice = kind === 'voice', paused = !device.voice_paused;
  confirmAction({title:voice ? (paused ? '暂停语音' : '恢复语音') : '解绑设备', expected:device.id,
    buttonLabel:voice ? (paused ? '确认暂停' : '确认恢复') : '确认解绑',
    description:voice ? (paused ? `将暂停 ${device.id} 的语音。已建立的语音连接可能结束。` : `将恢复 ${device.id} 的语音。恢复后可能建立模型连接并产生调用费用。`) :
      `将解除 ${device.id} 与当前主人的绑定及借用关系。硬件凭证、Wi-Fi 配置和既有记忆会保留。`,
    followupId:voice ? undefined : device.id,
    run:voice ? () => api(`/api/admin/devices/${encodeURIComponent(device.id)}/voice`,{method:'POST',body:{paused,confirm_device_id:device.id}}) :
      async () => {
        const current = await readUnbindState(device.id);
        if(!current.device || current.device.revoked || current.device.owner_user_id !== device.owner_user_id || current.device.active_user_id !== device.active_user_id)
          throw new ApiError('设备归属已变化，本次未提交。请关闭后重新核对目标。',409);
        return sendUnbind(device.id,current,false);
      }});
}

function followupKey() { return `admin-unbind-followups:${state.user?.id}`; }
function saveUnbindFollowups() {
  // 仅在当前浏览器会话保存目标与响应状态；刷新不能把超时转成可安全重试。
  try { sessionStorage.setItem(followupKey(),JSON.stringify([...state.unbindFollowups.values()])); } catch {}
  renderUnbindFollowups();
}
function restoreUnbindFollowups() {
  try {
    const records = JSON.parse(sessionStorage.getItem(followupKey()) || '[]');
    if(Array.isArray(records)) for(const item of records) {
      if(typeof item.id === 'string' && item.id.length <= 64 && Number.isSafeInteger(item.after) && item.after >= 0 &&
        ['partial','uncertain'].includes(item.response)) state.unbindFollowups.set(item.id,item);
    }
  } catch {}
  renderUnbindFollowups();
}
function renderUnbindFollowups() {
  const body = $('unbindFollowupsBody'); body.replaceChildren();
  $('unbindFollowups').hidden = state.unbindFollowups.size === 0;
  for(const item of state.unbindFollowups.values()) {
    const row = node('div','','followup-row'), detail = node('div');
    detail.append(node('strong',item.id),node('small',item.response === 'partial' ? '请求已返回异常，收尾尚未确认' : '请求结果未确认，后台可能仍在处理'));
    const actions = node('div','','row-actions'); actionButton(actions,'核对解绑结果',() => openUnbindRecovery(item.id));
    row.append(detail,actions); body.append(row);
  }
}
async function readUnbindState(id,signal) {
  const session = await api('/api/admin/session',{signal});
  if(String(session.user?.id) !== String(state.user?.id)) throw new ApiError('登录账号已变化，请重新打开后台后核对。',403);
  if(session.csrf_token) state.csrf = session.csrf_token;
  const query = new URLSearchParams({q:id,page:'1',page_size:'100'});
  const [devices,audits] = await Promise.all([
    api(`/api/admin/devices?${query}&status=all`,{signal}),api(`/api/admin/audit?${query}`,{signal}),
  ]);
  if(!Array.isArray(devices.items) || !Array.isArray(audits.items)) throw new ApiError('核对数据不完整，暂不能重试。');
  const entries = audits.items.filter(item => item.target_type === 'device' && String(item.target_id) === id &&
    item.action === 'unbind-device' && Number.isSafeInteger(item.id)).sort((a,b) => b.id-a.id);
  return {device:devices.items.find(item => item.id === id),entries,latest:entries[0],auditComplete:audits.total <= audits.items.length};
}
function unbindVerdict(id,current) {
  const device = current.device, record = state.unbindFollowups.get(id), latest = current.latest;
  if(!device) return {message:'未找到目标设备，核对未完成，不能重试收尾。'};
  if(device.revoked) return {message:'设备凭证已撤销，不能通过本次跟进重试。请核对设备状态。'};
  if(device.owner_user_id !== null || device.active_user_id !== null)
    return {message:`设备当前属于 ${device.owner_username || device.owner_user_id || '未知用户'}，归属已变化或解绑尚未生效。不能重试旧解绑收尾，请重新核对。`};
  if(latest?.result === 'success' && (!record || latest.id > record.after)) {
    state.unbindFollowups.delete(id); saveUnbindFollowups();
    return {message:'最近一次解绑审计已记录成功，当前设备未绑定。无需重试收尾。'};
  }
  const newer = record ? current.entries.filter(item => item.id > record.after) : [];
  if(record?.response === 'partial' && current.auditComplete && newer.length === 1 &&
    String(newer[0].actor_id) === String(state.user.id) && (!record.auditId || record.auditId === latest.id) &&
    ['started','failed'].includes(latest?.result)) {
    record.auditId = latest.id; saveUnbindFollowups();
    return {allowed:true,message:`当前设备未绑定；该次请求已返回 503，审计仍为${latest.result === 'started' ? '未完成' : '失败'}。\n本次核对不代表连接和运行任务已清理。可以明确确认后，只重试解绑收尾。`};
  }
  if(record || latest?.result === 'started') return {message:'解绑收尾尚未确认，后台可能仍在处理中，或审计无法与上次请求准确对应。\n请稍后重新核对；当前不能重试，也不能据此认定后台已完成。'};
  return {message:'当前设备未绑定，没有可安全重试的解绑收尾记录。'};
}
async function sendUnbind(id,current,cleanupOnly) {
  const record = {id,after:current.latest?.id || 0,response:'uncertain'};
  state.unbindFollowups.set(id,record); saveUnbindFollowups();
  try {
    const result = await api(`/api/admin/devices/${encodeURIComponent(id)}/unbind`,{method:'POST',
      body:cleanupOnly ? {confirm_device_id:id,cleanup_only:true} : {confirm_device_id:id}});
    state.unbindFollowups.delete(id);
    return result;
  } catch(error) {
    if(error.status === 503) record.response = 'partial';
    throw error;
  } finally { saveUnbindFollowups(); }
}
function recoveryControls(context) {
  const busy = context.checking || context.writing;
  $('unbindCheck').disabled = busy; $('unbindClose').disabled = context.writing;
  $('unbindConfirm').disabled = busy;
  $('unbindRetry').disabled = busy || !context.allowed || $('unbindConfirm').value !== context.id;
}
async function checkUnbind(context,notice='') {
  context.controller?.abort();
  const controller = new AbortController(); context.controller = controller;
  context.checking = true; context.allowed = false; context.current = null;
  $('unbindConfirm').value = ''; $('unbindConfirm').hidden = true; $('unbindLabel').hidden = true; $('unbindRetry').hidden = true;
  text('unbindStatus',`${notice ? notice+'\n' : ''}正在核对设备归属与解绑审计…`); recoveryControls(context);
  try {
    const current = await readUnbindState(context.id,controller.signal);
    if(recoveryContext !== context || context.controller !== controller) return;
    const verdict = unbindVerdict(context.id,current);
    context.allowed = Boolean(verdict.allowed); context.current = current;
    text('unbindStatus',`${notice ? notice+'\n' : ''}${verdict.message}`);
    $('unbindConfirm').hidden = !context.allowed; $('unbindLabel').hidden = !context.allowed; $('unbindRetry').hidden = !context.allowed;
    text('unbindLabel',`重新输入 ${context.id}，确认只重试解绑收尾`);
  } catch(error) {
    if(recoveryContext !== context || error.name === 'AbortError' || error.status === 401) return;
    text('unbindStatus',`${notice ? notice+'\n' : ''}核对未完成：${error.message}\n暂不能重试，请重新核对。`);
  } finally {
    if(recoveryContext === context && context.controller === controller) { context.checking = false; recoveryControls(context); }
  }
}
function openUnbindRecovery(id) {
  if(state.busy || state.denied || $('confirmDialog').open || $('unbindDialog').open) return;
  const context = {id,checking:false,writing:false,allowed:false}; recoveryContext = context;
  text('unbindTarget',id); $('unbindDialog').showModal(); checkUnbind(context);
}
function closeUnbindRecovery() {
  if(recoveryContext?.writing) return;
  recoveryContext?.controller?.abort(); recoveryContext = null; $('unbindDialog').close();
}
$('unbindDialog').addEventListener('cancel',event => { event.preventDefault(); closeUnbindRecovery(); });
$('unbindDialog').addEventListener('close',() => { recoveryContext?.controller?.abort(); recoveryContext = null; });
$('unbindClose').addEventListener('click',closeUnbindRecovery);
$('unbindCheck').addEventListener('click',() => { if(recoveryContext && !recoveryContext.writing) checkUnbind(recoveryContext); });
$('unbindConfirm').addEventListener('input',() => { if(recoveryContext) recoveryControls(recoveryContext); });
$('unbindForm').addEventListener('submit',async event => {
  event.preventDefault();
  const context = recoveryContext;
  if(!context?.allowed || context.checking || state.busy || state.denied || $('unbindConfirm').value !== context.id) return;
  context.writing = true; state.busy = true; recoveryControls(context);
  let notice;
  try {
    // 预检用于反馈；cleanup_only 由后端在设备锁内校验，保护预检后出现的新主人。
    const current = await readUnbindState(context.id);
    const verdict = unbindVerdict(context.id,current);
    if(!verdict.allowed || current.latest?.id !== context.current.latest?.id)
      throw new ApiError('核对结果已变化，本次未提交。请重新核对后决定。',409);
    await sendUnbind(context.id,current,true);
    notice = '服务已确认本次收尾成功。';
  } catch(error) {
    if(error.status === 401) return;
    notice = `${error.message}\n${error.status === 409 ? '本次重试未完成，请重新核对；不会自动重试。' : '尚未确认本次收尾结果，不会自动重试。'}`;
  } finally {
    await Promise.all([load('devices'),load('audit')]);
    if(recoveryContext === context && !state.denied) await checkUnbind(context,notice);
    state.busy = false; context.writing = false;
    if(recoveryContext === context) recoveryControls(context);
  }
});

document.querySelectorAll('.nav-item').forEach(button => button.addEventListener('click', () => setView(button.dataset.view)));
$('refreshButton').addEventListener('click', () => load());
$('menuToggle').addEventListener('click', () => { const open = $('shell').classList.toggle('menu-open'); $('menuToggle').setAttribute('aria-expanded',String(open)); });
document.addEventListener('click', event => { if($('shell').classList.contains('menu-open') && !event.target.closest('#sidebar,#menuToggle')) { $('shell').classList.remove('menu-open'); $('menuToggle').setAttribute('aria-expanded','false'); } });
for(const view of ['users','devices','audit']) {
  $(`${view === 'users' ? 'user' : view === 'devices' ? 'device' : 'audit'}SearchForm`).addEventListener('submit', event => {
    event.preventDefault(); const prefix = view === 'users' ? 'user' : view === 'devices' ? 'device' : 'audit';
    state.queries[view] = $(`${prefix}Search`).value.trim().slice(0,80); state.pages[view] = 1;
    if(view !== 'audit') state.statuses[view] = $(`${prefix}Status`).value;
    load(view);
  });
  $(`${view}Prev`).addEventListener('click', () => { if(state.pages[view] > 1) { state.pages[view]--; load(view); } });
  $(`${view}Next`).addEventListener('click', () => { state.pages[view]++; load(view); });
}
window.addEventListener('hashchange', () => { const view = location.hash.slice(1); if(views[view] && view !== state.view) setView(view); });

(async function init() {
  try {
    const admin = await api('/api/admin/session');
    const auth = await api('/api/auth/session');
    state.user = admin.user; state.csrf = auth.csrf_token || admin.csrf_token;
    if(!state.csrf) throw new ApiError('无法取得会话校验信息，请重新登录。');
    text('accountName',admin.user?.username);
    restoreUnbindFollowups();
    $('gate').hidden = true; $('shell').hidden = false;
    setView(location.hash.slice(1));
  } catch(error) {
    if(error.status === 401) return;
    gate(error.status === 403 ? '当前账号无权访问管理员后台。' : error.message, true);
  }
})();
