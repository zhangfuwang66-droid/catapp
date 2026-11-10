# 云端测试部署

本目录提供 Linux x86_64 / Python 3.10 的隔离测试部署示例。入口强制使用账号模式、单个 Uvicorn worker，并只监听 `127.0.0.1:8081`。电脑通过 SSH 隧道访问；此模板不开放公网 HTTP，也不包含反向代理和 TLS 配置。

下文的 `YOUR_SERVER_IP`、账号和设备编号需替换为自己的环境。SSH 隧道只供电脑访问，实体小猫连接云端仍需另行准备受信任的 HTTPS/WSS 入口。

## 本机确认

从仓库根目录运行：

```powershell
$env:PYTHONPATH = 'server'
server/.venv/Scripts/python.exe -m unittest discover -s server/tests -p test_cloud_test_entry.py -v
```

测试会创建临时数据库，验证启动、健康检查、登录、配对、账号隔离、设备 Bearer、错误凭证拒绝、自动备份和日志脱敏。测试会调用本地依赖，但不会调用真实模型、读取设备或写入云端。

## 云端修改前提

以下操作会在云服务器新增 `cybermiao-test` 系统用户、安装 Python 依赖、上传代码、创建独立数据目录和 systemd 服务，并会消耗磁盘空间；不会重启整台服务器，也不会修改现有数据库。执行前必须确认快照/备份可用，并确认你接受测试服务只占用本机回环端口 `8081`。

锁定文件针对 Linux x86_64 和 Python 3.10。先用只读命令确认服务器系统、Python 和 systemd；不要用其他 Python 版本安装这份锁定文件：

```bash
cat /etc/os-release
python3 --version
python3.10 --version
systemctl --version
df -h /opt /var/lib
```

## 推荐目录和权限

下面的命令需要 root 权限。示例使用 `/opt/cybermiao-test/app` 保存代码，`/opt/cybermiao-test/venv` 保存虚拟环境，`/var/lib/cybermiao-test` 保存数据库、日志、录制目录和自动备份。账号模式无需共用 API Key，用户绑定后在控制台保存自己的 Key。密文进入数据库，独立主密钥默认保存为数据目录下的 `provider.key`，两者都要私密备份。

```bash
useradd --system --home /opt/cybermiao-test --shell /usr/sbin/nologin cybermiao-test
install -d -o root -g root -m 0755 /opt/cybermiao-test /opt/cybermiao-test/app
install -d -o cybermiao-test -g cybermiao-test -m 0700 /var/lib/cybermiao-test
```

## 上传和安装

在本机仓库根目录执行 `scp`。压缩包只包含服务器代码和文档；不要上传 `.env`、`settings.ini`、设备凭证、数据库或运行数据：

```powershell
tar --exclude=.git --exclude='*/.venv' --exclude='*/.env' --exclude='*/.env.*' --exclude=artifacts --exclude='*.sqlite3*' --exclude='*.secret.*' --exclude='cybermiao-*.json' --exclude=records --exclude=models --exclude=snapshots --exclude=dist --exclude='desktop/config/settings.ini' -czf $env:TEMP\cybermiao-test-source.tgz server docs README.md
scp $env:TEMP\cybermiao-test-source.tgz root@YOUR_SERVER_IP:/tmp/
```

在服务器上解包并创建环境。系统需具备 Python 3.10 的 `venv` 和 `ensurepip`（Ubuntu 上通常来自 `python3.10-venv`）；若缺少，不要直接更改系统软件包，先确认安装方式：

```bash
tar -xzf /tmp/cybermiao-test-source.tgz -C /opt/cybermiao-test/app
python3.10 -m venv /opt/cybermiao-test/venv
/opt/cybermiao-test/venv/bin/python -m pip install --require-hashes -r /opt/cybermiao-test/app/server/requirements-linux-py310.lock
chown -R root:root /opt/cybermiao-test
chmod -R go-w /opt/cybermiao-test/app
```

从 Windows 打包上传的源码可能带有过宽的文件权限。上述命令将测试代码归属 root 并移除组和其他用户的写权限；服务由独立的 `cybermiao-test` 用户运行，Python 字节码也不会写入源码目录。不要将此权限调整用于现有项目或数据目录。

若环境缺少 `ensurepip`，可以先用 `python3.10 -m venv --without-pip /opt/cybermiao-test/venv` 创建空环境，再用校验后的 pip wheel 引导安装。以下是 pip 25.3 的固定版本示例，仅用于已确认兼容的环境：

