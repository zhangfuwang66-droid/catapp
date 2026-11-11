# 管理后台接口（第一版）

全部接口位于 `/api/admin`，只在 accounts 模式开放，复用登录 Cookie；每个请求实时核验显式管理员授权。写请求须携带同源 Origin 和 X-CSRF-Token。错误使用 `{detail: 中文说明}`，401 未登录、403 无权限或 CSRF、409 状态冲突、422 参数错误。管理员数据查询或概览文件统计失败时返回 503 和中文稍后重试提示，不回显异常原文或路径，不将查询失败伪装为零值或部分概览；WAL 文件正常缺失时容量为零。时间为 Unix 秒；容量为字节。所有响应 no-store。

- `GET /session` → `{user:{id,username},csrf_token}`。
- `GET /overview` → `{users:{total,enabled,disabled,admins},devices:{total,bound,online},uptime_seconds,storage:{database_bytes,wal_bytes,knowledge_bytes,memory_content_bytes,archive_bytes},backups:{...},model_billing:null}`。online 仅统计本进程已有运行时中、仍获当前使用者授权且处于 CONNECTED 状态的音频/摄像头连接；概览只核验这些候选设备并聚合所需容量，不逐条装饰离线设备或计算资料/记忆的完整分组统计。
- `GET /users?q=&status=all|enabled|disabled&page=1&page_size=20` → `{items:[{id,username,created_at,disabled,is_admin,device_count,key_configured}],total,page,page_size}`。
- `POST /users/{id}/status`，JSON `{disabled:true|false,confirm_username:"目标账号"}` → `{ok:true}`；禁止停用自己或最后一名启用管理员。
- `POST /users/{id}/revoke-sessions`，JSON `{confirm_username:"目标账号"}` → `{ok:true}`；撤销全部网页登录，清理相关连接和运行任务，禁止对自己执行。
- `GET /devices?q=&status=all|bound|unbound|online|offline&page=1&page_size=20` → `{items:[{id,display_name,device_type,owner_user_id,owner_username,active_user_id,active_username,loan_active,loan_pending,loan_expires_at,revoked,created_at,audio_online,camera_online,online,voice_paused}],total,page,page_size}`。device_type 根据登记编号的 `cat-`、`deer-` 前缀分别返回 cat、deer，其他编号返回 unknown，不根据显示名称猜测；未绑定时 voice_paused=null；读接口绝不创建运行时。
- `POST /devices/{id}/voice`，JSON `{paused:true|false,confirm_device_id:"目标ID"}` → `{ok:true,paused}`；恢复可能触发模型连接，UI 必须说明并确认。
- `POST /devices/{id}/unbind`，JSON `{confirm_device_id:"目标ID",cleanup_only:false}` → `{ok:true}`；保留硬件凭证、Wi-Fi 和记忆。手动重试旧解绑的收尾必须传 `cleanup_only:true`（严格布尔值）；该模式在请求开始、取得设备锁及最终授权检查时均要求 owner 和 active user 为空，重新绑定返回 409，不解绑新主人、不变更授权代次或凭证。省略该字段仍执行普通解绑。
- `GET /storage` → `{documents:{count,bytes,statuses:{状态:数量},chunks},memory:{conversations,messages,summaries,memories,content_bytes,archives,archive_bytes,archived_messages,policies:{模式:数量}},retention:{...}}`；只含聚合元数据，不返回私有正文或文件名。
- `GET /backups` → `{enabled,running,last_success_at,last_failure_at,last_error_type,backup_count,skipped_unverified,pruned}`，复用现有备份任务状态，无路径和异常原文；暂无成功时间不代表完成过备份，不提供在线恢复。
- `GET /audit?q=&page=1&page_size=20` → `{items:[{id,actor_id,actor_username,target_type,target_id,action,result,created_at}],total,page,page_size}`。result 为 started/success/failed，日志不接收任意详情文本。

