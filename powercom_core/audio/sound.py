"""
Sound wrapper backed by sound_lib.
"""

from pathlib import Path
from typing import Any

from sound_lib.stream import FileStream


class Sound:
    def __init__(self, file_path: str | Path, **kwargs: Any) -> None:
        self.file_path = Path(file_path)
        self.stream = FileStream(file=str(self.file_path), autofree=False)
        self.playedOnce = False
        self._direct = False
        self._freed = False
        self._position = [0.0, 0.0, 0.0]
        self._direction = [0.0, 0.0, 0.0]
        self._rolloff_factor = 100.0
        self._base_frequency = self.stream.frequency
        default_gain = kwargs.pop("gain", 100)
        self.gain = kwargs.pop("volume", default_gain)
        for key, value in kwargs.items():
            setattr(self, key, value)

    @property
    def x(self) -> float:
        return self._position[0]

    @x.setter
    def x(self, val: float) -> None:
        self._position[0] = val

    @property
    def y(self) -> float:
        return self._position[1]

    @y.setter
    def y(self, val: float) -> None:
        self._position[1] = val

    @property
    def z(self) -> float:
        return self._position[2]

    @z.setter
    def z(self, val: float) -> None:
        self._position[2] = val

    @property
    def position(self) -> list[float]:
        return self._position

    @position.setter
    def position(self, val: list[float]) -> None:
        self._position = val

    @property
    def direction(self) -> list[float]:
        return self._direction

    @direction.setter
    def direction(self, val: list[float]) -> None:
        self._direction = val

    @property
    def pitch(self) -> float:
        return (self.stream.frequency / self._base_frequency) * 100

    @pitch.setter
    def pitch(self, val: float) -> None:
        self.stream.frequency = self._base_frequency * (float(val) / 100)

    @property
    def direct(self) -> bool:
        return self._direct

    @direct.setter
    def direct(self, val: bool) -> None:
        self._direct = bool(val)

    @property
    def rolloffFactor(self) -> float:
        return self._rolloff_factor

    @rolloffFactor.setter
    def rolloffFactor(self, val: float) -> None:
        self._rolloff_factor = float(val)

    @property
    def looping(self) -> bool:
        return self.stream.looping

    @looping.setter
    def looping(self, val: bool) -> None:
        self.stream.looping = bool(val)

    @property
    def gain(self) -> float:
        return self.stream.volume * 100

    @gain.setter
    def gain(self, val: float | str) -> None:
        self.stream.volume = float(val) / 100

    @property
    def volume(self) -> float:
        return self.gain

    @volume.setter
    def volume(self, val: float | str) -> None:
        self.gain = val

    @property
    def isPlaying(self) -> bool:
        return self.stream.is_playing

    @property
    def isPaused(self) -> bool:
        return self.stream.is_paused

    @property
    def isStopped(self) -> bool:
        return self.stream.is_stopped

    def play(self) -> Any:
        if self.isPlaying:
            return None
        if not self.playedOnce:
            self.playedOnce = True
        return self.stream.play()

    def stop(self) -> Any:
        if self.isPlaying:
            return self.stream.stop()
        return None

    def pause(self) -> Any:
        if self.isPlaying:
            return self.stream.pause()
        return None

    def free(self) -> None:
        if self._freed:
            return
        self.stream.free()
        self._freed = True
