# -*- coding: utf-8 -*-
"""生成招标技术参数 Word 文档（纯标准库，无第三方依赖）。"""
import zipfile
import os

NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _esc(text):
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _runs(text, bold=False, size=None, color=None, font=None):
    """把文本拆成多个 run，支持简单加粗/字号/颜色/字体。"""
    xml = '<w:r>'
    rpr = '<w:rPr>'
    if bold:
        rpr += '<w:b/>'
    if size:
        rpr += f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>'
    if color:
        rpr += f'<w:color w:val="{color}"/>'
    if font:
        rpr += f'<w:rFonts w:ascii="{font}" w:hAnsi="{font}" w:eastAsia="{font}"/>'
    rpr += '</w:rPr>'
    xml += rpr
    xml += f'<w:t xml:space="preserve">{_esc(text)}</w:t>'
    xml += '</w:r>'
    return xml


def _para(text, *, bold=False, size=None, color=None, align=None, space_after=60,
          font="微软雅黑", before=0):
    align_xml = f'<w:jc w:val="{align}"/>' if align else ''
    spacing = f'<w:spacing w:before="{before}" w:after="{space_after}" w:line="300" w:lineRule="auto"/>'
    size_xml = ""
    if size:
        size_xml = '<w:sz w:val="%d"/><w:szCs w:val="%d"/>' % (size, size)
    return (
        '<w:p>'
        '<w:pPr>'
        + spacing + align_xml
        + '<w:rPr>'
        + f'<w:rFonts w:ascii="{font}" w:hAnsi="{font}" w:eastAsia="{font}"/>'
        + size_xml
        + '</w:rPr>'
        '</w:pPr>'
        + _runs(text, bold=bold, size=size, color=color, font=font)
        + '</w:p>'
    )


def _heading(text, level=1):
    size = {1: 32, 2: 26, 3: 22}.get(level, 22)
    align = "center" if level == 1 else None
    return _para(text, bold=True, size=size, align=align, space_after=120,
                 before=120 if level == 1 else 60)


def _table(headers, rows, widths=None):
    cols = len(headers)
    if widths is None:
        widths = [1000] * cols
    total = sum(widths)
    grid = '<w:tblGrid>' + ''.join(
        f'<w:gridCol w:w="{int(5000 * w / total)}"/>' for w in widths
    ) + '</w:tblGrid>'

    def _row(cells, is_header):
        cell_xml = []
        for c in cells:
            rpr = ''
            shd = ''
            if is_header:
                rpr = ('<w:rPr><w:b/><w:color w:val="FFFFFF"/>'
                       '<w:rFonts w:ascii="微软雅黑" w:hAnsi="微软雅黑" w:eastAsia="微软雅黑"/>'
                       '<w:sz w:val="20"/><w:szCs w:val="20"/></w:rPr>')
                shd = '<w:shd w:val="clear" w:color="auto" w:fill="D9E2F3"/>'
            cell = (
                '<w:tc>'
                '<w:tcPr>'
                '<w:tcW w:w="0" w:type="auto"/>'
                + shd
                + '<w:vAlign w:val="center"/>'
                '</w:tcPr>'
                '<w:p>'
                '<w:pPr><w:spacing w:before="40" w:after="40"/></w:pPr>'
                '<w:r>' + rpr + '<w:t xml:space="preserve">' + _esc(c) + '</w:t></w:r>'
                '</w:p>'
                '</w:tc>'
            )
            cell_xml.append(cell)
        return '<w:tr>' + ''.join(cell_xml) + '</w:tr>'

    body = '<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/>' \
           '<w:tblBorders>' \
           '<w:top w:val="single" w:sz="4" w:space="0" w:color="999999"/>' \
           '<w:left w:val="single" w:sz="4" w:space="0" w:color="999999"/>' \
           '<w:bottom w:val="single" w:sz="4" w:space="0" w:color="999999"/>' \
           '<w:right w:val="single" w:sz="4" w:space="0" w:color="999999"/>' \
           '<w:insideH w:val="single" w:sz="4" w:space="0" w:color="999999"/>' \
           '<w:insideV w:val="single" w:sz="4" w:space="0" w:color="999999"/>' \
           '</w:tblBorders></w:tblPr>' + grid
    body += _row(headers, True)
    for r in rows:
        body += _row(r, False)
    body += '</w:tbl>'
    return body


def build_document(parts):
    """parts: list of xml strings（段落/表格）。"""
    body = ''.join(parts)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{NS_W}">'
        '<w:body>'
        f'{body}'
        '<w:sectPr>'
        '<w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" '
        'w:header="720" w:footer="720" w:gutter="0"/>'
        '</w:sectPr>'
        '</w:body>'
        '</w:document>'
    )


CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '</Types>'
)

RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/>'
    '</Relationships>'
)


def save_docx(path, parts):
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', CONTENT_TYPES)
        z.writestr('_rels/.rels', RELS)
        z.writestr('word/document.xml', build_document(parts))
    return path


# ============ 内容 ============
P = []

P.append(_heading("AI 桌面陪伴机器人技术参数要求", 1))
P.append(_para("（整机完整版）", bold=False, size=20, align="center", space_after=160))

# 1. 整机参数
P.append(_heading("一、整机基础参数", 2))
P.append(_table(
    ["序号", "参数项", "技术指标要求"],
    [
        ["1", "充电接口", "USB Type-C"],
        ["2", "输入电压", "DC 5V"],
        ["3", "电池容量", "≥ 1000 mAh"],
        ["4", "主控平台", "ESP32 系列（双核，支持 PSRAM）"],
    ],
    widths=[1, 2, 4],
))
P.append(_para("", space_after=60))

# 2. 摄像头
P.append(_heading("二、摄像头采集模块", 2))
P.append(_table(
    ["序号", "参数项", "技术指标要求"],
    [
        ["1", "图像传感器", "OV2640 系列，DVP 并行接口，8 位数据总线"],
        ["2", "最高采集分辨率", "640×480（VGA）"],
        ["3", "输出图像格式", "JPEG 编码"],
        ["4", "JPEG 压缩质量", "参数 14（数值越小质量越高，范围 0–63）"],
        ["5", "主时钟频率", "20 MHz（XCLK）"],
        ["6", "帧缓冲数量", "2 帧（位于 PSRAM）"],
        ["7", "单帧缓冲容量", "不小于 128 KB"],
        ["8", "采集模式", "最新帧优先（CAMERA_GRAB_LATEST）"],
        ["9", "自动白平衡", "支持（含自动白平衡增益）"],
        ["10", "自动曝光", "支持"],
        ["11", "自动增益控制", "支持"],
        ["12", "镜像/翻转", "垂直翻转，可配置水平镜像"],
        ["13", "亮度/对比度/饱和度", "可调（默认：亮度 0、对比度 1、饱和度 0）"],
        ["14", "实时预览帧率", "默认 10 帧/秒（可调范围 1–30 帧/秒）"],
        ["15", "视觉分析拍照帧率", "12 帧/秒（按需短时提速）"],
        ["16", "音频优先期间保活帧率", "6 帧/秒"],
        ["17", "空闲待机帧率", "0 帧/秒（检测到观看端后自动恢复）"],
    ],
    widths=[1, 3, 5],
))
P.append(_para("", space_after=60))

# 3. 语音采集
P.append(_heading("三、语音采集模块（麦克风）", 2))
P.append(_table(
    ["序号", "参数项", "技术指标要求"],
    [
        ["1", "麦克风类型", "PDM 数字麦克风"],
        ["2", "采样率", "16 kHz"],
        ["3", "采样位宽", "16 bit"],
        ["4", "声道", "单声道（MONO）"],
        ["5", "分块时长", "20 ms/块（单块 640 字节）"],
        ["6", "上行传输方式", "批量聚合发送，单批 8 块（160 ms，5120 字节）"],
        ["7", "半双工控制", "TTS 播放期间自动停止麦克风采集"],
    ],
    widths=[1, 3, 5],
))
P.append(_para("", space_after=60))

# 4. 语音输出
P.append(_heading("四、语音输出模块（扬声器）", 2))
P.append(_table(
    ["序号", "参数项", "技术指标要求"],
    [
        ["1", "功放类型", "I2S 数字功放（MAX98357 系列）"],
        ["2", "输出采样率", "16 kHz"],
        ["3", "输出位宽", "32 bit（MSB）"],
        ["4", "输出声道", "立体声（STD 模式）"],
        ["5", "音量调节", "0–100% 数字音量控制，可远程下发"],
        ["6", "音频淡入", "支持（防爆音）"],
        ["7", "音频静音", "支持静音填充"],
    ],
    widths=[1, 3, 5],
))
P.append(_para("", space_after=60))

