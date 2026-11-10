# 管理后台现网部署与成对回退操作稿

状态：待本地验收、隔离副本迁移和用户逐项批准；本文命令尚未在服务器执行。

目标是 `123.57.23.227` 的 `cybermiao-test.service`。现有代码入口 `/opt/cybermiao-test/app` 指向 `/opt/cybermiao-test/upgrades/accounts-v2-6ad995182c54/source`，数据库为 `/var/lib/cybermiao-test/memory.sqlite3`。以下命令仅供批准后由操作者在服务器执行。部署包须来自经过验收的固定 Git commit，不能从未提交的工作树打包。小猫 `cat-649020AD4F7C` 的语音保持暂停。

## 变更前审批材料

- 原因：上线显式授权的 `/admin` 和 `/api/admin/*`，需要管理角色与审计的新数据库结构。
- 影响：短暂停服；会话可能需重新登录；新增管理表不改变原身份版本，但回退仍须配套部署前数据库，避免新版本业务写入留给旧代码；备份和副本验证占用磁盘。不会自动将 `owner` 授予管理员，不改变设备 Wi-Fi/凭证或语音暂停状态。
- 执行范围：固定代码包、独立数据库备份、独立主密钥备份、离线副本迁移、原子代码入口切换、服务重启与只读验收。每个真实服务器修改步骤须先把实际命令、备份位置、预计停服窗口交用户确认。

## 只读盘点和冻结

先确认现状、磁盘、Python 版本和当前代码入口。服务配置可能保留旧 Key；只读取下列固定字段，不直接打印 unit、Environment、EnvironmentFiles、ExecStart 或日志。主密钥位置从私密运维记录核对；若配置使用 `CYBERMIAO_SECRET_KEY_FILE`，将下文 `PROVIDER_KEY_FILE` 改为经核实的实际路径，不输出其他环境变量或密钥内容。

```bash
readlink -f /opt/cybermiao-test/app
systemctl show cybermiao-test.service --property=Id,ActiveState,SubState,MainPID,User,Group,WorkingDirectory,FragmentPath,DropInPaths
python3.10 --version
df -h /opt /var/lib/cybermiao-test
sudo -u cybermiao-test /opt/cybermiao-test/venv/bin/python /opt/cybermiao-test/app/server/tools/memory_database.py inspect --database /var/lib/cybermiao-test/memory.sqlite3
sudo -u cybermiao-test /opt/cybermiao-test/venv/bin/python /opt/cybermiao-test/app/server/tools/memory_database.py backup-status --database /var/lib/cybermiao-test/memory.sqlite3 --directory /var/lib/cybermiao-test/backups
```

记录 inspect 的 `schema_versions`、各表行数和备份状态，不保存正文。新 admin 表必须纳入 `database_schema.py` 的版本/列校验与 `memory_database.py` 的 inspect、backup、restore、backup-status；否则停止部署。`restore` 现有实现只恢复到**不存在的新路径**，不会原地覆盖。自动备份的 JSON receipt 仅适用于它自己的同源文件；手工备份另存 SHA-256。

## 批准后的备份与隔离演练

以下以 `RELEASE_ID` 代表已验收 commit 的短哈希，`SNAP_DIR` 为服务账号的私有数据库快照目录，`KEY_SNAP_DIR` 为 root 专属的配对主密钥与独立哈希目录。操作者先设置成实际值并核对绝对路径；磁盘空间需容纳旧库、备份、副本和新库。密钥备份须单独离线保管，不能放 Git、部署包、命令参数或日志。所有修改命令须在已批准的同一个 Bash 会话中运行，先启用失败即停，任何校验失败均不得继续切换或启动服务。

```bash
set -euo pipefail
RELEASE_ID=REPLACE_WITH_VERIFIED_COMMIT
SNAP_DIR=/var/lib/cybermiao-test/upgrade-snapshots/$RELEASE_ID
KEY_SNAP_DIR=/root/cybermiao-upgrade-keys/$RELEASE_ID
PROVIDER_KEY_FILE=/var/lib/cybermiao-test/provider.key
test -f "$PROVIDER_KEY_FILE"
test ! -L "$PROVIDER_KEY_FILE"
install -d -o cybermiao-test -g cybermiao-test -m 0700 /var/lib/cybermiao-test/upgrade-snapshots "$SNAP_DIR"
install -d -o root -g root -m 0700 /root/cybermiao-upgrade-keys "$KEY_SNAP_DIR"
sudo -u cybermiao-test /opt/cybermiao-test/venv/bin/python /opt/cybermiao-test/app/server/tools/memory_database.py backup --database /var/lib/cybermiao-test/memory.sqlite3 --output "$SNAP_DIR/pre-admin.sqlite3"
sha256sum "$SNAP_DIR/pre-admin.sqlite3" > "$KEY_SNAP_DIR/pre-admin.sha256"
chmod 0600 "$KEY_SNAP_DIR/pre-admin.sha256"
test ! -e "$KEY_SNAP_DIR/trial-provider.key"
install -o root -g root -m 0600 "$PROVIDER_KEY_FILE" "$KEY_SNAP_DIR/trial-provider.key"
cmp --silent "$PROVIDER_KEY_FILE" "$KEY_SNAP_DIR/trial-provider.key"
```

