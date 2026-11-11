# 公网网络边界准备

本目录的文件是供审阅和目标机验证的模板，生成器只写一个新目录。没有执行云端部署、SSH、依赖升级、证书申请、硬件或模型调用。原有 SSH 隧道方案继续见 [README](README.md)。本方案保留域名或 **IPv4 HTTPS/WSS**，后端固定为 `127.0.0.1:8081`、账号模式、一个 Uvicorn worker；不要以直接开放 8081 代替反代。

应用边界基于提交 `7315803`：校验规范 Host；关闭账号模式的 docs/redoc/OpenAPI；本地模式拒绝跨站请求并将旧 GET 写入口改为 POST。普通 HTTP 请求体上限 64 KiB、读取总时限 10 秒；唯一上传入口 `POST /api/knowledge/documents` 上限 20 MiB、读取总时限 60 秒；HTTP 并发 64，WS 并发 128。WS 文本按 UTF-8 字节计 16 KiB，音频二进制 256 KiB、相机二进制 1 MiB；Uvicorn 再限制消息 1 MiB、接收队列 8 条。

隔离故障注入确认 `7315803` 的未处理 500 响应遗漏应用安全头，已反馈主任务修复。此模板也为代理/应用错误补齐安全头，并保留上游后台严格 CSP。模板不能替代应用补丁、账号限速、凭证轮换或前端 XSS 修复。

## 文件与生成

- `render_public_edge.py`：仅使用标准库，验证 HTTPS 地址和 Linux 绝对路径，拒绝配置注入、未替换标记和覆盖已有输出目录；不读取证书或密钥内容。
- `nginx-public.conf.in`：放入专用 Nginx 的 `http {}`，不包含完整主配置。不要直接投入已有多站点实例：默认监听、map/zone 名、全局 real-IP 设置都需核对。
- `cybermiao-public.conf.in`：现有服务的 drop-in，切换到 `cloud_public_entry.py`，保留既有凭证、环境文件、数据与主密钥设置。
- `journald-cybermiao.conf`：独立日志命名空间限量；`cybermiao-edge.logrotate`：代理元数据日志轮转。

在仓库根目录生成一次可审阅的例子（文档地址不能用于实际连通性验收）：

```bash
python3 server/deploy/render_public_edge.py \
  --public-url https://198.51.100.27:8443 \
  --certificate /etc/letsencrypt/live/cat/fullchain.pem \
  --certificate-key /etc/letsencrypt/live/cat/privkey.pem \
  --output-dir ./edge-review
```

443 可以省略；非默认端口必须在网页、设备端、Origin、systemd 和 Nginx 中一致。IPv4 证书必须在 SAN 中包含该 IP，设备信任链也必须匹配；不要绕过证书校验。模板使用 TLS 1.2/1.3，TLS 站点自身是默认站点，支持 IP 客户端不发送 SNI 的情形，再通过 HTTP Host 白名单拒绝错误主机。缺少 SNI 不会被一概拒绝。浏览器 WS 使用同源 Cookie/Origin；原生设备仍用设备专属 Bearer，可以不携带 Origin。

## 代理信任和资源边界

模板假定公网直接连接这台 Nginx，没有 CDN、负载均衡或 PROXY protocol。不要继承 `real_ip_header`、宽泛的 `set_real_ip_from` 或客户端可控制的真实地址重写。Nginx **覆盖** `X-Forwarded-For` 为 TCP 对端 `$remote_addr`，固定 `Host`、`X-Forwarded-Proto=https`，移除 `Forwarded`、`X-Forwarded-Host/Port/Prefix`；Uvicorn 只信任 `127.0.0.1` 的代理头。不能使用 `$proxy_add_x_forwarded_for`。若有额外代理层，先重新设计并验证可信链，不能直接套用。

默认体积 64k，上传 exact location 才允许 POST/20m。`proxy_request_buffering off` 让应用从第一批数据开始执行总时限，避免代理先无限等待小块慢上传；Nginx 的 `client_body_timeout` 是两次读取间的空闲时限，并不是上传总时限。上传不自动重试，HTTP/WS 都不自动重试写请求。流式音频和 WS 禁用响应缓冲；300 秒代理读取空闲时限需与设备心跳匹配。

Nginx 大头缓冲为 `4 × 8k`，单个请求行/单头还受 8k 限制，可能比应用更早拒绝；应用另限总头字节 32 KiB、100 个头、URL 8 KiB。边缘 HTTP 全局并发 64 和 WS 全局并发 128 使用独立 zone，上传每 IP 2 个另用独立 zone；**每 IP 默认 96 个活跃请求/WS**，供同一 NAT 下设备与网页共享。20 台设备仅音频和相机就需要 40 条 WS，此默认值另留网页连接/轮询预算；它不承诺 10～20 人的实际吞吐，仍需按模型、网络和主机容量测量。`limit_conn` 不计尚未读完头的连接，所以仍需主配置的 `worker_connections`、内核连接队列、文件句柄和外部容量控制。