# 5. 语音交互
P.append(_heading("五、语音交互与 AI 能力", 2))
P.append(_table(
    ["序号", "参数项", "技术指标要求"],
    [
        ["1", "大模型", "Qwen 3.5 Omni Plus Realtime（流式实时多模态）"],
        ["2", "语音识别（ASR）", "实时流式识别（gummy-realtime-v1）"],
        ["3", "语音合成（TTS）", "实时流式合成，默认男声"],
        ["4", "语音活动检测", "服务端 VAD（阈值 0.5）"],
        ["5", "断句静音时长", "900 ms（可调）"],
        ["6", "前缀填充", "500 ms"],
        ["7", "交互模式", "全双工实时语音对话"],
        ["8", "视觉问答", "支持结合摄像头画面的多模态问答"],
        ["9", "同声传译", "支持中英双向实时互译"],
        ["10", "语音指令", "支持语音触发动作、模式切换"],
    ],
    widths=[1, 3, 5],
))
P.append(_para("", space_after=60))

# 6. 无线传输
P.append(_heading("六、无线传输", 2))
P.append(_table(
    ["序号", "参数项", "技术指标要求"],
    [
        ["1", "无线标准", "WiFi（802.11 b/g/n）"],
        ["2", "发射功率", "20 dBm（最大）"],
        ["3", "断线重连", "支持，重连间隔 15 s"],
        ["4", "视频/音频传输协议", "WebSocket 实时流"],
    ],
    widths=[1, 3, 5],
))
P.append(_para("", space_after=60))

# 7. 软件功能模块
P.append(_heading("七、软件功能模块", 2))
P.append(_table(
    ["序号", "功能模块", "功能说明"],
    [
        ["1", "语音对话", "全双工实时语音交互，支持打断、连续对话、上下文记忆"],
        ["2", "同声传译", "中英双向实时互译"],
        ["3", "视觉识别", "结合摄像头画面的多模态视觉问答"],
        ["4", "动作控制", "舵机驱动的头部（点头/摇头/歪头）与耳朵动作"],
        ["5", "摄像头采集", "实时视频流采集与网页端预览"],
        ["6", "记录分析", "定时拍照 + AI 生成活动描述 + 事后问答/图表分析"],
        ["7", "网页控制台", "实时画面、对话、翻译、记录、姿态控制的可视化管理界面"],
    ],
    widths=[1, 3, 6],
))
P.append(_para("", space_after=60))

# 8. 通信接口
P.append(_heading("八、通信接口", 2))
P.append(_table(
    ["序号", "接口", "协议", "说明"],
    [
        ["1", "/ws/camera", "WebSocket", "摄像头视频流上传"],
        ["2", "/ws_audio", "WebSocket", "音频双向流（麦克风上行/TTS 下行）"],
        ["3", "/ws/viewer", "WebSocket", "网页端视频预览推送"],
        ["4", "/ws_ui", "WebSocket", "网页控制台交互"],
        ["5", "/", "HTTP", "网页控制台页面"],
        ["6", "/api/health", "HTTP", "健康检查"],
        ["7", "/record/*", "HTTP", "记录分析（开始/停止/问答/图表）"],
        ["8", "/servo*、/performance_mode、/camera_setting", "HTTP", "姿态/模式/参数控制"],
    ],
    widths=[1, 4, 2, 5],
))
P.append(_para("", space_after=60))

# 9. 技术栈与运行环境
P.append(_heading("九、技术栈与运行环境", 2))
P.append(_table(
    ["序号", "项目", "技术指标"],
    [
        ["1", "服务端框架", "Python 3.11 + FastAPI + Uvicorn"],
        ["2", "实时通信", "WebSocket（asyncio 异步）"],
        ["3", "AI 服务", "阿里云百炼 DashScope（Qwen Omni Realtime）"],
        ["4", "设备固件", "Arduino 框架 + ESP-IDF 底层"],
        ["5", "前端", "原生 HTML5/CSS/JS（含 Chart.js 图表）"],
        ["6", "运行环境", "支持本地部署（Windows/Linux），可扩展云端部署"],
    ],
    widths=[1, 3, 6],
))
P.append(_para("", space_after=60))

# 10. 系统性能指标
P.append(_heading("十、系统性能指标", 2))
P.append(_table(
    ["序号", "指标项", "要求"],
    [
        ["1", "端到端语音响应延迟", "满足实时交互要求（音频分块 20 ms，批量上传 160 ms）"],
        ["2", "视觉分析响应", "拍照帧 80 ms 内到达，超时 0.8 s 自动回退"],
        ["3", "画面流畅度", "语音输入/TTS 期间保持画面连续，无卡死"],
        ["4", "视频传输", "支持多客户端并发预览"],
        ["5", "断线重连", "设备断网自动重连（15 s 间隔）"],
        ["6", "多任务并行", "摄像头采集、语音采集、TTS 播放分核异步并行（双核）"],
    ],
    widths=[1, 3, 5],
))

out = os.path.join(os.path.dirname(__file__), "招标技术参数-整机完整版.docx")
save_docx(out, P)
print("已生成:", out)