`pre-admin.sqlite3` 与 `trial-provider.key` 只用于隔离演练。它们不能替代下文停服点的 `cutover.sqlite3` 与 `cutover-provider.key`。备份数据库和主密钥需分别校验恢复能力；`cmp --silent` 仅检查副本一致，不输出密钥内容，`sha256sum` 清单只包含数据库。需要先将新代码解包到 `/opt/cybermiao-test/upgrades/$RELEASE_ID/source`，使用现有 Python 3.10 虚拟环境和锁定依赖；若锁文件变化，单独审核安装与兼容性。代码目录归 root、服务用户只读，数据目录由服务用户可写。

在不连接生产服务的副本上做迁移。**不得用新代码直接打开生产数据库做试迁移。**

```bash
install -o cybermiao-test -g cybermiao-test -m 0600 "$SNAP_DIR/pre-admin.sqlite3" "$SNAP_DIR/migration-trial.sqlite3"
sudo -u cybermiao-test /opt/cybermiao-test/venv/bin/python -c 'import sys; sys.path.insert(0, sys.argv[1]); from identity_store import IdentityStore; from admin_store import AdminStore; AdminStore(IdentityStore(sys.argv[2]))' "/opt/cybermiao-test/upgrades/$RELEASE_ID/source/server" "$SNAP_DIR/migration-trial.sqlite3"
sudo -u cybermiao-test /opt/cybermiao-test/venv/bin/python /opt/cybermiao-test/upgrades/$RELEASE_ID/source/server/tools/memory_database.py inspect --database "$SNAP_DIR/migration-trial.sqlite3"
```

上述命令只加载身份及管理员数据模块，不加载运行时或模型配置。比对迁移前后账号、设备、绑定、借用、资料、记忆、Key 密文的行数与可用性；在隔离副本测试显式管理员授予/撤销。旧代码可能忽略附加 admin 表，所以不能把旧 inspect 通过当成回退兼容证明。

## 切换与只读验收

切换前再生成停服点备份，包含隔离演练期间新增的全部已提交数据。停止服务并确认 inactive 后，用旧代码的 `memory_database.py backup` 生成 `cutover.sqlite3`，立即把该时点的主密钥保存为 `cutover-provider.key`，独立哈希保存到 root 专属目录。停服期间冻结其他 CLI/运维写入。以下三份文件组成默认回退包，缺一项或校验失败均停止发布。实际切换命令需要在审批时填写固定旧入口、固定新入口、最终备份路径及哈希。不可把 `app` 符号链接指回旧代码同时保留已迁移数据库。

```bash
systemctl stop cybermiao-test.service
test "$(systemctl show cybermiao-test.service --property=ActiveState --value)" = inactive
sudo -u cybermiao-test /opt/cybermiao-test/venv/bin/python /opt/cybermiao-test/app/server/tools/memory_database.py backup --database /var/lib/cybermiao-test/memory.sqlite3 --output "$SNAP_DIR/cutover.sqlite3"
sha256sum "$SNAP_DIR/cutover.sqlite3" > "$KEY_SNAP_DIR/cutover.sha256"
chmod 0600 "$KEY_SNAP_DIR/cutover.sha256"
test ! -e "$KEY_SNAP_DIR/cutover-provider.key"
install -o root -g root -m 0600 "$PROVIDER_KEY_FILE" "$KEY_SNAP_DIR/cutover-provider.key"
cmp --silent "$PROVIDER_KEY_FILE" "$KEY_SNAP_DIR/cutover-provider.key"
sha256sum -c "$KEY_SNAP_DIR/cutover.sha256"
sudo -u cybermiao-test /opt/cybermiao-test/venv/bin/python -c 'import sys; sys.path.insert(0, sys.argv[1]); from identity_store import IdentityStore; from admin_store import AdminStore; AdminStore(IdentityStore(sys.argv[2]))' "/opt/cybermiao-test/upgrades/$RELEASE_ID/source/server" /var/lib/cybermiao-test/memory.sqlite3
sudo -u cybermiao-test /opt/cybermiao-test/venv/bin/python "/opt/cybermiao-test/upgrades/$RELEASE_ID/source/server/tools/memory_database.py" inspect --database /var/lib/cybermiao-test/memory.sqlite3
test ! -e /opt/cybermiao-test/app.admin-next
ln -s "/opt/cybermiao-test/upgrades/$RELEASE_ID/source" /opt/cybermiao-test/app.admin-next
mv -Tf /opt/cybermiao-test/app.admin-next /opt/cybermiao-test/app
systemctl start cybermiao-test.service
curl --fail --silent http://127.0.0.1:8081/api/health
curl --fail --silent http://127.0.0.1:8081/api/ready
```

