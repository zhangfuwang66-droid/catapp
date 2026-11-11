# 当前视觉图片调用点的 Pillow 缓解

`device_runtime.prepare_ai_vision_image_b64` 接收设备协议规定的 JPEG 帧。现在只通过
`Image.open(..., formats=['JPEG'])` 解析 JPEG，不自动识别 BMP、PNG 等其他格式。
在 `convert` 触发像素加载前，检查宽高均为正且像素总量不超过
`4096 * 4096`（16,777,216）；这是面积上限，不是单边尺寸上限。
正常图片仍转为 RGB、保持比例缩略至 320×320 内，再以 JPEG quality=60、optimize=True 编码。
损坏、非 JPEG、超限或转换/编码失败时返回空字符串，调用方跳过该图片，
不再将失败的原始字节回退发送给模型。

Pillow 可选依赖缺失时，仅允许具有 JPEG 起始 `FF D8 FF` 和结束 `FF D9` 标记的
原始帧继续使用原有 Base64 路径；不加载其他解码器。标记检查不能证明完整内容合法，
该分支不执行像素尺寸校验或缩略，不能与启用 Pillow 的校验等同。

这只是上述 Pillow 调用点的兼容性缓解，并未修复锁文件中的依赖版本。
`requirements-linux-py310.lock` 仍锁定 Pillow 12.2.0，处于
[GHSA-24rc-8hj2-v98c / CVE-2026-96638](https://api.osv.dev/v1/vulns/GHSA-24rc-8hj2-v98c)
所列 `>=9.1.0,<13.0.0` 受影响范围。
[Pillow 13.0.0 官方元数据](https://pypi.org/pypi/Pillow/13.0.0/json) 要求 Python >=3.11，
不提供 cp310 轮子；本次不升级 Python，不修改依赖声明或锁，基线升级另行处理。
OpenCV 等其他图片处理路径不属于本次缓解范围，也不能据此声称整条图片输入链路已安全。

专项测试 `server/tests/test_vision_jpeg_security.py` 使用小型真实 JPEG/BMP/PNG 和 mock
检查格式限制、加载前尺寸拒绝、正常缩略、失败无回退及缺少 Pillow 的行为。
不构造巨型或恶意内存放大样本，不调用模型或硬件；Windows 本地测试与 Python 3.10
语法解析不代表 Linux/Python 3.10 运行验证。
