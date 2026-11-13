'use strict';

let firmwareState = null;
let firmwareBusy = false;
let firmwareReading = null;
let firmwareTaskKey = null;
let firmwareReadFailed = false;
const firmwarePhases = {queued:'已安排，等待设备空闲', downloading:'正在下载新版', verifying:'正在校验签名与文件', rebooting:'正在重启', pending_validation:'正在进行启动自检', updated:'更新完成', rolled_back:'已回退上一版', failed:'本次更新未完成'};
const firmwareErrors = {power_interrupted:'下载时设备掉电或重启', download_failed:'固件下载连接失败', download_integrity_failed:'下载中断或文件校验未通过', signature_invalid:'发布签名验证未通过', image_invalid:'固件格式验证未通过', authorization_lost:'任务授权已变更或无法重新确认', nvs_failed:'设备无法保存安全启动记录', ownership_changed:'设备归属已变化', rollback_unavailable:'没有可回退的已验证版本', manifest_invalid:'发布信息与设备不兼容', flash_begin_failed:'无法开始写入备用分区', boot_select_failed:'无法选择新的启动分区', rollback_invalid:'上一版固件未通过检查', rollback_failed:'无法切换到上一版', invalid_command:'设备无法识别这次更新任务', task_start_failed:'设备未能启动更新任务'};

function clearFirmware() {
  firmwareState = null;
  firmwareBusy = false; firmwareReading = null; firmwareTaskKey = null; firmwareReadFailed = false;
  document.getElementById('firmwareCurrentLabel').textContent = '当前版本';
  document.getElementById('firmwareCurrent').textContent = '等待设备上报';
  document.getElementById('firmwareReported').textContent = '';
  document.getElementById('firmwareAvailable').textContent = '待检查';
  document.getElementById('firmwareOnline').textContent = '等待设备状态';
  document.getElementById('firmwareHeadline').textContent = '设备连接已结束';
  document.getElementById('firmwareDetail').textContent = '请重新登录后查看设备更新。';
  document.getElementById('firmwareNotes').textContent = '';
  document.getElementById('firmwareProgressWrap').hidden = true;
  document.querySelector('.firmware-orbit').classList.remove('is-updating');
  feedback('firmwareFeedback','', '');
  document.getElementById('firmwareAuto').checked = false;
  ['firmwareInstall','firmwareRollback','firmwareAuto','firmwareRefresh'].forEach(id => { document.getElementById(id).disabled = true; });
}

function renderFirmware(state) {
  const current = state.current;
  const command = state.command;
  const active = command && ['queued','downloading','verifying','rebooting','pending_validation'].includes(command.state);
  const readonly = firmwareBusy || !state.supported || state.borrowed;
  const offlineTask = active && !state.online;
  const phase = offlineTask ? command.state === 'queued' ? '任务已保存，等待设备上线' : '上次进度 · ' + firmwarePhases[command.state] : firmwarePhases[command?.state];
  document.getElementById('firmwareCurrentLabel').textContent = current && !state.online ? '上次上报版本' : '当前版本';
  document.getElementById('firmwareCurrent').textContent = current?.version || '等待设备首次上报';
  document.getElementById('firmwareAvailable').textContent = state.available?.version || '暂无可用新版';
  document.getElementById('firmwareOnline').textContent = state.online ? '设备在线' : current ? '设备离线 · 等待下次联网' : '尚未收到设备状态';
  const reported = typeof state.reported_at === 'number' ? new Date(state.reported_at * 1000) : null;
  document.getElementById('firmwareReported').textContent = reported && Number.isFinite(reported.getTime()) ? '上次上报：' + reported.toLocaleString('zh-CN', {hour12:false}) : '';
  document.getElementById('firmwareHeadline').textContent = offlineTask ? command.state === 'queued' ? (command.action === 'rollback' ? '回退已安排，等待设备上线' : '更新已安排，等待设备上线') : '等待设备重新连接' : active ? phase : state.borrowed ? '结束借用后即可更新' : !current ? '等待设备联网并上报' : !state.supported ? '开启小鹿的下一段旅程' : command?.state === 'failed' ? '这次更新需要再检查' : command?.state === 'rolled_back' ? '已恢复到上一版' : state.available ? '新版本已准备好' : '你的设备已准备就绪';
  document.getElementById('firmwareDetail').textContent = state.borrowed ? '设备正在借用中。请先结束借用，再安排升级或回退。' : !current ? '联网后才能确认设备版本和更新能力，暂不判断是否需要 USB 升级。' : !state.supported ? '目前先支持小鹿，首次启用需要通过 USB 安装双分区版本。' : offlineTask ? command.state === 'queued' ? '任务已保存在服务器；设备下次开机联网、空闲后领取，关闭网页不影响执行。' : '这里保留的是设备上次上报的进度；重新联网后会核对实际结果，请保持供电。' : state.automatic_blocked ? '上一轮升级未完成或已回退；已暂停此版本的自动重试，你仍可手动升级。' : state.online ? '保持供电，更新期间会短暂暂停对话，完成后自动重连。' : '离线时也能安排更新，下次开机联网后会继续。';
  document.getElementById('firmwareNotes').textContent = state.available?.notes || '当前没有可用的新版本。';
  document.getElementById('firmwareAuto').checked = state.auto_update;
  document.getElementById('firmwareAuto').disabled = readonly;
  document.getElementById('firmwareInstall').disabled = readonly || active || !state.available;
  document.getElementById('firmwareRollback').disabled = readonly || active || !current?.rollback_available;
  document.getElementById('firmwareRollback').title = current?.previous_version ? '回退到 ' + current.previous_version : '完成一次成功升级后可使用';
  document.getElementById('firmwareRefresh').disabled = firmwareBusy;
  document.getElementById('firmwareProgressWrap').hidden = !command;
  document.querySelector('.firmware-orbit').classList.toggle('is-updating', !!active && !!state.online);
  const taskKey = command ? command.id + ':' + command.state : '';
  if(taskKey !== firmwareTaskKey) { firmwareTaskKey = taskKey; feedback('firmwareFeedback','',''); }
  if(command) {
    const completed = ['updated','rolled_back'].includes(command.state);
    const progress = Math.max(0, Math.min(completed ? 100 : 99, Number(command.progress) || 0));
    document.getElementById('firmwarePhase').textContent = phase || '等待状态同步';
    document.getElementById('firmwarePercent').textContent = progress + '%';
    document.getElementById('firmwareProgress').value = progress;
    document.getElementById('firmwareProgress').setAttribute('aria-valuetext', (phase || '') + ' ' + progress + '%');
    if(command.state === 'failed') feedback('firmwareFeedback','error','更新未完成：' + (firmwareErrors[command.error_code] || '设备未通过本次升级检查') + '。请检查设备状态后重试。');
    if(command.state === 'updated') feedback('firmwareFeedback','success','新版已通过启动自检，更新完成。');
    if(command.state === 'rolled_back') feedback('firmwareFeedback','success','已恢复上一版，账号和资料继续保留。');
  }
}