登录端点每 IP 5 次/分钟、突发 5，一般请求 20 次/秒、突发 40，WS **握手** 5 次/秒、突发 10。它们是起始配置，需要按真实 NAT/设备重连模式调优，不能代替账号/设备维度限速。WS 升级后消息限额由应用和 Uvicorn 执行。返回 429/503 的元数据可以用于观察限速，不记录消息或正文。这些措施只减少应用滥用，不能保证抵御链路耗尽或分布式拒绝服务。

## 证书续期

沿用现有证书颁发方式、账户和定时任务；生成器不会重建这些资源。稳定引用证书管理器提供的 `fullchain.pem` / `privkey.pem` 路径，包含续期使用的符号链接；确认 root Nginx master 能读私钥，普通用户不能读。不要把证书私钥放入代码、聊天、命令参数值或访问日志。

若现有续期方式是 HTTP-01 webroot，可在生成时增加：

```bash
  --acme-webroot /var/lib/cybermiao-acme
```

这会仅在 HTTP 80 的 `/.well-known/acme-challenge/` 下读取该目录的验证文件，限制 GET/HEAD、关闭目录列表，其他路径固定重定向到规范 HTTPS 地址。webroot 必须与现有续期任务一致、可被 Nginx worker 遍历读取，且不能混入数据库或任何私密文件。公网 80 需按验证方要求可达，即使产品 HTTPS 使用 8443；反代 HTTPS 下的点目录仍被拒绝。已有 DNS/API 等续期方式无需开放验证目录，80 可以保持关闭。不要把重定向或 Nginx 测试通过当成证书续期已完成。

证书管理器的成功部署钩子应先运行 `/usr/sbin/nginx -t`，成功后才 `systemctl reload nginx`。这两条命令由现有证书管理器或管理员在目标机执行；不会重启应用。使用独立 Nginx 配置路径/服务名时同步替换钩子和 logrotate 的 reopen 命令。失败时保留当前已加载证书，并在证书到期前处理；应单独监测实际证书到期时间、续期任务状态、IP SAN 和完整信任链。

## systemd 与日志

目标需先核对 Linux x86_64/Python 3.10 锁定环境，systemd 至少 247，以及 Nginx TLS/map/limit_conn/limit_req 模块。drop-in 清空 capabilities、限制地址族，隔离设备、内核、主目录和临时目录，代码保持只读，数据目录可写。没有启用未验证的系统调用白名单或 `MemoryDenyWriteExecute`，避免破坏 NumPy/OpenCV/MediaPipe。`TasksMax=256`、句柄 4096 也要在目标机负载下核对；内存/CPU 示例默认注释，未据此宣称容量。

检查既有配置的文件路径与权限，尤其 `CYBERMIAO_SECRET_KEY_FILE`/`LoadCredential`/`EnvironmentFile`。不要创建新 `provider.key` 替换既有密钥，不要重置这些设置；自定义密钥路径必须继续可读，数据、缓存、备份、录制目录必须在允许写入的路径中。不要把 `systemctl show Environment` 的内容贴到日志或聊天。

代理访问日志只含时间、对端 IP、固定路由类别、状态、字节数和耗时；没有原始 URI、查询、Cookie、Authorization、Referer 或正文。IP 仍属于访问元数据，限制读取权限和保留期。Nginx **错误日志不受 log_format 脱敏约束**，可能写请求行，所以模板站点设置 `error_log /dev/null crit`。专用主配置也应在 main/http 上核对并采用相同策略，避免请求在选中站点前进入其他错误日志；不要在隐私验收时开启 debug。若运行上必须恢复请求错误日志，需要明确其访问权限、保留期和可能含查询内容的事实，不能再宣称“全部日志均无请求内容”。

访问日志创建为 root:adm 0640、目录 0750，由 logrotate 每日/超过 10 MiB 时保留 7 份，压缩旧文件。`maxsize` 只在 logrotate 被调度时检查，并非实时磁盘硬上限；高流量需缩短调度间隔、设置磁盘告警/配额。先用 `logrotate --debug` 检查路径、adm 组和 reopen 命令。

应用继续使用私有脱敏滚动日志（单份 2 MiB，含当前文件最多 6 份，文件 0600）。systemd 命名空间 `cybermiao` 单独限制持久日志 64 MiB、运行时 32 MiB、保留 7 天并关闭转发；系统保留空间可能使日志更早轮转。通过 `journalctl --namespace=cybermiao -u cybermiao-test.service` 查看，保留受限的 root/日志管理员权限。不要为了本服务修改全机 journald 限量；不支持命名空间的目标应另行审阅兼容方案。

