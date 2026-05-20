"""
Built-in PowerCom event handling.
"""


from collections.abc import Callable
from pathlib import Path
from .audio.manager import Manager as AudioManager
from .config import Config
from .fileRandomizer import getRandomLine
from .logger import getServerLogger
from .notifiers import (
    ntfyNotifier,
    sendMGNotifyNotification,
    sendPushoverNotification,
    sendProwlNotification,
    sendSystemNotification,
)
from .speech import Speech, list_backends, list_voices

_audioManager: AudioManager | None = None
_config: Config | None = None

serverCaches = {}
SOUNDS_ROOT = Path('sounds')
CONFIG_KEYS = {
    'log',
    'maxlogfiles',
    'maxlogsize',
    'mgnotify',
    'mgnotifykey',
    'nosound',
    'nospeak',
    'notifymessage',
    'notifyloginout',
    'ntfy',
    'ntfypassword',
    'ntfytopic',
    'ntfyurl',
    'ntfyuser',
    'playbacktype',
    'prowl',
    'prowlkey',
    'pushover',
    'pushoverdevice',
    'pushoverexpire',
    'pushoverpriority',
    'pushoverretry',
    'pushoversound',
    'pushovertoken',
    'pushoveruser',
    'soundpack',
    'sounds',
    'soundvolume',
    'speech',
    'speechdmodule',
    'speechengine',
    'speechinterrupt',
    'speechpitch',
    'speechrate',
    'speechvoice',
    'speechvolume',
    'systemnotify',
}


def _getAudioManager() -> AudioManager:
    global _audioManager
    if _audioManager is None:
        _audioManager = AudioManager('sounds')
    return _audioManager


def _getConfig() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config


def getSoundPath(soundPack: str, sound: str) -> str | None:
    soundDirectory = SOUNDS_ROOT / soundPack
    if not soundDirectory.is_dir():
        return None
    matches = sorted(
        path for path in soundDirectory.iterdir()
        if path.is_file() and path.stem.lower() == sound.lower()
    )
    if not matches:
        return None
    return (Path(soundPack) / matches[0].name).as_posix()


def _getRandomLineOrDefault(filePath: str, default: str) -> str:
    try:
        return getRandomLine(filePath)
    except (FileNotFoundError, ValueError, IndexError):
        return default

def getServerSpeaker(serverName: str):
    if serverName in serverCaches:
        if 'speaker' in serverCaches[serverName]: return serverCaches[serverName]['speaker']
    config = _getConfig()
    outputModule = config.get(serverName, 'speechdmodule')
    rate= config.get(serverName, 'speechrate')
    volume = config.get(serverName, 'speechvolume')
    voice = config.get(serverName, 'speechvoice')
    pitch = config.get(serverName, 'speechpitch')
    speakerType = config.get(serverName, 'speechengine')
    kwargs = {}
    if outputModule is not None: kwargs['outputModule'] = outputModule
    if rate is not None: kwargs['rate'] = rate
    if voice is not None: kwargs['voice'] = voice
    if volume is not None: kwargs['volume'] = volume
    if pitch is not None: kwargs['pitch'] = pitch
    speaker = Speech(speakerType if speakerType is not None else 'auto', **kwargs)
    if serverName not in serverCaches: serverCaches[serverName] = {'speaker': speaker}
    else: serverCaches[serverName]['speaker'] = speaker
    return speaker

def clearServerSpeaker(serverName: str) -> None:
    if serverName in serverCaches:
        serverCaches[serverName].pop('speaker', None)

def _serverNameForCommand(cmd) -> str:
    try:
        return cmd.curServer.shortname
    except Exception:
        raise ValueError("No current server is selected.")

def _currentSpeechBackend(serverName: str) -> str:
    return str(_getConfig().get(serverName, 'speechengine') or 'auto')

def _currentSpeechVoice(serverName: str) -> str:
    value = _getConfig().get(serverName, 'speechvoice')
    return 'auto' if value is None else str(value)

def _formatVoice(voice: dict[str, int | str]) -> str:
    label = str(voice.get('name', f"Voice {voice.get('index', '')}"))
    language = str(voice.get('language', '') or '')
    if language:
        label = f"{label} [{language}]"
    return f"{voice.get('index')}: {label}"

