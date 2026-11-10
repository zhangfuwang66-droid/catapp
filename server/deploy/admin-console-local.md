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

## 功能与限制

提供聚合概览、用户分页搜索/启停/撤销会话、设备状态/暂停恢复语音/解绑、资料与记忆容量、现有备份任务状态以及脱敏操作审计。所有写操作复用会话、Origin、CSRF 和二次确认，读取列表不会创建设备运行时或模型连接。管理员授权/撤销仅由 `server/admin_cli.py` 执行，不提供网页提权。

在线状态依据当前进程可核实的音频/摄像头连接；没有连接时显示离线。离线恢复语音只保存偏好，后续硬件连接可能启动模型。停用或撤销会话会暂停目标当前使用设备的语音、撤销旧连接并取消相关任务。账号变更已提交而清理失败时返回 503，明确提示重试，审计保留“未完成”。

模型费用暂不提供，容量为聚合逻辑字节数及单独数据库/WAL磁盘占用，不提供私人正文或文件名。无在线恢复、数据库下载或任意命令入口。部署目标为 SQLite 单 worker；未在生产服务器、真实硬件、真实计费模型或 Python 3.10 解释器上运行验收。桌面资源沿用现有静态资源收集和模块导入，未运行 exe 打包。

服务器部署和成对回退参见 `admin-console-rollout.md`，须先针对具体命令另行确认。
