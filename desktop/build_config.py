"""构建所需的资源和依赖；所有仓库路径由本文件位置推导。"""
import hashlib
from pathlib import Path
import urllib.request

APP_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = APP_ROOT.parent
SERVER_ROOT = PROJECT_ROOT / "server"
MODEL_URL = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
MODEL_SHA256 = "fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1"


def prepare_model():
    """构建时下载固定版本并验 SHA-256；用户运行 exe 时无需下载。"""
    model = SERVER_ROOT / "models" / "hand_landmarker.task"
    if model.is_file() and hashlib.sha256(model.read_bytes()).hexdigest() == MODEL_SHA256:
        return model
    model.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(MODEL_URL, timeout=60) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != MODEL_SHA256:
        raise RuntimeError("手部识别模型校验失败，构建已停止")
    temporary = model.with_suffix(".download")
    temporary.write_bytes(data)
    temporary.replace(model)
    return model


def collect_bundle_files():
    from PyInstaller.utils.hooks import collect_all, collect_data_files

    def runtime_module(name):
        return not any(part in {"test", "tests"} or part.endswith("_test") for part in name.split("."))

    # MediaPipe 的 ctypes DLL 与任务子模块不能只依赖静态 import 检测。
    datas, binaries, hiddenimports = collect_all(
        "mediapipe", include_py_files=False, filter_submodules=runtime_module, on_error="raise",
    )
    datas += [(str(APP_ROOT / "assets" / "icon.ico"), "assets"),
              (str(APP_ROOT / "assets" / "icon.png"), "assets"),
              (str(APP_ROOT / "VERSION"), "."),
              (str(SERVER_ROOT / "web"), "server/web"),
              (str(prepare_model()), "server/models")]
    # CustomTkinter 的字体和主题 JSON 必须随单文件 exe 一同打包。
    datas += collect_data_files("customtkinter")
    hiddenimports += [
        "app", "memory_store", "runtime_paths", "document_parser", "pypdf", "pystray._win32", "PIL.IcoImagePlugin",
        "uvicorn.logging", "uvicorn.loops.asyncio", "uvicorn.lifespan.on",
        "uvicorn.protocols.http.h11_impl", "uvicorn.protocols.websockets.websockets_sansio_impl",
    ]
    return datas, binaries, hiddenimports
