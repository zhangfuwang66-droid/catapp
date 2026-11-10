'use strict';
const speechDialog = document.getElementById('speechDialog');
let speechBusy = false;

async function speechRequest(options={}) {
  const epoch = consoleEpoch;
  const response = await fetch('/api/account/speech',{...options,credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(15000),
    headers:{'Content-Type':'application/json','X-CSRF-Token':consoleCsrf}});
  const result = await response.json();
  if(epoch!==consoleEpoch || !consoleReady) throw new Error('登录状态已改变');
  if(!response.ok) throw new Error(typeof result.detail==='string' ? result.detail : '语音设置未保存');
  return result;
}

async function openSpeechSettings() {
  if(!accountConsole || !consoleReady) return;
  document.getElementById('speechFeedback').textContent='正在读取…'; speechDialog.showModal();
  try {
    const result=await speechRequest();
    document.getElementById('speechMode').value=result.mode;
    document.getElementById('speechLanguage').value=result.language;
    document.getElementById('speechHotwords').value=result.hotwords.join('，');
    document.getElementById('speechCorrections').value=Object.entries(result.corrections).map(([from,to])=>from+' → '+to).join('\n');
    document.getElementById('speechFeedback').textContent='';
  } catch(error) { document.getElementById('speechFeedback').textContent=error.message; }
}

document.getElementById('speechForm').addEventListener('submit',async event=>{
  event.preventDefault(); if(speechBusy) return;
  speechBusy=true; document.getElementById('saveSpeech').disabled=true;
  document.getElementById('speechFeedback').textContent='正在保存并重连本账号的语音…';
  try {
    const corrections={};
    for(const line of document.getElementById('speechCorrections').value.split('\n').filter(value=>value.trim())) {
      const pair=line.split(/→|=>/).map(value=>value.trim());
      if(pair.length!==2 || !pair[0] || !pair[1]) throw new Error('词语校正请按每行「原词 → 正确词」填写');
      corrections[pair[0]]=pair[1];
    }
    await speechRequest({method:'PUT',body:JSON.stringify({mode:document.getElementById('speechMode').value,
      language:document.getElementById('speechLanguage').value,
      hotwords:document.getElementById('speechHotwords').value.split(/[，,、\n]/).map(value=>value.trim()).filter(Boolean),corrections})});
    document.getElementById('speechFeedback').textContent='已保存。本账号的小猫将使用新设置重连。';
  } catch(error) { document.getElementById('speechFeedback').textContent=error.message; }
  finally { speechBusy=false;document.getElementById('saveSpeech').disabled=false; }
});
document.getElementById('closeSpeech').addEventListener('click',()=>speechDialog.close());

function renderTranscript(data) {
  const node=document.getElementById('transcriptStatus');
  if(data.phase==='draft') {node.textContent='正在核对你说的话…';return;}
  node.textContent=data.needs_confirmation ? '两次识别的数字或日期不同，小喵会先请你确认。' :
    data.changed ? '识别已校正：'+data.text : data.source==='fallback' ? '二次识别暂不可用，本轮使用原识别。' :
      data.source==='no_audio' ? '本轮音频不完整，使用原识别。' : '已确认你说的话。';
}