```bash
curl --fail --location --output /opt/cybermiao-test/pip-25.3-py3-none-any.whl https://files.pythonhosted.org/packages/44/3c/d717024885424591d5376220b5e836c2d5293ce2011523c9de23ff7bf068/pip-25.3-py3-none-any.whl
echo '9655943313a94722b7774661c21049070f6bbb0a1516bf02f7c8d5d9201514cd  /opt/cybermiao-test/pip-25.3-py3-none-any.whl' | sha256sum -c -
PYTHONPATH=/opt/cybermiao-test/pip-25.3-py3-none-any.whl /opt/cybermiao-test/venv/bin/python -m pip install --no-index --no-deps /opt/cybermiao-test/pip-25.3-py3-none-any.whl
/opt/cybermiao-test/venv/bin/python -m pip install --require-hashes -r /opt/cybermiao-test/app/server/requirements-linux-py310.lock
/opt/cybermiao-test/venv/bin/python -m pip check
```

仅在校验成功时继续执行安装。依赖下载需要能够访问对应 Python 包索引。

安装通过锁定文件固定全部依赖版本与哈希。`requirements-linux-py310.in` 在共享依赖之上增加 Linux/Python 3.10 的平台约束；依赖清单变更后，需要从 `server` 目录重新生成并在 Linux x86_64 / Python 3.10 目标下验证锁文件，再上传部署：

```powershell
uv pip compile --python-version 3.10 --python-platform x86_64-manylinux_2_17 --generate-hashes --only-binary=:all: --output-file requirements-linux-py310.lock requirements-linux-py310.in
```

安装完成后可删除 `/tmp/cybermiao-test-source.tgz`。OpenCV、MediaPipe 及其依赖会占用较多磁盘空间，安装前先检查实际可用空间。

### Ubuntu 22.04 的隔离图形运行库

若 Ubuntu 22.04 x86_64 环境缺少 `libGL.so.1`，OpenCV 和 MediaPipe 可能无法导入。以下示例从 Ubuntu 已配置的软件源下载三个运行库，只解包到测试目录，不安装系统包。校验值针对 Ubuntu 22.04 的 `1.4.0-1` amd64 包；如果来源、版本或架构不同，应先核对对应软件源的签名索引和哈希，不要照搬：

```bash
install -d -m 0755 /opt/cybermiao-test/native/debs /opt/cybermiao-test/native/root
cd /opt/cybermiao-test/native/debs
apt-get download libgl1=1.4.0-1 libglx0=1.4.0-1 libglvnd0=1.4.0-1
sha256sum -c <<'SHA256'
4fb2bbff94bb1a59bbdb40041e7a8ab58db2a003a151f4055357af03f0b78666  libgl1_1.4.0-1_amd64.deb
950068e8a3ad3905058d21a8c870c37989e48c0a537a7c57231c277af19f5395  libglx0_1.4.0-1_amd64.deb
6956d8be93b160b439a043141a061adfd54561d877423a956ace84d2fb3bfb4e  libglvnd0_1.4.0-1_amd64.deb
SHA256
dpkg-deb -x libgl1_1.4.0-1_amd64.deb /opt/cybermiao-test/native/root
dpkg-deb -x libglx0_1.4.0-1_amd64.deb /opt/cybermiao-test/native/root
dpkg-deb -x libglvnd0_1.4.0-1_amd64.deb /opt/cybermiao-test/native/root
sudo -u cybermiao-test env LD_LIBRARY_PATH=/opt/cybermiao-test/native/root/usr/lib/x86_64-linux-gnu MPLBACKEND=Agg MPLCONFIGDIR=/var/lib/cybermiao-test/matplotlib OPENBLAS_NUM_THREADS=1 /opt/cybermiao-test/venv/bin/python -c 'import cv2, mediapipe, numpy; print(cv2.__version__, mediapipe.__version__, numpy.__version__)'
```

仅在全部哈希校验成功后解包。服务文件通过 `LD_LIBRARY_PATH` 使用上述目录，并将 Matplotlib 缓存放在独立数据目录。该验证覆盖导入和基本图片编解码，未验证 GPU、显示窗口或实际摄像头。

## 账号独立 API Key

