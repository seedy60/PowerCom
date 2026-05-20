"""    
File line randomization functions for use with PowerCom
"""

from pathlib import Path
from random import choice

cache: dict[str, list[str]] = {}

def getRandomLine(filePath: str) -> str:
    if filePath not in cache:
        path = Path(filePath)
        if not path.exists():
            raise FileNotFoundError(f'the file {filePath} could not be found on the system.')
        with path.open('r', encoding='utf-8') as file:
            cache[filePath] = [line.strip() for line in file if line.strip()]
    if not cache[filePath]:
        raise ValueError(f'the file {filePath} did not contain any usable lines.')
    return choice(cache[filePath])

def clearCache():
    cache.clear()
