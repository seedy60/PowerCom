"""
Path resolver retained for compatibility with older powercom imports.
"""

from pathlib import Path


class Pool:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        if not self.path.is_dir():
            raise ValueError(f"{path} is not a directory.")

    def get(self, file: str) -> Path:
        return self.path / file

    def clear(self) -> None:
        return None
