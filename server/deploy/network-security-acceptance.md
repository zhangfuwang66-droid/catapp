# 网络安全独立验收记录

本记录独立于已有后台验收历史。测试使用真实 create_app、真实路由与权限依赖，仅隔离临时数据库、文件目录、后台索引和硬件/模型能力；不补挂路由，不替换安全中间件，不修改其他验收测试来适配产品。

## 当前结论

2026-11-11，初始攻击面验收尚未通过。已向主对话报告 Host、本地兼容写请求及本地 WebSocket 来源校验缺口。随后收到网络中间件契约，待产品实现与最终版本固定；此处的失败证据不能作为修复后的通过结论，本阶段未重跑完整 Python 回归。

产品来源为 5b52dce9df73ceb1c23a759b8b1a660fabb19ec9；QA 合并后的执行 HEAD 为 0f050409c398068185b9b38339ca27330c84666c，开始、结束一致。此次运行额外包含待提交的 server/tests/test_network_security_acceptance.py，产品文件没有改动。

## 初始实测

独立测试执行 14 个方法：6 个通过、8 个含失败；包括子用例共记录 16 个失败断言，没有 ERROR，进程退出码 1。unittest 内部耗时 3.345 秒，含解释器启动的记录耗时 8.006 秒。

- local 与设置固定 public_url 的 accounts 模式均接受 Host: attacker.example，GET /api/health 返回 200。local 也接受 localhost.attacker.example、127.0.0.1.attacker.example；伪造 Forwarded / X-Forwarded-Host 不会使攻击域 Host 被拒绝。
- local 下，外站 Origin 对 POST /voice、POST /api/devices/network-local/voice 的请求返回 200，并使真实 paused 状态发生变化。只带 Sec-Fetch-Site: cross-site、不带 Origin 的请求也能改变该状态。
- local 下，外站请求可进入 /servo、/servo_raw、/servo_raw_mode、/camera_setting、/tft_setting 和带 mode 的 /screen_mode 旧 GET 写入口，未在网络边界拒绝。测试不连接硬件，未执行实体控制。
- local /ws/viewer 接受外站 Origin，以及攻击域 Host 和相同攻击域 Origin 的 WebSocket 握手。
- 已通过的边界为：本机 Host 健康检查、local 同源语音写操作、accounts 登录跨站/Origin:null 拒绝、CSRF 失败不撤销会话及合法退出撤销会话、参数与内部异常不回显密码哨兵/数据库路径、静态目录穿越及 Windows 路径别名不泄露后台文件。

上述复现使用 TestClient，通过真实 ASGI 应用检查服务端边界。没有把跨源 JSON 请求的服务端接受等同于绕过浏览器 CORS，也没有将 ASGI 测试当作 HTTP 解析器或真实网络帧验证。

## 已确定契约与后续验收范围

- Host：local 允许私有 IP、localhost、可信 ASGI 绑定 host 或显式 public_url，拒绝外部 DNS Host；accounts 配置 public_url 时仅允许该 Host，但保留 loopback peer 的本机 health/ready。覆盖 IPv4/IPv6、大小写和端口、畸形/重复 Host，以及伪造代理头不能放宽直接连接的边界。
- 跨站与兼容：local unsafe HTTP 须匹配 Origin，或携带 X-CyberMiao-Request:1 且无跨站 Fetch Metadata；local UI/viewer WS 必须有 Origin，audio/camera 可无 Origin，有 Origin 均须匹配。旧 GET 写入口移至 POST，GET /screen_mode 只读且拒绝 mode 参数。覆盖错误协议/域名/端口、Origin:null 和重复来源头。
- 大请求：普通 HTTP body 64 KiB、10 秒；原 /api/knowledge/documents 上传保留 20 MiB、60 秒流式规则。覆盖声明长度、无长度分块、实际累计超限、CL/TE 歧义及无副作用；HTTP 并发 64、WS 128；头部 32 KiB/100 项、URL 8 KiB，拒绝非 identity Content-Encoding。
- WebSocket：文本 16 KiB、audio 二进制 256 KiB、camera 二进制 1 MiB，超限关闭码 1009，已关闭消息不再发送。覆盖 UTF-8 字节数、无模型/硬件访问、无超限帧进入缓存，以及合法小帧和新连接保持可用。可注入 NetworkLimits 后再按实际参数名测试超时与并发边界。
- 错误与会话：验证错误、内部异常及网络拒绝不回显密码、会话/硬件令牌、模型凭证或内部绝对路径；保留已有会话、CSRF、退出与连接撤销语义。
- 静态路径：目录穿越、编码斜杠/反斜杠、大小写、尾点和空格等 Windows 别名不能绕过后台保护或读到源码/配置。
- 响应与文档：所有响应添加 nosniff、DENY、no-referrer 和权限 policy；保留已有严格 CSP，或补充 frame-ancestors/object/base 限制；accounts 关闭 /docs、/redoc、/openapi.json。

最终固定版本到位后，先运行上述针对测试及受影响的认证、设备、资料上传、真实本地网络入口回归，再执行一次恰当的完整回归。所有执行版本和结果另行记录，不覆盖初始失败证据。

## 证据与执行环境

测试文件为 server/tests/test_network_security_acceptance.py。原始失败日志位于 QA 工作树的 artifacts/validation/network-security-baseline.log，元数据为 network-security-baseline-metadata.json。执行器 run_network_acceptance.py 同在该忽略目录，按显式指定的测试模块运行，不自动触发全量。

环境为 Windows 11、Python 3.13.14。源码路径仅为当前 C QA 工作树的 server 与 server/tests；复用 D 盘虚拟环境的解释器，不从其他工作树导入源码。子进程仅继承系统环境白名单，禁用 .env，使用临时数据/密钥目录，关闭主机硬件功能并禁止测试读取模型 Key。

未连接生产、云端、USB 或真实硬件，未使用真实 Key 或付费模型，未推送远程分支。Linux/Python 3.10、外部浏览器跨站实证及最终全量回归尚未执行。
