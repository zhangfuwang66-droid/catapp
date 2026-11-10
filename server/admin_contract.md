# 管理后台接口（第一版）

全部接口位于 `/api/admin`，只在 accounts 模式开放，复用登录 Cookie；每个请求实时核验显式管理员授权。写请求须携带同源 Origin 和 X-CSRF-Token。错误使用 `{detail: 中文说明}`，401 未登录、403 无权限或 CSRF、409 状态冲突、422 参数错误。时间为 Unix 秒；容量为字节。所有响应 no-store。

- `GET /session` → `{user:{id,username},csrf_token}`。
- `GET /overview` → `{users:{total,enabled,disabled,admins},devices:{total,bound,online},uptime_seconds,storage:{database_bytes,wal_bytes,knowledge_bytes,memory_content_bytes,archive_bytes},backups:{...},model_billing:null}`。online 仅统计本进程可核实音频/摄像头连接。
- `GET /users?q=&status=all|enabled|disabled&page=1&page_size=20` → `{items:[{id,username,created_at,disabled,is_admin,device_count,key_configured}],total,page,page_size}`。
- `POST /users/{id}/status`，JSON `{disabled:true|false,confirm_username:"目标账号"}` → `{ok:true}`；禁止停用自己或最后一名启用管理员。
- `POST /users/{id}/revoke-sessions`，JSON `{confirm_username:"目标账号"}` → `{ok:true}`；撤销全部网页登录，清理相关连接和运行任务，禁止对自己执行。
- `GET /devices?q=&status=all|bound|unbound|online|offline&page=1&page_size=20` → `{items:[{id,display_name,device_type,owner_user_id,owner_username,active_user_id,active_username,loan_active,loan_pending,loan_expires_at,revoked,created_at,audio_online,camera_online,online,voice_paused}],total,page,page_size}`。device_type 为 cat 或 unknown（鹿尚未接入）；未绑定时 voice_paused=null；读接口绝不创建运行时。
- `POST /devices/{id}/voice`，JSON `{paused:true|false,confirm_device_id:"目标ID"}` → `{ok:true,paused}`；恢复可能触发模型连接，UI 必须说明并确认。
- `POST /devices/{id}/unbind`，JSON `{confirm_device_id:"目标ID"}` → `{ok:true}`；保留硬件凭证、Wi-Fi 和记忆。
- `GET /storage` → `{documents:{count,bytes,statuses:{状态:数量},chunks},memory:{conversations,messages,summaries,memories,content_bytes,archives,archive_bytes,archived_messages,policies:{模式:数量}},retention:{...}}`；只含聚合元数据，不返回私有正文或文件名。
- `GET /backups` → `{enabled,running,last_success_at,last_failure_at,last_error_type,backup_count,skipped_unverified,pruned}`，复用现有备份任务状态，无路径和异常原文；暂无成功时间不代表完成过备份，不提供在线恢复。
- `GET /audit?q=&page=1&page_size=20` → `{items:[{id,actor_id,actor_username,target_type,target_id,action,result,created_at}],total,page,page_size}`。result 为 started/success/failed，日志不接收任意详情文本。

账号状态或会话撤销已提交、后续连接或任务清理失败时返回 503 并明确提示已生效及需要重试，审计保持 started（未完成），不谎报整体失败或成功。

分页 page>=1，page_size 为 1～100，q 最长 80。管理员授权/撤销仅通过服务端 `python server/admin_cli.py --database /绝对路径/memory.sqlite3 grant|revoke|list --username 账号`；不提供网页提权接口。禁止撤销最后启用管理员；回退程序可保留新增 admin 表，不改变原身份表版本。

用户停用或撤销网页登录会终止相关设备运行时和旧任务，当前使用者的语音偏好设为暂停，防止硬件自动重连恢复声音；启用账号不会恢复旧网页登录。后台取消的在处理资料任务保留原文并标记 failed，可由原用户在资料接口重试。停用账号的模型访问及后续后台任务受账号状态限制。容量为聚合逻辑正文/原始资料/压缩归档字节数，数据库磁盘占用单独展示。

账号写入与管理员撤销按用户锁串行；需要设备锁时统一按用户锁→设备锁获取，等设备期间释放用户锁，使管理员仍可撤销排队请求。排队可被取消，取得两把锁后重新核验当前会话。语音/模型设置、历史记忆清除及设备归属变更只在锁内授权通过后开始写入；已开始的写入与连接清理完成后，所属请求才结束或传播取消，避免遗留脱离请求的旧写任务，也避免管理员持用户锁等待旧请求造成互锁。资料上传接收正文期间不占用户锁，落库前锁内复核。此保证针对单个服务进程，部署仍按现有单进程方式运行。
