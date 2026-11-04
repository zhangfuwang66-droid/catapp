"""用户配置；保存前校验，临时文件写完后原子替换。"""
import configparser
import os
import tempfile
from pathlib import Path


class CyberMiaoConfig:
    def __init__(self, config_path: Path, legacy_path: Path | None = None):
        self.config_path = config_path
        self.config = configparser.ConfigParser(interpolation=None)
        self.config.read_dict({
            "Server": {"host": "0.0.0.0", "port": "8081"},
            "API": {"dashscope_api_key": ""},
            "UI": {"auto_open_browser": "true"},
        })
        source = config_path if config_path.exists() else legacy_path
        if source and source.is_file():
            loaded = configparser.ConfigParser(interpolation=None)
            with source.open(encoding="utf-8-sig") as stream:
                loaded.read_file(stream)
            self.config.read_dict({section: dict(loaded[section]) for section in loaded.sections()})
            # 早期启动器误把此选项写在 Server 分组下。
            if loaded.has_option("Server", "auto_open_browser") and not loaded.has_option("UI", "auto_open_browser"):
                self.config["UI"]["auto_open_browser"] = self.config["Server"]["auto_open_browser"]
        if source != config_path:
            self.save()

    def get(self, section: str, key: str, fallback: str = "") -> str:
        return self.config.get(section, key, fallback=fallback)

    def has_api_key(self) -> bool:
        key = self.get("API", "dashscope_api_key").strip()
        return bool(key and key not in {"your_api_key_here", "sk-fake-placeholder"})

    def server_options(self) -> dict:
        try:
            port = int(self.get("Server", "port"))
        except ValueError as exc:
            raise ValueError("端口必须是 1 到 65535 之间的整数") from exc
        if not 1 <= port <= 65535:
            raise ValueError("端口必须是 1 到 65535 之间的整数")
        return {"host": self.get("Server", "host", "0.0.0.0"), "port": port,
                "api_key": self.get("API", "dashscope_api_key").strip()}

    def save(self):
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=self.config_path.parent, prefix=".settings-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                self.config.write(stream)
            os.replace(temporary, self.config_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def update(self, api_key: str, port: str, auto_browser: bool):
        key = api_key.strip()
        if not key or key in {"your_api_key_here", "sk-fake-placeholder"}:
            raise ValueError("请填写 DashScope API Key")
        if any(char.isspace() for char in key):
            raise ValueError("API Key 中不能包含空白字符")
        try:
            number = int(port)
        except ValueError as exc:
            raise ValueError("端口必须是 1 到 65535 之间的整数") from exc
        if not 1 <= number <= 65535:
            raise ValueError("端口必须是 1 到 65535 之间的整数")
        previous = {section: dict(self.config[section]) for section in self.config.sections()}
        self.config["API"]["dashscope_api_key"] = key
        self.config["Server"]["port"] = str(number)
        self.config["UI"]["auto_open_browser"] = str(auto_browser).lower()
        try:
            self.save()
        except OSError:
            self.config.read_dict(previous)
            raise
