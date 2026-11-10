"""Public web resources cannot expose the protected administrator directory."""

from pathlib import Path

from starlette.staticfiles import StaticFiles


class PublicWebStaticFiles(StaticFiles):
    def __init__(self, directory):
        super().__init__(directory=str(directory))
        self.admin_directory = (Path(directory) / "admin").resolve()

    def lookup_path(self, path):
        full_path, stat_result = super().lookup_path(path)
        # Check the resolved filesystem destination, not the URL spelling. On
        # Windows, case, backslashes and trailing dots can identify the same file.
        if stat_result is not None and Path(full_path).is_relative_to(self.admin_directory):
            return "", None
        return full_path, stat_result
