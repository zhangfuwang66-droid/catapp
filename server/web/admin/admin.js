'use strict';

const $ = id => document.getElementById(id);
const views = {
  overview: ['概览', '账号、设备与服务状态'], users: ['用户', '账号权限与网页登录'],
  devices: ['设备', '绑定关系与连接状态'], storage: ['资料与记忆', '用量、处理与归档元数据'],
  backups: ['备份', '备份状态与异常'], audit: ['操作审计', '管理员操作记录'],
};
const state = {view:'overview', csrf:'', user:null, requests:{}, serial:{}, busy:false,
  pages:{users:1,devices:1,audit:1}, queries:{users:'',devices:'',audit:''},
  statuses:{users:'all',devices:'all'}};
let toastTimer;

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
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  const cancel = () => controller.abort();
  signal?.addEventListener('abort', cancel, {once:true});
  const headers = new Headers({'Accept':'application/json'});
  if(body !== undefined) headers.set('Content-Type', 'application/json');
  if(method !== 'GET' && method !== 'HEAD') {
    headers.set('X-CSRF-Token', state.csrf);
    headers.set('Origin', location.origin);
  }
  try {
    const response = await fetch(path, {method,headers,body:body === undefined ? undefined : JSON.stringify(body),
      credentials:'same-origin',cache:'no-store',signal:controller.signal});
    const payload = await response.json().catch(() => ({}));
    if(response.status === 401) { loginExpired(); throw new ApiError('登录已过期，请重新登录。', 401); }
    if(!response.ok) {
      const detail = typeof payload.detail === 'string' ? payload.detail : '';
      const fallback = response.status === 403 ? '无权执行此操作，或请求校验已失效。' :
        response.status === 409 ? '状态已变化，请刷新后重试。' : '请求失败，请稍后重试。';
      throw new ApiError(detail || fallback, response.status);
    }
    return payload;
  } catch(error) {
    if(error.name === 'AbortError') {
      if(signal?.aborted) throw error;
      throw new ApiError('请求超时，请检查网络后重试。');
    }
    if(error instanceof ApiError) throw error;
    throw new ApiError('暂时无法连接服务，请稍后重试。');
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
  if(!views[view]) return;
  state.requests[view]?.abort();
  const controller = new AbortController();
  const serial = (state.serial[view] || 0) + 1;
  state.requests[view] = controller; state.serial[view] = serial;
  $('refreshButton').disabled = true;
  if(['users','devices','audit'].includes(view)) {
    $(`${view}Prev`).disabled = true; $(`${view}Next`).disabled = true;
  }
  if(view === state.view) feedback('正在加载…');
  try {
    const data = await api(['users','devices','audit'].includes(view) ? queryPath(view) : `/api/admin/${view}`,
      {signal:controller.signal});
    if(serial !== state.serial[view]) return;
    ({overview:renderOverview,users:renderUsers,devices:renderDevices,storage:renderStorage,
      backups:renderBackups,audit:renderAudit})[view](data);
    if(view === state.view) feedback();
  } catch(error) {
    if(error.name === 'AbortError' || serial !== state.serial[view] || error.status === 401) return;
    if(view === state.view) feedback(error.message, 'error');
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
    addCell(row, device.device_type === 'cat' ? '小喵' : '未知类型');
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
    if(device.owner_user_id != null && !device.revoked) actionButton(actions, '解绑', () => deviceAction(device,'unbind'), true);
    addCell(row, '', 'actions').append(actions); body.append(row);
  });
  $('devicesEmpty').hidden = items.length > 0; $('devicesEmpty').textContent = data.total ? '本页暂无设备记录' : '暂无设备记录';
  labelRows(body);
  pagination('devices', data);
}
function renderStorage(data) {
  text('storageDocuments', data.documents?.count); text('storageMemories', data.memory?.memories);
  text('storageBytes', data.documents && data.memory ? bytes(Number(data.documents.bytes || 0) + Number(data.memory.content_bytes || 0) + Number(data.memory.archive_bytes || 0)) : '暂无');
  const statuses = data.documents?.statuses || {};
  text('storagePending', statuses.pending ?? statuses.queued ?? '暂无');
  text('storageTime', `更新于 ${new Intl.DateTimeFormat('zh-CN',{dateStyle:'medium',timeStyle:'short'}).format(new Date())}`);
  const body = $('storageBody'); body.replaceChildren();
  Object.entries(statuses).forEach(([status,count]) => {
    const row = node('tr'); addCell(row, '资料处理'); addCell(row, count); addCell(row, status); addCell(row, '暂无'); body.append(row);
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
    addCell(row,item.action || '未知操作'); addCell(row,`${item.target_type || '对象'} · ${item.target_id ?? '—'}`);
    addCell(row).append(pill(item.result === 'success' ? '成功' : item.result === 'failed' ? '失败' : item.result === 'started' ? '进行中' : '未知', item.result === 'success' ? 'good' : item.result === 'failed' ? 'bad' : 'warn'));
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
function confirmAction({title,description,expected,buttonLabel,run}) {
  if(state.busy) return;
  const dialog = $('confirmDialog');
  text('confirmTitle',title); text('confirmDescription',description);
  text('confirmLabel', `输入 ${expected} 以确认`);
  $('confirmLabel').hidden = false; $('confirmInput').hidden = false;
  $('confirmInput').value = ''; text('confirmError','');
  text('confirmSubmit',buttonLabel); $('confirmSubmit').disabled = true;
  dialog.showModal(); $('confirmInput').focus();
  const update = () => { $('confirmSubmit').disabled = state.busy || $('confirmInput').value !== expected; };
  const close = () => { dialog.close(); cleanup(); };
  const submit = async event => {
    event.preventDefault();
    if(state.busy || $('confirmInput').value !== expected) return;
    state.busy = true; $('confirmSubmit').disabled = true; $('confirmCancel').disabled = true;
    text('confirmSubmit','处理中…'); text('confirmError','');
    try { await run(); close(); toast('操作已完成'); await load(state.view); }
    catch(error) { if(error.status !== 401) text('confirmError',error.message || '操作未完成'); }
    finally { state.busy = false; $('confirmCancel').disabled = false; text('confirmSubmit',buttonLabel); update(); }
  };
  const cleanup = () => { $('confirmInput').removeEventListener('input',update); $('confirmForm').removeEventListener('submit',submit); $('confirmCancel').removeEventListener('click',close); dialog.removeEventListener('cancel',cleanup); };
  $('confirmInput').addEventListener('input',update); $('confirmForm').addEventListener('submit',submit);
  $('confirmCancel').addEventListener('click',close); dialog.addEventListener('cancel',cleanup,{once:true});
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
    run:() => api(`/api/admin/devices/${encodeURIComponent(device.id)}/${kind}`,{method:'POST',body:voice ? {paused,confirm_device_id:device.id} : {confirm_device_id:device.id}})});
}

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
    $('gate').hidden = true; $('shell').hidden = false;
    setView(location.hash.slice(1));
  } catch(error) {
    if(error.status === 401) return;
    gate(error.status === 403 ? '当前账号无权访问管理员后台。' : error.message, true);
  }
})();
