# 管理后台本地运行与验收

独立管理登录入口为 /admin/login，工作台为 /admin，仅在 accounts 模式向服务端显式授权的管理员开放。普通账号和设备 Bearer 不能读取管理页面、列表或详情。此文介绍本地隔离演示和复验，不表示生产环境已经部署。

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

在待验收工作树根目录打开专用 PowerShell 窗口。下面仅在当前进程保留系统环境白名单；解释器可以复用项目虚拟环境，源码路径始终指向当前工作树。完整执行证据使用临时数据目录并禁用 .env，未使用跨工作树的后端覆盖。

~~~powershell
$qaRuntimeVariables=@('PATH','SystemRoot','WINDIR','TEMP','TMP','USERPROFILE','APPDATA','LOCALAPPDATA','COMSPEC','PATHEXT','PROCESSOR_ARCHITECTURE','NUMBER_OF_PROCESSORS')
Get-ChildItem Env: | Where-Object { $_.Name -notin $qaRuntimeVariables } | Remove-Item
$env:PYTHONPATH="$((Get-Location).Path)/server;$((Get-Location).Path)/server/tests"
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHON_DOTENV_DISABLED='1'
$env:HAND_TRACKING_ENABLED='0'
$env:COVER_INTERACTION_ENABLED='0'
$env:ENABLE_ESP32_SCREEN_PUSH='0'
& 'D:\Github\cat\catapp\server\.venv\Scripts\python.exe' -m unittest discover -s server/tests -v
$env:NODE_PATH='C:\Users\20388\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules'
$env:BROWSER_EXECUTABLE='C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
$env:PYTHON_EXECUTABLE='D:\Github\cat\catapp\server\.venv\Scripts\python.exe'
node server/tests/admin_ui_test.cjs
node server/tests/admin_live_ui_test.cjs
node server/tests/admin_recovery_ui_test.cjs
node server/tests/admin_console_ui_test.cjs
node server/tests/admin_console_live_ui_test.cjs
node server/tests/admin_polish_ui_test.cjs
node server/tests/admin_experience_ui_test.cjs
node server/tests/web_ui_test.cjs
node server/tests/knowledge_ui_test.cjs
~~~

示例为主验收使用的 Edge 实际路径，可替换为本机实际安装的 Chrome 或 Edge 路径，并须能通过 NODE_PATH 解析 Playwright。上述 9 种脚本使用临时数据库、模拟模型或本地接口夹具。真实服务夹具只核验 create_app 的实际路由及请求方法，缺少接口直接失败，不补挂业务路由。截图及原始日志放在忽略提交的 artifacts 下，不提交数据库、凭证或个人数据。完整验收项目见 admin-console-acceptance.md，认证与隐私契约见 ../admin_auth_contract.md 和 ../admin_privacy_contract.md。

## 功能与数据保护

已验收的管理范围包括真实首页聚合、普通账号创建/详情/改名/启停/撤销会话/受保护空账号删除、设备登记/详情/重命名/解绑/撤销/凭证轮换/受保护空登记删除，以及容量、备份检查与校验历史、脱敏审计。网页不提供管理员提权、密码重置、真实 Key 读取或在线恢复。

POST /api/admin/auth/login 使用同源 Origin 和 JSON 用户名、密码。错误或不存在的凭证返回 401，密码正确的普通账号返回 403；失败不签发新会话，也不撤销浏览器原会话。管理员成功登录固定进入 /admin，响应没有 redirect/next，页面和接口不接受跳转目标。已有有效管理员会话访问登录页直接进入工作台；页面有明确的返回应用入口。

账号、设备与备份变更须携带有效管理员 Cookie、同源 Origin、CSRF 和目标二次确认，并在锁后及写事务内重新核验授权。POST /api/admin/auth/logout 使用会话、同源 Origin 和 CSRF，撤权后仍可退出；成功清除 Cookie、撤销旧令牌并返回 /admin/login?notice=logout。后台与原应用共享会话，退出后台也结束该浏览器的应用登录。管理员角色仅由服务端命令行授予和撤销，网页不能提权。

浅深主题覆盖侧栏、页面、抽屉和弹窗，并在刷新后保留。切页和弹窗有过渡效果，遵守 prefers-reduced-motion；读失败时隐藏旧快照，重试重新读取。服务运行时长以服务器返回值为锚点，显示时分秒并逐秒更新，刷新后重新校准。320/390 宽度下登录、后台和弹窗无水平溢出；新建账号的空必填框首次打开保持正常样式，原生必填校验仍阻止空提交，交互后显示错误。

