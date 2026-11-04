"""构建并运行 exe 冒烟检查；检查通过后才更新正式发布目录。"""
import argparse
import hashlib
from importlib import import_module, metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from build_config import APP_ROOT, PROJECT_ROOT, prepare_model


def check_dependencies():
    modules = {"PyInstaller": "pyinstaller", "fastapi": "fastapi", "uvicorn": "uvicorn",
               "numpy": "numpy", "cv2": "opencv-python", "mediapipe": "mediapipe",
               "matplotlib": "matplotlib", "PIL": "Pillow", "dotenv": "python-dotenv",
               "openai": "openai", "websockets": "websockets", "pystray": "pystray"}
    versions = {}
    for module, distribution in modules.items():
        import_module(module)  # 缺库、DLL 或导入错误均立即失败。
        versions[distribution] = metadata.version(distribution)
    return versions


def main():
    parser = argparse.ArgumentParser(description="构建赛博小喵桌面程序")
    parser.add_argument("--debug", action="store_true", help="构建带控制台的调试版")
    args = parser.parse_args()
    versions = check_dependencies()
    prepare_model()
    mode = "debug" if args.debug else "release"
    build_dir = PROJECT_ROOT / "artifacts" / "build" / "desktop" / mode
    destination = PROJECT_ROOT / "artifacts" / "debug" if args.debug else build_dir / "package"
    environment = dict(os.environ, CYBERMIAO_BUILD_CONSOLE="1" if args.debug else "0")
    subprocess.run([
        sys.executable, "-m", "PyInstaller", str(APP_ROOT / "CyberMiao.spec"),
        "--clean", "--noconfirm", "--distpath", str(destination), "--workpath", str(build_dir),
    ], cwd=APP_ROOT, env=environment, check=True)
    executable = destination / ("CyberMiaoDebug.exe" if args.debug else "CyberMiao.exe")
    report = build_dir / "runtime-check.json"
    with tempfile.TemporaryDirectory(prefix="小喵 exe 工作目录 ") as unrelated_directory:
        subprocess.run([str(executable), "--check-runtime", str(report)], cwd=unrelated_directory,
                       check=True, timeout=180,
                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    result = json.loads(report.read_text(encoding="utf-8"))
    if not result["ok"]:
        raise RuntimeError(f"EXE 运行检查失败，请查看 {report}")
    info = {"python": sys.version, "mode": mode, "dependencies": versions,
            "exe_bytes": executable.stat().st_size,
            "exe_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
            "runtime_check": result}
    (destination / "BUILD-INFO.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    if not args.debug:
        release = PROJECT_ROOT / "artifacts" / "release"
        release.mkdir(parents=True, exist_ok=True)
        for filename in ("CyberMiao.exe", "BUILD-INFO.json"):
            shutil.copy2(destination / filename, release / filename)
        guide = PROJECT_ROOT / "docs" / "使用手册.md"
        shutil.copy2(guide, release / "使用说明.md")
        shutil.copy2(guide, release / "README.txt")
    print(f"构建和运行检查通过：{executable}")


if __name__ == "__main__":
    main()
