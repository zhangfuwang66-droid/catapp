# 网络安全独立验收记录

本记录独立于已有后台验收历史。测试使用真实 create_app、真实路由与权限依赖，仅隔离临时数据库、文件目录、后台索引和硬件/模型能力；不补挂路由，不替换安全中间件。既有测试只在主对话明确授权后补同源夹具头和反代传输语义，保留业务与安全断言。

## 当前结论

2026-11-11，冻结网络候选完成 Windows/Python 3.13.14 范围内的独立安全验收与完整 Python 回归：508 项，502 通过、6 跳过、零失败和错误，退出码 0。此前发现的 4 类网络头/异常管线遗漏已由统一候选修复，原始失败证据和相应回归用例均保留。主聊天另报告冻结候选的 10 项 Edge 浏览器脚本全部退出码 0，证据归属与限制见下文。

冻结产品来源为 `9bbdf93300ba6cfd16569df97df522031d81480e`，QA 使用 `merge --no-edit` 合入后的 HEAD 为 `fcf6cc35d4ba04427672002009dbecaaac6c2f9e`。最终成功全量的实际执行 HEAD 为 `45b11adadfa0277cc973b8da92edb278e15dc3bd`，开始、结束相同，工作区状态两端均为空。相对冻结来源，Python 差异仅为获明确授权的 `server/tests/test_admin_experience_acceptance.py` 同源请求头夹具；产品 Python、JS、CSS 和模板未改变。各工作树仍有说明文档和历史 CI 文件差异，不据此声称整个 Git 树逐文件一致。

本结论限于记录的测试环境与覆盖范围。Linux/Python 3.10 应用运行尚未验证；锁文件中的 Pillow 12.2.0 基线升级仍待处理，当前 JPEG 调用点已有缓解，不能将其写成依赖漏洞已修复。

## 初始实测

产品来源为 5b52dce9df73ceb1c23a759b8b1a660fabb19ec9；QA 合并后的执行 HEAD 为 0f050409c398068185b9b38339ca27330c84666c，开始、结束一致。此次运行额外包含待提交的 server/tests/test_network_security_acceptance.py，产品文件没有改动。

独立测试执行 14 个方法：6 个通过、8 个含失败；包括子用例共记录 16 个失败断言，没有 ERROR，进程退出码 1。unittest 内部耗时 3.345 秒，含解释器启动的记录耗时 8.006 秒。

- local 与设置固定 public_url 的 accounts 模式均接受 Host: attacker.example，GET /api/health 返回 200。local 也接受 localhost.attacker.example、127.0.0.1.attacker.example；伪造 Forwarded / X-Forwarded-Host 不会使攻击域 Host 被拒绝。
- local 下，外站 Origin 对 POST /voice、POST /api/devices/network-local/voice 的请求返回 200，并使真实 paused 状态发生变化。只带 Sec-Fetch-Site: cross-site、不带 Origin 的请求也能改变该状态。
- local 下，外站请求可进入 /servo、/servo_raw、/servo_raw_mode、/camera_setting、/tft_setting 和带 mode 的 /screen_mode 旧 GET 写入口，未在网络边界拒绝。测试不连接硬件，未执行实体控制。
- local /ws/viewer 接受外站 Origin，以及攻击域 Host 和相同攻击域 Origin 的 WebSocket 握手。
- 已通过的边界为：本机 Host 健康检查、local 同源语音写操作、accounts 登录跨站/Origin:null 拒绝、CSRF 失败不撤销会话及合法退出撤销会话、参数与内部异常不回显密码哨兵/数据库路径、静态目录穿越及 Windows 路径别名不泄露后台文件。

上述复现使用 TestClient，通过真实 ASGI 应用检查服务端边界。没有把跨源 JSON 请求的服务端接受等同于绕过浏览器 CORS，也没有将 ASGI 测试当作 HTTP 解析器或真实网络帧验证。

## 首轮加固后的独立定点