账号状态、会话撤销、设备解绑或语音偏好已提交，后续连接、任务清理或设备同步失败时返回 503 并明确提示已生效及需要重试，审计保持 started（未完成），不谎报整体失败或成功。设备已解绑时可用相同确认编号及 `cleanup_only:true` 手动重试收尾，不重复变更设备授权代次；若期间重新绑定则返回 409。语音操作重试会再次同步设备状态。重试成功产生 success 审计，先前未完成尝试的 started 记录保留。

管理员写请求在全局、目标账号和设备锁排队期间可以取消，取得锁后重新检查原网页登录和管理员角色；网页登录已撤销、过期或账号停用返回 401，角色撤销返回 403。业务数据库变更的事务内再次核验授权，防止等待阻塞操作期间的撤销遗漏。已开始的合法变更在请求取消或后续撤权时仍完成清理及结果审计，完成前所属请求不会退出，也不会留下独立排队写任务。

开始审计写入未确认时返回 503，业务操作不执行；业务已完成而结果审计写入未确认时返回 503 并明确提示状态已生效，刷新核对后不要重复提交。业务操作和失败结果审计同时失败时，仅报告操作未完成及审计未确认，不声称已记录失败。

新增写路由统一使用 `Depends(require_admin_write)` 和 `await audited(request,user,target_type,target_id,action,perform,progress,locked=locked)`。`locked` 是带 `@asynccontextmanager` 的零参数工厂，按目标账号→设备顺序取得本操作需要的锁；全局锁由 `audited` 提供。不得在 `perform` 中重复取得同一把锁、为操作者额外取得账号锁或自行 shield 排队，以免互锁或留下取消后的写任务。

`perform` 通过 `run_blocking` 调用业务存储；在同一个 `BEGIN IMMEDIATE` 写事务内先调用 `admin_store.require_writer(db,actor.id,session_token)`，再复核目标当前状态与初始快照，最后执行变更。HTTP 必须传原请求 Cookie 中的 `SESSION_COOKIE`，不得用新登录或仅角色检查替代。事务成功返回后立即设置 `progress['committed']=True`，再等待连接、运行时及任务清理。返回白名单字段，审计只用固定动作和目标编号。会话失效映射 401、角色撤销映射 403、`ValueError` 映射脱敏 409；HTTP 状态异常由统一入口保留，审计故障统一返回脱敏 503。`begin_action` / `change_user` 的省略 session_token 兼容入口只供可信 CLI 或内部调用，不供网页使用，也不提供网页管理员提权。

管理资源仅通过受管理员权限保护的 `/admin` 和 `/web/admin/{index.html|admin.js|admin.css}` 提供。公共静态挂载在文件系统路径解析后排除整个后台目录；大小写、反斜杠等非规范别名返回 404，本地模式也不提供后台资源。

分页 page>=1，page_size 为 1～100，q 最长 80。请求超出末页（包括超大页码）返回空 items，保留筛选后的真实 total 和原请求页码。管理员授权/撤销仅通过服务端 `python server/admin_cli.py --database /绝对路径/memory.sqlite3 grant|revoke|list --username 账号`；不提供网页提权接口。禁止撤销最后启用管理员；回退程序可保留新增 admin 表，不改变原身份表版本。

用户停用或撤销网页登录会终止相关设备运行时和旧任务，当前使用者的语音偏好设为暂停，防止硬件自动重连恢复声音；启用账号不会恢复旧网页登录。后台取消的在处理资料任务保留原文并标记 failed，可由原用户在资料接口重试。停用账号的模型访问及后续后台任务受账号状态限制。容量为聚合逻辑正文/原始资料/压缩归档字节数，数据库磁盘占用单独展示。

账号写入与管理员撤销按用户锁串行；需要设备锁时统一按用户锁→设备锁获取，等设备期间释放用户锁，使管理员仍可撤销排队请求。排队可被取消，取得两把锁后重新核验当前会话。语音/模型设置、历史记忆清除及设备归属变更只在锁内授权通过后开始写入；已开始的写入与连接清理完成后，所属请求才结束或传播取消，避免遗留脱离请求的旧写任务，也避免管理员持用户锁等待旧请求造成互锁。资料上传接收正文期间不占用户锁，落库前锁内复核。此保证针对单个服务进程，部署仍按现有单进程方式运行。
