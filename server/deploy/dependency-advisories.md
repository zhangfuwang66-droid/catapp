# 有限公开依赖公告核对（2026-11-11）

只向官方 OSV/PyPI 发送公开包名和版本，没有上传用户数据、代码、配置、凭证或运行日志。没有安装/升级依赖，也没有改声明和锁文件。本记录是本轮网络边界工作的有限补充，后续依赖修正由主任务处理。

## 当前 Linux 锁与共享 venv

以下版本分别取自 `requirements-linux-py310.lock` 和本机共享 venv 的包元数据；它们不是目标服务器安装版本的证明。本次 OSV 精确版本查询没有返回匹配的项目为：

- FastAPI：锁/venv 均 0.162.0；Starlette：锁 1.7.0、venv 1.10.0。
- Uvicorn：锁 0.48.0、venv 0.54.0；python-multipart：锁 0.0.32、venv 0.0.33。
- aiohttp：锁/venv 均 3.14.5；pypdf：锁/venv 均 6.21.4；h11：锁/venv 均 0.16.0。
- httptools：锁 0.6.4、venv 0.9.0；websockets：锁 16.1.1、venv 17.4。

“未返回匹配”只描述此时该数据库的结果，不代表无漏洞、全部传递依赖已审计，或部署环境已验证。这里的本地应用测试使用共享 venv，不能证明较旧 Linux 锁在 Python 3.10 的运行结果。

**确认锁中的 Pillow 12.2.0 命中公开公告**：例如 [GHSA-24rc-8hj2-v98c / CVE-2026-96638](https://osv.dev/vulnerability/GHSA-24rc-8hj2-v98c)，BMP RLE 解码内存放大拒绝服务，公告受影响范围 `>=9.1.0,<13.0.0`，修复 `13.0.0`。该锁版本还存在其他 OSV 匹配，本记录不扩展为完整清单。当前共享 venv Pillow 13.0.0 没有返回匹配，不能用 venv 结果替代锁的结论。

PyPI 的 [12.2.0 元数据](https://pypi.org/pypi/pillow/12.2.0/json)声明 Python `>=3.10`，[13.0.0 元数据](https://pypi.org/pypi/pillow/13.0.0/json)声明 `>=3.11`。因此不能直接把 13 写入现有 Python 3.10 锁；需独立评估受支持运行时、可用安全修订或其他兼容方案。`device_runtime.py` 存在未限定格式的 `Image.open(BytesIO(...))`，本轮没有恶意图片复现或证实具体网络调用链可利用，不把包版本命中等同于已复现攻击。

## 声明允许的旧版本

普通 `requirements.txt` 的宽下限允许选择以下旧版本。默认解析器通常会尝试较新版本，但约束、缓存和既有环境仍可能留下旧版；安装成功本身不代表下限安全。只列代表性、已核对范围的公告：

- `fastapi>=0.109.0`：0.109.0 命中 [PYSEC-2024-38 / CVE-2024-24762](https://osv.dev/vulnerability/PYSEC-2024-38)，范围 `<0.109.1`，修复 0.109.1。
- `starlette>=0.35.0`：0.35.0 命中 [GHSA-f96h-pmfr-66vw / CVE-2024-47874](https://osv.dev/vulnerability/GHSA-f96h-pmfr-66vw)，multipart 拒绝服务，范围 `<0.40.0`，修复 0.40.0。
- `python-multipart>=0.0.6`：0.0.6 命中 [GHSA-pp6c-gr5w-3c5g / CVE-2026-42561](https://osv.dev/vulnerability/GHSA-pp6c-gr5w-3c5g)，无界 multipart part headers 拒绝服务，范围 `<0.0.27`，修复 0.0.27。
- `aiohttp>=3.9.0`：3.9.0 命中 [GHSA-5m98-qgg9-wh84 / CVE-2024-30251](https://osv.dev/vulnerability/GHSA-5m98-qgg9-wh84)，畸形 POST 解析拒绝服务，范围 `<3.9.4`，修复 3.9.4。
- `pypdf>=6.1.0,<7`：6.1.0 命中 [GHSA-4f6g-68pf-7vhv / CVE-2026-22691](https://osv.dev/vulnerability/GHSA-4f6g-68pf-7vhv)，畸形 startxref 导致长时间运行，范围 `<6.6.0`，修复 6.6.0。

这些补丁版本只针对列出的单个公告，**不是覆盖全部已知公告的推荐下限**。还有其他旧版本匹配，本轮没有展开。当前项目通过原始字节流接收文档，并在独立受限进程解析 PDF/DOCX；没有因此认定上列 multipart/aiohttp 服务端路径在当前项目均可触发，也没有把进程限额视为依赖修复。

## 来源和局限

使用 [OSV querybatch API](https://api.osv.dev/v1/querybatch) 按 PyPI 包名/版本查询，并读取命中公告的 `/v1/vulns/{id}` 记录，核对 aliases、ECOSYSTEM ranges、withdrawn 与上游公告引用。上列引用记录没有 withdrawn 标记。部分 GHSA/PYSEC 记录是同一 CVE 的别名，不能直接相加作为漏洞数量。

查询批次成功；两个公告详情请求出现 TLS EOF（`GHSA-2jv5-9r88-3w3p`、`GHSA-8qpw-xqxj-h4r2`），未重试扩展审计，也没有将读取失败视为无漏洞。FastAPI 示例来自成功获取的 PYSEC 别名记录。版本和公告在后续修订/部署前需要重新核对，本记录不代表全量供应链、操作系统库、Nginx/OpenSSL 或目标机安全扫描。