def _powercomShow(cmd, serverName: str) -> None:
    cmd.msg(
        f"PowerCom speech for {serverName}: "
        f"backend={_currentSpeechBackend(serverName)}, "
        f"voice={_currentSpeechVoice(serverName)}"
    )

def _powercomListBackends(cmd) -> None:
    rows = ["auto: Automatic (best available)"]
    rows.extend(f"{backend['id']}: {backend['name']}" for backend in list_backends())
    cmd.msg("\n".join(rows))

def _powercomListVoices(cmd, backendName: str) -> None:
    voices = list_voices(backendName)
    if not voices:
        cmd.msg(f"No voices were reported for backend {backendName}.")
        return
    cmd.msg("\n".join(_formatVoice(voice) for voice in voices))

def _setPowercomConfig(cmd, serverName: str, key: str, value: str) -> None:
    _getConfig().set(serverName, key, value)
    clearServerSpeaker(serverName)
    cmd.msg(f"{key} for {serverName} set to {value}.")

def do_powercom(cmd, line: str = "") -> None:
    """Manage PowerCom speech output.
    Usage:
        powercom
        powercom backends
        powercom backend [auto|backend_id]
        powercom voices [backend_id]
        powercom voice [auto|voice_index|voice_name]
        powercom test [text]
    """
    serverName = _serverNameForCommand(cmd)
    line = line.strip()
    if not line:
        _powercomShow(cmd, serverName)
        return

    subcommand, _, rest = line.partition(" ")
    subcommand = subcommand.lower()
    rest = rest.strip()
    if subcommand in ("show", "status"):
        _powercomShow(cmd, serverName)
    elif subcommand in ("backends", "backend-list"):
        _powercomListBackends(cmd)
    elif subcommand == "backend":
        if rest.lower() in ("list", "ls"):
            _powercomListBackends(cmd)
        elif rest:
            _setPowercomConfig(cmd, serverName, "speechEngine", rest)
        else:
            cmd.msg(f"speechEngine for {serverName} is {_currentSpeechBackend(serverName)}.")
    elif subcommand in ("voices", "voice-list"):
        _powercomListVoices(cmd, rest or _currentSpeechBackend(serverName))
    elif subcommand == "voice":
        if rest.lower() in ("list", "ls"):
            _powercomListVoices(cmd, _currentSpeechBackend(serverName))
        elif rest:
            value = "-1" if rest.lower() == "auto" else rest
            _setPowercomConfig(cmd, serverName, "speechVoice", value)
        else:
            cmd.msg(f"speechVoice for {serverName} is {_currentSpeechVoice(serverName)}.")
    elif subcommand == "test":
        text = rest or "PowerCom speech test."
        getServerSpeaker(serverName).both(text)
    else:
        raise ValueError(f"Unknown PowerCom subcommand: {subcommand}")

def help_powercom(cmd) -> None:
    cmd.msg(cmd._formatHelp(do_powercom.__doc__))

def getUserInfo(server, userid):
    """Gets user information from cache, otherwise attempts to get it from the server.

    Args:
        server (any): The  server that the user is beeing retreeved from, tipicly server.shortname.
        userid (any): The user id of the user beeing retreeved.
    """
    if server.shortname in serverCaches:
        if 'users' in serverCaches[server.shortname]:
            if userid in serverCaches[server.shortname]['users']: return serverCaches[server.shortname]['users'][userid]
    if userid not in server.users: return {}
    userName = server.users[userid].get('username') or ""
    nickname = server.users[userid].get('nickname') or ""
    admin = True if server.users[userid].usertype == '2' else False
    userInfo = {'userName': userName, 'nickname': nickname, 'admin': admin}
    if not server.shortname in serverCaches:
        serverCaches[server.shortname] = {'users': {userid: userInfo}}
    else:
        if  not 'users' in serverCaches[server.shortname]: serverCaches[server.shortname]['users'] = {userid: userInfo}
        else: serverCaches[server.shortname]['users'][userid] = userInfo
    return serverCaches[server.shortname]['users'][userid]

