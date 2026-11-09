'use strict';
const $ = id => document.getElementById(id);
const context = JSON.parse($('knowledgeContext').textContent);
const state = {epoch:0, csrf:'', user:null, ready:false, uploading:false, xhr:null, deleteId:null, refreshing:false};
const requests = new Set();
const channel = typeof BroadcastChannel === 'function' ? new BroadcastChannel('cybermiao-account') : null;
let noticeTimer;
class Stale extends Error {}
function el(tag, className='', text='') {
  const node = document.createElement(tag); node.className = className; node.textContent = text; return node;
}
function bytes(value) { return value < 1024**2 ? (value / 1024).toFixed(1) + ' KiB' : (value / 1024**2).toFixed(1) + ' MiB'; }
function docUrl(id) { return '/api/knowledge/documents/' + encodeURIComponent(id); }
function notify(message) {
  clearTimeout(noticeTimer); $('notice').textContent = message; $('notice').hidden = false;
  noticeTimer = setTimeout(() => { $('notice').hidden = true; },4500);
}
function report(error) { if(!(error instanceof Stale) && error.name !== 'AbortError') $('pageError').textContent = error.message; }
function clearPrivate() {
  state.epoch++; state.ready = false; state.csrf = ''; state.user = null; state.deleteId = null;
  state.xhr?.abort(); state.xhr = null; state.uploading = false; state.refreshing = false;
  requests.forEach(controller => controller.abort()); requests.clear();
  $('privateView').hidden = true; $('documentList').replaceChildren(); $('searchResults').replaceChildren();
  $('pageError').textContent = ''; $('searchStatus').textContent = ''; $('capacityText').textContent = '';
  $('fileInput').value = ''; $('query').value = ''; $('username').textContent = ''; $('avatar').textContent = '·';
  $('deleteDialog').close(); $('deleteName').textContent = ''; $('deleteError').textContent = '';
  $('progressText').textContent = ''; $('uploadProgress').hidden = true;
  $('notice').hidden = true; clearTimeout(noticeTimer);
}
function loginRequired() { clearPrivate(); location.replace('/?notice=login'); }
async function api(url, options={}) {
  const epoch = state.epoch, controller = new AbortController(); requests.add(controller);
  try {
    const headers = new Headers(options.headers || {});
    if(options.method && options.method !== 'GET' && state.csrf) headers.set('X-CSRF-Token',state.csrf);
    const response = await fetch(url,{...options,headers,signal:controller.signal,credentials:'same-origin',cache:'no-store'});
    const data = await response.json().catch(() => ({}));
    if(epoch !== state.epoch) throw new Stale();
    if(response.status === 401) { loginRequired(); throw new Stale(); }
    if(!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '操作失败，请稍后重试。');
    return data;
  } finally { requests.delete(controller); }
}
async function refresh() {
  if(!state.ready || state.refreshing) return;
  const epoch = state.epoch; state.refreshing = true; $('refreshBtn').disabled = true;
  try {
    if(context.mode === 'accounts') {
      const grant = await api('/api/auth/session');
      if(grant.user.id !== state.user?.id) { loginRequired(); return; }
      state.csrf = grant.csrf_token;
    }
    renderDocuments(await api('/api/knowledge'));
  } catch(error) { report(error); }
  finally { if(epoch === state.epoch) { state.refreshing = false; $('refreshBtn').disabled = false; } }
}
const statuses = {queued:'排队中',processing:'正在处理',ready:'可检索',keyword:'关键词可用',failed:'处理失败'};
function renderDocuments(data) {
  $('documentList').replaceChildren(); $('emptyState').hidden = data.documents.length > 0;
  $('listCount').textContent = data.documents.length;
  $('capacityText').textContent = bytes(data.usage.bytes) + ' / ' + bytes(data.limits.bytes);
  $('documentCount').textContent = data.usage.documents + ' / ' + data.limits.documents + ' 份';
  $('capacityBar').value = data.usage.bytes / data.limits.bytes * 100;
  const pending = data.documents.filter(doc => ['queued','processing'].includes(doc.status)).length;
  $('statusText').textContent = pending ? `${pending} 份资料正在处理，完成后会自动更新。` : '';
  for(const doc of data.documents) {
    const row = el('article','document-row'), info = el('div','document-info');
    const name = el('h3','document-name',doc.filename); name.title = doc.filename;
    const meta = el('div','document-meta');
    meta.append(el('span','',bytes(doc.size_bytes)),el('span','',doc.chunk_count + ' 个片段'),
      el('span','document-state ' + doc.status,statuses[doc.status] || '待处理'));
    info.append(name,meta); if(doc.message) info.append(el('p','document-error',doc.message));
    const actions = el('div','document-actions'), download = el('a','quiet','下载'); download.href = docUrl(doc.id) + '/download';
    const retry = el('button','quiet','重建索引'); retry.type = 'button'; retry.disabled = ['queued','processing'].includes(doc.status);
    retry.addEventListener('click',async () => {
      retry.disabled = true;
      try { await api(docUrl(doc.id) + '/reindex',{method:'POST'}); notify('已加入处理队列。'); await refresh(); }
      catch(error) { report(error); retry.disabled = false; }
    });
    const remove = el('button','quiet remove','删除'); remove.type = 'button';
    remove.addEventListener('click',() => {
      state.deleteId = doc.id; $('deleteName').textContent = doc.filename; $('deleteError').textContent = '';
      $('confirmDelete').disabled = false; $('deleteDialog').showModal();
    });
    actions.append(download,retry,remove);
    row.append(el('div','document-badge',doc.filename.split('.').pop().slice(0,5).toUpperCase()),info,actions);
    $('documentList').append(row);
  }
}
function sendFile(file,epoch) {
  return new Promise((resolve,reject) => {
    const xhr = new XMLHttpRequest(); state.xhr = xhr;
    xhr.open('POST','/api/knowledge/documents'); xhr.timeout = 65000;
    xhr.setRequestHeader('X-File-Name',encodeURIComponent(file.name)); xhr.setRequestHeader('Content-Type','application/octet-stream');
    if(state.csrf) xhr.setRequestHeader('X-CSRF-Token',state.csrf);
    xhr.upload.onprogress = event => {
      if(epoch !== state.epoch) return;
      const percent = event.lengthComputable ? Math.round(event.loaded / event.total * 100) : 0;
      $('progress').value = percent; $('progressText').textContent = `${file.name} · ${percent}%`;
    };
    xhr.onload = () => {
      if(epoch !== state.epoch) { reject(new Stale()); return; }
      if(xhr.status === 401) { loginRequired(); reject(new Stale()); return; }
      let data = {}; try { data = JSON.parse(xhr.responseText); } catch {}
      if(xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new Error(typeof data.detail === 'string' ? data.detail : '上传未完成，请重试。'));
    };
    xhr.onerror = () => reject(new Error('上传连接中断，请检查网络。'));
    xhr.ontimeout = () => reject(new Error('上传超时，请重试。')); xhr.onabort = () => reject(new Stale()); xhr.send(file);
  });
}
async function uploadFiles(files) {
  if(!state.ready || state.uploading || !files.length) return;
  const epoch = state.epoch; state.uploading = true; $('fileInput').disabled = true;
  $('uploadProgress').hidden = false; $('pageError').textContent = '';
  try {
    for(const file of files) {
      if(epoch !== state.epoch) throw new Stale();
      if(!file.size || file.size > 20 * 1024**2) throw new Error(file.name + '：文件不能为空，且不能超过 20 MiB。');
      const result = await sendFile(file,epoch); state.xhr = null;
      notify(result.created ? '上传成功，正在排队处理。' : '这份资料已经在你的资料库里了。'); await refresh();
    }
  } catch(error) { report(error); }
  finally {
    if(epoch === state.epoch) { state.uploading = false; state.xhr = null; $('fileInput').disabled = false; $('fileInput').value = ''; $('uploadProgress').hidden = true; }
  }
}
$('fileInput').addEventListener('change',event => uploadFiles(Array.from(event.target.files)));
$('cancelUpload').addEventListener('click',() => { state.xhr?.abort(); notify('已取消上传。'); });
['dragenter','dragover'].forEach(type => $('dropZone').addEventListener(type,event => { event.preventDefault(); $('dropZone').classList.add('dragging'); }));
['dragleave','drop'].forEach(type => $('dropZone').addEventListener(type,event => { event.preventDefault(); $('dropZone').classList.remove('dragging'); }));
$('dropZone').addEventListener('drop',event => uploadFiles(Array.from(event.dataTransfer.files)));
$('refreshBtn').addEventListener('click',refresh);
$('cancelDelete').addEventListener('click',() => $('deleteDialog').close());
$('confirmDelete').addEventListener('click',async () => {
  const id = state.deleteId; if(!id) return; $('confirmDelete').disabled = true;
  try {
    await api(docUrl(id),{method:'DELETE'}); $('deleteDialog').close(); $('searchResults').replaceChildren();
    $('searchStatus').textContent = '资料已删除，可以重新检索。'; notify('资料已删除。'); await refresh();
  } catch(error) { if(!(error instanceof Stale)) $('deleteError').textContent = error.message; }
  finally { $('confirmDelete').disabled = false; }
});
$('deleteDialog').addEventListener('close',() => { state.deleteId = null; $('deleteName').textContent = ''; });
$('searchForm').addEventListener('submit',async event => {
  event.preventDefault(); const epoch = state.epoch, query = $('query').value.trim(); if(!query || !state.ready) return;
  $('searchBtn').disabled = true; $('searchResults').replaceChildren(); $('searchStatus').textContent = '正在查找相关资料…';
  try {
    const data = await api('/api/knowledge/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query})});
    $('searchStatus').textContent = data.hits.length ? `找到 ${data.hits.length} 个相关片段，回答时会按需参考。` : '没有找到足够相关的资料。试着换个关键词，或确认文件已处理完成。';
    data.hits.forEach((hit,index) => {
      const article = el('article','search-hit'), top = el('div'), link = el('a','',`[${index + 1}] ${hit.filename}`);
      link.href = docUrl(hit.document_id) + '/download';
      top.append(link,el('span','',`片段 ${hit.ordinal} · ${hit.mode === 'hybrid' ? '混合检索' : '关键词'}`));
      article.append(top,el('p','',hit.text)); $('searchResults').append(article);
    });
  } catch(error) { if(!(error instanceof Stale) && error.name !== 'AbortError') $('searchStatus').textContent = error.message; }
  finally { if(epoch === state.epoch) $('searchBtn').disabled = false; }
});
$('logoutBtn').addEventListener('click',async () => {
  try { await api('/api/auth/logout',{method:'POST'}); channel?.postMessage({type:'changed'}); loginRequired(); }
  catch(error) { report(error); }
});
async function start() {
  clearPrivate(); const epoch = state.epoch; $('loading').hidden = false;
  try {
    if(context.mode === 'accounts') {
      const grant = await api('/api/auth/session'); state.csrf = grant.csrf_token; state.user = grant.user;
      $('username').textContent = grant.user.username; $('avatar').textContent = grant.user.username.slice(0,1).toUpperCase(); $('logoutBtn').hidden = false;
    } else { $('username').textContent = '本地资料库'; $('avatar').textContent = '喵'; $('privacyLabel').textContent = '当前本地服务的资料库'; }
    state.ready = true; $('fileInput').disabled = false; $('searchBtn').disabled = false;
    $('privateView').hidden = false; await refresh();
  } catch(error) { report(error); }
  finally { if(epoch === state.epoch) $('loading').hidden = true; }
}
channel?.addEventListener('message',() => { if(context.mode === 'accounts') loginRequired(); });
window.addEventListener('pagehide',clearPrivate); window.addEventListener('pageshow',event => { if(event.persisted) start(); });
document.addEventListener('visibilitychange',() => { if(!document.hidden) refresh(); });
setInterval(() => { if(!document.hidden) refresh(); },4000); start();