存储概况只展示数量、容量和处理状态。管理页面和读取接口不提供聊天、摘要、长期记忆、资料正文或私人文件名；列表、详情和审计不包含密码散列、硬件令牌、模型密文或数据库绝对路径，也不为读取统计创建设备运行时或读取模型凭证。

解绑保留硬件凭证和历史；撤销登记会使旧硬件凭证失效。有历史关联的撤销设备保留记录，只有符合契约的空误登记可永久删除。登记和轮换只在成功响应中返回一次凭证；响应丢失时先核对记录，再由操作者决定轮换，禁止自动重发。浏览器只在当前内存弹窗中保留凭证，复制或下载后的配发由操作者通过既有安全工具完成。

变更已提交但连接清理或审计确认未完成时可能返回 503；页面应核对实际目标状态并明确提示，不能盲目重试。借用期间设备的当前使用者与长期主人分别处理，双方历史始终隔离。容量和管理操作趋势不代表聊天量、模型调用量或费用。

手动备份遵守已有目录与间隔策略；近期备份仍有效时仅校验并明确说明未创建新副本。网页不修改目录、不下载数据库、不在线恢复。数据库快照不含独立加密主密钥，恢复时仍须部署方安全保留配对密钥。

## 验证环境与限制

2026-11-11，环境为 Windows 11、Python 3.13.14；主浏览器验收为 Edge，BROWSER_EXECUTABLE 实际为 C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe；QA 独立体验为 Chrome。后端功能基线为 511ff69b1a7ac600276cff4b6b9c01c90139c276；完整 Python 在包含固定集成候选 764b0f563ac0aacb5817aca2c72121bbdb325be0 及测试隔离修正的 QA 提交 54033ae235f8b12acb34babc14253b797daf81a7 上执行，开始和结束 HEAD 相同。

- 完整 Python：403 项，402 通过、1 跳过，退出码 0，耗时 454.657 秒。包含认证 21 项、独立体验 API 8 项、隐私哨兵 7 项、独立登录安全 8 项及其他完整回归。
- 唯一跳过项为 test_runtime_storage.RecordRetentionTests.test_directory_links_do_not_expand_cleanup_scope，原因是当前 Windows 用户没有符号链接权限。
- 浏览器共 9 种脚本均有本轮退出 0 的证据，分批执行。admin_live_ui_test、admin_console_ui_test、admin_console_live_ui_test、admin_experience_ui_test 的最终成功基线为 d9cfb8d71917f52ab8da32f1a09b0deff63005f8；admin_ui_test 为 764b0f563ac0aacb5817aca2c72121bbdb325be0；admin_recovery_ui_test、admin_polish_ui_test、web_ui_test、knowledge_ui_test 为 bb805938f68d6e28550022317fecc1342a6336b6。脚本后缀均为 .cjs。

最终浏览器基线 d9cfb8d 与完整 Python 基线 54033ae 的全部 Python 文件一致；后续差异为 README、浏览器测试和一处 CSS :user-invalid 选择器。最终四组浏览器覆盖这些前端影响，包括桌面和 390 宽度的新建账号原生校验；真实浅深主题和手机截图已目视核对。以上记录分别标明完整 Python 与分批浏览器版本。

QA 工作树的原始证据为 artifacts/validation/admin-experience-final-python.log 和 admin-experience-final-python-metadata.json。首轮失败记录 admin-experience-full-python.log 保留：当时 395 项中 393 通过、1 跳过、1 个同步资料种子与后台索引争用的测试错误；54033ae 仅隔离设备端点测试的索引启动，保留逐轮注入及无关问题不检索的断言，定点 15 项通过后完成上述 403 项复验。浏览器分批汇总位于主验收工作树 artifacts/validation/admin-polish-browsers.json，最终四组明细为 admin-polish-final-four-browsers.json；截图在 artifacts/admin-experience、artifacts/admin-ui 与 artifacts/web-tests。

Linux / Python 3.10 本轮未实跑。未启动 WSL、Docker 或虚拟机，未修改系统设置；未使用远程 CI 或推送远程分支。

未连接生产服务器、真实硬件或付费模型，未执行 exe 打包或生产部署。部署仍采用 SQLite 单 worker，具体审批、切换与配对回退步骤见 admin-console-rollout.md。
