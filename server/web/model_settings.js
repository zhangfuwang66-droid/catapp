'use strict';

const modelDialog = document.getElementById('modelDialog');
const modelInput = document.getElementById('modelKey');
const modelFeedback = document.getElementById('modelFeedback');
let modelBusy = false;

async function modelRequest(suffix='', options={}) {
  const epoch = consoleEpoch;
  const response = await fetch('/api/account/model' + suffix, {
    ...options, credentials:'same-origin', cache:'no-store', signal:AbortSignal.timeout(25000),
    headers:{'Content-Type':'application/json', 'X-CSRF-Token':consoleCsrf}
  });
  const result = await response.json();
  if(epoch !== consoleEpoch || !consoleReady) throw new Error('登录状态已变更，请重新打开设置');
  if(!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : '模型设置暂不可用');
  return result;
}

function showModelStatus(result) {
  document.getElementById('modelStatus').textContent = result.configured ? '已保存：' + result.masked : '尚未配置。填写后才能开始 AI 对话。';
  document.getElementById('testModel').disabled = !result.configured;
  document.getElementById('deleteModel').disabled = !result.configured;
}

async function openModelSettings() {
  if(!accountConsole || !consoleReady) return;
  modelInput.value = ''; modelFeedback.textContent = '正在读取设置…'; modelDialog.showModal();
  try { showModelStatus(await modelRequest()); modelFeedback.textContent = ''; }
  catch(error) { modelFeedback.textContent = error.message; }
}

async function changeModel(action) {
  if(modelBusy || !consoleReady) return;
  if(action === 'delete' && !confirm('移除本账号的 Key 后，AI 对话和后台模型处理会暂停。记忆与资料保留。确认移除？')) return;
  modelBusy = true;
  modelDialog.querySelectorAll('button').forEach(button => { button.disabled = true; });
  modelFeedback.textContent = action === 'test' ? '正在测试文本和实时语音连接…' : '正在保存配置并更新模型连接…';
  try {
    if(action === 'test') {
      const result = await modelRequest('/test', {method:'POST'});
      modelFeedback.textContent = result.message;
    } else {
      const result = await modelRequest('', action === 'delete' ? {method:'DELETE'} : {method:'PUT', body:JSON.stringify({api_key:modelInput.value})});
      modelInput.value = ''; showModelStatus(result);
      modelFeedback.textContent = action === 'delete' ? '已移除。AI 对话已停止，资料与记忆保留。' : '已保存。可点击测试；小猫会重新连接模型。';
    }
  } catch(error) { modelInput.value = ''; modelFeedback.textContent = error.message || '操作失败，请重试'; }
  finally {
    modelBusy = false;
    modelDialog.querySelectorAll('button').forEach(button => { button.disabled = false; });
    try { showModelStatus(await modelRequest()); } catch(_) {}
  }
}

modelDialog.addEventListener('close', () => { modelInput.value = ''; });
document.getElementById('modelForm').addEventListener('submit', event => { event.preventDefault(); changeModel('save'); });
document.getElementById('closeModel').addEventListener('click', () => { if(!modelBusy) modelDialog.close(); });
document.getElementById('testModel').addEventListener('click', () => changeModel('test'));
document.getElementById('deleteModel').addEventListener('click', () => changeModel('delete'));
