/* Deterministic, offline contract fixture. Every account and credential here is fictional. */
const fs=require('node:fs');
const http=require('node:http');
const path=require('node:path');
const {once}=require('node:events');
const root=path.resolve(__dirname,'..');
const files={'/admin':['web/admin/index.html','text/html; charset=utf-8'],'/web/admin/admin.js':['web/admin/admin.js','text/javascript; charset=utf-8'],
  '/web/admin/admin.css':['web/admin/admin.css','text/css; charset=utf-8']};
function createFixture() {
  const admin={id:'usr-admin',username:'admin',created_at:1730000000,disabled:false,is_admin:true,key_configured:true,device_count:0};
  const member={id:'usr-member',username:'example-member',created_at:1730000000,disabled:false,is_admin:false,key_configured:null,device_count:1};
  const baseDevice={display_name:'离线测试设备',device_type:'cat',created_at:1730000000,owner_user_id:null,owner_username:null,active_user_id:null,
    revoked:false,online:false,audio_online:false,camera_online:false,voice_paused:null,loan_active:false,loan_pending:false};
  const state={users:[admin,member],devices:[{...baseDevice,id:'cat-history',owner_user_id:member.id,owner_username:member.username,active_user_id:member.id,voice_paused:true},
    {...baseDevice,id:'deer-fixture',device_type:'deer',display_name:'离线小鹿'}, {...baseDevice,id:'unknown-fixture',device_type:'unknown',display_name:'待识别样例'}],
    audit:[],writes:[],reads:[],fail:null,onRead:null,rotation:0,backup:{enabled:true,running:false,scheduler_running:true,backup_count:1,
      skipped_unverified:0,pruned:0,last_success_at:1730000000,last_error_type:null},backupCreated:false};
  function deviceDetail(device) {
    const hasHistory=device.id === 'cat-history';
    return {device,credentials:{active_count:device.revoked ? 0 : 1,revoked_count:device.revoked ? 1 : 0,last_issued_at:1730000000},pairings:{active_count:0},
      history:{memberships:hasHistory ? 1 : 0,loans:0,conversations:hasHistory ? 2 : 0,memories:0},can_delete:device.revoked && !hasHistory && device.owner_user_id == null,
      delete_blockers:hasHistory ? ['存在历史归属记录'] : device.revoked ? [] : ['设备凭证尚未撤销']};
  }
  function userDetail(user) {
    const owned=state.devices.filter(d => d.owner_user_id === user.id), blockers=[];
    if(user.id === admin.id) blockers.push('current_account'); if(!user.disabled) blockers.push('enabled'); if(owned.length) blockers.push('device_ownership');
    return {user,active_sessions:user.id === member.id ? null : 0,devices:{owned,borrowed:[],history_count:owned.length},
      documents:{count:0,bytes:0,statuses:{},chunks:0},memory:{conversations:0,messages:0,summaries:0,memories:0,content_bytes:0,archives:0,archive_bytes:0,archived_messages:0,policies:0},
      deletion:{allowed:!blockers.length,blockers}};
  }
  function reply(res,status,data) { res.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'}); res.end(JSON.stringify(data)); }
  function audit(action,type,id) { state.audit.unshift({id:state.audit.length+1,actor_id:admin.id,actor_username:admin.username,target_type:type,target_id:id,action,result:'success',created_at:Date.now()/1000}); }
  function paged(items,url) {
    const q=(url.searchParams.get('q') || '').toLowerCase(), filter=url.searchParams.get('status'), page=Number(url.searchParams.get('page') || 1), size=Number(url.searchParams.get('page_size') || 20);
    items=items.filter(item => JSON.stringify(item).toLowerCase().includes(q));
    if(filter === 'enabled' || filter === 'disabled') items=items.filter(item => item.disabled === (filter === 'disabled'));
    if(filter === 'bound' || filter === 'unbound') items=items.filter(item => (item.owner_user_id == null) === (filter === 'unbound'));
    if(filter === 'online' || filter === 'offline') items=items.filter(item => item.online === (filter === 'online'));
    return {items:items.slice((page-1)*size,page*size),total:items.length,page,page_size:size};
  }
  const server=http.createServer(async(req,res) => {
    const url=new URL(req.url,'http://localhost'), file=files[url.pathname];
    if(file) { res.writeHead(200,{'Content-Type':file[1],'Cache-Control':'no-store'}); res.end(fs.readFileSync(path.join(root,file[0]))); return; }
    let body={};
    if(!['GET','HEAD'].includes(req.method)) { let raw=''; for await(const chunk of req) raw+=chunk; body=JSON.parse(raw || '{}'); state.writes.push({method:req.method,path:url.pathname,body,csrf:req.headers['x-csrf-token']}); }
    else state.reads.push(url.pathname);
    if(state.fail && state.fail.path === url.pathname && (!state.fail.method || state.fail.method === req.method)) { const failure=state.fail; state.fail=null; return reply(res,failure.status,{detail:failure.detail || '模拟服务暂时不可用'}); }
    if(req.method === 'GET' && state.onRead && await state.onRead(url,res,reply)) return;
    if(url.pathname.endsWith('/session')) return reply(res,200,{user:admin,csrf_token:'fixture-csrf'});
    if(url.pathname === '/api/admin/dashboard') {
      const now=new Date(); const activity=Array.from({length:7},(_,i) => ({date:new Date(now.getTime()-(6-i)*86400000).toISOString().slice(0,10),users:i === 6 ? state.users.length : 0,devices:i === 6 ? state.devices.length : 0,actions:i === 6 ? state.audit.length : 0}));
      return reply(res,200,{generated_at:Date.now()/1000,summary:{users:{total:state.users.length,enabled:state.users.filter(u => !u.disabled).length,disabled:state.users.filter(u => u.disabled).length},
        devices:{total:state.devices.length,bound:state.devices.filter(d => d.owner_user_id).length,online:0},uptime_seconds:3600,storage:{database_bytes:262144,wal_bytes:0},backups:state.backup},
        activity,recent_audit:state.audit.slice(0,8),attention:[{code:'pending',count:2,message:'有资料等待处理',level:'info',view:'storage'}],
        workload:{active_sessions:null},system:{python_version:'fixture',platform:'离线测试',database_engine:'SQLite',worker_mode:'single',disk_status:'unknown',disk_free_bytes:null}});
    }
    if(url.pathname === '/api/admin/audit') return reply(res,200,paged(state.audit,url));
    if(url.pathname === '/api/admin/storage') return reply(res,200,{documents:{count:6,bytes:1200,statuses:{queued:2,processing:1,ready:2,failed:1}},memory:{memories:0,content_bytes:0,archive_bytes:0,conversations:0,messages:0,summaries:0,archived_messages:0}});
    if(url.pathname === '/api/admin/backups') return reply(res,200,state.backup);
    if(url.pathname === '/api/admin/backups/history') return reply(res,200,{items:[{created_at:1730000000,bytes:262144,verified:true}],total:1,skipped_unverified:0,retain:7,interval_seconds:86400,includes_provider_key:false,status:state.backup});
    if(url.pathname === '/api/admin/backups/run') { audit('check-backup','system','database-backups'); return reply(res,200,{ok:true,created:state.backupCreated,status:state.backup,message:state.backupCreated ? '备份已创建并校验' : '已有符合间隔策略的近期备份，已完成校验'}); }
    const match=/^\/api\/admin\/(users|devices)(?:\/([^/]+))?(?:\/([^/]+))?$/.exec(url.pathname);
    if(!match) return reply(res,404,{detail:'接口不存在'});
    const [,kind,rawId,action]=match, id=rawId && decodeURIComponent(rawId), list=state[kind], target=list.find(item => item.id === id);
    if(req.method === 'GET') return reply(res,target ? 200 : id ? 404 : 200,id ? target ? kind === 'users' ? userDetail(target) : deviceDetail(target) : {detail:'记录不存在'} : paged(list,url));
    if(!id && req.method === 'POST') {
      if(kind === 'users') {
        if(list.some(user => user.username === body.username)) return reply(res,409,{detail:'账号名已存在'});
        const user={...member,id:'usr-new-'+list.length,username:body.username,key_configured:false,device_count:0}; list.push(user); audit('create-user','user',user.id); return reply(res,201,{ok:true,user});
      }
      if(list.some(device => device.id === body.id)) return reply(res,409,{detail:'设备 ID 已存在'});
      const device={...baseDevice,id:body.id,display_name:body.display_name}; list.push(device); audit('register-device','device',device.id);
      return reply(res,201,{ok:true,device_id:device.id,credential_once:true,credential:{device_id:device.id,token:'fictional-device-token-'+(++state.rotation)}});
    }
    if(!target) return reply(res,404,{detail:'记录不存在'});
    if(req.method === 'PATCH') {
      if(kind === 'users') { target.username=body.username; audit('rename-user','user',id); } else { target.display_name=body.display_name; audit('rename-device','device',id); }
      return reply(res,200,{ok:true,user:target,sessions_revoked:kind === 'users'});
    }
    if(req.method === 'DELETE') {
      if(kind === 'users' ? !userDetail(target).deletion.allowed : !deviceDetail(target).can_delete) return reply(res,409,{detail:'仍存在关联记录，无法删除'});
      list.splice(list.indexOf(target),1); audit(kind === 'users' ? 'delete-user' : 'delete-device',kind === 'users' ? 'user' : 'device',id); return reply(res,200,{ok:true});
    }
    if(action === 'status') { target.disabled=body.disabled; audit(body.disabled ? 'disable-user' : 'enable-user','user',id); }
    if(action === 'revoke-sessions') audit('revoke-sessions','user',id);
    if(action === 'revoke' || action === 'unbind') { target.owner_user_id=null; target.active_user_id=null; target.owner_username=null; if(action === 'revoke') target.revoked=true; audit(action === 'revoke' ? 'revoke-device' : 'unbind-device','device',id); }
    if(action === 'rotate-credential') {
      if(target.owner_user_id != null && !target.revoked) return reply(res,409,{detail:'请先解绑设备'});
      target.revoked=false; target.owner_user_id=null; target.owner_username=null; target.active_user_id=null; target.active_username=null;
      audit('rotate-device-credential','device',id);
      return reply(res,200,{ok:true,device_id:id,credential_once:true,binding_cleared:true,requires_usb_provisioning:true,credential:{device_id:id,token:'fictional-device-token-'+(++state.rotation)}});
    }
    return reply(res,200,{ok:true});
  });
  return {state,server,start:async() => { server.listen(0,'127.0.0.1'); await once(server,'listening'); return `http://127.0.0.1:${server.address().port}`; },
    close:() => { server.closeAllConnections(); server.close(); }};
}
module.exports={createFixture};
if(require.main === module) { const fixture=createFixture(); fixture.start().then(url => console.log(JSON.stringify({url}))); process.stdin.once('data',() => { fixture.close(); }); }