服务可以在没有共用 Key 的情况下启动。用户登录并绑定小猫后，在控制台「模型设置」填写自己的百炼 Key。网页只返回掩码，不把 Key 写入设备；文本、实时语音、视觉、情绪、资料索引和记忆总结都按账号获取凭证。没有 Key 的账号仍可绑定和管理已有数据，不能使用 AI 对话。

不要把 Key 放到命令参数、服务文件、Git 或聊天中。`provider.key` 遗失会导致已有密文无法解密，备份数据库时必须单独保管对应主密钥，详见[账号模型设置](../../docs/账号模型设置.md)。旧部署可能仍使用 `LoadCredential` 交付平台 Key，当前账号模式不会回落到该 Key；本模板不再要求创建该文件。

## 初始化独立数据库和账号

以下命令在服务启动前执行一次。`init` 只允许创建不存在的新数据库；导入 `app` 也可能创建数据库，如果数据库已存在，先使用 `inspect` 检查，切勿覆盖。密码由终端隐藏输入，不要放在 shell 历史或聊天中；设备凭证只保存到私密文件，尚未写入固件。

```bash
install -d -o cybermiao-test -g cybermiao-test -m 0700 /var/lib/cybermiao-test/provision
sudo -u cybermiao-test /opt/cybermiao-test/venv/bin/python /opt/cybermiao-test/app/server/tools/memory_database.py init --database /var/lib/cybermiao-test/memory.sqlite3
sudo -u cybermiao-test /opt/cybermiao-test/venv/bin/python /opt/cybermiao-test/app/server/tools/identity_admin.py --database /var/lib/cybermiao-test/memory.sqlite3 user-add --username owner
sudo -u cybermiao-test /opt/cybermiao-test/venv/bin/python /opt/cybermiao-test/app/server/tools/identity_admin.py --database /var/lib/cybermiao-test/memory.sqlite3 device-add --device-id cat-test-001 --name '测试小喵' --credential-output /var/lib/cybermiao-test/provision/cat-test-001.secret.json
chmod 0600 /var/lib/cybermiao-test/provision/cat-test-001.secret.json
```

示例 `cat-test-001` 仅供模拟验收，不能替代实体小猫的真实编号。实体接入时，先通过 [USB 工具](../../firmware/tools/README.md) 查询编号，再登记该编号并安全配发凭证；JSON 不能放进 URL、Git、普通日志或截图。服务器部署不会自动绑定实体设备。

## 安装服务和回环验收

先把本目录的 `cybermiao-test.service` 上传到 `/etc/systemd/system/`，再执行：

```bash
install -o root -g root -m 0644 /path/to/cybermiao-test.service /etc/systemd/system/cybermiao-test.service
systemctl daemon-reload
systemctl enable --now cybermiao-test.service
systemctl status --no-pager cybermiao-test.service
curl --fail --silent http://127.0.0.1:8081/api/health
curl --fail --silent http://127.0.0.1:8081/api/ready
```

日志只显示事件和错误类型，不显示 API Key、Bearer 凭证、聊天正文或摘要：

```bash
journalctl -u cybermiao-test.service --since '10 minutes ago' --no-pager
ls -la /var/lib/cybermiao-test/logs /var/lib/cybermiao-test/backups
```

## 本机 SSH 隧道

保持服务只监听回环地址，在本机 PowerShell 打开隧道：

```powershell
ssh -N -L 127.0.0.1:18081:127.0.0.1:8081 root@YOUR_SERVER_IP
```

再在本机浏览器打开 `http://127.0.0.1:18081/`，使用刚创建的账号登录。这里的 `18081` 是本机隧道端口，云端服务仍监听 `8081`。网页控制台的设备列表应为空，配对测试设备后只能看到自己的设备；另一账号不能读取该设备的记忆。测试完成后关闭 SSH 窗口即可断开隧道。

## 停止、回退和清理

这些命令会停止并删除测试服务及其**独立**目录，执行前确认其中没有要保留的数据。它们不会触碰其他项目：

```bash
systemctl disable --now cybermiao-test.service
rm -f /etc/systemd/system/cybermiao-test.service
systemctl daemon-reload
rm -rf /opt/cybermiao-test /etc/cybermiao-test /var/lib/cybermiao-test
userdel cybermiao-test
```

正式部署前仍需完成域名与 HTTPS/WSS、异机备份、监控、真实设备凭证配发、固件 TLS 联调和容量评估。模型配置、兼容性限制与真实接口探测方法见[服务器说明](../README.md)。
