"""
Sound manager backed by sound_lib.
"""

from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from time import sleep
from typing import Any

from sound_lib.main import BassError
from sound_lib.output import Output

from .sound import Sound

_shared_output: Output | None = None


def get_output() -> Output | None:
    global _shared_output
    if _shared_output is not None:
        return _shared_output
    try:
        _shared_output = Output()
    except BassError as error:
        if error.code != 14:
            raise
        return None
    return _shared_output


class Manager:
    def __init__(self, path: str, output: Output | None = None) -> None:
        self.path = Path(path)
        if not self.path.is_dir():
            raise ValueError(f"{path} is not a directory.")
        self.output = output or get_output()
        self.sequentialSounds: Queue[Sound] = Queue()
        self.replacedSound: Sound | None = None
        self.sequentialWorker = Thread(target=self._sequentialLoop, daemon=True)
        self.sequentialWorker.start()
        self.sounds: list[Sound] = []

    def _newSound(self, filePath: str, **kwargs: Any) -> Sound:
        sound = Sound(self.path / filePath, **kwargs)
        sound.direct = True
        return sound

    def _sequentialLoop(self) -> None:
        while True:
            try:
                sound = self.sequentialSounds.get(timeout=0.1)
            except Empty:
                continue
            try:
                sound.play()
                while sound.isPlaying:
                    sleep(0.003)
            finally:
                sound.free()
                self.sequentialSounds.task_done()

    def play(self, filePath: str, playType: int, **kwargs: Any) -> None:
        self.cleanSounds()
        sound = self._newSound(filePath, **kwargs)
        if playType == 0:
            self.sounds.append(sound)
            sound.play()
        elif playType == 1:
            if self.replacedSound is not None:
                if self.replacedSound.isPlaying:
                    self.replacedSound.stop()
                self.replacedSound.free()
            self.replacedSound = sound
            self.replacedSound.play()
        else:
            self.sequentialSounds.put(sound)

    def cleanSounds(self) -> None:
        active_sounds = []
        for sound in self.sounds:
            if sound.isPlaying:
                active_sounds.append(sound)
            else:
                sound.free()
        self.sounds = active_sounds