产品加固提交为 7315803140a59239ef4e55eb1dfb110c9c46a373。QA 在合入后的 1ebb23624f60804b4b27d0f914f87441bfa0fcad 运行原 14 项，全部通过，退出码 0；原始失败证据未覆盖。

随后补齐至 43 项独立安全测试，并与认证、设备授权、资料路由及网页回归一同执行。执行 HEAD 为 ad49ca187beff9a8617da704c6c1a3b8dfb21318，新增安全测试当时尚未提交；共 61 个方法，56 个通过、5 个含失败，含子用例共 8 个失败断言，无 ERROR，退出码 1，unittest 耗时 39.803 秒。当轮的 5 个失败方法分别为：

- 重复 Content-Encoding 中 identity 在前、gzip 在后时被接受，本地语音状态可改变；逆序请求已正确拒绝。两个子例分别读取自身初始状态，避免前一失败的状态变更造成重复误报。
- 重复 Sec-Fetch-Site 中 same-origin 在前、cross-site 在后时跨站标记被忽略，本地写请求被接受。
- accounts 未配置 public_url 时，畸形 DNS Host（百分号、竖线、空标签或标签起始连字符）被接受。
- 真实会话路由发生模拟内部异常时，500 正文已脱敏，但缺失网络契约要求的安全响应头。
- 既有 test_device_auth 的账号模式旧匿名入口用例要求 GET /servo 返回 404，方法迁移后实际为 405。该次执行仍保留原断言；主对话随后明确已移除的 GET 应精确返回 405，仍注册的旧匿名 GET 保持 404，并授权另笔测试提交补正确 POST 在 accounts 下仍为 404 且不创建 runtime。不将这一契约变化混称为越权成功。

已通过的新增覆盖包括普通 body 精确 64 KiB、声明/流式超限无副作用、超过通用限制的合法资料上传、头数量/字节数/URL 限制、CL/TE 歧义、注入小限值的 body 超时与 HTTP/WS 并发占位恢复、导航例外不放行私密 API、账号模式关闭 schema/docs、严格管理 CSP 保留，以及健康检查必须是真实 loopback peer 且仅限指定读入口。

7 项真实回环网络测试使用真实 uvicorn 与客户端连接，不访问外部网络。覆盖 HTTP Host、浏览器/硬件 WS Origin、UI 文本精确 16 KiB 与 UTF-8 字节数、音频二进制精确 256 KiB、camera 精确 1 MiB、viewer 文本超限、1009 关闭后不透传、不缓存超限帧及新连接恢复。测试服务器自身帧上限特意设为 2 MiB，以确认触发的是应用通道限制，而非传输层先行拒绝。

测试夹具修正已独立提交 ad49ca187beff9a8617da704c6c1a3b8dfb21318：DeviceEndpointsTests 仅添加默认同源 Origin，4 项通过；PublicProcessTests 保留公网 URI 权威，底层使用显式回环 socket，避免重复 Host，并保留仅由本机可信代理处理的 X-Forwarded-Proto。三种公网域名/IPv4/非默认端口流程通过。首次尝试 host/port 参数不被当前 websockets.sync 支持的适配错误日志也保留，未将其作为产品缺陷。

## 最终固定版本验收

统一候选 `0eed78a122e7c08c9586cc30843e8ef3988d032f` 修复了上述 4 类网络头/异常管线遗漏。旧匿名接口的方法契约由明确授权的独立测试提交 `95df431fd2e6f1b77cb6fc81df417e2d17c4d6bb` 更新：已移除 GET 精确断言 405，仍注册的旧匿名 GET 保持 404；正确同源且带有效 CSRF 的 POST 在 accounts 下仍为 404，并验证不调用 `runtime.get`。QA 在 `f34b36bc9e2e57a90892f1c68f89280f85534f60` 完成安全定点 71 项（43 项独立安全、16 项实际边界、12 项认证资源安全）及受影响入口 43 项，共 114 项通过、无跳过或失败，两组退出码均为 0。

