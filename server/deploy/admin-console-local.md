# 管理后台本地验收与预览

本轮集成分支为 `codex/admin-console`，入口 `/admin`。只有 accounts 模式和服务端显式授予的管理员可用；普通用户不会因拥有设备而自动获得管理员身份。

## 安全的临时预览

在本工作树根目录打开 PowerShell，运行以下命令。它通过测试 support 隔离配置，创建临时数据库、模拟用户和离线设备，仅监听 127.0.0.1；不读取真实配置或调用真实模型。

```powershell
$env:PYTHONPATH='server;server/tests'
& 'D:\Github\cat\catapp\server\.venv\Scripts\python.exe' -u server/tests/admin_web_fixture.py
```

终端打印 `{"url":"http://127.0.0.1:临时端口"}` 后，在浏览器访问该地址，使用 `site-admin` / `temporary-test-password` 登录，然后访问 `/admin`。普通账号为 `ordinary-user`，密码相同。以上均为每次重新创建的模拟账号。回到终端按 Enter 结束服务并清理临时数据库。

## 自动验收

```powershell
$env:PYTHONPATH='server'
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
& 'D:\Github\cat\catapp\server\.venv\Scripts\python.exe' -m unittest discover -s server/tests -v
$env:NODE_PATH='C:\Users\20388\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules'
$env:BROWSER_EXECUTABLE='C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
$env:PYTHON_EXECUTABLE='D:\Github\cat\catapp\server\.venv\Scripts\python.exe'
node server/tests/admin_ui_test.cjs
node server/tests/admin_live_ui_test.cjs
node server/tests/web_ui_test.cjs
node server/tests/knowledge_ui_test.cjs
```

截图输出到忽略提交的 `artifacts/admin-ui/` 和 `artifacts/web-tests/`。

## 2026-11-10 集成验收记录

代码与测试固定于 `867b5505f4fd4b125426815678e4663b87c74cc0`；后续提交仅补充验收及部署说明。执行环境为 Windows、Python 3.13.14 和 Edge Chromium，全部测试使用临时数据库、模拟账号及模拟模型。

- 完整 Python：247 项，246 通过，1 项因 Windows 当前用户无符号链接权限跳过；无失败。
- 排队写入专项：15 项通过，含停用/撤销、语音/模型/资料/记忆/设备写入的锁后复核、多个设备等待者推进、排队取消及已开始写入的完整收尾。相关功能 93 项回归通过。
- QA 与安全定点：21 项通过，含 7 项最终验收、Windows 静态路径别名、已提交解绑/语音变更的失败审计及重试、停服点备份保留演练期间新增数据。
- 浏览器：`admin_ui_test.cjs`、`admin_live_ui_test.cjs`、`web_ui_test.cjs`、`knowledge_ui_test.cjs` 全通过；覆盖桌面/手机、管理超时与丢失响应、状态核对、审计中文、真实 FastAPI 权限及原用户页面。目视检查管理页桌面/手机及用户控制台/资料库手机截图正常。
- 兼容性：16 个新增或修改的 Python 源文件通过 Python 3.10 grammar 解析；这不替代 Python 3.10/Linux 实际运行。

独立 QA 在 `codex/admin-qa` 的 `0450ad6d96ba1337dd118e3d4771b447036feadf` 复验了同一代码/测试基线（两者仅部署说明不同）：最终定点 7/7、完整 Python 246 通过及 1 跳过、四组浏览器全部通过；环境为 Windows 11、Python 3.13.14 和本机 Chrome。另外，89 个 Python 文件通过 Python 3.10 语法解析，部署稿 6 段 Bash 命令通过纯语法检查。该 QA 提交已推送并核实远端一致，其部署说明已纳入本分支。

本轮吸收的来源提交如下，未重复吸收各分支的同步基线提交：

- 后端 `codex/admin-console`：`e866bcc78b376ee0db8e4b22318e8738402e56f6`（排队写入及取消收尾），`867b5505f4fd4b125426815678e4663b87c74cc0`（设备等待夹具及子进程编码）。
- 安全 `codex/admin-security`：`82240fe80e559597f3f478a7782a7c4ae8f3287f`、`14edcd9829a5e3c63b5a11378e34a1b0cd054f16`，对应集成提交 `3fe9f96a744fc58045459eab58ec807ff06831ba`、`60561dfb18c7ed6d354b9e8ba539fa5c2e3903c5`。
- 前端 `codex/admin-ui`：`a492841bdb2b55a7df767d4938b2c9016ee16541`、`d4f60235b2b3b4c4d5d314832ac43e73e8e7366f`，对应集成提交 `2498058658c7d177ad4139cd599f6d3028ca188e`、`87cf78da489ec7b93e159557de0249c608ce187f`。
- QA `codex/admin-qa`：`9184354ce8ce3ed9ef15f0ef15e6a75d5d9838cc`、`effe1f46c0cf8973bf5c3be75c748f66185f4ecb`、`a04e420c95f7f6fc1c4cd7643bf15ad4f6434863`，对应集成提交 `3653c064bf001e0ea32433fd1d33d36876b8b0e3`、`4a7d1864b67b7943547773a3f49cb8433397f18e`、`f20ee7c640d2ff352d090303df6098e17b7a6e5c`。

本地原始日志位于忽略提交的 `artifacts/validation/`。本轮未连接服务器、真实硬件或付费模型，未执行 Linux/Python 3.10 运行验收、exe 打包、main 合并或部署。

## 功能与限制

提供聚合概览、用户分页搜索/启停/撤销会话、设备状态/暂停恢复语音/解绑、资料与记忆容量、现有备份任务状态以及脱敏操作审计。所有写操作复用会话、Origin、CSRF 和二次确认，读取列表不会创建设备运行时或模型连接。管理员授权/撤销仅由 `server/admin_cli.py` 执行，不提供网页提权。

在线状态依据当前进程可核实的音频/摄像头连接；没有连接时显示离线。离线恢复语音只保存偏好，后续硬件连接可能启动模型。停用或撤销会话会暂停目标当前使用设备的语音、撤销旧连接并取消相关任务，排队的旧写请求不会继续落库。账号、解绑或语音变更已提交而清理/同步失败时返回 503，明确提示已生效及重试，审计保留“未完成”；管理页遇到超时、丢失响应或 503 时核对原目标状态和审计，再允许用户按结果操作。

模型费用暂不提供，容量为聚合逻辑字节数及单独数据库/WAL磁盘占用，不提供私人正文或文件名。无在线恢复、数据库下载或任意命令入口。部署目标为 SQLite 单 worker；未在生产服务器、真实硬件、真实计费模型或 Python 3.10 解释器上运行验收。桌面资源沿用现有静态资源收集和模块导入，未运行 exe 打包。

服务器部署和成对回退参见 `admin-console-rollout.md`，须先针对具体命令另行确认。
