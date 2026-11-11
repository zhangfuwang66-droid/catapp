# 管理后台账号 CRUD 契约

路由由 `admin_accounts.router` 导出，主对话负责在应用注册。依赖安全提交 `cb8b58e` 的 `audited(..., locked=...)` 与 `AdminStore.require_writer`；目标用户锁在审计操作开始前取得，队列可取消，事务内再次核验会话及角色。路径前缀 `/api/admin`，沿用管理员会话、同源 Origin、X-CSRF-Token 和 `no-store`。不提供提权、密码重置或 Key 读取接口。

## 创建普通账号

`POST /users`，JSON：`{username,password,confirm_username}`。

账号沿用现有规则：NFKC、去首尾空白、转小写后为 3～32 位英文字母、数字、下划线、点或连字符，首字符为字母或数字；密码 10～128 字符且非全空白。`confirm_username` 须准确等于规范化后的新账号名。成功 **201**：`{ok:true,user:{id,username,created_at,disabled:false,is_admin:false,key_configured:false}}`。不自动登录，不授予管理员。

## 白名单详情

`GET /users/{user_id}`，成功 **200**：

```json
{
  "user": {"id":"usr_...","username":"alice","created_at":1794297600.0,"disabled":false,"is_admin":false,"key_configured":false},
  "active_sessions": 0,
  "devices": {"owned":[],"borrowed":[],"history_count":0},
  "documents": {"count":0,"bytes":0,"statuses":{},"chunks":0},
  "memory": {"conversations":0,"messages":0,"summaries":0,"memories":0,"content_bytes":0,"archives":0,"archive_bytes":0,"archived_messages":0,"policies":0},
  "deletion": {"allowed":false,"blockers":["enabled"]}
}
```

设备数组每项固定为 `{id,display_name,device_type,owner_user_id,active_user_id,revoked,loan_active,loan_pending,loan_expires_at}`。`owned` 为仍拥有的设备（包含撤销设备），`borrowed` 仅列未结束、未过期、当前仍有效的借用；过期借用不计入。`history_count` 为有历史归属记录的不同设备数。类型严格按 `cat-` / `deer-` 前缀识别，其余 `unknown`；不创建运行时。有效会话要求未撤销、未过期且账号启用。

所有容量为逻辑字节数。只返回计数、容量、状态和设备元数据；不返回密码哈希、会话令牌/摘要、借用邀请码、Key 明文/密文/掩码、资料文件名/正文、对话正文或记忆内容。

## 修改账号名

`PATCH /users/{user_id}`，JSON：`{username,confirm_username}`。`username` 为新名字，`confirm_username` 必须等于锁后当前旧账号名。成功 **200**：`{ok:true,user:{id,username,created_at,disabled,is_admin,key_configured},sessions_revoked:true}`。

禁止修改当前管理员自己的账号。改名保留用户 ID、设备、资料和记忆，事务内撤销旧网页登录；随后持有用户锁清理该用户请求、连接、运行时及资料/记忆后台任务。登录提交会话前同时复核账号名，密码校验期间发生的改名会使旧账号名登录失败。新旧账号名相同也执行会话撤销和清理，允许在部分生效后用当前名字确认重试。已有 Key 只返回布尔状态。

## 删除停用空账号

`DELETE /users/{user_id}`，JSON：`{confirm_username}`。成功 **200**：`{ok:true,deleted_user_id:"usr_..."}`。

仅允许删除已经停用、无设备拥有/借用/历史关联且无私人数据或配置的空账号。禁止删除当前管理员或最后一名启用管理员。有数据返回 **409** 中文原因，并提示先处理，不自动清空任何私人数据；详情 `deletion.blockers` 可用于界面预检，写入时仍会在事务内重新核验。

阻止原因代码固定为：`current_account`、`enabled`、`last_admin`、`device_ownership`、`device_history`、`device_loans`、`device_preferences`、`documents`、`memory`、`memory_policies`、`key_configured`、`speech_settings`。历史借用、已结束归属和已领取配对也阻止删除。删除仅移除空账号、其管理员角色及网页登录记录，已有脱敏管理审计永久保留，不随账号删除。

## 错误及审计

错误统一 `{detail:中文说明}`：401 会话失效，403 非管理员/CSRF/事务授权撤销，404 目标不存在，409 名字重复、确认不符、目标变化或删除不满足条件，422 参数格式错误，503 读取或写入/收尾失败。参数错误不回显密码或完整输入。无数据库异常原文和路径。

修改与删除在用户锁后及数据库写事务内复核操作者、会话和目标，创建也在事务内复核授权。审计动作为 `create-user`、`rename-user`、`delete-user`，目标类型 `user`，不记录密码。改名已生效而后续清理失败时返回 **503**，明确提示已生效、刷新后重试，审计保留 `started`；普通拒绝记录 `failed`，完整成功记录 `success`。
