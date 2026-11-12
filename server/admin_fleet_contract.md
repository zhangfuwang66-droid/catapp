# 设备管理与首页扩展接口

全部位于 `/api/admin`，沿用显式管理员、Cookie、Origin、CSRF、审计及请求取消约定。只读接口不创建运行时、不连接模型、不返回用户正文或任何凭证。

## 设备生命周期

- `POST /devices`：`{id,display_name,confirm_device_id}`；201 返回 `{ok:true,device_id,credential:{device_id,token},credential_once:true}`。登记未绑定设备；凭证只在此次响应返回，不进入列表、审计、浏览器持久存储。响应丢失时不得自动重发，请核对是否登记成功后手动轮换凭证。
- `GET /devices/{id}`：`{device,credentials:{active_count,revoked_count,last_issued_at},pairings:{active_count},lifecycle,history:{memberships,loans,conversations,memories},can_delete,delete_blockers}`；device 沿用列表白名单及真实连接状态。
- `PATCH /devices/{id}`：`{display_name,confirm_device_id}`；返回 `{ok:true}`。仅改显示名，不改身份、归属、网络、声音。
- `POST /devices/{id}/revoke`：`{confirm_device_id}`；返回 `{ok:true,requires_usb_provisioning:true,message}`。撤销全部硬件凭证与待用配对码、结束借用并解除绑定、关闭旧连接。记忆、资料及历史保留。可重复收尾，不重复增加授权代次。硬件不会自动显示配对码，须签发并安全配发新凭证后恢复。
- `POST /devices/{id}/rotate-credential`：`{confirm_device_id}`；返回同登记的一次性 credential 响应，另有 `binding_cleared:true,requires_usb_provisioning:true,message`。允许未绑定设备，或已安全停用的设备；正常使用中仍绑定的设备返回409。若用户停用后仍保留旧主人，恢复会在同一事务结束旧借用与使用关系、清除绑定，再签发全新凭证；历史记忆保留，既有语音偏好设为暂停。旧凭证立即且永久无效。用户须通过既有安全配发工具更新实体设备，本页面不连接 USB。
- `DELETE /devices/{id}`：`{confirm_device_id}`；返回 `{ok:true,requires_usb_provisioning:true,message}`。仅可删除已撤销、未绑定、无任何历史关联/记忆/策略的空设备，避免误删真实数据。有历史的设备保留撤销记录；详情提供中文删除阻止原因。删除保留管理员审计。同一芯片编号可以重新登记，但必须签发并安全配发新凭证，原凭证不会复活。

详情 `lifecycle` 字段：`state` 为 `bound|borrowed|unbound|revoked|credential_missing`，另含 `pairing_available,recovery_requires_usb,can_unbind,can_rotate_credential,next_action,message`。`next_action` 为 `unbind_to_transfer|wait_for_pairing|rotate_credential_and_provision`。`pairing_available` 仅表示服务器允许有效凭证申请配对码，不代表最新签发的凭证已送入实体设备；签发响应会始终提示 USB 配发。只读元数据不包含凭证明文或散列。

用户侧 `/api/devices/{id}/unbind` 是转交解绑，保留 Wi-Fi、硬件凭证，设备可立即请求新配对码；历史记忆仍按账号隔离。用户侧 `/revoke` 是安全停用，会撤销硬件凭证，恢复须管理员签发新凭证并 USB 配发。首次绑定和重新绑定均默认暂停语音，由新主人在控制台明确开启，不恢复旧收音状态。

未知设备返回404，重复ID、状态冲突、确认错误返回409，格式错误422。所有写在同一事务复核管理员会话及角色。设备锁内复核初读的归属与授权代次；等待时归属改变则409。已变更但连接清理或审计未确认返回503，禁止前端自动重试。

## 首页

`GET /dashboard` 返回：

```text
{
  generated_at: Unix秒,
  summary: 原GET /overview响应,
  activity: [{date: UTC的YYYY-MM-DD,users:当日新增账号,devices:当日登记设备,actions:当日管理操作}],
  recent_audit: 最近8条脱敏审计记录,
  attention: [{code,level:"info"|"warning",count,message,view:"users"|"devices"|"storage"|"backups"|"audit"}],
  workload: {documents:{状态:数量},active_sessions,unbound_devices,revoked_devices,incomplete_actions},
  system: {python_version,platform,worker_mode:"single",database_engine:"SQLite",disk_total_bytes,disk_free_bytes,disk_status:"ok"|"low"|"unknown"}
}
```

近7天按UTC自然日聚合，包含今天；空白日期为真实的0。只统计管理员操作，不声称是聊天量、AI用量或计费趋势。磁盘读失败显示unknown，不伪造健康状态。不提供数据库下载、任意命令、在线恢复或网页提权。

## 备份操作

- `GET /backups` 原状态增加 `scheduler_running`。`running` 现在准确表示正在执行备份，不再把调度器等待下一次运行误报为正在备份。
- `GET /backups/history`：`{items:[{created_at,bytes,verified:true}],total,skipped_unverified,retain,interval_seconds,includes_provider_key:false,status:原备份状态}`。只列校验通过的最近20份数据库备份，不返回路径、文件名、回执或数据库内容。独立模型加密主密钥不在这些数据库快照中，恢复仍须使用部署方安全保留的对应主密钥。
- `POST /backups/run`：`{confirm:"检查并备份"}`，按已配置策略检查备份、到期时创建并校验。返回 `{ok:true,created:true|false,status,message}`；已有近期备份时created=false并明确说明已检查，没有伪造新备份。未配置备份目录409。写请求同样CSRF、二次确认、审计；排队后开始操作前复核会话。不允许网页修改备份目录或执行恢复。