## 目标机检查与切换顺序

以下是部署时供管理员执行的步骤，本任务没有执行。先确认现有配置/数据库及其对应密钥有可恢复备份；本次切换只备份和替换配置，不迁移数据库。

1. 确认服务当前启动入口、User/Group、主配置与 drop-in 路径、监听端口，以及实际管理 SSH 端口/CIDR。用 `systemctl show -p ExecStart -p User -p Group -p FragmentPath -p DropInPaths cybermiao-test.service` 查看不含环境密钥的属性。保留第二条管理连接。
2. 把当前 Nginx 配置、服务主文件和现有 drop-in、日志轮转/命名空间配置备份到 root 私有目录，记录当时启用的规则和服务状态。记录哪些目标路径原先不存在，便于仅撤回本次新增文件。
3. 将生成文件和真实证书路径逐项审阅。Nginx 放入已有主配置的 `http {}` include 目录，确认没有重复默认站点或同名 zone。logrotate 文件放 `/etc/logrotate.d/cybermiao-edge`；服务 drop-in 放 `/etc/systemd/system/cybermiao-test.service.d/30-public-edge.conf`；日志命名空间配置放 `/etc/systemd/journald@cybermiao.conf.d/30-limits.conf`。创建代理日志目录/文件权限：

```bash
install -d -o root -g adm -m 0750 /var/log/nginx
touch /var/log/nginx/cybermiao-access.log
chown root:adm /var/log/nginx/cybermiao-access.log
chmod 0640 /var/log/nginx/cybermiao-access.log
nginx -t
systemd-analyze verify /etc/systemd/system/cybermiao-test.service
logrotate --debug /etc/logrotate.d/cybermiao-edge
```

4. 仅在目标机检查通过、回滚文件就绪后，执行 `systemctl daemon-reload`，启动/重启对应 `systemd-journald@cybermiao.service` 以读取命名空间配置，再重启 `cybermiao-test.service`。已有 WS 和音频流会中断；需安排维护窗口。检查回环健康和就绪：

```bash
curl --fail --silent http://127.0.0.1:8081/api/health
curl --fail --silent http://127.0.0.1:8081/api/ready
ss -ltnp
```

5. 确认后端仅监听 127.0.0.1，按下一节保留管理口并限制入口，再执行 `systemctl reload nginx`。外部使用真实证书验证 HTTPS/WSS，不使用 `curl -k`、浏览器忽略证书错误或固件禁用验证。检查规范 Host 成功、错误 Host 被拒绝、docs/OpenAPI/旧匿名接口不可用、无凭证设备 WS 被拒绝、有效模拟设备 Bearer 可连接、浏览器同源登录和 CSRF、错误响应安全头及后台严格 CSP。
6. 核对真实证书续期任务及部署钩子，必要时按证书管理器的官方方式做续期 dry-run。IP 证书是否支持该方式和验证端口由实际颁发方决定。本地自签名验证不覆盖这些事项。

## 防火墙：保留管理通道再收紧

先只读查看云安全组、`ufw status numbered`、`nft list ruleset` 及 IPv6 状态。沿用正在管理规则的工具，不同时混用 UFW、裸 nft 和 iptables，不 flush 或重建整机规则。下面仅适用于**已由 UFW 管理**且已替换实际参数的例子；不自动启用尚未启用的防火墙：

```bash
# 示例 CIDR 是文档保留地址，必须换成真实管理出口；SSH_PORT 也应核实。
ADMIN_CIDR=203.0.113.5/32
SSH_PORT=22
HTTPS_PORT=8443
ufw allow from "$ADMIN_CIDR" to any port "$SSH_PORT" proto tcp comment 'existing management access'
ufw allow "$HTTPS_PORT"/tcp comment 'CyberMiao HTTPS WSS'
# 仅 HTTP-01 续期需要时开放 80；否则可保持关闭。
# ufw allow 80/tcp comment 'certificate HTTP validation'
ufw deny 8081/tcp comment 'backend loopback only'
ufw status numbered
```

先用第二条连接验证管理规则，再按既有策略审阅默认入站拒绝；不要直接运行 `ufw --force enable`。已有更前的 8081 allow 规则可能覆盖 deny，需要在保留现有管理和其他服务的前提下精确修改。云安全组同样只放行实际 HTTPS/WSS 端口、必要的管理 CIDR/端口及续期端口；8081 不对外开放。

同时核对 UFW 的 IPv6 管理和云 IPv6 规则。模板包含 IPv6 listen，但只接受同一规范域名/IP Host，不代表可以留下宽松 IPv6 入站；没有使用 IPv6 的部署可审阅后移除对应 listen，不能通过关闭所有防火墙来排障。应用需要出站模型 HTTPS/DNS 等能力，本模板没有盲目加入出站全拒绝。

