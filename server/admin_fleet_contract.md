# 设备管理与首页扩展接口

全部位于 `/api/admin`，沿用显式管理员、Cookie、Origin、CSRF、审计及请求取消约定。只读接口不创建运行时、不连接模型、不返回用户正文或任何凭证。

## 设备生命周期

- `POST /devices`：`{id,display_name,confirm_device_id}`；201 返回 `{ok:true,device_id,credential:{device_id,token},credential_once:true}`。登记未绑定设备；凭证只在此次响应返回，不进入列表、审计、浏览器持久存储。响应丢失时不得自动重发，请核对是否登记成功后手动轮换凭证。
- `GET /devices/{id}`：`{device,credentials:{active_count,revoked_count,last_issued_at},pairings:{active_count},history:{memberships,loans,conversations,memories},can_delete,delete_blockers}`；device 沿用列表白名单及真实连接状态。
- `PATCH /devices/{id}`：`{display_name,confirm_device_id}`；返回 `{ok:true}`。仅改显示名，不改身份、归属、网络、声音。
- `POST /devices/{id}/revoke`：`{confirm_device_id}`；返回 `{ok:true}`。撤销全部硬件凭证与待用配对码、结束借用并解除绑定、关闭旧连接。记忆、资料及历史保留。可重复收尾，不重复增加授权代次。
- `POST /devices/{id}/rotate-credential`：`{confirm_device_id}`；返回同登记的一次性 credential 响应。仅允许未绑定设备；旧凭证立即无效，已撤销设备重新启用。用户须通过既有安全配发工具更新实体设备，本页面不连接 USB。
- `DELETE /devices/{id}`：`{confirm_device_id}`；返回 `{ok:true}`。仅可删除已撤销、未绑定、无任何历史关联/记忆/策略的空设备，避免误删真实数据。有历史的设备保留撤销记录；详情提供中文删除阻止原因。删除保留管理员审计。

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