def prittifyName(userid, userinfo):
    name = ''
    if 'nickname' in userinfo: name = f'"{userinfo['nickname']}"'
    if 'userName' in userinfo:
        if name and name != userinfo['userName']: name += f' ({userinfo['userName']})'
        else: name = f'"{userinfo['userName']}"'
    if not name: name = f'User {userid}'
    return name

def prittifyEvent(server, event):
    output = f'{server.shortname} -'
    userid = None
    destuserid = None
    if     'userid' in event.parms:
        userid = event.parms.userid
    elif 'srcuserid' in event.parms:
        userid = event.parms.srcuserid
    elif 'kickerid' in event.parms:
        userid = event.parms.kickerid
    if 'destuserid' in event.parms:
        destuserid = event.parms.destuserid
        destUserinfo = getUserInfo(server, destuserid)
        destPrittyName = prittifyName(destuserid, destUserinfo)
    if userid is not None and userid != '0':
        userinfo = getUserInfo(server, userid)
        prittyName = prittifyName(userid, userinfo)
        userTypeString = 'admin' if userinfo['admin'] == True else 'user'
    match event.event:
        case 'loggedin':
            loginMessage = _getRandomLineOrDefault('text/logins.txt', 'logged in')
            output += f' {userTypeString} {prittyName} {loginMessage}'
        case 'loggedout':
            logOutMessage = _getRandomLineOrDefault('text/logouts.txt', 'logged out')
            output += f' {userTypeString} {prittyName} {logOutMessage}'
        case 'adduser':
            channelName= server.channelname(event.parms.chanid)
            channelName = channelName if 'the root channel' not in channelName.lower() else 'root'
            output += f' {userTypeString} {prittyName} joined channel {channelName}'
        case 'removeuser':
            channelName= server.channelname(event.parms.chanid)
            channelName = channelName if 'the root channel' not in channelName.lower() else 'root'
            output += f' {userTypeString} {prittyName} left channel {channelName}'
        case 'updateuser':
            statusMSG = event.parms.statusmsg if 'statusmsg' in event.parms else ''
            nickname = event.parms.nickname
            statusMode = event.parms.statusmode
            if statusMode in ('0', '4096', '256'):
                statusMode = 'available'
            elif statusMode in ('1', '4097', '257'):
                statusMode = 'away'
            elif statusMode in ('2', '4098', '258'):
                statusMode = 'questioning'
            elif statusMode in ('6144', '2048', '2304'):
                statusMode = 'streaming media'
            else:
                statusMode = f'unknown status{statusMode}'
            output += f'{prittyName} -'
            if userinfo.get('statusmode', 0) != event.parms.statusmode:
                output += f' Status set to {statusMode}.'
            if userinfo.get('statusmsg', '') != statusMSG:
                output += f' Status message set to {event.parms.statusmsg}.'
            if userinfo['nickname'] != nickname:
                output += f' Nickname set to {nickname}.'
        case 'addfile':         
            fileName = event.parms.filename if 'filename' in event.parms else 'unknown file'
            owner = event.parms.owner
            output += f'file {fileName} uploaded to {server.channelname(event.parms.chanid)} by {owner}'
        case 'removefile':
            fileName = event.parms.filename if 'filename' in event.parms else 'unknown file'
            output += f'file {fileName} deleted from {server.channelname(event.parms.chanid)}'
        case 'fileaccepted':
            output += f'File transfer for {event.parms.filename} initiated'
        case 'filecompleted':
            output += f'File transfer for {event.parms.filename} complete'
        case 'addchannel':
            channelname = server.channelname(event.parms.chanid)
            output += f'channel {channelname} created'
        case 'removechannel':
            channelName = serverCaches[server.shortname]['channels'][event.parms.chanid] if event.parms.chanid in serverCaches[server.shortname]['channels'] else f'with id {event.parms.chanid}'
            output += f'channel {channelName} deleted'
        case 'updatechannel':
            channelname = server.channelname(event.parms.chanid)
            output += f'channel {channelname} updated'
        case 'kicked':
            if userid == '0':
                if 'chanid' not in event.parms: output += f'Kicked from server: multiple logins disallowed'
                else: output += f'Kicked from channel with id {event.parms.chanid}: channel deleted'
                return output
            if 'chanid' in event.parms: output += f'Kicked from channel {server.channelname(event.parms.chanid)} by {prittyName}'
            else: output += f'kicked from server by {prittyName}'
        case 'messagedeliver':
            if event.parms.type == '1':
                output += f'Private message from {prittyName}'
                if destuserid is not None and destuserid != server.me.userid: output += f' to {destPrittyName}'
                output += f': {event.parms.content}'
            elif event.parms.type == '2':
                channelName = server.channelname(event.parms.chanid)
                output += f'channel Message from {prittyName}'
                if server.me.chanid != event.parms.chanid: output += f' to {channelName}'
                output += f': {event.parms.content}'
            if event.parms.type == '3':
                output += f'Broadcast message from {prittyName}: {event.parms.content}'
            elif event.parms.type =='4':
                output += F'Custom message from {prittyName}: {event.parms.content}'
        case 'serverupdate':
            output += 'server updated'
        case _: return output + event.event
    return output

