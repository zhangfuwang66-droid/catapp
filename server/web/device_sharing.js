'use strict';

let shareInFlight = false;
let shareSubmitted = false;

async function refreshHistory() {
  const result = await api('/api/account/memories');
  $('historyDevices').replaceChildren();
  for(const device of result.devices || []) {
    const button = element('button','secondary',device.display_name + ' · ' + device.id);
    button.type = 'button'; button.addEventListener('click', () => openMemory(device));
    $('historyDevices').append(button);
  }
  if(!result.devices?.length) $('historyDevices').textContent = '还没有设备使用历史。';
}

function shareDescription() {
  const action = $('shareAction').value;
  $('loanDuration').hidden = action !== 'loan';
  $('shareDescription').textContent = action === 'loan' ?
    '生成一次性借用码，交给同事在自己的账号中添加。对方接收后使用自己的 Key、资料与记忆，借用期间只有对方能控制小猫。到期自动归还，Wi-Fi 保留。' :
    action === 'unbind' ? '结束当前所有连接，保留 Wi-Fi 和硬件身份，联网后屏幕重新显示配对码。你的记忆留在当前账号，新主人不会继承。' :
    '结束当前借用或撤销尚未使用的邀请，设备回到长期主人；对方的历史记忆保留在对方账号。';
  $('shareSubmit').disabled = shareInFlight || shareSubmitted || !shareDevice || $('shareConfirm').value !== shareDevice.id;
  $('shareSubmit').textContent = shareInFlight ? '正在处理…' : shareSubmitted ? '已提交，请关闭后核对' : action === 'unbind' ? '确认解绑，保留 Wi-Fi' : '确认操作';
}

function openShare(device, action='unbind') {
  if(shareInFlight) return;
  shareSubmitted = false;
  shareDevice = device; $('shareForm').reset(); $('shareError').textContent = ''; $('loanCode').textContent = ''; $('loanCode').hidden = true;
  $('shareTitle').textContent = device.display_name + ' · 解绑与借用';
  $('shareConfirmLabel').textContent = '输入 ' + device.id + ' 以确认';
  const select = $('shareAction');
  Array.from(select.options).forEach(option => { option.disabled = device.role === 'guest' && option.value !== 'end'; });
  select.value = device.role === 'guest' ? 'end' : action;
  shareDescription(); $('shareDialog').showModal();
}

$('shareAction').addEventListener('change',shareDescription);
$('shareConfirm').addEventListener('input',shareDescription);
$('shareForm').addEventListener('submit',async event => {
  event.preventDefault(); const device = shareDevice;
  if(shareInFlight || shareSubmitted || !device || $('shareConfirm').value !== device.id) return;
  const action = $('shareAction').value;
  shareInFlight = true; shareSubmitted = true; $('shareDialog').dataset.busy = 'true';
  $('shareAction').disabled = true; $('shareConfirm').disabled = true;
  shareDescription(); $('shareError').textContent = '正在处理…';
  try {
    const suffix = action === 'end' ? 'loan/end' : action;
    const result = await api('/api/devices/' + encodeURIComponent(device.id) + '/' + suffix, {method:'POST', body:JSON.stringify({
      confirm_device_id:device.id, ...(action === 'loan' ? {hours:Number($('loanHours').value)} : {})})});
    if(shareDevice !== device) return;
    if(action === 'loan') {
      if(typeof result.code !== 'string' || !/^[A-Z0-9]{4}-?[A-Z0-9]{4}$/.test(result.code)) throw new RequestError('借用码响应不完整，请核对设备状态。');
      $('loanCode').textContent = result.code + ' · 5 分钟内使用，仅可接收一次'; $('loanCode').hidden = false;
      $('shareConfirm').value = ''; $('shareError').textContent = '借用码已生成，请交给对方；对方接收前你仍可正常使用。';
    } else if(action === 'unbind') {
      $('shareError').textContent = result.message || '已解绑，Wi-Fi 保留。设备联网并核验身份后，屏幕会重新显示配对码；原账号记忆保留，新主人需用自己的账号绑定。';
    } else { $('shareDialog').close(); notice('借用已结束。'); }
    await refreshDevices();
  } catch(error) {
    if(shareDevice === device && !(error instanceof StaleRequest)) {
      shareSubmitted = ![400,422].includes(error.status);
      report('shareError',error);
      if(shareSubmitted) $('shareError').textContent += ' 请关闭窗口并刷新设备列表核对；不会自动重试。';
      await refreshDevices();
    }
  }
  finally {
    shareInFlight = false; $('shareDialog').dataset.busy = 'false';
    $('shareAction').disabled = false; $('shareConfirm').disabled = false; shareDescription();
  }
});
$('shareDialog').addEventListener('cancel',event => { if(shareInFlight) event.preventDefault(); });
$('shareDialog').addEventListener('close', () => { shareDevice = null; $('loanCode').textContent = ''; $('shareConfirm').value = ''; });
