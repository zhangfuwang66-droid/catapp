# 云端测试部署

此目录只描述第一台云服务器的**测试部署**。入口强制使用账号模式、单个 Uvicorn worker，并只监听 `127.0.0.1:8081`。公网访问必须通过 SSH 隧道；本阶段不开放公网 HTTP，也不配置反向代理和 TLS。

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

下面的命令需要 root 权限。示例使用 `/opt/cybermiao-test/app` 保存代码，`/opt/cybermiao-test/venv` 保存虚拟环境，`/var/lib/cybermiao-test` 保存数据库、日志、录制目录和自动备份。API Key 写入 `/etc/cybermiao-test/api_key`，权限为 `root:root 0600`，不会进入仓库、命令行参数或日志。

```bash
useradd --system --home /opt/cybermiao-test --shell /usr/sbin/nologin cybermiao-test
install -d -o root -g root -m 0755 /opt/cybermiao-test /opt/cybermiao-test/app /etc/cybermiao-test
install -d -o cybermiao-test -g cybermiao-test -m 0700 /var/lib/cybermiao-test
install -o root -g root -m 0600 /path/to/api_key /etc/cybermiao-test/api_key
```

## 上传和安装

在本机仓库根目录执行 `scp`。压缩包只包含服务器代码和文档；不要上传 `.env`、`settings.ini`、设备凭证、数据库或运行数据：

```powershell
tar --exclude=.git --exclude='*/.venv' --exclude='*/.env' --exclude='*/.env.*' --exclude=artifacts --exclude='*.sqlite3*' --exclude='*.secret.*' --exclude='cybermiao-*.json' --exclude=records --exclude=models --exclude=snapshots --exclude=dist --exclude='desktop/config/settings.ini' -czf $env:TEMP\cybermiao-test-source.tgz server docs README.md
scp $env:TEMP\cybermiao-test-source.tgz root@123.57.23.227:/tmp/
```

在服务器上解包并创建环境。系统需具备 Python 3.10 的 `venv` 和 `ensurepip`（Ubuntu 上通常来自 `python3.10-venv`）；若缺少，不要直接更改系统软件包，先确认安装方式：

```bash
tar -xzf /tmp/cybermiao-test-source.tgz -C /opt/cybermiao-test/app
python3.10 -m venv /opt/cybermiao-test/venv
/opt/cybermiao-test/venv/bin/python -m pip install --require-hashes -r /opt/cybermiao-test/app/server/requirements-linux-py310.lock
chown -R root:root /opt/cybermiao-test
```

本次测试服务器缺少 `ensurepip`。已在隔离目录使用 `python3.10 -m venv --without-pip /opt/cybermiao-test/venv` 创建环境，并使用已校验 SHA-256 的 pip 25.3 wheel 引导安装，没有安装系统包。需要在同版本环境重做时，可在创建空虚拟环境后执行：

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

本次 Ubuntu 22.04 x86_64 服务器缺少 `libGL.so.1`，会导致 OpenCV 和 MediaPipe 无法导入。以下操作从 Ubuntu 已配置的软件源下载三个运行库，只解包到测试目录，不安装系统包。校验值针对 Ubuntu 22.04 的 `1.4.0-1` amd64 包；如果来源、版本或架构不同，应先核对对应软件源的签名索引和哈希，不要照搬：

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

## 安全录入 API Key

在服务器 root 终端交互录入 Key；输入时不会回显，也不会进入命令历史：

```bash
umask 077
read -r -s -p 'DashScope API Key: ' key
printf '\n'
printf '%s' "$key" > /etc/cybermiao-test/api_key
chmod 0600 /etc/cybermiao-test/api_key
unset key
```

不要把 Key 放到命令参数、服务文件、Git 或聊天中。systemd 使用 `LoadCredential` 在启动时向服务交付该文件。

## 初始化独立数据库和账号

以下命令在服务启动前执行一次。`init` 只允许创建不存在的新数据库；导入 `app` 也可能创建数据库，如果数据库已存在，先使用 `inspect` 检查，切勿覆盖。密码由终端隐藏输入，不要放在 shell 历史或聊天中；设备凭证只保存到私密文件，尚未写入固件。

```bash
install -d -o cybermiao-test -g cybermiao-test -m 0700 /var/lib/cybermiao-test/provision
sudo -u cybermiao-test /opt/cybermiao-test/venv/bin/python /opt/cybermiao-test/app/server/tools/memory_database.py init --database /var/lib/cybermiao-test/memory.sqlite3
sudo -u cybermiao-test /opt/cybermiao-test/venv/bin/python /opt/cybermiao-test/app/server/tools/identity_admin.py --database /var/lib/cybermiao-test/memory.sqlite3 user-add --username owner
sudo -u cybermiao-test /opt/cybermiao-test/venv/bin/python /opt/cybermiao-test/app/server/tools/identity_admin.py --database /var/lib/cybermiao-test/memory.sqlite3 device-add --device-id cat-test-001 --name '测试小喵' --credential-output /var/lib/cybermiao-test/provision/cat-test-001.secret.json
chmod 0600 /var/lib/cybermiao-test/provision/cat-test-001.secret.json
```

凭证 JSON 必须通过受保护的 USB 配发流程写入设备；不能放进 URL、Git、普通日志或截图。当前只做服务器和模拟设备验收，不会因为这次部署自动绑定实体设备。

## 安装服务和回环验收

先把本目录的 `cybermiao-test.service` 上传到 `/etc/systemd/system/`，确认 API Key 文件已就位，再执行：

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
ssh -N -L 8081:127.0.0.1:8081 root@123.57.23.227
```

再在本机浏览器打开 `http://127.0.0.1:8081/`，使用刚创建的账号登录。网页控制台的设备列表应为空，配对测试设备后只能看到自己的设备；另一账号不能读取该设备的记忆。测试完成后关闭 SSH 窗口即可断开隧道。

## 停止、回退和清理

这些命令会停止并删除测试服务及其**独立**目录，执行前确认其中没有要保留的数据。它们不会触碰其他项目：

```bash
systemctl disable --now cybermiao-test.service
rm -f /etc/systemd/system/cybermiao-test.service
systemctl daemon-reload
rm -rf /opt/cybermiao-test /etc/cybermiao-test /var/lib/cybermiao-test
userdel cybermiao-test
```

正式部署前仍需：域名、HTTPS/WSS、异机备份、监控、真实设备凭证配发、固件 TLS 联调，以及普通 Omni/视觉接口的单独适配。此次模型验证只确认了 `qwen3.8-omni-flash-realtime` + `Tina` 的实时链路和 `qwen-plus` 摘要。
