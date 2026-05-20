"""Model and serializer for the PowerCom ttcom.conf builder."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


CORE_KEYS = {
    "autologin",
    "chanid",
    "channel",
    "chanpassword",
    "encrypted",
    "gender",
    "hidden",
    "host",
    "nickname",
    "password",
    "silent",
    "statusmode",
    "statusmsg",
    "tcpport",
    "udpport",
    "username",
}

FEATURE_KEYS = {
    "log",
    "maxlogfiles",
    "maxlogsize",
    "mgnotify",
    "mgnotifykey",
    "nosound",
    "nospeak",
    "notifymessage",
    "notifyloginout",
    "ntfy",
    "ntfypassword",
    "ntfytopic",
    "ntfyurl",
    "ntfyuser",
    "playbacktype",
    "prowl",
    "prowlkey",
    "pushover",
    "pushoverdevice",
    "pushoverexpire",
    "pushoverpriority",
    "pushoverretry",
    "pushoversound",
    "pushovertoken",
    "pushoveruser",
    "soundpack",
    "sounds",
    "soundvolume",
    "speech",
    "speechdmodule",
    "speechengine",
    "speechinterrupt",
    "speechpitch",
    "speechrate",
    "speechvoice",
    "speechvolume",
    "systemnotify",
}

KEY_DISPLAY = {
    "autologin": "autoLogin",
    "chanid": "chanid",
    "channel": "channel",
    "chanpassword": "chanpassword",
    "encrypted": "encrypted",
    "gender": "gender",
    "hidden": "hidden",
    "host": "host",
    "log": "log",
    "maxlogfiles": "maxLogFiles",
    "maxlogsize": "maxLogSize",
    "mgnotify": "mgNotify",
    "mgnotifykey": "mgNotifyKey",
    "nickname": "nickname",
    "nosound": "noSound",
    "nospeak": "noSpeak",
    "notifymessage": "notifyMessage",
    "notifyloginout": "notifyLogInOut",
    "ntfy": "ntfy",
    "ntfypassword": "ntfyPassword",
    "ntfytopic": "ntfyTopic",
    "ntfyurl": "ntfyUrl",
    "ntfyuser": "ntfyUser",
    "password": "password",
    "playbacktype": "playbackType",
    "prowl": "prowl",
    "prowlkey": "prowlKey",
    "pushover": "pushover",
    "pushoverdevice": "pushoverDevice",
    "pushoverexpire": "pushoverExpire",
    "pushoverpriority": "pushoverPriority",
    "pushoverretry": "pushoverRetry",
    "pushoversound": "pushoverSound",
    "pushovertoken": "pushoverToken",
    "pushoveruser": "pushoverUser",
    "silent": "silent",
    "soundpack": "soundPack",
    "sounds": "sounds",
    "soundvolume": "soundVolume",
    "speech": "speech",
    "speechdmodule": "speechdModule",
    "speechengine": "speechEngine",
    "speechinterrupt": "speechInterrupt",
    "speechpitch": "speechPitch",
    "speechrate": "speechRate",
    "speechvoice": "speechVoice",
    "speechvolume": "speechVolume",
    "statusmode": "statusmode",
    "statusmsg": "statusmsg",
    "systemnotify": "systemNotify",
    "tcpport": "tcpport",
    "udpport": "udpport",
    "username": "username",
}

DEFAULT_CORE = {
    "autologin": "0",
    "hidden": "0",
    "silent": "0",
    "nickname": "PowerCom User",
}

DEFAULT_FEATURES = {
    "speech": "true",
    "speechengine": "auto",
    "speechinterrupt": "true",
    "speechvoice": "-1",
    "sounds": "true",
    "soundpack": "default",
    "soundvolume": "100",
    "playbacktype": "overlapping",
    "notifyloginout": "true",
    "notifymessage": "true",
    "systemnotify": "false",
    "ntfy": "false",
    "ntfyurl": "https://ntfy.sh",
    "prowl": "false",
    "mgnotify": "false",
    "pushover": "false",
    "log": "true",
    "maxlogsize": "4",
    "maxlogfiles": "5",
}


@dataclass
class ServerConfig:
    name: str
    values: dict[str, str] = field(default_factory=dict)
    overrides: dict[str, str] = field(default_factory=dict)


@dataclass
class ConfigDocument:
    defaults: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_CORE))
    feature_defaults: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_FEATURES))
    servers: list[ServerConfig] = field(default_factory=list)


def canonical_key(key: str) -> str:
    return key.strip().lower()


def display_key(key: str) -> str:
    return KEY_DISPLAY.get(canonical_key(key), key)


def clean_values(values: dict[str, str]) -> dict[str, str]:
    cleaned = {}
    for key, value in values.items():
        value = str(value).strip()
        if value != "":
            cleaned[canonical_key(key)] = value
    return cleaned


def validate_document(document: ConfigDocument) -> list[str]:
    errors = []
    seen = set()
    for index, server in enumerate(document.servers, start=1):
        name = server.name.strip()
        if not name:
            errors.append(f"Server {index} needs a short name.")
            continue
        lowered = name.lower()
        if lowered in seen:
            errors.append(f"Server name {name} is used more than once.")
        seen.add(lowered)
        if not server.values.get("host", "").strip():
            errors.append(f"Server {name} needs a host.")
    return errors


def render_document(document: ConfigDocument) -> str:
    errors = validate_document(document)
    if errors:
        raise ValueError("\n".join(errors))

    lines = [
        "; PowerCom server configuration file.",
        "; Generated by the PowerCom configuration builder.",
        "",
        "[server defaults]",
    ]
    _append_key_values(lines, document.defaults)
    _append_key_values(lines, document.feature_defaults)

    for server in document.servers:
        lines.extend(["", f"[server {server.name.strip()}]"])
        _append_key_values(lines, server.values)
        _append_key_values(lines, server.overrides)

    return "\n".join(lines).rstrip() + "\n"


def parse_document(path: str | Path) -> ConfigDocument:
    document = ConfigDocument()
    document.defaults.clear()
    document.feature_defaults.clear()
    current_section = None
    current_server = None

    for raw_line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line[0] in ";#":
            continue
        if line.startswith("[") and "]" in line:
            header = line[1:].split("]", 1)[0].strip()
            parts = header.split(None, 1)
            current_section = None
            current_server = None
            if len(parts) == 2 and parts[0].lower() == "server":
                if parts[1].lower() == "defaults":
                    current_section = "defaults"
                else:
                    current_section = "server"
                    current_server = ServerConfig(parts[1])
                    document.servers.append(current_server)
            continue
        if "=" not in line or current_section is None:
            continue
        key, value = line.split("=", 1)
        key = canonical_key(key)
        value = value.strip()
        if current_section == "defaults":
            if key in FEATURE_KEYS:
                document.feature_defaults[key] = value
            else:
                document.defaults[key] = value
        elif current_server is not None:
            if key in FEATURE_KEYS:
                current_server.overrides[key] = value
            else:
                current_server.values[key] = value

    document.defaults = {**DEFAULT_CORE, **document.defaults}
    document.feature_defaults = {**DEFAULT_FEATURES, **document.feature_defaults}
    return document


def save_document(document: ConfigDocument, path: str | Path) -> None:
    Path(path).write_text(render_document(document), encoding="utf-8")


def _append_key_values(lines: list[str], values: dict[str, str]) -> None:
    for key, value in values.items():
        value = str(value).strip()
        if value:
            lines.append(f"{display_key(key)}={value}")
