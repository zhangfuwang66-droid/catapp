'use strict';

// 在样式和页面首次绘制前恢复主题；外部脚本兼容 script-src 'self'。
let savedTheme;
try { savedTheme = localStorage.getItem('admin-theme'); } catch {}
document.documentElement.dataset.theme = ['light','dark'].includes(savedTheme) ? savedTheme :
  matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';

function initializeAdmin() {
const $ = id => document.getElementById(id);
const views = {
  overview: ['概览', '账号、设备与服务状态'], users: ['用户', '账号权限与网页登录'],
  devices: ['设备', '绑定关系与连接状态'], storage: ['存储概况', '数量、容量与处理状态，不展示私人正文'],
  backups: ['备份', '备份状态与异常'], audit: ['操作审计', '管理员操作记录'],
};
const auditActions = new Map([
  ['disable-user','停用账号'], ['enable-user','启用账号'], ['revoke-sessions','撤销网页登录'],
  ['pause-voice','暂停语音'], ['resume-voice','恢复语音'], ['unbind-device','解绑设备'],
  ['grant-admin','授予管理员'], ['revoke-admin','撤销管理员'],
  ['create-user','创建账号'], ['rename-user','修改账号名'], ['delete-user','删除空账号'],
  ['register-device','登记设备'], ['create-device','登记设备'], ['rename-device','修改设备名'],
  ['update-device','修改设备名'], ['revoke-device','撤销设备凭证'], ['rotate-credential','轮换设备凭证'],
  ['rotate-device-credential','轮换设备凭证'], ['delete-device','删除空设备'],
  ['check-backup','检查并备份'],
]);
const deviceTypes = new Map([['cat','小喵'],['deer','小鹿'],['unknown','未知类型']]);
// queued 是当前资料库值；兼容服务端曾返回的 pending，二者都计入待处理。
const documentStatuses = new Map([
  ['queued','待处理'], ['pending','待处理'], ['processing','处理中'],
  ['ready','已就绪'], ['keyword','关键词检索可用'], ['failed','处理失败'],
]);
const state = {view:'overview', csrf:'', user:null, requests:{}, serial:{}, busy:false, denied:false, loggingOut:false, unbindFollowups:new Map(),
  pages:{users:1,devices:1,audit:1}, queries:{users:'',devices:'',audit:''},
  statuses:{users:'all',devices:'all'}};
let toastTimer, recoveryContext;
let detailContext, editorContext, credentialMemory;
let uptimeAnchor, uptimeTimer;
const iconPaths = {
  overview:'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
  users:'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2 M16 3a4 4 0 0 1 0 8 M22 21v-2a4 4 0 0 0-3-3.9 M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0',
  devices:'M8 3h8a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2 M10 17h4 M10 7h4',
  storage:'M3 7V4h7l2 3h9v13H3z M7 12h10 M7 16h6',
  backups:'M4 6h16v4H4z M6 10v10h12V10 M10 14h4 M8 3h8',
  audit:'M8 3h10v18H5V6z M9 8h5 M9 12h5 M9 16h5',
  shield:'M12 3 3 7v5c0 5 9 9 9 9s9-4 9-9V7z M8 12l3 3 5-6',
  theme:'M20 14A8 8 0 0 1 10 4a8 8 0 1 0 10 10',
  clock:'M20 12a8 8 0 1 1-16 0 8 8 0 0 1 16 0 M12 7v5l3 2',
  refresh:'M20 7v5h-5 M4 17v-5h5 M6.1 7a7 7 0 0 1 11.5-1L20 9 M4 15l2.4 3A7 7 0 0 0 18 17',
  menu:'M4 6h16 M4 12h16 M4 18h16', plus:'M12 5v14 M5 12h14', close:'M6 6l12 12 M18 6 6 18',
  logout:'M9 3H4v18h5 M9 12h12 M17 8l4 4-4 4',
};
function insertIcons() {
  document.querySelectorAll('[data-icon]').forEach(element => {
    const svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
    svg.setAttribute('viewBox','0 0 24 24'); svg.setAttribute('aria-hidden','true'); svg.classList.add('icon');
    const path = document.createElementNS(svg.namespaceURI,'path'); path.setAttribute('d',iconPaths[element.dataset.icon] || iconPaths.overview);
    svg.append(path); element.prepend(svg);
  });
}
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  $('themeToggle').setAttribute('aria-label',theme === 'dark' ? '切换为浅色主题' : '切换为深色主题');
  $('themeToggle').setAttribute('aria-pressed',String(theme === 'dark'));
}
insertIcons();
applyTheme(document.documentElement.dataset.theme);
requestAnimationFrame(() => document.documentElement.classList.add('theme-ready'));
$('themeToggle').addEventListener('click',() => {
  const theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'; applyTheme(theme);
  try { localStorage.setItem('admin-theme',theme); } catch {}
});

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
function knownBoolean(value,yes,no) { return value === true ? yes : value === false ? no : '未提供'; }
function auditLabel(action) { return auditActions.get(action) || (action ? `未知操作（${action}）` : '未知操作'); }
function auditPill(result) {
  return pill(result === 'success' ? '成功' : result === 'failed' ? '失败' : result === 'started' ? '未完成' : '未知',
    result === 'success' ? 'good' : result === 'failed' ? 'bad' : 'warn');
}
function duration(value) {
  if(value == null || !Number.isFinite(Number(value))) return '暂无';
  const seconds = Math.max(0, Math.floor(Number(value)));
  const days = Math.floor(seconds / 86400), hours = Math.floor(seconds % 86400 / 3600);
  const minutes = Math.floor(seconds % 3600 / 60);
  return days ? `${days}天${hours ? `${hours}小时` : ''}` : hours ? `${hours}小时${minutes ? `${minutes}分` : ''}` : `${minutes}分钟`;
}
function tickUptime() {
  clearTimeout(uptimeTimer);
  if(!uptimeAnchor || state.view !== 'overview' || document.hidden) return;
  const value = uptimeAnchor.seconds + Math.max(0,performance.now() - uptimeAnchor.at) / 1000;
  const seconds = Math.floor(value), days = Math.floor(seconds / 86400);
  const hours = Math.floor(seconds % 86400 / 3600), minutes = Math.floor(seconds % 3600 / 60), remainder = seconds % 60;
  const clock = [hours,minutes,remainder].map(part => String(part).padStart(2,'0')).join(':');
  const metric = $('metricUptime');
  metric.replaceChildren();
  if(days) metric.append(node('span',`${days}天`,'uptime-days'));
  metric.append(node('span',clock,'uptime-clock'));
  metric.setAttribute('aria-label',`${days ? `${days}天` : ''}${hours}小时${minutes}分${remainder}秒`);
  // 每次从单调时钟重新计算，后台节流或系统时间变化不会累加误差。
  uptimeTimer = setTimeout(tickUptime,Math.max(25,1000 - (value * 1000 % 1000)));
}
function anchorUptime(value) {
  clearTimeout(uptimeTimer);
  uptimeAnchor = value != null && value !== '' && Number.isFinite(Number(value)) && Number(value) >= 0 ?
    {seconds:Number(value),at:performance.now()} : null;
  if(uptimeAnchor) tickUptime();
  else { text('metricUptime','暂无'); $('metricUptime').removeAttribute('aria-label'); }
}
document.addEventListener('visibilitychange',tickUptime);
window.addEventListener('pagehide',() => clearTimeout(uptimeTimer));
window.addEventListener('pageshow',tickUptime);
function feedback(message='', kind='') {
  $('pageFeedback').hidden = !message;
  $('pageFeedback').className = `feedback ${kind}`;
  $('pageFeedback').textContent = message;
  if(message && kind === 'error') {
    const retry=node('button','重新加载','text-button'); retry.type='button'; retry.addEventListener('click',() => load()); $('pageFeedback').append(node('span',' '),retry);
  }
}
function toast(message, error=false) {
  clearTimeout(toastTimer);
  $('toast').textContent = message;
  $('toast').className = error ? 'toast error' : 'toast';
  $('toast').hidden = false;
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, 4500);
}
function gate(message, denied=false) {
  if(denied) {
    document.documentElement.classList.add('access-denied');
    clearTimeout(uptimeTimer);
    clearCredential();
  }
  $('shell').hidden = true;
  $('gate').hidden = false;
  $('gate').className = denied ? 'gate denied' : 'gate';
  $('gate').replaceChildren(node('span', message));
  if(denied) {
    const link = node('a','返回后台登录'); link.href = '/admin/login'; $('gate').append(link);
    if(state.csrf) {
      const button = node('button','退出后台','neutral-button'); button.type='button'; button.id='gateLogout';
      button.addEventListener('click',logoutAdmin); $('gate').append(button);
    }
  }
}
function loginExpired() {
  Object.values(state.requests).forEach(controller => controller.abort());
  clearTimeout(uptimeTimer);
  clearCredential();
  window.location.assign('/admin/login?notice=expired');
}
async function api(path, {method='GET', body, signal, timeout=12000}={}) {
  if(state.denied && path !== '/api/admin/auth/logout') throw new ApiError('管理员权限已失效，请重新登录后核对。',403);
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
        response.status === 409 ? '状态已变化，请刷新后重试。' : response.status === 422 ? '提交内容不符合要求，请核对字段格式。' : writing ? '服务未能确认操作结果。' : '请求失败，请稍后重试。';
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
  if(!views[view] || state.denied || state.loggingOut) return false;
  state.requests[view]?.abort();
  const controller = new AbortController();
  const serial = (state.serial[view] || 0) + 1;
  state.requests[view] = controller; state.serial[view] = serial;
  const section = $(`view-${view}`);
  section.inert = true;
  section.classList.remove('view-enter');
  section.hidden = true;
  if(view === state.view) {
    $('refreshButton').disabled = true;
    text('loadingLabel',`正在读取${views[view][0]}…`);
    $('viewLoading').hidden = false;
    feedback();
  }
  if(['users','devices','audit'].includes(view)) {
    $(`${view}Prev`).disabled = true; $(`${view}Next`).disabled = true;
  }
  section.setAttribute('aria-busy','true');
  try {
    let data;
    if(view === 'overview') {
      try { data = await api('/api/admin/dashboard',{signal:controller.signal}); }
      catch(error) {
        if(error.status !== 404) throw error;
        data = {summary:await api('/api/admin/overview',{signal:controller.signal})};
      }
    } else if(view === 'backups') {
      const status = await api('/api/admin/backups',{signal:controller.signal});
      try { data = {...await api('/api/admin/backups/history',{signal:controller.signal}),status}; }
      catch(error) { if(error.status !== 404) throw error; data = {status}; }
    } else data = await api(['users','devices','audit'].includes(view) ? queryPath(view) : `/api/admin/${view}`,{signal:controller.signal});
    if(serial !== state.serial[view] || controller.signal.aborted || state.loggingOut) return false;
    ({overview:renderOverview,users:renderUsers,devices:renderDevices,storage:renderStorage,
      backups:renderBackups,audit:renderAudit})[view](data);
    if(view === state.view) {
      feedback();
      $('viewLoading').hidden = true;
      section.classList.add('view-enter');
      section.hidden = false;
    }
    return true;
  } catch(error) {
    if(error.name === 'AbortError' || serial !== state.serial[view] || error.status === 401) return false;
    if(view === state.view) feedback(error.message, 'error');
    return false;
  } finally {
    if(state.requests[view] === controller) {
      delete state.requests[view];
      section.setAttribute('aria-busy','false');
      section.inert = false;
      if(view === state.view) { $('refreshButton').disabled = false; $('viewLoading').hidden = true; }
    }
  }
}
function setView(view) {
  if(state.loggingOut) return;
  if(!views[view]) view = 'overview';
  state.view = view;
  // 切页先隐藏所有旧快照，只有当前页最新请求成功后才揭示内容。
  document.querySelectorAll('.view').forEach(element => { element.hidden = true; });
  document.querySelectorAll('.nav-item').forEach(button => {
    button.classList.toggle('active', button.dataset.view === view);
    if(button.dataset.view === view) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });
  text('pageTitle', views[view][0]); text('crumbTitle', views[view][0]); text('pageSubtitle', views[view][1]);
  const menuWasOpen = $('shell').classList.contains('menu-open');
  setMenu(false);
  if(menuWasOpen) $('main').focus({preventScroll:true});
  history.replaceState(null, '', `#${view}`);
  tickUptime();
  load(view);
}
function renderOverview(dashboard) {
  const data = dashboard.summary || dashboard;
  text('metricUsers', data.users?.total); text('metricUsersNote', data.users ? `${data.users.enabled ?? '未提供'} 启用 · ${data.users.disabled ?? '未提供'} 停用` : '未接入');
  text('metricDevices', data.devices?.total); text('metricDevicesNote', data.devices ? `${data.devices.bound} 已绑定` : '未接入');
  anchorUptime(data.uptime_seconds);
  const storage = data.storage;
  const total = storage && Number(storage.database_bytes || 0) + Number(storage.wal_bytes || 0);
  text('metricStorage', storage ? bytes(total) : '暂无');
  text('metricStorageNote', storage ? '数据库与 WAL 文件' : '未接入');
  text('overviewConnections', data.devices?.online == null ? '未接入' : `${data.devices.online} 台可核实连接`);
  text('overviewVoice', '请在设备页查看');
  text('overviewBackup', data.backups?.last_success_at ? date(data.backups.last_success_at) : '暂无成功备份');
  text('overviewTime', `更新于 ${new Intl.DateTimeFormat('zh-CN',{dateStyle:'medium',timeStyle:'short'}).format(new Date())}`);
  if(dashboard.generated_at) text('overviewTime',`更新于 ${date(dashboard.generated_at)}`);
  const chart = $('activityChart'); chart.replaceChildren();
  const activity = Array.isArray(dashboard.activity) ? dashboard.activity : [];
  if(activity.length) {
    const max = Math.max(1,...activity.flatMap(day => [day.users,day.devices,day.actions].map(value => Number(value) || 0)));
    activity.forEach(day => {
      const group = node('div','','chart-day'), bars = node('div','','chart-bars');
      group.setAttribute('role','img'); group.setAttribute('aria-label',`${day.date}：新增账号 ${day.users ?? '未提供'}，登记设备 ${day.devices ?? '未提供'}，管理操作 ${day.actions ?? '未提供'}`);
      for(const [key,className] of [['users','user'],['devices','device'],['actions','action']]) {
        const bar = node('span','',`chart-bar ${className}`); bar.style.height = `${Math.max(0,Number(day[key]) || 0)/max*100}%`; bar.title = `${day.date} ${key === 'users' ? '新增账号' : key === 'devices' ? '登记设备' : '管理操作'}：${day[key] ?? '未提供'}`; bars.append(bar);
      }
      group.append(bars,node('span',String(day.date).slice(5).replace('-','/'),'chart-date')); chart.append(group);
    });
  } else chart.append(node('p','活动统计暂未接入，未生成趋势。','empty-inline'));
  const attention = $('attentionList'); attention.replaceChildren();
  const items = Array.isArray(dashboard.attention) ? dashboard.attention : [];
  text('attentionCount',Array.isArray(dashboard.attention) ? `${items.length} 项` : '未接入');
  items.forEach(item => {
    const button = node('button','',`attention-item ${item.level === 'info' ? 'info' : ''}`); button.type='button';
    const content = node('div'); content.append(node('strong',item.message || '待核对事项'),node('small',`${item.count ?? '未提供'} 条 · 查看详情`));
    button.append(node('span','','attention-dot'),content,node('span','↗')); button.addEventListener('click',() => setView(item.view)); attention.append(button);
  });
  if(!items.length) attention.append(node('p',Array.isArray(dashboard.attention) ? '当前没有待处理提醒' : '待处理提醒暂未接入','empty-inline'));
  const recent = $('recentAudit'); recent.replaceChildren();
  if(Array.isArray(dashboard.recent_audit) && dashboard.recent_audit.length) dashboard.recent_audit.forEach(item => {
    const row = node('div','','recent-row'), content = node('div');
    content.append(node('strong',auditLabel(item.action)),node('small',`${item.actor_username || '未知管理员'} · ${item.target_id || '未提供对象'}`));
    row.append(node('span','↗','log-mark'),content,node('time',date(item.created_at)),auditPill(item.result)); recent.append(row);
  }); else recent.append(node('p',Array.isArray(dashboard.recent_audit) ? '暂无操作记录' : '近期审计暂未接入，请前往操作审计查看。','empty-inline'));
  const system = $('systemInfo'); system.replaceChildren(); system.hidden = !dashboard.system;
  if(dashboard.system) {
    const value = dashboard.system;
    for(const label of [`Python ${value.python_version || '未提供'}`,`${value.platform || '平台未提供'}`,`${value.database_engine || '数据库未提供'}`,
      value.worker_mode === 'single' ? '单进程服务' : '运行模式未提供',
      value.disk_status === 'unknown' || value.disk_free_bytes == null ? '磁盘空间：未知' : `磁盘可用 ${bytes(value.disk_free_bytes)}${value.disk_status === 'low' ? ' · 空间偏低' : ''}`,
      `有效网页登录：${dashboard.workload?.active_sessions ?? '未提供'}`]) system.append(node('span',label));
  }
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
    const row = node('tr'); const name = addCell(row), link = node('button',user.username,'name-button'); link.type='button'; link.setAttribute('aria-label',`查看用户 ${user.username}`); link.addEventListener('click',() => openDetail('users',user.id));
    name.append(link, node('small', `ID ${user.id}${user.is_admin ? ' · 管理员' : ''}`));
    addCell(row).append(pill(user.disabled ? '已停用' : '已启用', user.disabled ? 'bad' : 'good'));
    addCell(row, user.device_count ?? '暂无');
    addCell(row).append(pill(knownBoolean(user.key_configured,'已配置','未配置'), user.key_configured === true ? 'good' : ''));
    addCell(row, date(user.created_at));
    const actions = node('div', '', 'row-actions');
    const self = String(user.id) === String(state.user?.id);
    actionButton(actions,'详情',() => openDetail('users',user.id));
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
    const row = node('tr'); const name = addCell(row), link = node('button',device.display_name || device.id,'name-button'); link.type='button'; link.setAttribute('aria-label',`查看设备 ${device.id}`); link.addEventListener('click',() => openDetail('devices',device.id)); name.append(link,node('small', device.id));
    addCell(row, deviceTypes.get(device.device_type) || '未知类型');
    const relation = addCell(row);
    relation.append(node('strong', device.owner_username || (device.owner_user_id == null ? '未绑定' : `ID ${device.owner_user_id}`)));
    if(device.loan_active) relation.append(node('small', `当前由 ${device.active_username || '未知用户'} 借用${device.loan_expires_at ? ` · 至 ${date(device.loan_expires_at)}` : ''}`));
    else if(device.loan_pending) relation.append(node('small', '借用邀请待接受'));
    addCell(row).append(pill(device.revoked ? '已撤销' : knownBoolean(device.online,'已连接','未连接'), device.revoked ? 'bad' : device.online ? 'good' : ''));
    const voice = device.voice_paused == null ? '未接入' : device.voice_paused ? '已暂停' : '可用';
    const voiceCell = addCell(row); voiceCell.append(pill(voice, device.voice_paused === true ? 'warn' : device.voice_paused === false ? 'good' : ''));
    voiceCell.append(node('small', `音频 ${knownBoolean(device.audio_online,'已连接','未连接')} · 摄像头 ${knownBoolean(device.camera_online,'已连接','未连接')}`));
    const actions = node('div', '', 'row-actions');
    actionButton(actions,'详情',() => openDetail('devices',device.id));
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
  const distribution = $('storageDistribution'); distribution.replaceChildren();
  const totalDocuments = Object.values(statuses || {}).reduce((sum,value) => sum + Number(value || 0),0);
  if(totalDocuments) Object.entries(statuses).forEach(([status,count]) => {
    const row = node('div','','distribution-row'), track = node('div','','distribution-track'), bar = node('i');
    bar.style.width = `${Number(count)/totalDocuments*100}%`; track.append(bar);
    row.append(node('span',documentStatuses.get(status) || '未知状态','distribution-label'),track,node('span',count,'distribution-count')); distribution.append(row);
  }); else distribution.append(node('p',statuses ? '暂无资料处理记录' : '资料处理状态未提供','empty-inline'));
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
function renderBackups(history) {
  const data = history.status || history;
  const body = $('backupsBody'); body.replaceChildren();
  text('backupEnabled',knownBoolean(data.enabled,'已启用','未启用'));
  text('backupRunning',data.running === true ? '正在执行备份' : data.scheduler_running === true ? '调度器等待中' : data.scheduler_running === false ? '调度器未运行' : '调度状态未提供');
  text('backupCount',data.backup_count ?? '未提供'); text('backupLastSuccess',date(data.last_success_at)); text('backupSkipped',data.skipped_unverified ?? '未提供');
  text('backupSummary', data.enabled === false ? '自动备份未启用' : `已保留 ${data.backup_count ?? '未知'} 份 · 跳过未验证 ${data.skipped_unverified ?? '未知'} 份 · 已清理 ${data.pruned ?? '未知'} 份`);
  const rows = [];
  if(data.last_success_at) rows.push({time:data.last_success_at,status:'最近成功',problem:'无'});
  if(data.running) rows.push({time:null,status:'运行中',problem:'无'});
  if(data.last_error_type) rows.push({time:data.last_failure_at,status:'最近异常',problem:data.last_error_type});
  rows.forEach(item => { const row = node('tr'); addCell(row,date(item.time)); addCell(row).append(pill(item.status,item.status === '最近异常' ? 'bad' : item.status === '运行中' ? 'warn' : 'good')); addCell(row,item.problem); body.append(row); });
  $('backupsEmpty').hidden = rows.length > 0;
  $('backupsEmpty').textContent = data.enabled === false ? '自动备份未启用' : '暂无备份执行记录';
  labelRows(body);
  const entries = $('backupHistoryBody'); entries.replaceChildren();
  (history.items || []).forEach(item => {
    const row = node('tr'); addCell(row,date(item.created_at)); addCell(row,bytes(item.bytes)); addCell(row).append(pill(item.verified === true ? '已校验' : '未确认',item.verified === true ? 'good' : 'warn')); entries.append(row);
  });
  labelRows(entries); $('backupHistoryEmpty').hidden = entries.children.length > 0;
  text('backupHistoryEmpty',Array.isArray(history.items) ? '暂无校验通过的备份' : '备份历史接口暂未接入');
  text('backupPolicy',history.interval_seconds == null ? '备份策略未提供' : `检查间隔 ${duration(history.interval_seconds)} · 保留 ${history.retain ?? '未提供'} 份 · 已验证 ${history.total ?? '未提供'} 份`);
}
function renderAudit(data) {
  const body = $('auditBody'); body.replaceChildren();
  const items = Array.isArray(data.items) ? data.items : [];
  items.forEach(item => {
    const row = node('tr'); addCell(row,date(item.created_at)); addCell(row,item.actor_username || `ID ${item.actor_id}`);
    const action = addCell(row,auditLabel(item.action));
    if(item.action) action.title = item.action;
    addCell(row,`${item.target_type || '对象'} · ${item.target_id ?? '—'}`);
    addCell(row).append(auditPill(item.result));
    body.append(row);
  });
  $('auditEmpty').hidden = items.length > 0; $('auditEmpty').textContent = data.total ? '本页暂无审计记录' : '暂无审计记录';
  labelRows(body);
  pagination('audit', data);
}
function actionButton(parent, label, handler, danger=false, disabled=false) {
  const button = node('button',label,danger ? 'danger' : ''); button.type = 'button';
  button.disabled = disabled;
  if(disabled) button.title = '当前状态不允许此操作，请先核对详情中的条件';
  button.addEventListener('click', handler); parent.append(button);
}
function confirmAction({title,description,expected,buttonLabel,run,followupId,successMessage}) {
  const dialog = $('confirmDialog');
  if(state.busy || state.denied || dialog.open || $('unbindDialog').open || $('editorDialog').open || $('credentialDialog').open) return;
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
      let failure, result;
      try { result = await run(); } catch(error) { failure = error; }
      if(failure?.status === 401) return;
      const notice = failure ? `${failure.message || '未能确认操作结果。'}\n${!failure.status || failure.status >= 500 ?
        '操作可能已经生效或仍在处理中，勿直接重复提交。' : '请先核对状态，勿直接重复提交。'}` : '操作已完成。';
      if(active) { text('confirmSubmit','正在核对…'); text('confirmError',`${notice}\n正在核对${views[view][0]}与操作审计…`); }
      // 写请求超时或中断后服务端可能仍在执行；只读核对，不自动重试写入。
      const checked = (await Promise.all([load(view),load('audit')])).every(Boolean);
      const message = `${notice}\n${checked ? `已刷新${views[view][0]}与操作审计，请关闭后核对。` :
        '状态或审计刷新未完成，请关闭后手动刷新核对。'}`;
      if(active && !failure && result?.credential) { cleanup(); dialog.close(); showCredential(result); }
      else if(active && !failure && checked) { cleanup(); dialog.close(); toast(successMessage ? successMessage(result) : '操作已完成'); }
      else if(active) text('confirmError',message);
      else toast(`${title}：${message}`,Boolean(failure) || !checked);
      if(detailContext && $('detailDialog').open) await loadDetail(detailContext);
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

const userBlockers = new Map([
  ['current_account','当前登录账号'],['enabled','账号尚未停用'],['last_admin','最后一名启用管理员'],
  ['device_ownership','仍拥有设备'],['device_history','存在设备历史'],['device_loans','存在设备借用记录'],
  ['device_preferences','存在设备偏好'],['documents','存在资料'],['memory','存在对话或记忆'],
  ['memory_policies','存在记忆策略'],['key_configured','存在模型配置'],['speech_settings','存在语音设置'],
]);
function detailSection(title,fields) {
  const section = node('section','','detail-section'), list = node('dl','','detail-grid');
  section.append(node('h3',title),list);
  fields.forEach(([label,value]) => { const pair = node('div'); pair.append(node('dt',label),node('dd',value == null || value === '' ? '未提供' : value)); list.append(pair); });
  return section;
}
async function loadDetail(context) {
  context.controller?.abort(); const controller = new AbortController(); context.controller = controller;
  const body = $('detailContent'); body.replaceChildren(node('p','正在读取详情…','empty-inline'));
  try {
    const data = await api(`/api/admin/${context.kind}/${encodeURIComponent(context.id)}`,{signal:controller.signal});
    if(detailContext !== context || controller !== context.controller) return;
    body.replaceChildren();
    (context.kind === 'users' ? renderUserDetail : renderDeviceDetail)(data,body);
  } catch(error) {
    if(detailContext !== context || error.name === 'AbortError' || error.status === 401 || state.denied) return;
    body.replaceChildren(node('p',error.status === 404 ? '该记录已不存在，或详情接口尚未接入。' : error.message,'feedback error'));
    const button = node('button','重新加载','neutral-button'); button.type='button'; button.addEventListener('click',() => loadDetail(context)); body.append(button);
  }
}
function openDetail(kind,id) {
  if(state.busy || state.denied || $('editorDialog').open || $('confirmDialog').open) return;
  detailContext?.controller?.abort(); detailContext = {kind,id};
  text('detailKind',kind === 'users' ? 'ACCOUNT / 账号详情' : 'DEVICE / 设备详情'); text('detailTitle',id);
  if(!$('detailDialog').open) $('detailDialog').showModal(); loadDetail(detailContext);
}
function renderUserDetail(data,body) {
  const user = data.user; if(!user) throw new ApiError('账号详情不完整，请重新加载。');
  text('detailTitle',user.username);
  const actions = node('div','','detail-actions row-actions'), self = String(user.id) === String(state.user?.id);
  actionButton(actions,'修改账号名',() => editUser(user),false,self);
  actionButton(actions,user.disabled ? '启用账号' : '停用账号',() => userAction(user,'status'),!user.disabled,self);
  actionButton(actions,'撤销登录',() => userAction(user,'revoke-sessions'),true,self); body.append(actions);
  body.append(detailSection('账号信息',[
    ['用户 ID',user.id],['创建时间',date(user.created_at)],['状态',knownBoolean(user.disabled,'已停用','已启用')],
    ['角色',knownBoolean(user.is_admin,'管理员','普通用户')],['有效网页登录',data.active_sessions ?? '未提供'],['模型配置',knownBoolean(user.key_configured,'已配置','未配置')],
  ]));
  const devices = node('section','','detail-section'); devices.append(node('h3','设备关系'));
  for(const [label,items] of [['拥有',data.devices?.owned],['借用',data.devices?.borrowed]]) {
    const list=node('ul','','detail-devices');
    if(Array.isArray(items) && items.length) items.forEach(item => { const li=node('li',`${label} · ${item.display_name || item.id}`); li.append(node('small',`${deviceTypes.get(item.device_type) || '未知类型'} · ${item.id}`)); list.append(li); });
    else list.append(node('li',Array.isArray(items) ? `暂无${label}设备` : `${label}设备信息未提供`));
    devices.append(list);
  }
  body.append(devices,detailSection('资料与记忆汇总',[
    ['资料数量',data.documents?.count],['资料占用',bytes(data.documents?.bytes)],['记忆条目',data.memory?.memories],
    ['对话数量',data.memory?.conversations],['消息数量',data.memory?.messages],['历史设备数',data.devices?.history_count],
  ]));
  const danger=node('section','','danger-zone'); danger.append(node('h3','删除空账号'));
  const blockers = data.deletion?.blockers;
  danger.append(node('p',data.deletion?.allowed === true ? '仅移除已停用的空账号与登录记录，脱敏审计保留。此操作无法撤销。' :
    `暂不能删除：${Array.isArray(blockers) && blockers.length ? blockers.map(code => userBlockers.get(code) || '存在尚未处理的关联').join('、') : '删除条件未确认'}。`));
  const buttons=node('div','','row-actions'); actionButton(buttons,'永久删除空账号',() => confirmAction({title:'永久删除空账号',expected:user.username,buttonLabel:'确认永久删除',
    description:`将永久删除 ${user.username}。只允许已停用且无关联数据的空账号，操作无法撤销，审计记录保留。`,
    run:() => api(`/api/admin/users/${encodeURIComponent(user.id)}`,{method:'DELETE',body:{confirm_username:user.username}})}),true,data.deletion?.allowed !== true || self);
  danger.append(buttons); body.append(danger);
}
function renderDeviceDetail(data,body) {
  const device=data.device; if(!device) throw new ApiError('设备详情不完整，请重新加载。');
  text('detailTitle',device.display_name || device.id);
  const actions=node('div','','detail-actions row-actions');
  actionButton(actions,'修改名称',() => editDevice(device));
  if(device.owner_user_id == null) actionButton(actions,'轮换凭证',() => confirmAction({title:'轮换设备凭证',expected:device.id,buttonLabel:'确认轮换',
    description:`将使 ${device.id} 的旧凭证立即失效，并生成仅此次展示的新凭证。已撤销设备将重新启用。请先准备安全配发工具；响应丢失时不要重复提交。`,
    run:() => api(`/api/admin/devices/${encodeURIComponent(device.id)}/rotate-credential`,{method:'POST',body:{confirm_device_id:device.id}})}),true);
  actionButton(actions,device.revoked ? '核对并重试撤销收尾' : '撤销凭证',() => confirmAction({title:device.revoked ? '核对并重试撤销收尾' : '撤销设备凭证',expected:device.id,buttonLabel:device.revoked ? '确认重试收尾' : '确认撤销',
    description:device.revoked ? `设备 ${device.id} 的凭证已撤销。该状态不代表连接清理已完成；请核对审计后，明确确认是否手动重试收尾。历史数据保留，不会自动重复执行。` :
      `将撤销 ${device.id} 的全部硬件凭证、配对码和绑定/借用关系，并关闭旧连接。历史数据与记忆保留。`,
    run:() => api(`/api/admin/devices/${encodeURIComponent(device.id)}/revoke`,{method:'POST',body:{confirm_device_id:device.id}})}),true);
  body.append(actions,detailSection('设备信息',[
    ['设备 ID',device.id],['类型',deviceTypes.get(device.device_type) || '未知类型'],['创建时间',date(device.created_at)],
    ['凭证状态',knownBoolean(device.revoked,'已撤销','未撤销')],['主人',device.owner_username || (device.owner_user_id == null ? '未绑定' : device.owner_user_id)],
    ['连接',knownBoolean(device.online,'已连接','未连接')],['语音',device.voice_paused == null ? '未接入' : device.voice_paused ? '已暂停' : '可用'],
    ['当前使用者',device.active_username || device.active_user_id || '无'],
  ]),detailSection('凭证与历史',[
    ['有效凭证',data.credentials?.active_count],['已撤销凭证',data.credentials?.revoked_count],['最近签发',date(data.credentials?.last_issued_at)],
    ['有效配对码',data.pairings?.active_count],['历史归属记录',data.history?.memberships],['借用记录',data.history?.loans],
    ['对话数量',data.history?.conversations],['记忆数量',data.history?.memories],
  ]));
  const danger=node('section','','danger-zone'); danger.append(node('h3','删除误登记的空设备'),node('p',data.can_delete === true ?
    '此设备已撤销且无历史关联，可以永久移除误登记记录。操作无法撤销，审计保留。' :
    `暂不能删除：${Array.isArray(data.delete_blockers) && data.delete_blockers.length ? data.delete_blockers.join('、') : '需要先撤销凭证，且设备必须没有任何历史关联'}。有历史的设备保留撤销记录。`));
  const buttons=node('div','','row-actions'); actionButton(buttons,'永久删除空设备',() => confirmAction({title:'永久删除空设备',expected:device.id,buttonLabel:'确认永久删除',
    description:`将永久删除误登记设备 ${device.id}，操作无法撤销。仅允许已撤销、未绑定且无历史关联的空设备。`,
    run:() => api(`/api/admin/devices/${encodeURIComponent(device.id)}`,{method:'DELETE',body:{confirm_device_id:device.id}})}),true,data.can_delete !== true);
  danger.append(buttons); body.append(danger);
}
function closeDetail() { detailContext?.controller?.abort(); detailContext=null; $('detailDialog').close(); }
$('detailClose').addEventListener('click',closeDetail);
$('detailDialog').addEventListener('close',() => {
  if($('detailDialog').open) return;
  detailContext?.controller?.abort(); detailContext=null;
});
function openEditor(spec) {
  if(state.busy || state.denied || $('editorDialog').open || $('confirmDialog').open || $('credentialDialog').open) return;
  const context={...spec,submitted:false,view:state.view,inputs:{}}; editorContext=context;
  text('editorTitle',spec.title); text('editorDescription',spec.description); text('editorSubmit',spec.submitLabel || '确认保存'); text('editorCancel','取消');
  $('editorSubmit').disabled=false; $('editorCancel').disabled=false; $('editorError').textContent='';
  const fields=$('editorFields'); fields.replaceChildren();
  spec.fields.forEach(field => {
    const label=node('label',field.label), input=node('input'); input.id=`editor-${field.name}`; input.name=field.name; label.htmlFor=input.id;
    input.type=field.type || 'text'; input.required=field.required !== false; input.autocomplete=field.type === 'password' ? 'new-password' : 'off'; input.spellcheck=false;
    for(const prop of ['minLength','maxLength','pattern','placeholder']) if(field[prop] != null) input[prop]=field[prop];
    input.value=field.value || ''; context.inputs[field.name]=input; fields.append(label,input);
    if(field.help) fields.append(node('small',field.help,'field-help'));
  });
  $('editorDialog').showModal(); Object.values(context.inputs)[0]?.focus();
}
function closeEditor() {
  if(state.busy) return;
  $('editorFields').replaceChildren(); editorContext=null; $('editorDialog').close();
}
$('editorCancel').addEventListener('click',closeEditor);
$('editorDialog').addEventListener('cancel',event => { event.preventDefault(); closeEditor(); });
$('editorDialog').addEventListener('close',() => { $('editorFields').replaceChildren(); editorContext=null; });
$('editorForm').addEventListener('submit',async event => {
  event.preventDefault(); const context=editorContext;
  if(!context || context.submitted || state.busy || state.denied || !$('editorForm').reportValidity()) return;
  const values=Object.fromEntries(Object.entries(context.inputs).map(([key,input]) => [key,input.value]));
  const problem=context.validate?.(values);
  if(problem) { text('editorError',problem); return; }
  context.submitted=true; state.busy=true; Object.values(context.inputs).forEach(input => input.disabled=true);
  $('editorCancel').disabled=true; $('editorSubmit').disabled=true; text('editorSubmit','正在提交…'); $('editorError').textContent='';
  try {
    const result=await context.run(values);
    // 密码只用于这一次请求，不随详情刷新或错误留在表单中。
    for(const [key,input] of Object.entries(context.inputs)) { if(input.type === 'password') { input.value=''; values[key]=''; } }
    if(editorContext === context) { $('editorDialog').close(); if(result?.credential) showCredential(result); else toast('操作已完成'); }
    await Promise.all([load(context.view),load('audit')]);
    if(detailContext && $('detailDialog').open) await loadDetail(detailContext);
  } catch(error) {
    Object.entries(context.inputs).forEach(([key,input]) => { if(input.type === 'password') { input.value=''; values[key]=''; } });
    const correctable = [400,422].includes(error.status) || (error.status === 409 && context.allowConflictCorrection);
    if(correctable) { context.submitted=false; Object.values(context.inputs).forEach(input => input.disabled=false); }
    if(error.status !== 401 && editorContext === context) text('editorError',`${error.message}\n${correctable ? '本次未写入，请修改内容后再次明确提交；密码需重新输入。' :
      '本次表单已提交，不能直接重发。请关闭后刷新列表和审计核对结果，再重新操作。'}`);
    await Promise.all([load(context.view),load('audit')]);
  } finally {
    state.busy=false;
    if(editorContext === context) { $('editorCancel').disabled=false; text('editorCancel',context.submitted ? '关闭' : '取消'); text('editorSubmit',context.submitted ? '已提交' : '再次确认提交'); $('editorSubmit').disabled=context.submitted; }
  }
});
const usernameField={name:'username',label:'账号名',minLength:3,maxLength:32,help:'3～32 位英文字母、数字、下划线、点或连字符；保存时转为小写。'};
function normalizedUsername(value) { return value.normalize('NFKC').trim().toLowerCase(); }
function usernameProblem(value) { return /^[a-z0-9][a-z0-9_.-]{2,31}$/.test(normalizedUsername(value)) ? '' : '账号名格式不正确，请按提示填写。'; }
function createUser() {
  openEditor({title:'新增普通用户',description:'创建一个普通账号，不授予管理员权限。请通过安全方式把初始登录信息交付给使用者。',submitLabel:'确认创建',allowConflictCorrection:true,
    fields:[usernameField,{name:'password',label:'初始密码',type:'password',minLength:10,maxLength:128,help:'10～128 字符，不能全部为空白。'},
      {name:'repeat',label:'再次输入初始密码',type:'password',minLength:10,maxLength:128},{name:'confirm',label:'再次输入新账号名以确认'}],
    validate:value => usernameProblem(value.username) || (!value.password.trim() ? '密码不能全部为空白。' : '') ||
      (value.password !== value.repeat ? '两次密码不一致。' : '') || (value.confirm !== normalizedUsername(value.username) ? '确认账号名需与规范化后的小写账号名一致。' : ''),
    run:value => api('/api/admin/users',{method:'POST',body:{username:normalizedUsername(value.username),password:value.password,confirm_username:value.confirm}})});
}
function editUser(user) {
  openEditor({title:'修改账号名',description:`修改 ${user.username} 后，其旧网页登录会被撤销，连接和运行任务会清理。用户 ID、设备、资料与记忆保留。`,
    fields:[{...usernameField,value:user.username},{name:'confirm',label:`输入当前账号名 ${user.username} 以确认`}],
    validate:value => usernameProblem(value.username) || (value.confirm !== user.username ? '请输入当前账号名确认。' : ''),
    run:value => api(`/api/admin/users/${encodeURIComponent(user.id)}`,{method:'PATCH',body:{username:normalizedUsername(value.username),confirm_username:value.confirm}})});
}
function createDevice() {
  openEditor({title:'登记设备',description:'登记一个未绑定设备，并生成仅此次展示的凭证。请准备安全配发工具；响应丢失时先核对登记结果，不要重复提交。',submitLabel:'确认登记',allowConflictCorrection:true,
    fields:[{name:'id',label:'设备 ID',maxLength:64,pattern:'[A-Za-z0-9][A-Za-z0-9_\\-]{0,63}',help:'1～64 位字母、数字、下划线或连字符。ID 创建后不可修改。'},
      {name:'display_name',label:'设备名称',maxLength:80},{name:'confirm',label:'再次输入设备 ID 以确认'}],
    validate:value => !/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(value.id) ? '设备 ID 格式不正确。' : !value.display_name.trim() ? '设备名称不能为空。' : value.confirm !== value.id ? '两次设备 ID 不一致。' : '',
    run:value => api('/api/admin/devices',{method:'POST',body:{id:value.id,display_name:value.display_name.trim(),confirm_device_id:value.confirm}})});
}
function editDevice(device) {
  openEditor({title:'修改设备名称',description:`只修改 ${device.id} 的显示名称，设备身份与归属保留。`,
    fields:[{name:'display_name',label:'设备名称',value:device.display_name,maxLength:80},{name:'confirm',label:`输入设备 ID ${device.id} 以确认`}],
    validate:value => !value.display_name.trim() ? '设备名称不能为空。' : value.confirm !== device.id ? '请输入准确的设备 ID 确认。' : '',
    run:value => api(`/api/admin/devices/${encodeURIComponent(device.id)}`,{method:'PATCH',body:{display_name:value.display_name.trim(),confirm_device_id:value.confirm}})});
}
function showCredential(result) {
  if(typeof result.credential?.token !== 'string' || !result.credential.token) { toast('操作已提交，但凭证响应不完整。请核对设备后决定是否轮换。',true); return; }
  credentialMemory={device_id:result.credential.device_id || result.device_id,token:result.credential.token};
  text('credentialTarget',credentialMemory.device_id); $('credentialValue').value=credentialMemory.token; $('credentialFeedback').textContent='';
  $('credentialDialog').showModal();
}
function clearCredential() { credentialMemory=null; $('credentialValue').value=''; $('credentialFeedback').textContent=''; }
$('credentialClose').addEventListener('click',() => $('credentialDialog').close());
$('credentialDialog').addEventListener('close',clearCredential);
$('credentialCopy').addEventListener('click',async() => {
  if(!credentialMemory) return;
  try { await navigator.clipboard.writeText(credentialMemory.token); text('credentialFeedback','已复制，请通过安全渠道配发。'); }
  catch { $('credentialValue').focus(); $('credentialValue').select(); text('credentialFeedback','无法自动复制，已选中凭证，请手动复制。'); }
});
$('credentialDownload').addEventListener('click',() => {
  if(!credentialMemory) return;
  const url=URL.createObjectURL(new Blob([JSON.stringify(credentialMemory,null,2)],{type:'application/json'}));
  const link=node('a'); link.href=url; link.download=`${String(credentialMemory.device_id).replace(/[^A-Za-z0-9_-]/g,'_')}-credential.secret.json`;
  link.click(); setTimeout(() => URL.revokeObjectURL(url),1000); text('credentialFeedback','下载已发起，请安全保存凭证文件。');
});
window.addEventListener('pagehide',clearCredential);
$('createUser').addEventListener('click',createUser); $('createDevice').addEventListener('click',createDevice);
$('openAudit').addEventListener('click',() => setView('audit'));
$('runBackup').addEventListener('click',() => confirmAction({title:'检查并备份',expected:'检查并备份',buttonLabel:'确认检查并备份',
  description:'将按服务端已配置策略检查备份。到达间隔时创建并校验新备份；已有近期备份时只校验，不强制重复创建。此操作不下载、不恢复数据。',
  run:() => api('/api/admin/backups/run',{method:'POST',body:{confirm:'检查并备份'},timeout:30000}),
  successMessage:result => result?.message || (result?.created === false ? '已检查近期备份，尚未到创建间隔。' : '备份已创建并校验。')}));

document.querySelectorAll('.nav-item').forEach(button => button.addEventListener('click', () => setView(button.dataset.view)));
$('refreshButton').addEventListener('click', () => load());
const mobileNavigation = matchMedia('(max-width:760px)');
function setMenu(open) {
  open = mobileNavigation.matches && open;
  $('shell').classList.toggle('menu-open',open);
  $('menuToggle').setAttribute('aria-expanded',String(open));
  $('navScrim').hidden = !open;
  $('sidebar').inert = mobileNavigation.matches && !open;
  document.querySelector('.main-wrap').inert = open;
}
mobileNavigation.addEventListener('change',() => setMenu(false));
setMenu(false);
$('menuToggle').addEventListener('click',() => { setMenu(!$('shell').classList.contains('menu-open')); if($('shell').classList.contains('menu-open')) document.querySelector('.nav-item.active')?.focus(); });
$('navScrim').addEventListener('click',() => { setMenu(false); $('menuToggle').focus(); });

async function logoutAdmin() {
  if(state.busy || state.loggingOut) return;
  state.loggingOut = true;
  $('logoutButton').disabled = true;
  if($('gateLogout')) $('gateLogout').disabled = true;
  document.querySelector('.main-wrap').inert = true;
  Object.values(state.requests).forEach(controller => controller.abort());
  try {
    await api('/api/admin/auth/logout',{method:'POST'});
    clearCredential();
    clearTimeout(uptimeTimer);
    window.location.assign('/admin/login?notice=logout');
  } catch(error) {
    if(error.status === 401) return;
    toast(`${error.message} 请重试退出；若已失效，将返回登录页。`,true);
    state.loggingOut = false;
    $('logoutButton').disabled = false;
    if($('gateLogout')) $('gateLogout').disabled = false;
    setMenu($('shell').classList.contains('menu-open'));
  }
}
$('logoutButton').addEventListener('click',logoutAdmin);
document.addEventListener('keydown',event => { if(event.key === 'Escape' && $('shell').classList.contains('menu-open')) $('navScrim').click(); });
document.addEventListener('click', event => { if($('shell').classList.contains('menu-open') && !event.target.closest('#sidebar,#menuToggle')) $('navScrim').click(); });
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
    state.user = admin.user; state.csrf = admin.csrf_token;
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
}
if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded',initializeAdmin,{once:true});
else initializeAdmin();
