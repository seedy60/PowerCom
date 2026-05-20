"""
Speech and braille output through Prism.
"""

from typing import Any

from prism import BackendId, Context


_backend_aliases = {
    "avspeech": "AV_SPEECH",
    "av_speech": "AV_SPEECH",
    "boypcreader": "BOY_PC_READER",
    "boy_pc_reader": "BOY_PC_READER",
    "jaws": "JAWS",
    "mac": "AV_SPEECH",
    "mackintalk": "AV_SPEECH",
    "ns": "AV_SPEECH",
    "nsspeech": "AV_SPEECH",
    "nvda": "NVDA",
    "onecore": "ONE_CORE",
    "one_core": "ONE_CORE",
    "orca": "ORCA",
    "pctalker": "PC_TALKER",
    "pc_talker": "PC_TALKER",
    "sapi": "SAPI",
    "sapi5": "SAPI",
    "sense_reader": "SENSE_READER",
    "sensereader": "SENSE_READER",
    "speech-dispatcher": "SPEECH_DISPATCHER",
    "speech_dispatcher": "SPEECH_DISPATCHER",
    "speechd": "SPEECH_DISPATCHER",
    "spiel": "SPIEL",
    "system_access": "SYSTEM_ACCESS",
    "systemaccess": "SYSTEM_ACCESS",
    "uia": "UIA",
    "voiceover": "VOICE_OVER",
    "voice_over": "VOICE_OVER",
    "window_eyes": "WINDOW_EYES",
    "windoweyes": "WINDOW_EYES",
    "zdsr": "ZDSR",
    "zoom_text": "ZOOM_TEXT",
    "zoomtext": "ZOOM_TEXT",
}


def _normalize_backend_token(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def resolve_backend_id(context: Context, output_type: str) -> BackendId:
    output_type = (output_type or "").strip()
    if not output_type:
        raise ValueError("Backend name is empty.")

    normalized = _normalize_backend_token(output_type)
    mapped_name = _backend_aliases.get(normalized)
    if mapped_name:
        return BackendId[mapped_name]

    for index in range(context.backends_count):
        backend_id = context.id_of(index)
        if backend_id.name == "INVALID":
            continue
        tokens = {
            _normalize_backend_token(backend_id.name),
            _normalize_backend_token(context.name_of(backend_id)),
        }
        if normalized in tokens:
            return backend_id

    for candidate in (output_type, output_type.lower()):
        try:
            backend_id = context.id_of(candidate)
            if backend_id.name != "INVALID":
                return backend_id
        except Exception:
            pass

    enum_key = output_type.upper().replace("-", "_").replace(" ", "_")
    try:
        return BackendId[enum_key]
    except KeyError:
        pass

    normalized_enum = _normalize_backend_token(enum_key)
    for member_name, member in BackendId.__members__.items():
        if _normalize_backend_token(member_name) == normalized_enum:
            return member

    return BackendId(int(output_type, 0))


def list_backends() -> list[dict[str, str]]:
    context = Context()
    backends = []
    for index in range(context.backends_count):
        backend_id = context.id_of(index)
        if backend_id.name == "INVALID" or not context.exists(backend_id):
            continue
        backends.append({"id": backend_id.name, "name": context.name_of(backend_id)})
    return backends


def list_voices(output_type: str = "auto") -> list[dict[str, int | str]]:
    context = Context()
    backend = (
        context.create_best()
        if not output_type or output_type.strip().lower() == "auto"
        else context.create(resolve_backend_id(context, output_type))
    )
    features = backend.features
    if features.supports_refresh_voices:
        backend.refresh_voices()
    if not features.supports_count_voices:
        return []

    voices = []
    for index in range(backend.voices_count):
        name = f"Voice {index}"
        language = ""
        if features.supports_get_voice_name:
            try:
                name = backend.get_voice_name(index)
            except Exception:
                pass
        if features.supports_get_voice_language:
            try:
                language = backend.get_voice_language(index)
            except Exception:
                pass
        voices.append({"index": index, "name": name, "language": language})
    return voices


class Speech:
    """
    Class for speaking and brailling output.
    """

    def __init__(self, outputType: str = "auto", **params: Any) -> None:
        self.outputType = outputType
        self.params = params
        self.context = Context()
        self.output = self._getOutput(outputType)
        self._apply_params(params)

    def _getOutput(self, outputType: str):
        outputType = (outputType or "auto").strip()
        if outputType.lower() == "auto":
            return self.context.create_best()
        try:
            backend_id = self._resolve_backend_id(outputType)
            return self.context.create(backend_id)
        except Exception:
            return self.context.create_best()

    def _resolve_backend_id(self, outputType: str) -> BackendId:
        return resolve_backend_id(self.context, outputType)

    def _apply_params(self, params: dict[str, Any]) -> None:
        features = self.output.features
        if "rate" in params and features.supports_set_rate:
            self._set_backend_property("rate", float(params["rate"]))
        if "volume" in params and features.supports_set_volume:
            volume = float(params["volume"])
            if volume > 1:
                volume /= 100
            self._set_backend_property("volume", volume)
        if "pitch" in params and features.supports_set_pitch:
            self._set_backend_property("pitch", float(params["pitch"]))
        if "voice" in params and features.supports_set_voice:
            self._set_voice(params["voice"])

    def _set_backend_property(self, name: str, value: float) -> None:
        try:
            setattr(self.output, name, value)
        except Exception:
            pass

    def _set_voice(self, voice: Any) -> None:
        try:
            voice_index = int(float(voice))
            if voice_index < 0:
                return
            self.output.voice = voice_index
            return
        except (TypeError, ValueError):
            pass
        except Exception:
            return

        features = self.output.features
        if features.supports_refresh_voices:
            self.output.refresh_voices()
        if not features.supports_count_voices:
            return
        requested = str(voice).casefold()
        for index in range(self.output.voices_count):
            try:
                if self.output.get_voice_name(index).casefold() == requested:
                    self.output.voice = index
                    return
            except Exception:
                continue

    def speak(self, text: str, interrupt: bool = True) -> None:
        """
        Speaks the given text with the selected backend.
        """
        if not text:
            return
        features = self.output.features
        if interrupt and features.supports_stop:
            self.output.stop()
        if features.supports_speak:
            self.output.speak(text, interrupt=interrupt)

    def braille(self, text: str) -> None:
        """
        Brailles the given text.
        """
        if not text:
            return
        if self.output.features.supports_braille:
            self.output.braille(text)

    def both(self, text: str, interrupt: bool = True) -> None:
        """
        Outputs the given text through speech and braille when supported.
        """
        if not text:
            return
        features = self.output.features
        if interrupt and features.supports_stop:
            self.output.stop()
        if features.supports_output:
            self.output.output(text, interrupt=interrupt)
            return
        self.braille(text)
        if features.supports_speak:
            self.output.speak(text, interrupt=interrupt)