## 配置回滚

先恢复备份的 Nginx 配置并运行 `nginx -t`，恢复原服务/drop-in、日志配置和本次确实改过的防火墙规则，随后 daemon-reload、按原方案重启服务、reload Nginx。原先不存在的文件只删除这次新增的确切路径；不能删除整个 drop-in 目录、数据目录、数据库、主密钥或证书续期账户。

如果退回 `cloud_test_entry.py`，同时撤下公网代理入口，回到 SSH 隧道方式验收；它没有公网入口的代理/站点设置。不要让公网仍指向退回的隧道入口。回环健康失败时先保留外部拒绝和管理连接，通过受限日志诊断。HSTS 设置为 86400 秒、不启用 preload/includeSubDomains；已访问浏览器的 HSTS 会在配置回滚后继续生效到过期，回滚不能立即清除。

## 本地验证范围

独立真实应用回归在 `server/tests/test_network_boundary_review.py`：临时数据库和模拟运行时，覆盖本地跨站/Host、旧 GET 不写、IP 加非默认端口、代理头伪装、上传流式超限/授权先于读取、原生 Bearer 与浏览器 Origin、UTF-8 WS 容量。不会调用真实模型和硬件。

`server/tests/test_public_edge_templates.py` 的默认测试只验证生成器；可选 Linux 检查需要显式提供 `CYBERMIAO_REVIEW_NGINX`，使用本地 OpenSSL 生成临时 IP SAN 证书、严格校验 TLS，启动临时代理和 127.0.0.1:8081 模拟后端。它仅临时改写监听/日志/临时文件路径，验证 Nginx 语法、转发头、体积、WS Upgrade、CSP/错误头、查询与凭证日志哨兵以及 HTTP 验证目录。运行前确认本机 8081 空闲；不停止已有服务。

```bash
python3 -m unittest discover -s server/tests -p test_public_edge_templates.py -v
CYBERMIAO_REVIEW_NGINX=/path/to/local/nginx \
  python3 -m unittest discover -s server/tests -p test_public_edge_templates.py -v
```

2026-11-11 已执行的有限验证：共享 Python 3.13 venv 的真实应用回归 5 项通过（2.904 秒）；WSL Ubuntu 26.04 / Python 3.14 / Nginx 1.28.3 的生成器和代理测试 10 项通过（1.236 秒，含 `nginx -t` 和每 IP 默认 96 的回归）。使用已注册的 Ubuntu 发行版，通过 `wsl -d Ubuntu --exec` 执行命令；调用会按需启动/唤起该发行版，未先记录每次调用前的运行状态，因此不宣称“未启动 WSL”。没有新装发行版。

WSL 原有 Python、OpenSSL、systemd-analyze、logrotate 可用，**原先没有 Nginx**。执行 `apt-get download nginx`，下载 Ubuntu 已配置仓库的 `nginx_1.28.3-2ubuntu1_amd64.deb`，再 `dpkg-deb -x` 解包到本地临时/仓库忽略目录；没有 `apt install`、`dpkg -i`、系统配置更改、系统服务启用/启动或服务器操作。代理测试确实启动了**本地临时 Nginx 进程**（回环随机 HTTP/TLS 端口）和 `127.0.0.1:8081` 模拟后端，测试结束退出；临时自签名证书由现有 OpenSSL 生成。Nginx 不是目标机已安装实例。

最终代理测试实际执行命令如下（Windows PowerShell 发起；不表示部署命令）：

```powershell
wsl -d Ubuntu --exec env CYBERMIAO_REVIEW_NGINX=/mnt/c/Users/20388/.codex/worktrees/admin-security/catapp/artifacts/nginx-review/root/usr/sbin/nginx python3 -m unittest discover -s /mnt/c/Users/20388/.codex/worktrees/admin-security/catapp/server/tests -p test_public_edge_templates.py -v
```

systemd 259 对“原服务加生成 drop-in”的临时合并副本执行 `systemd-analyze verify --man=no /tmp/.../cybermiao-test.service`，退出码 0、无诊断；仅将本地不存在的 `/opt/cybermiao-test/venv/bin/python` 替成 `/usr/bin/python3` 以检查语法，没有启动应用服务。`logrotate --debug --state /tmp/.../state /tmp/.../config` 也通过，未实际轮转或执行 reopen 钩子。没有重跑全套应用测试；Linux 中没有运行 Python 3.10 应用或安装其依赖锁。

目标机 Python 3.10/依赖锁与这些本地版本不同；目标机版本、既有配置与路径、证书链和续期、云防火墙、真实设备 TLS/心跳、CPU/内存/磁盘容量仍需部署时验证。本地检查通过不代表这些目标条件已满足。公开包版本的有限公告核对及 Python 3.10/Pillow 限制见[依赖公告记录](dependency-advisories.md)。
