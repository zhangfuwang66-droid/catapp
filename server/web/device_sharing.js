'use strict';

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
  $('shareSubmit').disabled = !shareDevice || $('shareConfirm').value !== shareDevice.id;
}

function openShare(device) {
  shareDevice = device; $('shareForm').reset(); $('shareError').textContent = ''; $('loanCode').textContent = ''; $('loanCode').hidden = true;
  $('shareTitle').textContent = device.display_name + ' · 转交与借用';
  $('shareConfirmLabel').textContent = '输入 ' + device.id + ' 以确认';
  const select = $('shareAction');
  Array.from(select.options).forEach(option => { option.disabled = device.role === 'guest' && option.value !== 'end'; });
  select.value = device.role === 'guest' || device.loan_active || device.loan_pending ? 'end' : 'loan';
  shareDescription(); $('shareDialog').showModal();
}

$('shareAction').addEventListener('change',shareDescription);
$('shareConfirm').addEventListener('input',shareDescription);
$('shareForm').addEventListener('submit',async event => {
  event.preventDefault(); const device = shareDevice;
  if(!device || $('shareConfirm').value !== device.id) return;
  $('shareSubmit').disabled = true; $('shareError').textContent = '正在处理…';
  const action = $('shareAction').value;
  try {
    const suffix = action === 'end' ? 'loan/end' : action;
    const result = await api('/api/devices/' + encodeURIComponent(device.id) + '/' + suffix, {method:'POST', body:JSON.stringify({
      confirm_device_id:device.id, ...(action === 'loan' ? {hours:Number($('loanHours').value)} : {})})});
    if(action === 'loan') {
      $('loanCode').textContent = result.code + ' · 5 分钟内使用，仅可接收一次'; $('loanCode').hidden = false;
      $('shareConfirm').value = ''; $('shareError').textContent = '借用码已生成，请交给对方；对方接收前你仍可正常使用。';
    } else { $('shareDialog').close(); notice(action === 'unbind' ? '已解绑，网络保留。小猫联网后重新显示配对码。' : '借用已结束。'); }
    await refreshDevices();
  } catch(error) { report('shareError',error); }
  finally { shareDescription(); }
});
$('shareDialog').addEventListener('close', () => { shareDevice = null; $('loanCode').textContent = ''; $('shareConfirm').value = ''; });