async function refreshFirmware() {
  if(!accountConsole || !consoleReady || firmwareBusy || firmwareReading) return;
  const reading = {}; firmwareReading = reading;
  const epoch = consoleEpoch;
  try {
    const state = await deviceJson('/firmware');
    if(epoch !== consoleEpoch || firmwareReading !== reading || firmwareBusy) return;
    if(firmwareReadFailed) { firmwareReadFailed = false; feedback('firmwareFeedback','',''); }
    firmwareState = state; renderFirmware(state);
  } catch(error) {
    if(epoch === consoleEpoch && firmwareReading === reading && !firmwareBusy) { firmwareReadFailed = true; feedback('firmwareFeedback','error',error.message); }
  } finally { if(firmwareReading === reading) firmwareReading = null; }
}

async function changeFirmware(action) {
  if(!firmwareState || firmwareBusy) return;
  const prompt = (action === 'rollback' ? '安排回退到上一版？设备领取任务后会重启，账号、网络和表情保留。' : '安排固件升级？设备空闲后会暂停对话并重启，请保持供电。') + (firmwareState.online ? '' : '当前设备离线，任务会等到下次联网后执行。');
  if(!window.confirm(prompt)) return;
  const epoch = consoleEpoch;
  firmwareBusy = true; firmwareReading = null; renderFirmware(firmwareState);
  feedback('firmwareFeedback','pending',action === 'rollback' ? '正在安排回退…' : '正在安排更新…');
  try {
    const command = await deviceJson('/firmware/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,release_id:action === 'install' ? firmwareState.available.id : '',confirm_device_id:CONSOLE_CONTEXT.device_id})});
    if(epoch === consoleEpoch) { firmwareState = {...firmwareState,command}; renderFirmware(firmwareState); feedback('firmwareFeedback','success','任务已保存，等待设备领取；关闭网页不影响执行。'); }
  } catch(error) {
    if(epoch === consoleEpoch) feedback('firmwareFeedback','error',error.message + '。正在查询实际任务状态，请勿连续点击。');
  } finally {
    if(epoch === consoleEpoch) { firmwareBusy = false; renderFirmware(firmwareState); await refreshFirmware(); }
  }
}

document.getElementById('firmwareInstall').addEventListener('click', () => changeFirmware('install'));
document.getElementById('firmwareRollback').addEventListener('click', () => changeFirmware('rollback'));
document.getElementById('firmwareRefresh').addEventListener('click', refreshFirmware);
document.getElementById('firmwareAuto').addEventListener('change', async event => {
  if(!firmwareState || firmwareBusy) return;
  const epoch = consoleEpoch;
  const automatic = event.target.checked;
  const previousPolicy = firmwareState.auto_update;
  firmwareBusy = true; firmwareReading = null;
  if(firmwareState) { firmwareState = {...firmwareState, auto_update:automatic}; renderFirmware(firmwareState); }
  try {
    const state = await deviceJson('/firmware/policy',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({auto_update:automatic})});
    if(epoch === consoleEpoch) { firmwareState = state; feedback('firmwareFeedback','success',automatic ? '已开启空闲自动升级，连续空闲 5 分钟后领取新版。' : '已切回手动升级。'); }
  } catch(error) { if(epoch === consoleEpoch) { firmwareState = {...firmwareState,auto_update:previousPolicy}; feedback('firmwareFeedback','error',error.message + '。设置尚未确认，正在重新查询。'); } }
  finally { if(epoch === consoleEpoch) { firmwareBusy = false; renderFirmware(firmwareState); await refreshFirmware(); } }
});
setInterval(() => { if(location.hash === '#firmware' && !document.hidden) refreshFirmware(); }, 3000);
if(!accountConsole) document.querySelector('.nav-links a[href="#firmware"]').hidden = true;