冻结来源 9bbdf933 合入后的首次完整运行在 `fcf6cc35d4ba04427672002009dbecaaac6c2f9e` 开始、结束，工作区状态均为空，Python 来源差异为空。508 项中 501 通过、1 失败、6 跳过，无错误，退出码 1；unittest 耗时 394.352 秒，子进程耗时 403.852 秒。唯一失败是 `test_dedicated_login_is_absent_in_local_mode` 的 `403 != 404`：本地 TestClient 的 POST 缺少 Origin，先被新的网络边界拒绝。该失败日志保留。

主对话明确授权仅补该夹具的默认 `Origin: http://127.0.0.1`，保留所有 GET/POST 的 404 及安全断言，未修改产品；独立提交为 `45b11adadfa0277cc973b8da92edb278e15dc3bd`。随后完整后台体验模块 8 项及 3 项相关本地检查共 11 项通过、无跳过，退出码 0，开始、结束均为 45b11ad 且状态为空；再按明确授权执行必要的完整复跑。

最终完整复跑于 2026-11-11 23:08–23:14（Asia/Shanghai）完成：508 项，502 通过、6 跳过、零失败和错误，`OK (skipped=6)`，退出码 0；unittest 耗时 385.621 秒，子进程耗时 390.345 秒。实际命令为 `D:\Github\cat\catapp\server\.venv\Scripts\python.exe -m unittest discover -s server/tests -v`。元数据确认两端 HEAD 均为 45b11ad，两端状态为空；相对请求来源 9bbdf933 的 Python 差异与允许差异均仅含上述夹具文件，未预期差异为空。此结果来自实际完整运行。

6 项跳过分别为 `PublicEdgeNginxTests` 的 5 项（`optional Linux Nginx binary not supplied`，要求 POSIX 及 `CYBERMIAO_REVIEW_NGINX`）和目录链接清理范围的 1 项（当前用户没有符号链接权限，已核对源码）。具体用例名保存在完整日志与元数据中。

主聊天报告在安全环境白名单下使用 Edge，开始、结束来源均为 9bbdf933，10 项浏览器脚本全部退出码 0；证据为主工作树的 `artifacts/validation/network-browser-summary.json`，并非本 QA 独立读取的文件。安全协作者另报告本地 WSL 模板测试 10 项通过；QA 未启动 WSL，Nginx 模板使用模拟上游，不能作为 Linux/Python 3.10 完整应用验证，也不将 Windows 的 5 项跳过计作通过。

JPEG 调用点已限制格式、在加载前限制像素面积，失败返回空字符串且不回退原始字节；完整回归的 7 项专项测试均通过。缺少 Pillow 时仅检查 JPEG 起止标记，不做像素校验。详见 [pillow-jpeg-mitigation.md](pillow-jpeg-mitigation.md)。Linux/Python 3.10 锁仍为 Pillow 12.2.0，处于 GHSA-24rc-8hj2-v98c / CVE-2026-96638 的受影响范围；Pillow 13 要求 Python >=3.11，基线升级仍待处理。实际 Windows 环境为 Pillow 13.0.0，调用点缓解不等于锁文件漏洞修复，也不覆盖其他图像路径。生产未改动。

## 已验收契约与范围

