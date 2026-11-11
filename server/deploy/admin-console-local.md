# 管理后台本地运行与验收

管理入口为 /admin，仅在 accounts 模式向服务端显式授权的管理员开放。普通账号和设备 Bearer 不能读取管理页面、列表或详情。此文介绍本地隔离演示和复验，不表示生产环境已经部署。

## 隔离预览

在仓库根目录打开 PowerShell，使用已安装项目依赖的 Python：

~~~powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
& 'D:\Github\cat\catapp\server\.venv\Scripts\python.exe' -u server/tools/preview_admin.py
~~~

脚本仅监听 127.0.0.1，默认由系统选择空闲端口。启动后输出可直接打开的 /admin 地址、模拟账号和当前代码实际注册的功能；可用 --port 8089 指定本机端口。管理员为 preview-admin、preview-admin-two，普通账号为 preview-owner、preview-guest，模拟密码均为 preview-only-password。Ctrl+C 退出并清理临时数据库、备份、资料和记录目录。

所有登记和历史均为本次生成的模拟数据，不连接实体猫鹿或 USB。应用导入前清除继承的业务配置并禁用 .env 读取；不读真实数据库、模型地址或 Key。使用模拟摘要和固定本地向量，阻止设备运行时、设备 WebSocket 和模型设置。备份演示只访问本次临时目录。一次性登记凭证仅属于模拟设备，不应配发给真实硬件。

## 自动验收

~~~powershell
$env:PYTHONPATH='server'
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONDONTWRITEBYTECODE='1'
& 'D:\Github\cat\catapp\server\.venv\Scripts\python.exe' -m unittest discover -s server/tests -v
$env:NODE_PATH='C:\Users\20388\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules'
$env:BROWSER_EXECUTABLE='C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
$env:PYTHON_EXECUTABLE='D:\Github\cat\catapp\server\.venv\Scripts\python.exe'
node server/tests/admin_ui_test.cjs
node server/tests/admin_live_ui_test.cjs
node server/tests/admin_recovery_ui_test.cjs
node server/tests/web_ui_test.cjs
node server/tests/knowledge_ui_test.cjs
~~~

浏览器须使用本机实际安装的 Chromium 路径；上述 Edge 路径仅为示例。测试使用临时数据库和模拟模型。截图及原始验收日志放在忽略提交的 artifacts 下，不提交数据库、凭证或个人数据。完整验收项目见 admin-console-acceptance.md，接口语义见 ../admin_contract.md 与 ../admin_fleet_contract.md。

## 功能与数据保护

管理范围包括首页聚合、账号与设备的列表和详情、确认后的维护操作、容量统计、备份状态与校验历史、脱敏审计。新增账号、设备生命周期和备份检查的实际可用性以固定集成版本的接口与测试为准，不能把旧版本通过记录外推到新功能。

所有写请求须携带有效管理员 Cookie、同源 Origin、CSRF 和目标二次确认，并在锁后及写事务内重新核验授权。管理员角色仅由服务端命令行授予和撤销，网页不能提权。详情和统计不创建设备运行时，不读取私人正文、密码散列、硬件令牌或模型密文。

解绑保留硬件凭证和历史；撤销登记会使旧硬件凭证失效。有历史关联的撤销设备保留记录，只有符合契约的空误登记可永久删除。登记和轮换只在成功响应中返回一次凭证；响应丢失时先核对记录，再由操作者决定轮换，禁止自动重发。浏览器只在当前内存弹窗中保留凭证，复制或下载后的配发由操作者通过既有安全工具完成。

变更已提交但连接清理或审计确认未完成时可能返回 503；页面应核对实际目标状态并明确提示，不能盲目重试。借用期间设备的当前使用者与长期主人分别处理，双方历史始终隔离。容量和管理操作趋势不代表聊天量、模型调用量或费用。

手动备份遵守已有目录与间隔策略；近期备份仍有效时仅校验并明确说明未创建新副本。网页不修改目录、不下载数据库、不在线恢复。数据库快照不含独立加密主密钥，恢复时仍须部署方安全保留配对密钥。

## 验证环境与限制

独立 QA 本机环境为 Windows 11、Python 3.13.14，浏览器测试使用本机 Chromium。2026-11-11 新增的第二轮独立 HTTP 验收 6 项、隔离预览 2 项均通过；它们尚不能代表完整 CRUD 集成版本通过。最终固定版本需重新执行完整 Python 和浏览器验收并记录准确通过、失败和跳过数。

本机没有可用的 Linux / Python 3.10 运行环境；现有 WSL 与 Docker 均未启动。本次没有启动虚拟机、WSL、Docker 或修改系统设置。保留的 Linux Python 3.10 工作流只是一份可复现流程；此前公开 Actions 运行 42786930235 的结论为 failure、作业数为 0，没有解释器或测试执行证据，不能写成兼容性通过。当前不再依赖远程 CI，也不推送远程分支。

未连接生产服务器、真实硬件或付费模型，未执行 exe 打包或生产部署。部署仍采用 SQLite 单 worker，具体审批、切换与配对回退步骤见 admin-console-rollout.md。