管理员授权是**独立批准步骤**，仅对明确指定的真实账号执行：

```bash
sudo -u cybermiao-test /opt/cybermiao-test/venv/bin/python /opt/cybermiao-test/app/server/admin_cli.py --database /var/lib/cybermiao-test/memory.sqlite3 grant --username REPLACE_WITH_APPROVED_USERNAME
```

用浏览器会话验证：未登录 `/admin` 303 跳登录、管理 API 401，普通用户与设备 Bearer 无权读取，已授权管理员可见聚合数据；写操作先只检查缺 Origin/CSRF 被拒绝，不在生产数据上执行停用/解绑/恢复语音。检查 `cat-649020AD4F7C` 仍暂停、绑定与资料仍在。审计与日志不得出现 Key、密码、令牌或正文。

## 故障回退

先停服并保留失败现场的数据库、WAL/SHM、当前主密钥和日志到 root 私有目录。默认用**旧版代码加停服点 `cutover.sqlite3`、独立 `cutover.sha256` 和配对的 `cutover-provider.key`**成对回退。这保留演练期间直到切换停服点的全部已提交数据；演练备份不进入回退流程。恢复会撤销备份内会话和未使用配对码；新版本上线后发生的写入保存在失败现场中，不会自动合入旧库，必须另行核对和制定数据转移方案，不能用更旧的演练备份兜底。

```bash
systemctl stop cybermiao-test.service
test "$(systemctl show cybermiao-test.service --property=ActiveState --value)" = inactive
test -f "$KEY_SNAP_DIR/cutover-provider.key"
test ! -L "$KEY_SNAP_DIR/cutover-provider.key"
sha256sum -c "$KEY_SNAP_DIR/cutover.sha256"
CUTOVER_SHA256=$(python3.10 -c 'from pathlib import Path; import re,sys; s=Path(sys.argv[1]).read_text().splitlines(); assert len(s)==1; value=s[0].split()[0]; assert re.fullmatch("[0-9a-f]{64}",value); print(value)' "$KEY_SNAP_DIR/cutover.sha256")
sudo -u cybermiao-test /opt/cybermiao-test/venv/bin/python /opt/cybermiao-test/upgrades/accounts-v2-6ad995182c54/source/server/tools/memory_database.py restore --backup "$SNAP_DIR/cutover.sqlite3" --output "$SNAP_DIR/restored-old.sqlite3" --sha256 "$CUTOVER_SHA256"
sudo -u cybermiao-test /opt/cybermiao-test/venv/bin/python /opt/cybermiao-test/upgrades/accounts-v2-6ad995182c54/source/server/tools/memory_database.py restore --backup "$SNAP_DIR/cutover.sqlite3" --output "$SNAP_DIR/restored-old.sqlite3" --sha256 "$CUTOVER_SHA256" --apply --confirm-service-stopped
sudo -u cybermiao-test /opt/cybermiao-test/venv/bin/python /opt/cybermiao-test/upgrades/accounts-v2-6ad995182c54/source/server/tools/memory_database.py inspect --database "$SNAP_DIR/restored-old.sqlite3"
install -d -o root -g root -m 0700 "$SNAP_DIR/failed-live"
test ! -e "$KEY_SNAP_DIR/failed-live-provider.key"
install -o root -g root -m 0600 "$PROVIDER_KEY_FILE" "$KEY_SNAP_DIR/failed-live-provider.key"
for suffix in '' -wal -shm -journal; do
  if test -e "/var/lib/cybermiao-test/memory.sqlite3$suffix"; then
    mv "/var/lib/cybermiao-test/memory.sqlite3$suffix" "$SNAP_DIR/failed-live/"
  fi
done
install -o cybermiao-test -g cybermiao-test -m 0600 "$SNAP_DIR/restored-old.sqlite3" /var/lib/cybermiao-test/memory.sqlite3
install -o cybermiao-test -g cybermiao-test -m 0600 "$KEY_SNAP_DIR/cutover-provider.key" "$PROVIDER_KEY_FILE"
cmp --silent "$PROVIDER_KEY_FILE" "$KEY_SNAP_DIR/cutover-provider.key"
test ! -e /opt/cybermiao-test/app.admin-rollback
ln -s /opt/cybermiao-test/upgrades/accounts-v2-6ad995182c54/source /opt/cybermiao-test/app.admin-rollback
mv -Tf /opt/cybermiao-test/app.admin-rollback /opt/cybermiao-test/app
systemctl start cybermiao-test.service
curl --fail --silent http://127.0.0.1:8081/api/health
curl --fail --silent http://127.0.0.1:8081/api/ready
```

所有命令须在固定 RELEASE_ID、实际密钥位置、停服点备份及 SHA-256、预计停服窗口明确后逐项批准。若缺少匹配的 cutover 回退包，保留现场并停止；不得自动改用 `pre-admin.sqlite3` 或其他更早备份。不要从自动备份目录随意挑最新文件回退。回退后再次核对语音暂停、绑定、资料、Key 可用性和普通用户权限。
