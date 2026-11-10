'use strict';

function renderRetention(policy) {
  $('memoryMode').value = policy.mode || 'long_term';
  $('retentionStatus').textContent = '闲置 ' + (policy.idle_days || 0) + ' 天 · 未总结 ' + (policy.pending_messages || 0) +
    ' 条 · 已压缩 ' + (policy.compressed_messages || 0) + ' 条原文' + (policy.archived_at ? ' · 已归档' : '');
  $('restoreMemory').hidden = !policy.compressed_messages && !policy.archived_at;
  $('retentionFeedback').textContent = '';
}

async function updateMemoryPolicy(restore=false) {
  const id = state.memoryId; if(!id) return;
  $('saveRetention').disabled = true; $('restoreMemory').disabled = true;
  $('retentionFeedback').textContent = restore ? '正在校验并恢复原文…' : '正在保存…';
  try {
    const path = '/api/account/memories/' + encodeURIComponent(id);
    if(restore) await api(path + '/restore',{method:'POST'});
    else await api(path + '/retention',{method:'PUT',body:JSON.stringify({mode:$('memoryMode').value})});
    const result = await api(path);
    if(state.memoryId !== id) return;
    renderRetention(result.retention);
    $('retentionFeedback').textContent = restore ? '原文已恢复，摘要和事实保持可用。' : '已保存。不会自动永久删除记忆。';
  } catch(error) { if(state.memoryId === id) report('retentionFeedback',error); }
  finally { $('saveRetention').disabled = false; $('restoreMemory').disabled = false; }
}
$('saveRetention').addEventListener('click', () => updateMemoryPolicy());
$('restoreMemory').addEventListener('click', () => updateMemoryPolicy(true));
