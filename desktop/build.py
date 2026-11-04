"""构建并运行 exe 冒烟检查；检查通过后才更新正式发布目录。"""
import argparse
import hashlib
from importlib import import_module, metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from build_config import APP_ROOT, PROJECT_ROOT, prepare_model

PORTABLE_NAME = "CyberMiao-Portable-x64.exe"
INSTALLER_NAME = "CyberMiao-Setup-x64.exe"
INSTALLER_SCRIPT = APP_ROOT / "installer" / "CyberMiao.iss"


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


def find_inno_compiler(explicit_path=None):
    candidates = [
        explicit_path,
        os.environ.get("INNO_SETUP_COMPILER"),
        shutil.which("ISCC.exe"),
        shutil.which("ISCC"),
        str(Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Programs" / "Inno Setup 6" / "ISCC.exe"),
        str(Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Inno Setup 6" / "ISCC.exe"),
        str(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Inno Setup 6" / "ISCC.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate).resolve()
    raise FileNotFoundError(
        "找不到 Inno Setup 6 的 ISCC.exe。请安装 Inno Setup 6，或通过 --inno-compiler 指定 ISCC.exe 路径。"
    )


def normalize_version(version):
    value = (version or "0.0.0").strip()
    if value[:1].lower() == "v":
        value = value[1:]
    if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}", value):
        raise ValueError("版本号只能包含字母、数字、点、加号、下划线和连字符")
    return value


def compile_installer(portable_exe, output_dir, version, compiler):
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        str(compiler),
        f"/DAppVersion={version}",
        f"/DSourceExe={Path(portable_exe).resolve()}",
        f"/DOutputDir={output_dir}",
        str(INSTALLER_SCRIPT),
    ], cwd=APP_ROOT, check=True)
    installer = output_dir / INSTALLER_NAME
    if not installer.is_file():
        raise RuntimeError(f"Inno Setup 未生成预期安装器：{installer}")
    return installer


def file_record(path):
    digest = hashlib.sha256()
    size = 0
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return {"bytes": size, "sha256": digest.hexdigest()}


def publish_release(package_dir, version, compiler):
    """发布通过检查的包，并原子更新根目录的用户启动入口。"""
    package_dir = Path(package_dir).resolve()
    info = json.loads((package_dir / "BUILD-INFO.json").read_text(encoding="utf-8"))
    executable = package_dir / "CyberMiao.exe"
    if not info.get("runtime_check", {}).get("ok"):
        raise RuntimeError("运行检查未通过，不能发布主程序")
    if hashlib.sha256(executable.read_bytes()).hexdigest() != info["exe_sha256"]:
        raise RuntimeError("EXE 与构建记录的哈希不一致，不能发布主程序")
    release = PROJECT_ROOT / "artifacts" / "release"
    release.mkdir(parents=True, exist_ok=True)

    portable = release / PORTABLE_NAME
    shutil.copy2(executable, portable)
    installer = compile_installer(portable, release, version, compiler)

    info["version"] = version
    info["architecture"] = "x64"
    info["release_assets"] = {
        PORTABLE_NAME: file_record(portable),
        INSTALLER_NAME: file_record(installer),
    }
    (release / "BUILD-INFO.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    guide = PROJECT_ROOT / "docs" / "使用手册.md"
    shutil.copy2(guide, release / "使用说明.md")
    shutil.copy2(guide, release / "README.txt")

    for entry in (release / "CyberMiao.exe", PROJECT_ROOT / "CyberMiao.exe"):
        temporary = entry.with_name(f"{entry.stem}.publish{entry.suffix}")
        try:
            shutil.copy2(executable, temporary)
            os.replace(temporary, entry)
        except PermissionError:
            if entry == PROJECT_ROOT / "CyberMiao.exe":
                print("警告：根目录 CyberMiao.exe 正在使用，已保留运行中的文件；两个发行文件已生成。")
                return portable
            raise
        finally:
            temporary.unlink(missing_ok=True)
    return entry


def main():
    parser = argparse.ArgumentParser(description="构建赛博小喵桌面程序")
    parser.add_argument("--debug", action="store_true", help="构建带控制台的调试版")
    parser.add_argument("--version", default="0.0.0", help="安装器显示的版本号，例如 1.0.0 或 v1.0.0")
    parser.add_argument("--inno-compiler", help="Inno Setup 6 的 ISCC.exe 路径")
    args = parser.parse_args()
    if os.name != "nt" or sys.maxsize <= 2**32:
        raise SystemExit("正式发行包仅支持在 Windows x64 Python 环境中构建。")
    version = normalize_version(args.version)
    compiler = None if args.debug else find_inno_compiler(args.inno_compiler)
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
        try:
            subprocess.run([str(executable), "--check-runtime", str(report)], cwd=unrelated_directory,
                           check=True, timeout=180,
                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            for diagnostic in (report, report.with_suffix(".log")):
                if diagnostic.is_file():
                    print(f"EXE 运行检查诊断：{diagnostic}", file=sys.stderr, flush=True)
                    print(diagnostic.read_text(encoding="utf-8"), file=sys.stderr, flush=True)
            raise
    result = json.loads(report.read_text(encoding="utf-8"))
    if not result["ok"]:
        raise RuntimeError(f"EXE 运行检查失败，请查看 {report}")
    info = {"python": sys.version, "mode": mode, "version": version, "architecture": "x64",
            "dependencies": versions,
            "exe_bytes": executable.stat().st_size,
            "exe_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
            "runtime_check": result}
    (destination / "BUILD-INFO.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    if not args.debug:
        executable = publish_release(destination, version, compiler)
        print(f"发布下载项：{PROJECT_ROOT / 'artifacts' / 'release' / PORTABLE_NAME}")
        print(f"发布下载项：{PROJECT_ROOT / 'artifacts' / 'release' / INSTALLER_NAME}")
    print(f"构建和运行检查通过：{executable}")


if __name__ == "__main__":
    main()
