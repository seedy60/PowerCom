"""
Contains functions for sending notifications via ntfy, Prowl, MG Notify, Pushover, and desktop notifications.
"""

import subprocess
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from notifypy import Notify
import pyprowl
import ntfpy

appName = 'PowerCom'
mgNotifyURL = 'https://notify.mad-gamer.com/api/notify'
pushoverURL = 'https://api.pushover.net/1/messages.json'
VALID_PUSHOVER_PRIORITIES = {'-2', '-1', '0', '1', '2'}


class NotificationError(RuntimeError):
    """Raised when an external notification provider rejects a request."""


def _error_body(error: HTTPError) -> str:
    try:
        return error.read().decode('utf-8', errors='replace').strip()
    except Exception:
        return ''


def _format_http_error(provider: str, error: HTTPError) -> str:
    message = f'{provider} failed: HTTP {error.code} {error.reason}'
    body = _error_body(error)
    if body:
        message += f': {body[:500]}'
    return message


def _send_form_request(url: str, payload: dict[str, str], method: str) -> None:
    encoded = urlencode(payload)
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
        'User-Agent': appName,
    }
    if method == 'GET':
        request = Request(f'{url}?{encoded}', headers={'User-Agent': appName}, method='GET')
    else:
        request = Request(url, data=encoded.encode('utf-8'), headers=headers, method=method)
    with urlopen(request, timeout=10) as response:
        response.read()


def _normalize_optional_value(value: str | int | float | bool | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        value = '1' if value else '0'
    elif isinstance(value, float) and value.is_integer():
        value = str(int(value))
    else:
        value = str(value).strip()
    return value if value != '' else None


def _normalize_pushover_priority(priority: str | int | float | bool | None) -> str | None:
    value = _normalize_optional_value(priority)
    if value is None:
        return None
    if value not in VALID_PUSHOVER_PRIORITIES:
        allowed = ', '.join(sorted(VALID_PUSHOVER_PRIORITIES, key=int))
        raise NotificationError(f'Pushover priority must be one of {allowed}; got {value!r}.')
    return value


def _normalize_pushover_seconds(name: str, value: str | int | float | bool | None, minimum: int, maximum: int | None = None) -> str:
    normalized = _normalize_optional_value(value)
    if normalized is None:
        raise NotificationError(f'Pushover priority 2 requires {name}.')
    try:
        seconds = int(normalized)
    except ValueError as error:
        raise NotificationError(f'Pushover {name} must be a whole number of seconds; got {normalized!r}.') from error
    if seconds < minimum:
        raise NotificationError(f'Pushover {name} must be at least {minimum} seconds.')
    if maximum is not None and seconds > maximum:
        raise NotificationError(f'Pushover {name} must be at most {maximum} seconds.')
    return str(seconds)

# Patch notify_py PowerShell call to hide the window
if sys.platform == "win32":
    try:
        from notifypy.os_notification import windows
        _orig_run = windows.subprocess.run

        def _runNoWindow(*popenargs, **kwargs):
            if isinstance(popenargs[0], (list, tuple)) and popenargs[0] and "powershell" in popenargs[0][0].lower():
                kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
            return _orig_run(*popenargs, **kwargs)

        windows.subprocess.run = _runNoWindow
    except ImportError:
        pass

_systemNotifier = Notify(default_notification_application_name=appName)

def sendSystemNotification(title: str, message: str) -> None:
    _systemNotifier.title = title
    _systemNotifier.message = message
    _systemNotifier.send(block=False)

def sendProwlNotification(prowlKey: str, title: str, message: str) -> None:
    prowlObj = pyprowl.Prowl(prowlKey, appName=appName)
    prowlObj.notify(title, message)

def sendMGNotifyNotification(mgNotifyKey: str, message: str, title: str = appName) -> None:
    payload = {'key': mgNotifyKey.strip(), 'msg': message, 'title': title}
    errors = []
    for method in ('POST', 'GET'):
        try:
            _send_form_request(mgNotifyURL, payload, method)
            return
        except HTTPError as error:
            errors.append(_format_http_error('MG Notify', error))
            if method == 'POST' and error.code in {400, 404, 405, 415, 501}:
                continue
            raise NotificationError(errors[-1]) from error
        except URLError as error:
            raise NotificationError(f'MG Notify failed: {error.reason}') from error
    raise NotificationError('; fallback also failed: '.join(errors))

def sendPushoverNotification(
    pushoverUser: str,
    pushoverToken: str,
    title: str,
    message: str,
    device: str | None = None,
    sound: str | None = None,
    priority: str | int | float | bool | None = None,
    retry: str | int | float | bool | None = None,
    expire: str | int | float | bool | None = None,
) -> None:
    payload = {
        'token': pushoverToken,
        'user': pushoverUser,
        'title': title,
        'message': message,
    }
    if device:
        payload['device'] = device
    if sound:
        payload['sound'] = sound
    priorityValue = _normalize_pushover_priority(priority)
    if priorityValue is not None:
        payload['priority'] = priorityValue
    if priorityValue == '2':
        payload['retry'] = _normalize_pushover_seconds('retry', retry, minimum=30)
        payload['expire'] = _normalize_pushover_seconds('expire', expire, minimum=1, maximum=10800)
    request = Request(
        pushoverURL,
        data=urlencode(payload).encode('utf-8'),
        headers={'Content-Type': 'application/x-www-form-urlencoded', 'User-Agent': appName},
        method='POST',
    )
    try:
        with urlopen(request, timeout=10) as response:
            response.read()
    except HTTPError as error:
        raise NotificationError(_format_http_error('Pushover', error)) from error
    except URLError as error:
        raise NotificationError(f'Pushover failed: {error.reason}') from error

class ntfyNotifier:
    def __init__(self, serverURL: str, topic: str, user=None, password=None):
        self.serverURL   = serverURL
        self.ntfyServer = ntfpy.NTFYServer(serverURL)
        self.topic = topic
        self.user = user
        if self.user is not None:
            ntfyUser = ntfpy.NTFYUser(self.user, password)
            self.NTFYClient = ntfpy.NTFYClient(self.ntfyServer, self.topic, user=ntfyUser)
        else:
            self.NTFYClient = ntfpy.NTFYClient(self.ntfyServer, self.topic)

    def sendNotification(self, title, message):
        self.NTFYClient.send(message=message, title=title).text