class PowerComEventHandler:
    def __init__(self, server, event, runCommand):
        self.server = server
        self.event = event
        self.runCommand = runCommand
        # Do not process events from a server that is not logged in
        if not self.server.loggedIn: return
        # do not process events if they are from hour user.
        if 'userid' in self.event.parms and self.server.me.userid == self.event.parms.userid: return
        # ensure caches will be initialized after login.
        if self.event.event == 'ok':
            self.initializeCache()
        # do not process events that are caused by commands. This ensures that events like kicked and banned actualy work properly.
        if self.event.event in ('ok', 'left', 'joined', 'error'): return
        # Do not process spammy typing notifications.
        if self.event.event =='messagedeliver':
            if self.event.parms.type == '4' and self.event.parms.content.startswith('typing'): return
        self.prittyEvent = prittifyEvent(self.server, self.event)
        self.playSound()    
        self.speak(self.prittyEvent)
        if self.event.event in ('loggedin', 'loggedout', 'messagedeliver'): self.notify()
        self.logEvent()
        self.handleCache()

    def speak(self, message):
        config = _getConfig()
        doSpeak = config.get(self.server.shortname, 'speech')
        if doSpeak != True and doSpeak is not None: return
        noSpeak = config.get(self.server.shortname, 'nospeak')
        interrupt = config.get(self.server.shortname, 'speechinterrupt')
        if interrupt is None: interrupt = True
        if noSpeak:
            if self.event.event in noSpeak.split('+'): return
        speaker = getServerSpeaker(self.server.shortname)
        speaker.both(message, interrupt = interrupt)

    def notify(self):
        config = _getConfig()
        if self.event.event in ('loggedin', 'loggedout') and config.get(self.server.shortname, 'notifyloginout') == False: return
        elif self.event.event == 'messagedeliver' and config.get(self.server.shortname, 'notifymessage') == False: return
        title = self.makeTitle()
        if config.get(self.server.shortname, 'systemnotify') == True:
            self.runNotifier('System notification', lambda: sendSystemNotification(title, self.prittyEvent))
        if config.get(self.server.shortname, 'ntfy') == True:
            self.runNotifier('ntfy', lambda: self.ntfyNotify(title, self.prittyEvent))
        if config.get(self.server.shortname, 'prowl') == True:
            self.runNotifier('Prowl', lambda: self.prowlNotify(title, self.prittyEvent))
        if config.get(self.server.shortname, 'mgnotify') == True:
            self.runNotifier('MG Notify', lambda: self.mgNotify(title, self.prittyEvent))
        if config.get(self.server.shortname, 'pushover') == True:
            self.runNotifier('Pushover', lambda: self.pushoverNotify(title, self.prittyEvent))

    def runNotifier(self, provider: str, callback: Callable[[], None]) -> None:
        try:
            callback()
        except Exception as error:
            detail = str(error)
            if detail.lower().startswith(provider.lower()):
                self.server.errorFromEvent(detail)
            else:
                self.server.errorFromEvent(f'{provider} notification failed: {detail}')

    def makeTitle(self):
        titles = {
            'loggedin': 'User logged in.',
            'loggedout': 'User logged out.',
            'kicked': 'Kicked from server' if 'chanid' not in self.event.parms else 'kicked from channel'
        }
        if self.event.event == 'messagedeliver':
            match self.event.parms.type:
                case '1': return 'Private message received'
                case '2': return 'Channel message received'
                case '3': return 'Broadcast message received'
                case '4': return 'custom message received'
        else: return titles[self.event.event] if self.event.event in titles else 'unknown event'

    def ntfyNotify(self, title: str, message: str):
        config = _getConfig()
        serverUrl = config.get(self.server.shortname, 'ntfyurl')
        if not serverUrl: serverUrl = 'https://ntfy.sh'
        topic = config.get(self.server.shortname, 'ntfytopic')
        if not topic: raise NameError(f'ntfytopic not found in server config for {self.server.shortname}')
        user = config.get(self.server.shortname, 'ntfyuser')
        password = config.get(self.server.shortname, 'ntfypassword')
        notifyer = ntfyNotifier(serverUrl, topic, user, password)
        notifyer.sendNotification(title, message)

    def prowlNotify(self, title: str, message: str):
        prowlKey = _getConfig().get(self.server.shortname, 'prowlkey')
        if not prowlKey: raise NameError(f'prowlkey not found in config for {self.server.shortname}')
        sendProwlNotification(prowlKey, title, message)

    def mgNotify(self, title: str, message: str):
        mgNotifyKey = _getConfig().getRaw(self.server.shortname, 'mgnotifykey')
        if not mgNotifyKey: raise NameError(f'mgnotifykey not found in config for {self.server.shortname}')
        sendMGNotifyNotification(mgNotifyKey, message, title)

    def pushoverNotify(self, title: str, message: str):
        config = _getConfig()
        pushoverUser = config.getRaw(self.server.shortname, 'pushoveruser')
        pushoverToken = config.getRaw(self.server.shortname, 'pushovertoken')
        if not pushoverUser: raise NameError(f'pushoveruser not found in config for {self.server.shortname}')
        if not pushoverToken: raise NameError(f'pushovertoken not found in config for {self.server.shortname}')
        sendPushoverNotification(
            pushoverUser,
            pushoverToken,
            title,
            message,
            device=config.getRaw(self.server.shortname, 'pushoverdevice'),
            sound=config.getRaw(self.server.shortname, 'pushoversound'),
            priority=config.getRaw(self.server.shortname, 'pushoverpriority'),
            retry=config.getRaw(self.server.shortname, 'pushoverretry'),
            expire=config.getRaw(self.server.shortname, 'pushoverexpire'),
        )

    def logEvent(self):
        config = _getConfig()
        log = config.get(self.server.shortname, 'log')
        if log != True and log is not None: return
        maxSize = config.get(self.server.shortname, 'maxlogsize') 
        maxSize = maxSize * 1024 * 1024 if maxSize is not None else 4 * 1024 * 1024
        maxFiles = config.get(self.server.shortname, 'maxlogfiles')
        if maxFiles is None: maxFiles = 5
        logger = getServerLogger(self.server.shortname, 'logs', maxSize, maxFiles)
        logger.info(self.prittyEvent)

    def playSound(self):
        config = _getConfig()
        playSounds = config.get(self.server.shortname, 'sounds')
        if playSounds != True and  playSounds is not  None: return
        noSound = config.get(self.server.shortname, 'nosound')
        if noSound is not None:
            if self.event.event in noSound.split('+'): return
        soundPack = config.get(self.server.shortname, 'soundpack')
        if soundPack is None: soundPack = 'default'
        sounds = {
            'loggedin': 'in',
            'loggedout': 'out',
            'adduser': 'join',
            'removeuser': 'leave',
            'updateuser': 'status',
            'addchannel': 'channelcreate',
            'removechannel': 'channelremove',
            'updatechannel': 'channelupdate',
            'addfile': 'fileadd',
            'removefile': 'fileremove',
            'fileaccepted': 'filetransfer',
            'filecompleted': 'filetransfercomplete',
            'serverupdate': 'serverupdate                                       ',
            'kicked': 'kicked'
        }
        if self.event.event == 'messagedeliver':
            if self.event.parms.type == '1': sound = 'user'
            elif self.event.parms.type == '2': sound = 'channel'
            elif self.event.parms.type == '3': sound = 'broadcast'
            if self.event.parms.type == '4': sound = 'message'
        else:
            if self.event.event in sounds: sound = sounds[self.event.event]
            else: return
        fullSoundPath = getSoundPath(soundPack, sound)
        if fullSoundPath is None: return
        playerType = config.get(self.server.shortname, 'playbacktype')
        if playerType is None: playerType = 'overlapping'
        volume = config.get(self.server.shortname, 'soundVolume')
        if volume is  None: volume = 100
        match playerType.lower():
            case 'onebyone':
                _getAudioManager().play(fullSoundPath, 2, volume=volume)
            case 'interrupting':
                _getAudioManager().play(fullSoundPath, 1, volume=volume)
            case 'overlapping':
                _getAudioManager().play(fullSoundPath, 0, volume=volume)
            case _:
                raise ValueError(f'Failed to play sound:\n unsupported playback type {playerType}')

    def handleCache(self):
        if self.event.event == 'loggedout': 
            if not self.server.shortname in serverCaches: return
            if not 'users' in serverCaches[self.server.shortname]: return
            if not self.event.parms.userid in serverCaches[self.server.shortname]['users']: return
            serverCaches[self.server.shortname]['users'].pop(self.event.parms.userid)
        if self.event.event == 'loggedin':
            userName = self.server.users[self.event.parms.userid].get('username') or ""
            nickname = self.server.users[self.event.parms.userid].get('nickname') or ""
            admin = True if self.server.users[self.event.parms.userid].usertype == '2' else False
            userInfo = {'userName': userName, 'nickname': nickname, 'admin': admin}
            if not self.server.shortname in serverCaches:         serverCaches[self.server.shortname] = {'users': {self.event.parms.userid: userInfo}}
            elif  not 'users' in serverCaches[self.server.shortname]: serverCaches[self.server.shortname]['users'] = {self.event.parms.userid: userInfo}
            else: serverCaches[self.server.shortname]['users'][self.event.parms.userid] = userInfo
        if self.event.event == 'updateuser':
            serverCaches[self.server.shortname]['users'][self.event.parms.userid]['nickname'] = self.event.parms.nickname
            serverCaches[self.server.shortname]['users'][self.event.parms.userid]['statusmode'] = self.event.parms.statusmode
            serverCaches[self.server.shortname]['users'][self.event.parms.userid]['statusmsg'] = self.event.parms.statusmsg
        if self.event.event == 'addchannel':
            channelName = self.server.channelname(self.event.parms.chanid)
            if not self.server.shortname in serverCaches:         serverCaches[self.server.shortname] = {'channels':{self.event.parms.chanid: channelName}}
            elif  not 'channels' in serverCaches[self.server.shortname]: serverCaches[self.server.shortname]['channels'] = {self.event.parms.chanid: channelName}
        if self.event.event == 'updatechannel':
            channelName = self.server.channelname(self.event.parms.chanid)
            if not self.server.shortname in serverCaches:         serverCaches[self.server.shortname] = {'channels':{self.event.parms.chanid: channelName}}
            elif  not 'channels' in serverCaches[self.server.shortname]: serverCaches[self.server.shortname]['channels'] = {self.event.parms.chanid: channelName}
            else: serverCaches[self.server.shortname]['channels'][self.event.parms.chanid] = channelName

    def initializeCache(self):
        if self.server.shortname  not in serverCaches: serverCaches[self.server.shortname] = {'users': {}, 'channels': {}}
        print('ok')
        for u in self.server.users:
            u = self.server.users[u]
            userInfo = {'username': u['username'], 'usertype': u['usertype']}
            userInfo['nickname'] = prittifyName(u['userid'], userInfo)
            serverCaches[u['userid']] = userInfo
        for c in self.server.channels:
            c = self.server.channels[c]
            serverCaches[self.server.shortname]['channels'][c['chanid']] = self.server.channelname(c['chanid'])

def apply(server, parmline, runCommand) -> None:
    PowerComEventHandler(server, parmline, runCommand)