- Host：local 允许私有 IP、localhost、可信 ASGI 绑定 host 或显式 public_url，拒绝外部 DNS Host；accounts 配置 public_url 时仅允许该 Host，但保留 loopback peer 的本机 health/ready。覆盖 IPv4/IPv6、大小写和端口、畸形/重复 Host，以及伪造代理头不能放宽直接连接的边界。
- 跨站与兼容：local unsafe HTTP 须匹配 Origin，或携带 X-CyberMiao-Request:1 且无跨站 Fetch Metadata；local UI/viewer WS 必须有 Origin，audio/camera 可无 Origin，有 Origin 均须匹配。旧 GET 写入口移至 POST，GET /screen_mode 只读且拒绝 mode 参数。覆盖错误协议/域名/端口、Origin:null 和重复来源头。
- 大请求：普通 HTTP body 64 KiB、10 秒；原 /api/knowledge/documents 上传保留 20 MiB、60 秒流式规则。覆盖声明长度、无长度分块、实际累计超限、CL/TE 歧义及无副作用；HTTP 并发 64、WS 128；头部 32 KiB/100 项、URL 8 KiB，拒绝非 identity Content-Encoding。
- WebSocket：文本 16 KiB、audio 二进制 256 KiB、camera 二进制 1 MiB，超限关闭码 1009，已关闭消息不再发送。覆盖 UTF-8 字节数、无模型/硬件访问、无超限帧进入缓存，以及合法小帧和新连接保持可用。通过实际 create_app(network_limits=...) 注入 0.25 秒 body 超时与 HTTP/WS 各 2 个并发名额验证占位和释放，不用大量连接压测代替边界断言。
- 错误与会话：验证错误、内部异常及网络拒绝不回显密码、会话/硬件令牌、模型凭证或内部绝对路径；保留已有会话、CSRF、退出与连接撤销语义。
- 静态路径：目录穿越、编码斜杠/反斜杠、大小写、尾点和空格等 Windows 别名不能绕过后台保护或读到源码/配置。
- 响应与文档：所有响应添加 nosniff、DENY、no-referrer 和权限 policy；保留已有严格 CSP，或补充 frame-ancestors/object/base 限制；accounts 关闭 /docs、/redoc、/openapi.json。

上述定点、受影响入口回归及冻结候选的完整回归均已执行，版本、结果与跳过范围按各阶段分别记录，初始失败证据保持原样。

## 证据与执行环境

测试文件为 server/tests/test_network_security_acceptance.py。原始失败日志位于 QA 工作树的 artifacts/validation/network-security-baseline.log，元数据为 network-security-baseline-metadata.json。执行器 run_network_acceptance.py 同在该忽略目录；仅在明确传入 `ALL_SERVER_TESTS` 时执行完整发现，本次两次完整运行均经过明确授权。

同目录另保留 network-security-first-hardening、network-security-header-probes、network-security-resource-wire、network-security-expanded-first-candidate、network-fixture-adaptation、network-proxy-fixture-corrected 各阶段的 .log 与 -metadata.json。扩展 61 项日志为修正子例状态隔离后的完整定点记录；首个 header-probes 日志包含由前一失败状态延续造成的额外断言失败，不据此重复统计产品问题。

最终阶段另保留 `network-unified-security-targeted`、`network-unified-entry-targeted`、`network-security-final-full-python`、`network-local-login-fixture-corrected`、`network-security-final-corrected-full-python` 各自的 `.log` 与 `-metadata.json`，以及实际包版本快照 `network-security-runtime-packages.json`。最终完整元数据记录来源、允许差异、两端 HEAD/状态、退出码、数量、耗时和跳过原因。

环境为 Windows 11（`Windows-11-10.0.26300-SP0`）、Python 3.13.14；FastAPI 0.162.0、Starlette 1.10.0、Uvicorn 0.54.0、httpx 0.28.1、websockets 17.4、Pillow 13.0.0。该共享 Windows 虚拟环境不等于 Linux/Python 3.10 锁文件环境。

源码路径仅为 `C:\Users\20388\.codex\worktrees\admin-qa\catapp\server` 与其 `tests`；`D:\Github\cat\catapp\server\.venv\Scripts\python.exe` 仅复用解释器，不从其他工作树导入源码。子进程仅继承系统环境白名单，设置 `PYTHON_DOTENV_DISABLED=1`，使用临时数据、数据库与密钥目录，模型基础 Key 置空，关闭 `HAND_TRACKING_ENABLED`、`COVER_INTERACTION_ENABLED` 与 `ENABLE_ESP32_SCREEN_PUSH`。具体环境键和路径保留在元数据中。

QA 未连接生产、云端、USB 或真实硬件，未使用真实 Key 或付费模型，未推送远程分支。Windows 完整回归已经完成；Linux/Python 3.10 应用运行与锁文件依赖升级仍不在已完成结论内。
