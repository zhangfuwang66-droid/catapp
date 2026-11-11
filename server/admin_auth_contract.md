# 独立管理员登录与原应用入口

本契约用于主对话编写 `web/admin/login.html`、`login.css`、`login.js`。管理登录与小喵登录使用独立页面和认证入口，继续共享现有站点会话 Cookie；同一浏览器同时保持一个账号身份。不提供注册、提权或密码重置。

## 页面及精确资源

- `GET /admin/login`：accounts 模式下匿名与普通账号均可打开，已有有效且启用的显式管理员会话时 303 到固定 `/admin`。可选 `next` 只接受精确值 `/admin`；可选 `notice` 只接受 `expired`、`logout`。未知、重复或非法查询参数返回 422，不解码第二次，不归一化外部地址、反斜杠或路径别名。
- `GET /web/admin/login.css`、`GET /web/admin/login.js`：accounts 模式下公开的精确资源路径。不得整体公开 admin 目录；其他后台文件继续鉴权，大小写、反斜杠、尾点等路径别名继续 404。登录 HTML 只通过 `/admin/login` 提供。
- 未登录或会话失效访问 `/admin` 时 303 到 `/admin/login`；仍有有效普通用户会话时 403。非 accounts 模式下管理登录页、认证接口、登录资源均 404。
- 页面使用现有管理 CSP，页面、资源与 API 均 `Cache-Control: no-store`。前端收到管理接口 401 时跳 `/admin/login?notice=expired`，退出成功跳 `/admin/login`。403 不无限自动登录重试。

## 登录

`POST /api/admin/auth/login`，同源 Origin，JSON `{username,password}`。用户名最大 128 字符、沿用 NFKC/去首尾空白/小写规范化，密码为 1～128 字符。拒绝额外字段；参数错误不回显密码或完整输入。登录前没有 CSRF token，因此登录使用严格同源 Origin 校验。

该 API 的查询参数仅支持可选 `next=/admin`；其他值、重复 next、客户端传入 redirect（查询或 JSON）均 422。`redirect` 只作为固定响应值，不从请求计算。协议相对地址、外部 URL、反斜杠、点路径、编码后仍不等于 `/admin` 的目标均拒绝。

成功 **200**：`{ok:true,user:{id,username},csrf_token,redirect:"/admin"}`。仅显式授权且启用的管理员可登录；创建会话的同一写事务内再次核验密码对应身份、账号名、启用状态及管理员角色。沿用登录限流，不能通过切换两个登录入口绕过。

成功签发新的 `cybermiao_session` Cookie：HttpOnly、SameSite=Strict、Path=/，HTTPS 时 Secure，有效期 7 天；替换并撤销浏览器原会话及其连接。令牌明文只进入 HttpOnly Cookie，不进入 JSON、日志或网页存储。

不存在、密码错误、账号停用或非管理员统一 **401**：`{detail:"账号或密码错误，或账号未获管理员授权"}`。失败不签发新会话、不写 Cookie、不撤销浏览器原有会话。跨站/缺失 Origin **403**，参数格式 **422**，限流 **429** 并携带 Retry-After，存储或收尾故障 **503** 脱敏中文说明。以上响应均 no-store。

## 会话与退出

- `GET /api/admin/session`：保留 `{user:{id,username},csrf_token}`。实时核验现有会话与管理员资格；失效 **401**，角色撤销但仍登录 **403**。
- `POST /api/admin/auth/logout`：有效登录 Cookie、同源 Origin、X-CSRF-Token。撤销当前会话和对应连接并清除 Cookie，**200** `{ok:true}`；会话已失效 **401**，来源或 CSRF 无效 **403**。不要求仍有管理员角色，确保被撤权账号能够退出。共享 Cookie 意味着退出也结束该浏览器中的小喵登录。

## 原应用入口

`POST /api/auth/login` 和 `GET /api/auth/session` 在既有响应上增加顶层 `is_admin:boolean`，由服务端查询当前账号的显式且启用的管理员授权。其余 Cookie、CSRF、账号和设备契约不变。

原应用根据 `is_admin === true` 显示指向 `/admin` 的“管理后台”入口；普通用户不显示，退出/账号切换时清除入口状态。普通登录页可显示指向 `/admin/login` 的“管理员登录”入口。入口标记只用于显示，不能替代后台权限核验。
