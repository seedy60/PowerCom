"""
config class for PowerCom
"""

from pathlib import Path
from threading import Thread

from conf import conf
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class Config:
    def __init__(self):
        self.configPath = Path('ttcom.conf')
        self.serverConfigs = conf.servers()
        self.watcher = ConfigWatcher(str(self.configPath), self.reloadConf)
        self.observer = Observer()
        self.observer.schedule(self.watcher, '.', recursive = False)
        self.observer.start()
        self.observerThread = Thread(target = self.observer.join, daemon = True)
        self.observerThread.start()

    def get(self, serverName: str, itemName: str):
        configValue = self.getRaw(serverName, itemName)
        if configValue is None: return None
        return self._convertConfigValue(configValue)

    def getRaw(self, serverName: str, itemName: str):
        try:
            serverConfig = self.serverConfigs[serverName]
        except ValueError as e:
            print(e)
            return None
        try:
            return serverConfig[itemName]
        except KeyError:
            return None

    def set(self, serverName: str, itemName: str, value: str) -> None:
        self._writeServerValue(serverName, itemName, value)
        self.reloadConf()

    def _writeServerValue(self, serverName: str, itemName: str, value: str) -> None:
        text = self.configPath.read_text(encoding='utf-8-sig')
        lines = text.splitlines()
        start = self._findServerSection(lines, serverName)
        if start is None:
            raise KeyError(f'Server {serverName} was not found in {self.configPath}.')

        end = len(lines)
        for index in range(start + 1, len(lines)):
            if self._isSectionHeader(lines[index]):
                end = index
                break

        itemNameLower = itemName.lower()
        target = None
        for index in range(start + 1, end):
            line = lines[index]
            stripped = line.strip()
            if not stripped or stripped[0] in ';#' or '=' not in stripped:
                continue
            key = stripped.split('=', 1)[0].strip().lower()
            if key == itemNameLower:
                target = index

        newLine = f'{itemName} = {value}'
        if target is None:
            lines.insert(end, newLine)
        else:
            indent = lines[target][:len(lines[target]) - len(lines[target].lstrip())]
            lines[target] = f'{indent}{newLine}'

        self.configPath.write_text('\n'.join(lines) + '\n', encoding='utf-8')

    @staticmethod
    def _isSectionHeader(line: str) -> bool:
        stripped = line.strip()
        return stripped.startswith('[') and ']' in stripped

    def _findServerSection(self, lines: list[str], serverName: str) -> int | None:
        requested = serverName.lower()
        for index, line in enumerate(lines):
            if not self._isSectionHeader(line):
                continue
            section = line.strip()[1:].split(']', 1)[0].strip()
            parts = section.split(None, 1)
            if len(parts) == 2 and parts[0].lower() == 'server':
                if parts[1].lower() == requested:
                    return index
        return None

    @staticmethod
    def _convertConfigValue(configValue: str):
        if configValue.isnumeric() and configValue != '1' and configValue != '0': return float(configValue)
        match configValue.lower():
            case 'y' | 'yes' | '1' | 'true': return True
            case 'n' | 'no' | '0' | 'false': return False
            case _: return configValue

    def reloadConf(self):
        self.serverConfigs = conf.servers()

class ConfigWatcher(FileSystemEventHandler):
    def __init__(self, configPath, reloadFunc):
        self.configPath = configPath
        self.reloadFunc = reloadFunc

    def on_modified(self, event):
        self.reloadFunc()
