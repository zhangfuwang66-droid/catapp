'use strict';

let firmwareState = null;
let firmwareBusy = false;
let firmwareReading = false;
const firmwarePhases = {queued:'已安排，等待设备空闲', downloading:'正在下载新版', verifying:'正在校验签名与文件', rebooting:'正在重启', pending_validation:'正在进行启动自检', updated:'更新完成', rolled_back:'已回退上一版', failed:'本次更新未完成'};
const firmwareErrors = {power_interrupted:'下载时设备掉电或重启', download_failed:'固件下载连接失败', download_integrity_failed:'下载中断或文件校验未通过', signature_invalid:'发布签名验证未通过', image_invalid:'固件格式验证未通过', authorization_lost:'任务授权已变更或无法重新确认', nvs_failed:'设备无法保存安全启动记录', ownership_changed:'设备归属已变化', rollback_unavailable:'没有可回退的已验证版本'};

function clearFirmware() {
  firmwareState = null;
  document.getElementById('firmwareCurrent').textContent = '等待设备上报';
  document.getElementById('firmwareAvailable').textContent = '待检查';
  document.getElementById('firmwareOnline').textContent = '等待设备状态';
  document.getElementById('firmwareHeadline').textContent = '设备连接已结束';
  document.getElementById('firmwareDetail').textContent = '请重新登录后查看设备更新。';
  document.getElementById('firmwareNotes').textContent = '';
  document.getElementById('firmwareProgressWrap').hidden = true;
  document.getElementById('firmwareFeedback').textContent = '';
  document.getElementById('firmwareAuto').checked = false;
  ['firmwareInstall','firmwareRollback','firmwareAuto'].forEach(id => { document.getElementById(id).disabled = true; });
}

function renderFirmware(state) {
  const current = state.current;
  const command = state.command;
  const active = command && ['queued','downloading','verifying','rebooting','pending_validation'].includes(command.state);
  const readonly = firmwareBusy || !state.supported || state.borrowed;
  document.getElementById('firmwareCurrent').textContent = current?.version || '需要首次 USB 升级';
  document.getElementById('firmwareAvailable').textContent = state.available?.version || '已是当前发布版本';
  document.getElementById('firmwareOnline').textContent = state.online ? '设备在线' : '设备离线或尚未上报';
  document.getElementById('firmwareHeadline').textContent = active ? firmwarePhases[command.state] : !state.supported ? '开启小鹿的下一段旅程' : state.available ? '新版本已准备好' : '你的设备已准备就绪';
  document.getElementById('firmwareDetail').textContent = state.borrowed ? '设备正在借用中。请先结束借用，再安排升级或回退。' : !state.supported ? '目前先支持小鹿，首次启用需要通过 USB 安装双分区版本。' : state.automatic_blocked ? '上一轮升级未完成或已回退；已暂停此版本的自动重试，你仍可手动升级。' : state.online ? '保持供电，更新期间会短暂暂停对话，完成后自动重连。' : '离线时也能安排更新，下次开机联网后会继续。';
  document.getElementById('firmwareNotes').textContent = state.available?.notes || '当前没有可用的新版本。';
  document.getElementById('firmwareAuto').checked = state.auto_update;
  document.getElementById('firmwareAuto').disabled = readonly;
  document.getElementById('firmwareInstall').disabled = readonly || active || !state.available;
  document.getElementById('firmwareRollback').disabled = readonly || active || !current?.rollback_available;
  document.getElementById('firmwareRollback').title = current?.previous_version ? '回退到 ' + current.previous_version : '完成一次成功升级后可使用';
  document.getElementById('firmwareRefresh').disabled = firmwareBusy;
  document.getElementById('firmwareProgressWrap').hidden = !command;
  if(command) {
    document.getElementById('firmwarePhase').textContent = firmwarePhases[command.state] || '等待状态同步';
    document.getElementById('firmwarePercent').textContent = command.progress + '%';
    document.getElementById('firmwareProgress').value = command.progress;
    document.getElementById('firmwareProgress').setAttribute('aria-valuetext', (firmwarePhases[command.state] || '') + ' ' + command.progress + '%');
    document.querySelector('.firmware-orbit').classList.toggle('is-updating', !!active);
    if(command.state === 'failed') feedback('firmwareFeedback','error','更新未完成：' + (firmwareErrors[command.error_code] || '设备未通过本次升级检查') + '。原启动槽仍保留，请检查状态后重试。');
    if(command.state === 'updated') feedback('firmwareFeedback','success','新版已通过启动自检，更新完成。');
    if(command.state === 'rolled_back') feedback('firmwareFeedback','success','已恢复上一版，账号和资料继续保留。');
  }
}

async function refreshFirmware() {
  if(!accountConsole || !consoleReady || firmwareBusy || firmwareReading) return;
  firmwareReading = true;
  const epoch = consoleEpoch;
  try {
    const state = await deviceJson('/firmware');
    if(epoch !== consoleEpoch) return;
    firmwareState = state; renderFirmware(state);
  } catch(error) {
    if(epoch === consoleEpoch) feedback('firmwareFeedback','error',error.message);
  } finally { firmwareReading = false; }
}

async function changeFirmware(action) {
  if(!firmwareState || firmwareBusy) return;
  const prompt = action === 'rollback' ? '回退到上一版？设备会重启，账号、网络和表情保留。' : '开始固件升级？设备空闲后会暂停对话并重启，请保持供电。';
  if(!window.confirm(prompt)) return;
  const epoch = consoleEpoch;
  firmwareBusy = true; renderFirmware(firmwareState);
  feedback('firmwareFeedback','pending',action === 'rollback' ? '正在安排回退…' : '正在安排更新…');
  try {
    await deviceJson('/firmware/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,release_id:action === 'install' ? firmwareState.available.id : '',confirm_device_id:CONSOLE_CONTEXT.device_id})});
    if(epoch === consoleEpoch) feedback('firmwareFeedback','success','任务已保存，等待设备领取；关闭网页不影响执行。');
  } catch(error) {
    if(epoch === consoleEpoch) feedback('firmwareFeedback','error',error.message + '。正在查询实际任务状态，请勿连续点击。');
  } finally {
    firmwareBusy = false;
    if(epoch === consoleEpoch) await refreshFirmware();
  }
}

document.getElementById('firmwareInstall').addEventListener('click', () => changeFirmware('install'));
document.getElementById('firmwareRollback').addEventListener('click', () => changeFirmware('rollback'));
document.getElementById('firmwareRefresh').addEventListener('click', refreshFirmware);
document.getElementById('firmwareAuto').addEventListener('change', async event => {
  const epoch = consoleEpoch;
  const automatic = event.target.checked;
  firmwareBusy = true;
  if(firmwareState) { firmwareState = {...firmwareState, auto_update:automatic}; renderFirmware(firmwareState); }
  try {
    const state = await deviceJson('/firmware/policy',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({auto_update:automatic})});
    if(epoch === consoleEpoch) { firmwareState = state; feedback('firmwareFeedback','success',automatic ? '已开启空闲自动升级，连续空闲 5 分钟后领取新版。' : '已切回手动升级。'); }
  } catch(error) { if(epoch === consoleEpoch) feedback('firmwareFeedback','error',error.message); }
  finally { firmwareBusy = false; if(epoch === consoleEpoch) await refreshFirmware(); }
});
setInterval(() => { if(location.hash === '#firmware' && !document.hidden) refreshFirmware(); }, 3000);
if(!accountConsole) document.querySelector('.nav-links a[href="#firmware"]').hidden = true;
