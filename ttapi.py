"""TeamTalk server connection manager.

Author: Doug Lee
Credits to Chris Nestrud and Simon Jaeger for some ideas and a bit of code.

Copyright (C) 2011-2026- Doug Lee

This program is free software: you can redistribute it and/or modify it
under the terms of the GNU General Public License as published by the
Free Software Foundation, either version 3 of the License, or (at your
option) any later version.

This program is distributed in the hope that it will be useful, but
WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License
for more details.

You should have received a copy of the GNU General Public License along
with this program. If not, see <http://www.gnu.org/licenses/>.

"""

import os, sys, re, socket, ssl, time, shutil
import urllib.request, urllib.parse
import threading
from mplib.mycmd import safePrint
from mplib.progressReporter import ProgressReporter
from mplib.attrdict import AttrDict
from mplib.dglutils import platform_string
from parmline import ParmLine, TTParms, KeywordParm, IntParm, StringParm, ListParm
from conf import conf
import ttflags

# ToDo: There might be a better place for this function.
def xmlToDict(xml):
    """Return a one-level dict of parameter/value pairs from the given XML string.
    This is meant only for shallow XML structures where there are no repeated parameter names and the
    container(s) of all of them are not ambiguous.
    Technically, this also requires that structures like <blah>...</blah> not occur inside any data field.
    """
    parts = [["xml", xml]]
    d = {}
    while len(parts):
        tag,val = parts.pop(0)
        if val.startswith("<"):
            # Dismiss the container tag name here.
            parts.extend(re.findall(r'<([^<>]+?)>(.*?)</\1>', val))
        else:
            d[tag] = val
    return d

class ServerState:
    """Connection states for a server.
    Usage:
        s = ServerState([initialState]) # defaults to "disconnected"
        s("connected")  # change
        s()  # get current state
    index is also public but should not be set directly.
    """
    states = [
        "disconnected",  # No connection exists.
        "connecting",  # Connection being made.
        "connected",  # Connected and welcome message received (also after logout).
        "loggingIn",  # Connected, login request sent.
        "loginError",  # Connected, login tried but rejected.
        "loggingOut",  # Logged in, logout request sent.
        "loggedIn"   # Login sequence completed.
    ]

    def __init__(self, initialState="disconnected"):
        self(initialState)
        # Sets self.index.

    def __call__(self, newState=None):
        if newState is not None:
            self.index = self.states.index(newState)
        return self.states[self.index]


class TeamTalkServerConnection:
    """Objects in this class represent connections to a TeamTalk
    server. Calling connect() on one of these objects will
    either create a connection or raise an IOError (specifically
    a socket.error or an IOError with a homegrown message)
    exception. When the connection is broken, either by a
    disconnect() call or by a connection failure, the object
    should be abandoned, and any new connection to the server
    should be made via a new object. The object, on successful
    connection, will internally manage keep-alive pinging,
    sufficient "welcome" line parsing to obtain the "usertimeout"
    value (for figuring out ping frequency), and the passing
    of incoming text lines (events) back to the object's creator
    via a callback.

    The creator of an object in this class is notified of important
    events via a callback function passed to __init__(). The callback
    function is called with the following for the possible events:

        "_connected_": Connection established.
        string of arbitrary length: An inbound text line, with line ending.
        "_disconnected_": Connection lost or ended.

    This class spawns the following threads for each object:
        - watcher() watches for and processes all inbound text until the connection ends.
        - pinger manages pinging when the connection is active.
    Call send() with raw lines (without line endings) to send commands
    to the server. To find out why the connection ended, examine the
    disconnectReason string. The welcomeParms AttrDict contains the
    parameters from the server's "welcome" line. For convenience,
    userid contains the connection's TeamTalk userid as a string.
    usertimeout is the effective usertimeout value (from the "welcome"
    line, but a "serverupdate" line could change it).
    """
    SSLContext = ssl.SSLContext()
    SSLContext.verify_mode = ssl.CERT_NONE
    SSLContext.check_hostname = False

    def __init__(self, parent, shortname, host, port=None, encrypted=False, callback=None):
        """Create a TeamTalk server connection object. Host and port define
        the connection endpoint. shortname is used to refer to the
        connection and in the naming of the threads created for this
        connection. callback is called to handle events.
        Parent is provided for convenience in debugging.
        If self.stop is True, do not retry connections to this endpoint.
        """
        self.raw = False
        self.stop = False
        self.parent = parent
        self.shortname = shortname
        self.host = host
        self.port = port
        if not self.port: self.port = 10333
        self.encrypted = encrypted
        self.state = "starting"
        if not callback:
            callback = self.simpleCallback
        self.callback = callback
        self.shuttingDown = False
        self.sock = None
        self.sockfile = None
        self.welcomeParms = None
        self.systemId = None
        self.userid = None
        self.usertimeout = None
        self.disconnectReason = ""
        self.threads = {}
        self.curid = None

    def dup(self):
        """Return a duplicate of this object that works in raw mode, for file transfers.
        """
        dup = TeamTalkServerConnection(self.parent, self.shortname, self.host, self.port, self.encrypted, None)
        dup.raw = True
        dup.callback = None
        return dup

    def __enter__(self):
        """For file transfer dups.
        """
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """For file transfer dups.
        """
        self.disconnect()
        self.terminate()

    def __del__(self):
        """Called when this object is garbage-collected.
        """
        self.terminate()

    def terminate(self):
        """Call to destroy this connection object.
        """
        self.shuttingDown = True

    def notifyCaller(self, msg):
        """Send msg (str or int) to the caller.
        """
        if self.callback:
            try:
                self.callback(msg)
            except Exception as e:
                self.parent.errorFromEvent(f"notifyCaller: {str(e)}")

    def newThread(self, target):
        """Start a new thread for this server connection.
        """
        th = threading.Thread(target = target)
        th.daemon = True
        th.name = self.shortname +"_" +target.__name__ +"_" +th.name
        self.threads[target.__name__] = th
        th.start()
        return th

    def threadEnding(self):
        """Returns True if this method's caller's thread is scheduled to end.
        """
        return self.shuttingDown or self.stop

    def threadName(self, which):
        """Return the full name of the thread based on its local name,
        e.g., "pinger."
        """
        return self.threads[which].name

    def simpleCallback(self, cargo):
        """A simple example of a callback, and the default if none is
        provided at object creation time. Simply prints lines received
        and indicates non-line events in a user-friendly manner.
        """
        if type(cargo) is str:
            safePrint(f"{self.shortname}: {cargo.rstrip()}")
            return
        # int.
        safePrint("*** %s %s" % (
            self.shortname,
            ["disconnected", "connected", "welcome received"][cargo]
        ))

    def connect(self):
        """Connect to the server. Call only once per object.
        Raises an IOError on failure to connect.
        This may be a socket.error or a homegrown-message IOError
        indicating a problem with the start of the TeamTalk
        client-server protocol.
        This call also does the following:
            - Signals connection to server.
            - Accepts and parses the "welcome" server message.
            - Passes the welcome message back to the caller.
            - Sets self.encrypted to reflect actual connection type.
            - Gets the usertimeout for determining ping frequency.
            - Collects other welcome-line parameters into self.welcomeParms.
            - Starts the pinger thread for this server.
            - Signals disconnection on error during all that.
        If this object is being created via the dup method, self.raw will be True and no threads will be created. This is for use with file transfers.
        """
        self.state = "connecting"
        try:
            self.sock = socket.create_connection((self.host, int(self.port)), 10)
        except Exception:
            self.disconnect(noCallback=True)
            raise
        # The above line may raise a socket.error.
        # If not, signal connection.
        self.state = "notifyConnect"
        self.notifyCaller('_connected_ ipaddr="{0}" tcpport={1}'.format(*self.sock.getpeername()))
        self.state = "welcomeWait"
        welcomeLine = None
        # If we know this should be encrypted, still wait briefly for a non-encrypted Welcome line,
        # then send the SSL handshake if it didn't arrive. This avoids rapid connect/disconnect cycles
        # when a server downgrades to plain text.
        # If we expect a plain-text connection, wait a bit for the Welcome, then change mind.
        self.sock.settimeout(1.5 if self.encrypted else 7)
        self.sockfile = self.sock.makefile("r", encoding="utf-8")
        # Get the welcome line, plain or encrypted.
        try:
            welcomeLine = self.sockfile.readline()
        except socket.timeout:
            # Assume SSL now.
            welcomeLine = None
            try:
                ssock = self.SSLContext.wrap_socket(self.sock, server_hostname=self.host)
            except ssl.SSLError as ssle:
                # SSL negotiation failed.
                self.disconnect(reason=str(ssle))
                raise IOError("Handshake failed")
            # SSL established, welcome line should be next.
            self.encrypted = True
            self.sock = ssock
            self.sockfile = self.sock.makefile("r", encoding="utf-8")
        self.sock.settimeout(20)
        try:
            # If the welcome line is text, it came from a plain link; None means SSL.
            if welcomeLine is None:
                welcomeLine = self.sockfile.readline()
            else:
                self.encrypted = False
            self.state = "notifyWelcome"
            self.systemId = ""
            welcomeParts = welcomeLine.split(None, 1)
            if welcomeParts:
                self.systemId = welcomeParts.pop(0)
            if ("userid=" in welcomeLine and "usertimeout=" in welcomeLine
            and self.systemId.lower() in ["teamtalk", "welcome"]):
                # Historic: Use "welcome" for the event keyword in case old plugin code expects this.
                welcomeLine = "welcome " +welcomeParts[0]
            else:
                # Non TeamTalk service, such as ssh.
                self.disconnect(reason=f"Unexpected intro line: {welcomeLine}", stop=True)
                raise IOError("Teamtalk systemId line expected, got '%s' instead" % (
                    welcomeLine
                ))
            self.notifyCaller(welcomeLine)
            welcomeLine = ParmLine(welcomeLine)
            self.welcomeParms = welcomeLine.parms
            self.userid = welcomeLine.parms.userid
            self.usertimeout = int(welcomeLine.parms.usertimeout)
            self.protocol = welcomeLine.parms.protocol
            if not self.raw:
                self.state = "makeThreads"
                self.newThread(self.watcher)
                self.noPing = False
                self.eventPingReset = threading.Event()
                self.newThread(self.pinger)
            self.state = "connected"
            # No timeouts after connect so packets don't split up.
            self.sock.settimeout(None)
        except Exception as e:
            self.state = "disconnecting"
            self.disconnect()
            raise

    def disconnect(self, reason="", noCallback=False, stop=False):
        """Disconnect and send the corresponding callback signal (unless noCallback is True).
        Does nothing if there is no connection established.
        This is called internally on error and can also be called by
        this object's creator. reason, if passed, is placed in the
        disconnectReason instance variable for examination by the
        object's creator after a disconnect.
        If stop is True, sets self.stop for the parent to notice.
        """
        self.disconnectReason = reason
        # Logical or, so a later stop=False call won't cancel this signal.
        self.stop |= stop
        if not noCallback:
            self.notifyCaller("_disconnected_")
        try: self.sock.shutdown(socket.SHUT_RDWR)
        except Exception: pass
        try: self.sock.close()
        except Exception: pass
        self.callback = None
        self.shuttingDown = True
        # Force end of ping wait and exit of that thread.
        self.noPing = True
        try: self.eventPingReset.set()
        except AttributeError: pass
        self.state = "disconnected"

    def pinger(self):
        """Ping the server as needed.
        This runs in its own thread.
        """
        started = False
        while not self.threadEnding():
            pingtime = float(self.usertimeout)
            # 0.3 works for timeout=1, which stock tt clients can't handle!
            # As of Jan 8 2024 at least, no known client, even TTCom at ping time 0.1, can survive usertimeout=0.
            if pingtime <= 15: pingtime = max(0.3, pingtime*0.25)
            elif pingtime <= 30: pingtime *= 0.3125
            elif pingtime <= 60: pingtime *= 0.5
            elif pingtime <= 90: pingtime *= 0.625
            else: pingtime *= 0.75
            # If we wait pingtime seconds for the first ping, a manual connect without login will often time out usertimeout seconds later.
            # Pinging immediately, on the other hand, can cause a manual login command on an SSL-encrypted server to fail.
            # This establishes a happy medium.
            if not started:
                pingtime = min(5.0, pingtime)
            # Let a usertimeout change trigger an immediate ping in case it gets shorter.
            if self.eventPingReset.wait(pingtime):
                self.eventPingReset.clear()
                if self.noPing:
                    self.noPing = False
                    continue
            started = True
            try: self.sock.send(b"ping\r\n")
            except socket.error as e:
                self.disconnect(f"Error during ping: {e!s}")
                return

    def _isConnected(self):
        """Returns True if this stream appears to be connected.
        There might be a better way to write this.
        """
        if not self.sock or not self.sockfile: return False
        fileno = None
        try: fileno = self.sock.fileno()
        except Exception: pass
        return (fileno is not None)

    def watcher(self):
        """Handles all inbound text.
        Eats pongs that answer pings sent by this object.
        Runs as its own thread.
        """
        err = None
        try:
            for line in self.sockfile:
                if not line:
                    # This probably won't happen; EOF should end the loop.
                    self.disconnect("EOF encountered during read")
                    return
                if line.startswith("teamtalk "):
                    # TeamTalk 5 protocol starts with this instead of welcome.
                    line = "welcome " +line[9:]
                if self.threadEnding():
                    self.disconnect("Shutting down")
                    return
                ll = line.rstrip().lower()
                if ll.startswith("begin id="):
                    self.curid = ll.split("=")[1]
                elif ll.startswith("end id="):
                    self.curid = None
                elif not self.curid and ll == "pong":
                    # Pongs sent as part of a user command should be in an id block.
                    continue
                self.notifyCaller(line)
        except IOError as e:
            err = e
        # Connection failure by error or just end of stream.
        if err:
            self.disconnect(f"Error during read: {err!s}")
        else:
            self.disconnect("EOF during read")

    def send(self, line):
        """Send a command to this server.
        line is a plain text line without line ending.
        Returns True on success and False on error.
        disconnect() is called on an IOError.
        """
        line = str(line).rstrip() +"\r\n"
        bline = line.encode("utf-8")
        try:
            self.sock.send(bline)
        except (AttributeError, IOError):
            # AttributeError probably means self.sock is None.
            self.disconnect("Error during send")
            return False
        return True


class TeamtalkServer:
    """Each object in this class represents a single TeamTalk server.
    send() and sendWithWait() are used to send commands to the server,
    and processLine() handles incoming lines from the server.
    processLine also dispatches incoming events (each line is an
    event) to the various event_*() methods in this class.
    """

    def _getState(self): return self._state()
    def _setState(self, val): self._state(val)
    state = property(_getState, _setState, None, "Current connection state")

    def __init__(self, host, tcpport=10333, shortname="", parms=None):
        if parms is None: parms = {}
        self._state = ServerState()
        self.conn = None
        self.ev_loggedIn = threading.Event()
        self.ev_loggedOut = threading.Event()
        self.ev_idblockDone = threading.Event()
        self.logintime = None
        self.manualCM = False
        self.lastError = None
        self.curID = 0
        self.waitID = 0
        self.maxID = 127
        self._collecting = 0
        self._outputCollection = []
        self.host = host
        self.tcpport = tcpport
        self.systemId = None
        self.encrypted = False
        if not shortname: shortname = host
        self.shortname = shortname
        self.autoLogin = 0
        # This platform includes Windows info, WSL, Cygwin, etc.
        parms["clientname"] = f"PowerCom ({platform_string()})"
        parms["version"] = conf.version
        parms.setdefault("udpport", self.tcpport)
        # Teamtalk 4.3 clients pop up an error like this if there is
        # no nickname= parameter on the login command line:
        #    An error occurred while perform a requested command:
        #    User not found
        #    OK  
        # So we force a null nickname if none is provided.
        # [DGL, 2012-01-02]
        parms.setdefault("nickname", "")
        self.loginParms = parms
        self.disconnect()

    def clear(self):
        """Clear this object (on init or disconnect).
        """
        self.conn = None
        self.waitID = 0
        self.curID = 0
        self.ev_loggedIn.clear()
        self.ev_loggedOut.clear()
        self.state = "disconnected"
        self.systemId = None
        self.info = AttrDict()
        self.channels = dict()
        self.users = dict()
        self.files = dict()
        # This becomes the bearware.dk return XML block from a web login if this server uses that.
        self.bwxml = None
        self.me = None

    @property
    def loggedIn(self):
        """True if we are logged into this server.
        Specifically, this is True from when the login command's "ok" response arrives until logout or disconnect.
        This property is a convenience for triggers to use in order to avoid firing during the login process.
        """
        return self.state == "loggedIn"

    def adjustedUserRights(self, rights):
        """The user rights for a user, adjusted for server version.
        Servers older than 5.15 get user-message and channel-message rights added here.
        """
        try: ver = self.info.version
        except Exception:
            return rights
        if self.comparableVersion(ver) < self.comparableVersion("5.15"):
            isStr = isinstance(rights, str)
            if isStr: rights = int(rights)
            rights |= 0x00C00000
            if isStr: rights = str(rights)
        return rights

    @staticmethod
    def comparableVersion(ver):
        """Return a string that would sort this version correctly in chronological order.
        Assumes major.minor.branch.build format but can add branch and build if not given.
        Also assumes a maximum length per part of five digits.
        """
        segs = ver.split(".")
        segs = [int(s) for s in segs]
        while len(segs) < 4: segs.append(0)
        ver = ".".join([f"{s:05d}" for s in segs])
        return ver

    def disconnect(self):
        """Disconnect from server and clean up.
        """
        if self.conn:
            self.conn.disconnect()
            if self.conn: self.conn.terminate()
        self.clear()

    def terminate(self):
        """Called to destroy this object.
        """
        self.autoLogin = 0
        self.disconnect()

    def connect(self, retry=False):
        """Connect to the server.
        Returns True if there is a connection on exit and False if not.
        If retry is True, tries until successful.
        The pause between retries is 10 seconds.
        """
        conn = self.conn
        while True:
            if conn:
                if conn.threadEnding():
                    return False
                return True
            conn = TeamTalkServerConnection(self,
                self.shortname, self.host, self.tcpport, self.encrypted,
                self.processLine
            )
            self.conn = conn
            self.state = "connecting"
            try:
                conn.connect()
            except IOError:
                self.state = "disconnected"
                if conn.stop and not self.manualCM:
                    self.manualCM = True
                    what_happened = "Stopping connection attempts"
                    if conn.disconnectReason:
                        what_happened += f": {conn.disconnectReason}"
                    self.errorFromEvent(what_happened)
                conn = None
                self.conn = None
                if retry and (self.autoLogin and not self.manualCM):
                    time.sleep(10)
                    continue
                return False
            self.state = "connected"
            return True

    def waitOn(self, event, timeout=5.0):
        """Wait on an event with the given timeout.
        Return True if the vent fired and False if not.
        Used by threads to allow shutdown.
        """
        event.wait(timeout)
        return event.is_set()

    def login(self, background=False):
        """Log into the server.
        If background is True, tries connecting until successful,
        and does so in the background, returning True immediately.
        Does not retry actual login though.
        If background is False, returns True if logged in on exit and False if not.
        """
        # This is the protocol level sent during login. Update as things evolve.
        prot = "5.14"
        if background:
            th = threading.Thread(target=self.login)
            th.daemon = True
            th.start()
            return True
        retry = True
        if not self.connect(retry):
            self.errorFromEvent("Connect failed, login aborted")
            return False
        if self.ev_loggedIn.is_set(): return True
        # Don't allow extra stuff from the config file entry to get sent as part of a login command.
        lp = self.loginParms.copy()
        keeps = set(["nickname", "username", "password", "clientname", "version", "id"])
        dels = set(list(lp.keys())) - keeps
        for k in dels:
            if k in lp: del lp[k]
        lp["protocol"] = prot
        if not self._adjustForBearwareLogin(lp):
            # Any relevant messages should be printed by that call.
            return False
        self.state = "loggingIn"
        try:
            self.send(ParmLine("login", lp))
        except IOError:
            # Connection failure.
            self.errorFromEvent("Connection failed during login attempt")
            self.disconnect()
            return False
        # event_ok() and event_error() can set this event.
        if not self.waitOn(self.ev_loggedIn, 10):
            self.errorFromEvent("Login timed out")
            return False
        if self.state == "loginError":
            # event_error() did this, and already printed the message.
            self.ev_loggedIn.clear()
            #self.state = "connected"
            return True
        self.state = "loggedIn"
        return True

    def _adjustForBearwareLogin(self, lp):
        """Adjust login parms in lp for Bearware login, if appropriate.
        Sets self.bwxml if a Bearware login is involved as well.
        Returns True if either nothing is needed or what is needed is done.
        """
        if not self.info.get("accesstoken"): return True
        if lp.get("username") != "bearware" or lp.get("password"): return True
        self.bwxml = None
        try:
            id = conf.option("username", section="bearwareid")
            token = conf.option("token", section="bearwareid")
        except Exception:
            self.errorFromEvent("Bearware login information is not set up")
            return False
        # Safety checks - this could be user-generated material.
        if (not id.endswith("@bearware.dk")
        # ToDo: Verify this character set and length limit against the website requirements.
        or len(id) > 50 or not re.match(r'^[-a-zA-Z0-9_]+$', id.rsplit("@", 1)[0])
        or len(token) != 64 or not re.match(r'^[0-9a-fA-F]+$', token)
        ):
            self.errorFromEvent("Bearware login info format error")
            safePrint(f"id {id} token {token}")
            return False
        url = "https://www.bearware.dk/teamtalk/weblogin.php?" +urllib.parse.urlencode((
            ("client", conf.name),
            ("version", f"0.0.0.{conf.version}"),
            ("dllversion", "0.0.0.0000"),
            ("os", sys.platform),
            ("service", "bearware"),
            ("action", "clientauth"),
            ("username", id),
            ("token", token),
            ("accesstoken", self.info['accesstoken']),
        ))
        with urllib.request.urlopen(url) as stream: xml = stream.read().decode("UTF-8")
        # On auth failure, that throws an error that prints: HTTPError: HTTP Error 401: Authentication Failed
        self.bwxml = xml
        # Replace "bearware" with the actual id to use for login.
        # ToDo: Should probably check for exact match against what is in that xml block.
        lp["username"] = id
        return True

    def processLine(self, line):
        """Callback to process inbound text a line at a time.
        Passed by connect() as the TeamTalkServerConnection callback for events.
        Uses ParmLine to get eventname,parms (AttrDict) from the line,
        then dispatches the event to a method named event_<eventname>.
        If no such method exists for an event, handles this condition.
        """
        parmline = ParmLine(line)
        # When collecting text, don't dispatch events.
        if self._handleCollection(parmline):
            return
        # When we internally set an id and this is a start/end-block
        # line for it, don't call hookEvents for it.
        isOurBlockMarker = (self.waitID > 0
            and parmline.event in ["begin", "end"]
            and parmline.parms.id == str(self.waitID)
        )
        if not isOurBlockMarker:
            self.hookEvents(parmline, False)
        # Protect a bit from rogue transmissions.
        # This would require a custom TeamTalk server though.
        # This check makes sure nothing but underscores and letters
        # appear in an event name.
        if not parmline.event.replace("_", "").isalpha():
            self.errorFromEvent(f"Invalid line: {line}")
            return
        try: eventFunc = getattr(self, f"event_{parmline.event}")
        except AttributeError: 
            self.errorFromEvent(f"Unrecognized line: {line}")
            return
        try:
            if not eventFunc(parmline.parms):
                self.outputFromEvent(line.rstrip())
        except Exception as e:
            self.errorFromEvent(f"Event dispatch failure: {line}")
            raise
        finally:
            if not isOurBlockMarker:
                self.hookEvents(parmline, True)

    def _handleRecycling(self, force=False):
        """Handle autoLogin-on-logout as appropriate.
        """
        if force or (self.autoLogin and not self.manualCM):
            self.outputFromEvent("Reconnecting")
            task = lambda: self.login(True)
            th = threading.Timer(5, task)
            th.setDaemon(True)
            th.start()

    def _handleCollection(self, parmline):
        """Manages the process of collecting a command response.
        Helper for processLine() and sendWithWait().
        sendWithWait() signals a collection start by calling self._startCollecting():
        _startCollecting() sets self.waitID and sets self._collecting to 1.
        processLine() calls this method when collection is in progress,
        instead of dispatching events.
        This method handles the transition of _collecting from 1 to 2 and back to 0.
        It also eats the relevant Begin and End events.
        When the response block is done, this method resets waitID to 0.
        sendWithWait() watches for this then collects the output by calling self._stopCollecting().
        _stopCollecting() returns the collected output and clears it.
        """
        isConnect = (parmline.event == "_connected_")
        isDisconnect = (parmline.event == "_disconnected_")
        # If no collection is in progress.
        if not self._collecting: return False
        # If collection has been requested but the response hasn't started.
        if self._collecting == 1:
            if parmline.event == "begin" and parmline.parms.id == str(self.waitID):
                # Start of atomic response line set.
                # No unrelated line should interrupt this.
                # It terminates with an End id=... event.
                # Eat the Begin event and start collecting.
                self._outputCollection = []
                self._collecting = 2
                self.ev_idblockDone.clear()
                return True
            # Something unrelated slipped in between this command and
            # its response block.
            # If this is a connect/disconnect, clean up the mess.
            if isConnect or isDisconnect:
                self.errorFromEvent("Output collection aborted by server connection interruption")
                # Treat like the end of the response line set.
                # TODO: Might need to do more here.
                self._collecting = 0
                self.waitID = 0
                self.ev_idblockDone.set()
            # Handle this line normally even if it cut short a response collection.
            return False
        # Finally if the response is ongoing (self._collecting==2).
        if ((isConnect or isDisconnect)
        or parmline.event == "end" and parmline.parms.id == str(self.waitID)):
            # End of response line set.
            self._collecting = 0
            self.waitID = 0
            if isConnect or isDisconnect:
                self.errorFromEvent("Output collection truncated by server connection interruption")
                self.ev_idblockDone.set()
                # Let connect/disconnect events through.
                return False
            self.ev_idblockDone.set()
            # Eat the closing "end id=..." event.
            return True
        # Not end of set, so collect the line.
        self._outputCollection.append(parmline)
        # And don't pass it through as an event to process now.
        return True

    def hookEvents(self, parmline, afterDispatch):
        """Stub that subclasses can override for multi-event processing.
        This method is called twice per event:
        once before and once after the event is dispatched.
        The afterDispatch parameter indicates which type of call is occurring.
        """
        pass

    def send(self, line):
        """Send a command to this server.
        line can be anything with an str value.
        Raises a custom IOError on failure.
        """
        try:
            if not self.conn.send(str(line)):
                raise IOError("Connection lost")
        except AttributeError:
            raise IOError("Not connected")

    def sendWithWait(self, line, returnResults=False):
        """Send a command to this server and wait for it to complete.
        line can be anything with an str value.
        If returnResults is True, the command's response is returned
        instead of generating events. Returned responses take
        the form of a list of ParmLine objects.
        See _handleCollection() for a description of the response collection process.
        IOErrors and EOF cause a connection reset but also bubble up.
        """
        self.curID += 1
        if self.curID > self.maxID:
            self.curID = 1
        line = str(line).rstrip()
        line += f" id={self.curID:0d}"
        self.ev_idblockDone.clear()
        if returnResults: self._startCollecting(self.curID)
        else:
            self.waitID = self.curID
        try: self.send(line)
        except IOError:
            # Connection failure.
            if returnResults: self._stopCollecting()
            self.disconnect()
            # Break any waiting code so everything can restart.
            raise
        if not self.waitOn(self.ev_idblockDone, 8):
            self.errorFromEvent(f"Timeout on {line.split(None, 1)[0]} command")
            self.waitID = 0
        if returnResults:
            return self._stopCollecting()

    def nonEmptyNickname(self, user, flags="", forceDetails=False, includeUserType=False, noLimit=False):
        """Make sure not to output a null string for a user with no nickname.
        This method can handle user and ban parmlines as input, and also str or int userid.
        forceDetails causes userid and IP address to be included.
        If includeUserType is True, "User" or "Admin" will precede the user information.
        noLimit precludes shortening based on the user-set nickLimit option when present.
        Flags is meant to replace those three and add further options:
            d is forceDetails, t is includeUserType, n is noLimit.
            c includes client name after username.
        """
        flags = set(flags)
        if forceDetails: flags.add("d")
        if includeUserType: flags.add("t")
        if noLimit: flags.add("n")
        if isinstance(user, int): user = str(user)
        if isinstance(user, str):
            try: user = self.users[user]
            except KeyError:
                # Happens on servers where we can't see participants without being in the same channel.
                # These servers can make admins visible to an extent, and admins can send user messages.
                name = f"<userid {user}>"
                # We can't get any more info for this one.
                return name
        nickname = user.get("nickname")
        username = user.get("username") or ""
        name = nickname
        idIncluded = False
        if name:
            maxlen = 0 if "n" in flags else int(conf.option("nickLimit") or 0)
            if not maxlen or len(name) <= maxlen:
                name = '"' +name +'"'
            else:
                # Dots are outside quotes so users can't fake the dots.
                name = '"' +name[:maxlen] +'"...'
            if username: name += " (" +username +")"
        else:
            if username:
                name = "(" +username +")"
            else:
                name = f"<nameless user {user.userid}>"
                flags.add("d")
                idIncluded = True
        if "t" in flags:
            utype = user.usertype
            if utype == "1": utype = "User"
            elif utype == "2": utype = "Admin"
            else: utype = f"UserType{utype}"
            name = f"{utype} {name}"
        if "c" in flags and user.get("clientname"):
            name += " " + user.get("clientname")
        if "d" not in flags: return name
        ip = user.get("ipaddr")
        if not ip or ip.startswith("0.0.0.0"):
            ip = user.get("udpaddr")
            if not ip or ip.startswith("0.0.0.0"): ip = ""
            if ip: ip = "UDP " +ip.rsplit(":", 1)[0]
        if ip: ip = f"from {ip}"
        if ip: name += " " +ip
        if not idIncluded:
            name += f" (userid {user.userid})"
        return name

    def channelname(self, id, isRawName=False, preserveRootName=False):
        """Adjust channel names for printing as appropriate.
        Pass a channel ID, or a channel name with isRawName=True.
        "/" becomes "the root channel" unless preserveRootName is True.
        """
        if isRawName: name = id
        else:
            ch = self.channels[id]
            try: name = ch.channel
            except Exception: name = None
            if not name:
                # In case .channel goes away...
                # TT5 introduced .name and .parentid.
                name = ""
                while True:
                    name = "/".join(ch.name, name)
                    ch = self.channels[str(ch.parentid)]
                    if not int(ch.parentid): break
                name += "/"
        if name == "/" and not preserveRootName:
            name = "the root channel"
        return name

    def collectingOutput(self, line):
        """Indicate if output is being collected and collect it if so.
        """
        if self._collecting == 2:
            self._outputCollection.append(ParmLine(line))
            return True
        return False

    def _startCollecting(self, id):
        """Start collecting output for return to caller.
        See _handleCollection() for a description of the collection process.
        """
        self.ev_idblockDone.clear()
        self.waitID = int(id)
        self._outputCollection = []
        self._collecting = 1

    def _stopCollecting(self):
        """Stop collecting output for return to caller.
        Returns the output collected so far.
        See _handleCollection() for a description of the collection process.
        """
        self._collecting = 0
        lines = self._outputCollection
        self._outputCollection = []
        return lines

    def output(self, line, raw=False, fromEvent=False):
        """Call to print a line to the user about this server connection.
        Raw=True means leave out the server's shortname.
        fromEvent=True means this is from an asynchronous event.
        Material from events is still handled like non-event text
        if we are waiting for a command result.
        """
        msg = TeamtalkServer.write
        if fromEvent and not self.waitID:
            msg = TeamtalkServer.writeEvent
        if raw: msg(line)
        else: msg(f"{self.shortname} - {line}")

    def outputFromEvent(self, line, raw=False):
        """For event output. See output() for details.
        """
        self.output(line, raw, fromEvent=True)

    def errorFromEvent(self, line, raw=False):
        """For event error output. See output() for details.
        """
        self.output(line, raw, fromEvent=True)

    def getFile(self, f, localPath, localName=None):
        """Download the file represented by the given file object, which must have at least chanid and filename properties.
        If the path does not exist or is not a folder, or if the file already does exist, raises a ValueError.
        If localName is given, use that name locally. Renaming to ad _n where n increments from 1 will be done to avoid clobbering a local file as necessary.
        If both localPath and localName are None, instead verifies whether the file can be downloaded.
        In this case, True is returned for file downloadable and False if there is an error.
        This provides a way to test for files deleted on server but that still have ids in TeamTalk.
        """
        if localPath is not None:
            if not os.path.exists(localPath): raise ValueError(f"{localPath} does not exist")
            if not os.path.isdir(localPath): raise ValueError(f"{localPath} is not a folder")
            fname0 = localName if localName else f.filename
            fname = fname0
            idx = 0
            while os.path.exists(os.path.join(localPath, fname)):
                idx += 1
                fname = f"{os.path.splitext(fname0)[0]}_{idx:d}{os.path.splitext(fname0)[1]}"
            #safePrint(f"Downloading {f.filename} to {localPath}/{fname}")
            fullpath = os.path.join(localPath, fname)
            # This shouldn't happen but the check is here because the above loop is not atomic and a file could be created between then and now.
            if os.path.exists(fullpath): raise ValueError(f"{fullpath} already exists")
            if fname != f.filename:
                self.outputFromEvent(f"Remote file {f.filename} will be called {fname} here.")
        parms = TTParms([KeywordParm("regrecvfile"),
            StringParm("filename", f.filename, rawValue=True),
            IntParm("chanid", f.chanid),
        ])
        for i in range(0, 4):
            results = self.sendWithWait(parms, True)
            if len(results) == 2 and results[0].event == "fileaccepted" and results[1].event == "ok":
                break
            if len(results) >= 1 and results[0].event == "error" and int(results[0].parms.number) == 3011:
                # File not found on server.
                if not localPath and not localName: return False
                # When actually downloading, fall through for handling below.
            if len(results) >= 1 and results[0].event == "error" and int(results[0].parms.number) == 3009:
                # Server failed to create file
                # This happens rarely but often enough and otherwise would break a multi-file transfer.
                # Seen by author 3/15/2021 and by Anthony C. 11/3/2021.
                self.outputFromEvent("File get failed to start, retrying")
                time.sleep(1.0)
                continue
            if len(results) == 0:
                # When error-handling event code traps and prints the error and sends nothing back here.
                break
        if len(results) != 2 or results[0].event != "fileaccepted" or results[1].event != "ok":
            raise ValueError(", ".join([str(l) for l in results]))
        tid = results[0].parms.transferid
        if not tid: raise ValueError("Missing or invalid transfer id")
        with self.conn.dup() as ftconn:
            if not ftconn.send(f"recvfile transferid={tid}"):
                # True so the file can't be considered invalid and therefore worthy of deletion by the caller.
                if not localPath and not localName: return True
                return
            line = ParmLine(ftconn.sockfile.readline())
            if line.event != "fileready" or line.parms.transferid != tid or not line.parms.filesize:
                raise ValueError(str(line))
            if not localPath and not localName:
                # Abort; we just wanted to know if the file is ok.
                ftconn.send("quit")
                time.sleep(0.2)
                return True
            fsize = int(line.parms.filesize)
            stream = ftconn.sock.makefile("rb")
            if not ftconn.send("filedeliver"): return
            fleft = fsize
            bufsize = 32768
            with open(fullpath, "wb") as fout:
                with ProgressReporter(f"{f.filename}", lambda: (fsize-fleft)/fsize) as pr:
                    while fleft:
                        fchunk = min(fleft, bufsize)
                        buf = stream.read(fchunk)
                        fout.write(buf)
                        fleft -= len(buf)
            ftconn.send("quit")
            time.sleep(0.5)
            del stream
            ftconn.disconnect()
            ftconn.terminate()
            if os.stat(fullpath).st_size != fsize: raise ValueError("Wrong file size")

    def sendFile(self, localPath, remoteName=None, chanid=None):
        """Upload the file at localPath to the current or given channel with the same filename, or remoteName if given.
        The user must be in a channel if a chanid is not given.
        Only admins can send to a non-current channel.
        If the path does not exist or is not a file, raises a ValueError.
        Retries on temporary server-side failure to start the upload, up to a total of four tries.
        Renaming to ad _n where n increments from 1 will be done to avoid clobbering a remote file as necessary.
        """
        if chanid is None:
            chanid = self.me.get("chanid")
        if not chanid: raise ValueError("Need a destination channel")
        if not os.path.exists(localPath): raise ValueError(f"{localPath} does not exist")
        if not os.path.isfile(localPath): raise ValueError(f"{localPath} is not a file")
        fname0 = remoteName if remoteName else os.path.basename(localPath)
        fname = fname0
        idx = 0
        while [f for f in self.files.values() if f.chanid==chanid and f.filename.lower()==fname.lower()]:
            idx += 1
            fname = f"{os.path.splitext(fname0)[0]}_{idx:d}{os.path.splitext(fname0)[1]}"
        if fname != os.path.basename(localPath):
            self.outputFromEvent(f"Local file {os.path.basename(localPath)} will be uploaded as {fname}")
        fsize = os.stat(localPath).st_size
        parms = TTParms([KeywordParm("regsendfile"),
            StringParm("filename", fname, rawValue=True),
            IntParm("chanid", chanid),
            IntParm("filesize", int(fsize)),
        ])
        for i in range(0, 4):
            results = self.sendWithWait(parms, True)
            if len(results) == 2 and results[0].event == "fileaccepted" and results[1].event == "ok":
                break
            if len(results) >= 1 and results[0].event == "error" and int(results[0].parms.number) == 3009:
                # Server failed to create file
                # This seems to happen sometimes during multi-file transfers. [DGL, 2021-03-15]
                self.outputFromEvent("File send failed to start, retrying")
                time.sleep(1.0)
                continue
            if len(results) == 0:
                # Shouldn't happen but did for a while on quotes in file names.
                # The event handling system prints the syntax error (number=1000) and nothing comes back here.
                # I am presuming the command's id is not included in the syntax error event.
                # [DGL, 2022-06-15]
                break
        if len(results) != 2 or results[0].event != "fileaccepted" or results[1].event != "ok":
            raise ValueError(", ".join([str(l) for l in results]))
        tid = results[0].parms.transferid
        if not tid: raise ValueError("Missing or invalid transfer id")
        with self.conn.dup() as ftconn:
            if not ftconn.send(f"sendfile transferid={tid}"): return
            line = ParmLine(ftconn.sockfile.readline())
            if line.event != "filedeliver" or line.parms.filename != fname or not line.parms.filesize:
                raise ValueError(str(line))
            stream = ftconn.sock.makefile("wb")
            fleft = fsize
            bufsize = 32768
            with open(localPath, "rb") as fin:
                with ProgressReporter(f"{fname}", lambda: (fsize-fleft)/fsize) as pr:
                    while fleft:
                        fchunk = min(fleft, bufsize)
                        buf = fin.read(fchunk)
                        stream.write(buf)
                        stream.flush()
                        fleft -= fchunk
            line = ParmLine(ftconn.sockfile.readline())
            if line.event != "filecompleted":
                raise ValueError(str(line))
            ftconn.send("quit")
            time.sleep(0.5)
            del stream
            ftconn.disconnect()
            ftconn.terminate()
            # ToDo: No remote file size check; this would require waiting for the addfile event.

    @staticmethod
    def standardizeSummaryFlags(flags):
        """Standardize summary flags into a set. Flags are m include me, c channels, n not in channels, t text, v voice.
        Rules:
            * If flags comes in as str, it is lower-cased and goes out as set.
            * If any unrecognized flag comes in, it raises a ValueError.
            * No flags becomes all but m (cntv).
            * If "a" is included, flags become all.
            * If c and n are both missing, they are both added.
            * If t and v are both missing, they are both added.
        The net result is that the following are true:
            * Default behavior is to include everyone except me.
            * Just c or n includes everyone respectively in or not in channels.
            * Just t or v respectively includes all voice or text clients anywhere.
            * Other specifications are honored without alteration.
            * Including me must be done explicitly.
        As a final step, "a" is added if all recognized flags are set, to ease recognition of this case.
        """
        if isinstance(flags, str): flags = set(flags.lower())
        includeMe = False
        if "m" in flags:
            flags.remove("m")
            includeMe = True
        wrong = flags - set("acntv")
        if wrong:
            raise ValueError("Unrecognized flag or flags: " +" ".join(wrong))
        if not flags or "a" in flags:
            flags = set("acntv")
        if "c" not in flags and "n" not in flags:
            flags.add("c")
            flags.add("n")
        if "v" not in flags and "t" not in flags:
            flags.add("v")
            flags.add("t")
        all = set("cntv")
        if flags & all == all:
            flags.add("a")
        else:
            try: flags.remove("a")
            except Exception: pass
        if includeMe:
            flags.add("m")
        return flags

    def applySummaryFlags(self, users, flags):
        """Apply (already standardized) summary flags to filter the given set of users from a server.
        """
        users = [u for u in users if u.userid != self.me.userid or "m" in flags]
        if "v" not in flags:
            # Only text clients.
            users = [u for u in users if int(u.packetprotocol) == 0]
        if "t" not in flags:
            # Only voice clients. Currently only 1, but other values are allowed here.
            # In TeamTalk 4, there were multiple values for voice clients I believe. [DGL, 2021-11-23]
            users = [u for u in users if int(u.packetprotocol or 0) != 0]
        if "c" not in flags:
            users = [u for u in users if not u.get("chanid")]
        if "n" not in flags:
            users = [u for u in users if u.get("chanid")]
        return users

    def summarizeChannels(self, flags="a"):
        """Summarize who is where on this server.
        This current user is omitted unless flag m is included.
        """
        flags = self.standardizeSummaryFlags(flags)
        if self.state != "loggedIn":
            state = self.state
            if self.conn and self.conn.state and self.conn.state != self.state:
                state += "/" +self.conn.state
            self.output(state)
            return
        users = self.applySummaryFlags(self.users.values(), flags)
        if not len(users):
            if "a" in flags:
                self.output("No users are connected.")
            else:
                self.output("No matching users are connected.")
            return
        activeChannels = {}
        for user in users:
            channel = user.get("channel")
            if channel is None:
                cid = user.get("chanid")
                if cid:
                    # The next line threw a KeyError once, Jan 29 2019, on Laura's server.
                    # Chris Duffley reported it again on the OnYourStream server, June 13, 2022.
                    # We therefore protect against this and use the cid to indicate the occasion to the user.
                    try: channel = self.channels[cid].channel
                    except KeyError:
                        channel = f"<channel{cid}>"
            if not channel: channel = ""
            activeChannels.setdefault(channel, [])
            activeChannels[channel].append(self.nonEmptyNickname(user))
        lines = []
        nchannels = 0
        nusers = 0
        for channel in sorted(activeChannels):
            people = activeChannels[channel]
            people.sort(key=lambda p: p.lower())
            n = len(people)
            nusers += n
            if channel:
                nchannels += 1
                lines.append("    %s (%d): %s" % (
                    self.channelname(channel, True),
                    n,
                    ", ".join(people)
                ))
            else:
                lines.append("    %d not in a channel: %s" % (
                    n,
                    ", ".join(people)
                ))
        lines.insert(0, "Users %d, active channels %d:" % (nusers, nchannels))
        self.output("\n".join(lines))

    def summarizeVersions(self, proto=None):
        """Summarize users by TeamTalk packet protocol, client name, and client version on this server.
        This current user is omitted.
        proto, if given, restricts to a particular packet protocol by number. -1 means all but 0.
        """
        if self.state != "loggedIn":
            state = self.state
            if self.conn and self.conn.state and self.conn.state != self.state:
                state += "/" +self.conn.state
            self.output(state)
            return
        users = [u for u in self.users.values() if u.userid != self.me.userid]
        if not len(users):
            self.output("No users are connected.")
            return
        versions = {}
        for user in users:
            version = user.get("version")
            if version is None: version = ""
            client = user.get("clientname")
            if client is None: client = ""
            protocol = user.get("packetprotocol")
            if proto == -1:
                if protocol == "0": continue
            elif proto is not None:
                if protocol != str(proto): continue
            if protocol is None: protocol = "pp<unknown>"
            else: protocol = f"pp{protocol}"
            version = f"{protocol} {client} {version}".strip()
            versions.setdefault(version, set())
            versions[version].add(self.nonEmptyNickname(user))
        lines = []
        nversions = 0
        nusers = 0
        for version in sorted(versions):
            people = list(versions[version])
            people.sort(key=lambda p: p.lower())
            n = len(people)
            nusers += n
            if version:
                nversions += 1
                lines.append("%6d %s: %s" % (
                    n, version, ", ".join(people)
                ))
            else:
                lines.append("%6d without version or clientname: %s" % (
                    n,
                    ", ".join(people)
                ))
        if not len(lines):
            self.output("No users matched the filter.")
            return
        lines.insert(0, "Users %d, versions/clients %d:" % (nusers, nversions))
        self.output("\n".join(lines))

    def subBitNames(self):
        """Return a list of bit names for sublocal and subpeer.
        """
        bitnames = [
            "user messages", "channel messages",
            "broadcast messages", "typing notifications",
            "audio", "video",
            "desktop", "desktopAccess",
            "stream"
        ]
        return bitnames

    def updateParms(self, category, parms, newParms, silent=False, preserve=None):
        """Update parms with newParms and report changes as appropriate.
        If preserve is a nonempty list or tuple, any parameters not included in preserve or newParms are removed from parms;
        i.e., newParms replaces parms except for preserved elements.
        """
        if preserve is None: preserve = []
        oldParms = parms.copy()
        if preserve:
            parms.clear()
            for k in preserve: parms[k] = oldParms[k]
        parms.update(newParms)
        # Special handling of channel updates.
        if (("parentid" in newParms and "chanid" in oldParms)
        or "name" in newParms):
            # This channel's name or parent channel changed.
            self._updateChannelValue(parms)
        # Special handling of status changes.
        if ("statusmode" in newParms or "statusmsg" in newParms) and "statustime" not in parms:
            parms["statustime"] = time.monotonic()
        if silent: return
        all = set(oldParms.keys()) | set(parms.keys())
        buf = []
        statusDone = False
        for k in sorted(all):
            # channel duplicates name and parentid;
            # statustime is handled below with status updates in general.
            # motd changes when system vars change; other changes are reported by motd_raw.
            if k in ["channel", "statustime", "motd"]: continue
            v1 = oldParms.get(k)
            v2 = parms.get(k)
            # Type-independent way to avoid None problems below.
            if v1 is None and v2 is not None: v1 = v2.__class__()
            elif v2 is None and v1 is not None: v2 = v1.__class__()
            # Special handling of statuses (mode and message).
            if k == "statusmsg" or k == "statusmode":
                if v1 == v2: continue
                if not statusDone: self.doStatus(buf, parms, oldParms)
                statusDone = True
                continue
            # Special handling of sublocal and subpeer.
            if k == "sublocal" or k == "subpeer":
                if v1 == v2: continue
                removed,added = ttflags.Subscriptions.comm(int(v1), int(v2))[0:2]
                ki = "local subscription changes" if k == "sublocal" else "remote subscription changes"
                if removed: ki += f" -{removed.toString('c')}"
                if added: ki += f" +{added.toString('c')}"
                buf.append(ki)
                continue
            # Similarly special handling of logevents.
            if k == "logevents":
                if v1 == v2: continue
                removed,added = ttflags.LogEvents.comm(int(v1), int(v2))[0:2]
                ki = "log events"
                if removed: ki += f" removed {removed.toString('w')}"
                if added: ki += f" added {added.toString('w')}"
                buf.append(ki)
                continue
            # Special things we don't want to report normally (or at all).
            if k == "udpaddr":
                # Ignore UDP ports, which can change really often.
                v1,v2 = v1.rsplit(":", 1)[0], v2.rsplit(":", 1)[0]
                if v1 == "[::]" or v1 == "0.0.0.0": v1 = ""
                if v2 == "[::]" or v2 == "0.0.0.0": v2 = ""
            # Skip parms that did not change in substance.
            if v1 == v2 or (not v1 and not v2):
                continue
            elif v1 and not v2:
                buf.append(f"{k} cleared")
                continue
            elif v2 and not v1:
                buf.append(f"{k} \"{v2}\"")
                continue
            # v1 != v2:
            if isinstance(v1, str) and isinstance(v2, str) and v1.startswith("[") and v2.startswith("["):
                # A list value.
                l1 = v1[1:-1].split(",")
                l2 = v2[1:-1].split(",")
                if len(l1) == len(l2):
                    for i in range(0, len(l1)):
                        v1 = str(l1[i])
                        v2 = str(l2[i])
                        if v1 != v2:
                            ki = "%s[%d]" % (k, i+1)
                            self.includeUpdate(buf, ki, v1, v2)
                    continue
            self.includeUpdate(buf, k, v1, v2)
        buf = ", ".join(buf)
        if not buf: return
        if category:
            buf = f"{category}: {buf}"
        self.outputFromEvent(buf)

    def _updateChannelValue(self, chan):
        """For TT5 servers, update chan.channel in case name or parentid changed.
        The updateChannel event does not include the .channel property on TT5.
        """
        path = "/"
        c = chan
        while c.parentid and c.parentid != "0":
            path = f"/{c.name}{path}"
            c = self.channels[c.parentid]
        chan["channel"] = path

    def addrAndPort(self, udpaddr):
        """Split and return address and port out of a UDP address.
        Input formats: 1.2.3.4:5678 or [IPV6addr]:5678.
        """
        if not udpaddr: return ("","")
        if "]:" in udpaddr:
            addr,port = udpaddr.split("]:", 1)
            addr = addr.replace("[", "")
        else:
            addr,port = udpaddr.split(":", 1)
        return addr,port

    def makeTTString(self, userInfo=None, cid=None, verGiven=None):
        """Make a string that can be saved as a .tt file.
        userInfo, if passed, is a dict containing username and password keys.
        cid, if passed, is a string or integer chanid to join.
        verGiven, if passed, is the intended TeamTalk client version (i.e., "5.1").
        Returns the string formed.
        Requires an active and logged-in server connection.
        """
        if self.state != "loggedIn":
            return ""
        if verGiven: ver = verGiven
        else:
            ver = self.info.version
            if ver < "5.0": ver = "4.0"
            else:
                try: ver = re.sub(r'(\d\.\d)\..*', r'\1', ver)
                except Exception: ver = ""
            if not ver: ver = "5.0"
        tmpl = (
"""<?xml version="1.0" encoding="UTF-8" ?>
<teamtalk version="%(ver)s">
    <host>
        <name>%(name)s</name>
        <address>%(hostaddr)s</address>
        <tcpport>%(tcpport)s</tcpport>
        <udpport>%(udpport)s</udpport>
        <encrypted>%(encrypted)s</encrypted>
        <auth>
            <username>%(username)s</username>
            <password>%(password)s</password>
        </auth>
        <join>
            <channel>%(channel)s</channel>
            <password>%(chanpassword)s</password>
        </join>
    </host>
</teamtalk>
""")
        if not userInfo:
            userInfo = {
                "username": "",
                "password": ""
            }
        if cid:
            channel = self.channels[str(cid)]
        else:
            channel = AttrDict({"channel": "", "password": ""})
        encrypted = None
        # Try to use actual current connection state.
        try: encrypted = self.conn.encrypted
        except: pass
        if encrypted is None:
            # Use ttcom.conf value as a fallback.
            encrypted = self.encrypted
        ttinfo = {
            "name": self.shortname,
            "hostaddr": self.host,
            "tcpport": self.tcpport,
            "udpport": self.loginParms.get("udpport") or self.info.udpport or self.tcpport,
            "encrypted": str(encrypted).lower(),
            "username": userInfo.username or "",
            "password": userInfo.password or "",
            "channel": channel.channel or "",
            "chanpassword": channel.password or "",
            "ver": ver
        }
        return tmpl % ttinfo

    def makeURLString(self, userInfo=None, cid=None):
        """Make a TT URL.
        userInfo, if passed, is a dict containing username and password keys.
        cid, if passed, is a string or integer chanid to join.
        Returns the string formed.
        Requires an active and logged-in server connection.
        """
        if self.state != "loggedIn":
            return ""
        if not userInfo:
            userInfo = {
                "username": "",
                "password": ""
            }
        if cid:
            channel = self.channels[str(cid)]
        else:
            channel = AttrDict({"channel": "", "password": ""})
        encrypted = None
        # Try to use actual current connection state.
        try: encrypted = self.conn.encrypted
        except: pass
        if encrypted is None:
            # Use ttcom.conf value as a fallback.
            encrypted = self.encrypted
        ttinfo = {
            "name": self.shortname,
            "hostaddr": self.host,
            "tcpport": self.tcpport,
            "udpport": self.loginParms.get("udpport") or self.info.udpport or self.tcpport,
            "encrypted": str(encrypted).lower(),
            "username": userInfo.username or "",
            "password": userInfo.password or "",
            "channel": channel.channel or "",
            "chanpassword": channel.password or "",
        }
        url = f"tt://{ttinfo['hostaddr']}"
        uparms = []
        if (ttinfo.get("tcpport") and ttinfo["tcpport"] != "10333") or (ttinfo.get("udpport") and ttinfo["udpport"] != "10333"):
            uparms.append(("tcpport", ttinfo["tcpport"]))
            uparms.append(("udpport", ttinfo["udpport"]))
        if ttinfo.get("username"):
            uparms.append(("username", ttinfo["username"]))
        if ttinfo.get("password"):
            uparms.append(("password", ttinfo["password"]))
        if ttinfo.get("channel"):
            uparms.append(("channel", ttinfo["channel"]))
        if ttinfo.get("chanpassword"):
            uparms.append(("chanpasswd", ttinfo["chanpassword"]))
        if ttinfo.get("encrypted") not in [None, "false"]:
            uparms.append(("encrypted", ttinfo["encrypted"]))
        if uparms:
            uparms = urllib.parse.urlencode(uparms, quote_via=urllib.parse.quote, safe='/')
            url = "?".join([url, uparms])
        return url

    def includeUpdate(self, lst, name, v1, v2):
        """Include a change in the named item given old and new values.
        """
        if v1 == v2: return
        if name == "nickname":
            # The original nickname is already printed, so no need to repeat it.
            lst.append(f"{name} changed to \"{v2}\"")
            return
        lst.append(f"{name} changed from \"{v1}\" to \"{v2}\"")

    def doStatus(self, lst, parms, oldParms):
        """Report user status changes intelligently.
        Helper for updateParms(). Reporting rules:
            - Report only changed status flags.
            - Always report status message if present.
        Formats:
            status active
            status idle (away)
            status message "Busy"  (means only the message changed)
            status message cleared   (means that's all that happened)
        """
        oldstat,newstat = int(oldParms.get("statusmode", 0)), int(parms.get("statusmode", 0))
        changes = []
        bitsleft = 0xFFFFFFFF
        changes.extend(self.doFlagBits(oldstat, newstat, 1, ["active", "idle"]))
        changes.extend(self.doFlagBits(oldstat, newstat, 2, ["", "question"]))
        bitsleft ^= 3
        changes.extend(self.doFlagBits(oldstat, newstat, 4096+256, ["m", "f", "n", "gender4"]))
        bitsleft ^= 4096+256
        changes.extend(self.doFlagBits(oldstat, newstat, 512, ["disabled video", "enabled video"]))
        bitsleft ^= 512
        changes.extend(self.doFlagBits(oldstat, newstat, 2048, ["stopped streaming", "started streaming"]))
        bitsleft ^= 2048
        changes.extend(self.doFlagBits(oldstat, newstat, bitsleft, None))
        buf = ", ".join(changes)
        stat = parms.get("statusmsg")
        oldstat = oldParms.get("statusmsg")
        if stat:
            if buf: buf += " (" +stat +")"
            else: buf = 'message "' +stat +'"'
        elif not buf and oldstat:
            buf = "message cleared"
        if not buf: return
        # Include the time since the last status change.
        statusTime = time.monotonic()
        if parms.get("statustime") is None:
            diff = ""
        else:
            diff = self.secsToTime(statusTime -parms["statustime"])
            sinceLogin = self.logintime is not None and abs(parms["statustime"] - self.logintime) < 5.0
        parms["statustime"] = statusTime
        statbuf = ""
        # Only for non-zero times.
        if diff.replace("0", "").replace(":", ""):
            if sinceLogin:
                statbuf = f", last status change was before your login {diff} ago"
            else:
                statbuf = f", last status change {diff} ago"
        if buf: buf = f"status {buf}{statbuf}"
        if buf: lst.append(buf)

    @staticmethod
    def secsToTime(secs, days=False, decimals=False):
        """Convert integer or float seconds to hh:mm:ss. Hours are allowed to exceed 24. Float seconds are rounded to the nearest second.
        The rounding can be disabled by passing decimals=True.
        Days can be included by passing days=True.
        This changes things like 49:2:0 to 2:1:2:0.
        This function is meant for durations more than clock times.
        """
        if isinstance(secs, str): secs = float(secs)
        if isinstance(secs, float) and not decimals:
            secs = int(secs+0.5)
        mm,ss = divmod(secs, 60)
        hh,mm = divmod(mm, 60)
        if days:
            dd,hh = divmod(hh, 24)
            dd = int(dd)
        hh = int(hh)
        mm = int(mm)
        if not decimals:
            ss = int(ss)
        fmt = "{0:02d}:{1:02d}:{2:05.3f}" if decimals else "{0:02d}:{1:02d}:{2:02d}"
        buf = fmt.format(hh, mm, ss)
        if days and dd > 0:
            buf = f"{dd:d}:" +buf
        return buf

    # Used by doFlagBits().
    default_bitnames = [("off%d" % (bit+1), "on%d" % (bit+1)) for bit in range(0, 32)]

    def doFlagBits(self, oldval, newval, bits=None, names=None):
        """Return a list indicating what changed between oldval and newval.
        oldval, newval: Old and new int values to compare.
        bits: An int of the bits to examine, defaults to 0xFFFFFFFF.
        names: A list of names for the bits. Possibilities:
            - None or not passed: Reports each bit as on/off<n>, <n> being the 1-based bit number.
            - List of one name per set bit in bits, lsb first.
              Missing values here will be handled as for the previous case.
            - List of one name for each combination of bits in bits.
              Requires len(names) to be one more than the value
              obtained by collecting all 1 bits in bits at the LSB end.
        Names are usually strings, but when naming bits individually,
        a name can be a two-element list or tuple of (offName, onName).
        Examples for bits=3, oldval=0, and newval=2, then for oldval=2 and newval=0:
        names=["b1","b2"]: "b2", "b1"
        names=["v1","v2","v3","v4"]: "v3", "v1"
        names=None: "on2", "off2"
        """
        changes = []
        if not names:
            # Name all bits "on" or "off" and their number, 1-based from LSB;
            # see default_bitnames above.
            names = []
        if not bits: # None or 0
            # Use all bits.
            bits = 0xFFFFFFFF
            cnt = 32
        else:
            # Arrange for oldval and newval to contain the wanted bits at the LSB end.
            # bits is also changed to be the mask for the bits at the new position,
            # which makes bits double as a count of possible bit combinations (minus 1).
            # cnt becomes the number of bits being used.
            bits,oldval,newval,cnt = self.collectBits(bits, oldval, newval)
        # bits is non-zero, and all bits are at the LSB end.
        if len(names) == bits+1:
            # Bits named as a unit.
            if oldval & bits != newval & bits:
                changes.append(names[newval])
        else:
            # Bits named individually, some possibly by default naming.
            for i in range(0, cnt):
                if i < len(names):
                    name = names[i]
                else:
                    name = self.default_bitnames[i]
                if type(name) is str:
                    name = ("", name)
                o = oldval & 1
                n = newval & 1
                if n and not o:
                    name = name[1]
                elif o and not n:
                    name = name[0]
                else:
                    name = ""
                if name:
                    changes.append(name)
                oldval >>= 1
                newval >>= 1
        return changes

    def collectBits(self, bits0, oldval0, newval0):
        """Arrange for oldval and newval to contain the wanted bits at the LSB end.
        bits is also changed to be the mask for the bits at the new position,
        which makes bits double as a count of possible bit combinations (minus 1).
        cnt becomes the number of bits being used.
        """
        bits,oldval,newval,cnt = (0,0,0,0)
        newbit = 1
        while bits0:
            if bits0 & 1:
                bits |= newbit
                if oldval0 & 1: oldval |= newbit
                if newval0 & 1: newval |= newbit
                newbit <<= 1
                cnt += 1
            bits0 >>= 1
            oldval0 >>= 1
            newval0 >>= 1
        return (bits, oldval, newval, cnt)

# Event functions, corresponding to actual event words sent by Teamtalk servers.

    def event__connected_(self, parms):
        """Internally-generated event signaling connect/welcome to server.
        The "welcome" event will fire at about the same time.
        """
        self.outputFromEvent("Connected")
        return True

    def event__disconnected_(self, parms):
        """Internally-generated event signaling disconnect from server.
        """
        stop = False
        try: stop = self.conn.stop
        except: pass
        buf = "Disconnected"
        reason = ""
        try: reason = self.conn.disconnectReason
        except Exception: pass
        if reason:
            buf += " (" +reason +")"
        self.outputFromEvent(buf)
        self.clear()
        if not stop:
            self._handleRecycling()
        return True

    def event_begin(self, parms):
        """Sent after a request that includes "id=31" or similar.
        All text from this to the corresponding "end" event are the reply.
        Response collection circumvents this; see _handleCollection().
        """
        if self.waitID and parms.get("id") == str(self.waitID):
            # Eat this one quietly.
            return True
        # Process this in the default manner.
        return False

    def event_end(self, parms):
        """Sent after a request that includes "id=31" or similar.
        All text from this back to the corresponding "begin" event are the reply.
        Response collection circumvents this; see _handleCollection().
        """
        if self.waitID and parms.get("id") == str(self.waitID):
            # Signal the end of the corresponding command's reply.
            self.waitID = 0
            self.ev_idblockDone.set()
            # Then eat this line quietly.
            return True
        # Process this in the default manner.
        return False

    def event_welcome(self, parms):
        """Sent on successful connection to the server.
        """
        self.systemId = self.conn.systemId
        self.updateParms("Welcome", self.info, parms, silent=True)
        userid = self.info.userid
        self.users.setdefault(userid, AttrDict())
        self.me = self.users[userid]
        self.me["userid"] = userid
        if self.systemId != "teamtalk":
            self.outputFromEvent(f"SystemId is {self.systemId}")
        return True

    def event_ok(self, parms):
        """Sent at the end of a successful login process.
        Also sent on a successful ChangeNick command and at other times.
        There are no parameters for this event.
        """
        if self.state == "loggingIn":
            self.state = "loggedIn"
            self.outputFromEvent("Login successful (server version %s)" % (
                self.info.version
            ))
            self.lastError = None
            self.ev_loggedIn.set()
            self.logintime = time.monotonic()
            self._handleInitStatus(parms)
            self._handleInitChannel(parms)
            return True
        # Handle in the default manner otherwise.
        return False

    def _handleInitStatus(self, parms):
        """Handle any initial status setting on login.
        This is where the gender field is defined and interpreted into a statusmode, according to the following - case insensitive:
            If gender is anything starting with m, male will be set. Covers male and man.
            If gender is anything starting with f or w, female will be set. Covers female and woman.
            If gender is anything starting with n, neutral will be set. Covers neutral and nonbinary.
            Otherwise, and if gender is missing or empty, statusmode will not be changed.
        Note that gender overrides statusmode if both are present, meaning gender-related bits are governed by gender when found.
        """
        me = self.me
        loginParms = self.loginParms
        statusmode = loginParms.get("statusmode")
        statusmsg = loginParms.get("statusmsg")
        gender = loginParms.get("gender")
        if statusmode is None and statusmsg is None and gender is None: return
        statusmode = 0 if statusmode is None else int(statusmode)
        if gender is not None and len(gender) > 0:
            gender = gender[0].lower()
            if gender in ["m"]:
                if statusmode & 256: statusmode -= 256
                if statusmode & 4096: statusmode -= 4096
            elif gender in ["f", "w"]:
                statusmode |= 256
                if statusmode & 4096: statusmode -= 4096
            elif gender in ["n"]:
                statusmode |= 4096
                if statusmode & 256: statusmode -= 256
        line = ParmLine("changestatus", {
            "statusmode": statusmode,
            "statusmsg": statusmsg,
        })
        self.send(line)

    def _handleInitChannel(self, parms):
        """Handle any initial channel joining on login.
        """
        me = self.me
        loginParms = self.loginParms
        # chanid overrides channel but either is allowed.
        chanid = loginParms.get("chanid")
        channel = loginParms.get("channel")
        if not chanid and channel: 
            if channel == "/": chanid = 1
            else:
                channel = [c for c in self.channels.values()
                    if channel == self.channelname(c["chanid"])
                ]
                if len(channel) != 1: return
                chanid = channel[0].chanid
        if not chanid: return
        line = f"join chanid={chanid!s}"
        pw = loginParms.get("chanpassword")
        if pw: line += f' password="{pw}"'
        self.send(line)

    def event_accepted(self, parms):
        """Sent when a login is accepted, before user/channel updates.
        Includes info about the just-logged-in user.
        For the signal of successful login completion, see the "ok" event.
        """
        self.updateParms("Login accepted", self.users[parms['userid']], parms, silent=True)
        self.reportRightsIssues()
        return True

    def reportRightsIssues(self):
        """Report user rights values that could compromise use of this program on a server.
        """
        try: rights = int(self.me["userrights"])
        except KeyError: return
        if not (rights & 0x1):
            self.errorFromEvent("Warning: Multiple logins disallowed")
        if not (rights & 0x2):
            self.errorFromEvent("Warning: Unable to see channel participants")

    def event_loggedin(self, parms):
        """Sent when a user successfully logs into the server.
        """
        self.users.setdefault(parms.userid, AttrDict())
        # For when someone pulls a list of users from several servers at once.
        self.users[parms['userid']].server = self
        self.updateParms("Logged in", self.users[parms['userid']], parms, silent=True)
        if self.state != "loggingIn":
            self.outputFromEvent("%s logged in" %
                (self.nonEmptyNickname(parms.userid, "t")
            ))
        return True

    def event_serverupdate(self, parms):
        """Sent when the server info is being updated.
        """
        ut1 = self.conn.usertimeout
        self.updateParms("Server update", self.info, parms, silent=(self.state=="loggingIn"))
        try: ut2 = int(parms.usertimeout)
        except AttributeError: return True
        self.conn.usertimeout = ut2
        if ut2 < ut1:
            # Trigger immediate ping so we don't get tossed off the server for being late by the new rules.
            self.conn.noPing = False
            self.conn.eventPingReset.set()
        return True

    def event_addchannel(self, parms):
        """Sent when a channel is created and when this user is logging in.
        """
        self.channels.setdefault(parms.chanid, AttrDict())
        self.updateParms("Add channel", self.channels[parms['chanid']], parms, silent=True)
        # Only show channel creations if we're not logging in right now.
        # Otherwise there's quite a flood of these on some servers.
        if self.state != "loggingIn":
            self.outputFromEvent(f"New channel {self.channels[parms.chanid].channel}")
        return True

    def event_removechannel(self, parms):
        """Sent when a channel is removed from the server.
        """
        self.outputFromEvent(f"Removed channel {self.channels[parms.chanid].channel}")
        del self.channels[parms['chanid']]
        return True

    def event_updatechannel(self, parms):
        """Sent when a channel is changed.
        """
        chan = self.channels[parms.chanid]
        name = chan.channel
        self.updateParms(name, self.channels[parms.chanid], parms, preserve=("parentid", "channel"))
        return True

    def event_adduser(self, parms):
        """Sent when a user joins a channel and when this user is logging in.
        """
        try: user = self.users[parms.userid]
        except KeyError:
            # This happens on servers where users are not visible until you join their channel. The loggedin event is not sent for these.
            self.users.setdefault(parms.userid, AttrDict())
            # For when someone pulls a list of users from several servers at once.
            self.users[parms.userid].server = self
            user = self.users[parms.userid]
            self.updateParms("Add user to channel", user, parms, True)
            self.users[parms['userid']].temporary = True
        else:
            self.updateParms("Add user", user, parms, True)
        if self.state != "loggingIn":
            issues = ""
            self.outputFromEvent("%s joined %s" % (
                self.nonEmptyNickname(parms.userid),
                self.channelname(parms.chanid)
            ))
        return True

    def event_removeuser(self, parms):
        """Sent when a user leaves a channel.
        """
        self.outputFromEvent("%s left %s" % (
            self.nonEmptyNickname(parms.userid),
            self.channelname(parms.chanid)
        ))
        u = self.users[parms['userid']]
        del u['chanid']
        try: del u['channel']
        except KeyError: pass
        if self.users[parms.userid].temporary:
            # This user record sprang up on a channel join,
            # which means this server hides users until you join their channel.
            del self.users[parms.userid]
        return True

    def event_loggedout(self, parms):
        """Sent when a user logs out of the server.
        """
        if not parms:
            # This is a logout of this user.
            self.outputFromEvent("You are logged out")
            self.state = "connected"
            self.channels = dict()
            self.users = dict()
            userid = self.info.userid
            self.users.setdefault(userid, AttrDict())
            self.me = self.users[userid]
            self.me["userid"] = userid
            self.ev_loggedIn.clear()
            self.ev_loggedOut.set()
            self._handleRecycling()
            return True
        self.outputFromEvent(f"{self.nonEmptyNickname(self.users[parms.userid], 't')} logged out")
        del self.users[parms['userid']]
        return True

    def logout(self):
        """Log out of the server.
        Returns True if logged out on exit and False if not.
        """
        if not self.ev_loggedIn.is_set():
            return True
        self.ev_loggedOut.clear()
        self.sendWithWait("logout")
        self.ev_loggedOut.wait(10)
        if not self.ev_loggedOut.is_set():
            self.errorFromEvent("Timeout on logging out")
            return False
        if self.ev_loggedIn.is_set():
            self.errorFromEvent("Timeout on logging out (loggedIn flag still set)")
            return False
        return True

    def event_updateuser(self, parms):
        """Sent when a user's status or other information changes.
        """
        try: user = self.users[parms.userid]
        except KeyError:
            # This happens on servers where users are not visible until you join their channel. The loggedin event is not sent for these.
            # Admin logins, even predating this instance's login, send this event without a corresponding previous loggedin event.
            self.users.setdefault(parms.userid, AttrDict())
            # For when someone pulls a list of users from several servers at once.
            self.users[parms.userid].server = self
            user = self.users[parms.userid]
            # ToDo: Making this completely silent might not always be best, but the alternative tends to include a lot of unhelpful subscription change information.
            self.updateParms("Add user to server", user, parms, True)
            self.users[parms['userid']].temporary = True
        else:
            name = self.nonEmptyNickname(parms.userid)
            self.updateParms(name, self.users[parms['userid']], parms)
        return True

    def event_messagedeliver(self, parms):
        """Sent when a public or private message reaches this user. Message types that can arrive here:
            - Channel messages when this user is in a channel or intercepting someone's channel messages.
            - User messages to this user or to someone for whom this user is intercepting user messages.
            - Broadcast messages to the entire server.
            - Typing indicators from TeamTalk clients that send them.
            - Private user messages from another text client that implements this feature.
        """
        msg = self.formattedMessage(parms)
        if msg: self.outputFromEvent(msg)
        return True

    def formattedMessage(self, parms):
        """Return a formatted (for speaking or printing) version of an incoming message.
        Supports user, channel, and broadcast messages, intercepts, and typing indicators.
        Formats:
            Broadcast: !sender! content
            Channel message: <sender> content
            PM: *sender* content
            TTCom PM: =sender= content
            Typing indicator: *user is typing*
        For intercepts, "Intercepted" precedes and "to" and a target are included.
        Multiline messages appear here as multiline content, rather than with \n separators.
        Any irregular line endings are removed and converted to \n (the actual newline).
        """
        msg = ""
        mtype = parms.type
        content = parms.content
        typeInd = None
        if mtype == "4":
            if content == r'typing\r\n1': typeInd = True
            elif content == r'typing\r\n0': typeInd = False
        content = content.replace(r'\r\n', '\n')
        content = content.replace(r'\n', '\n')
        content = content.replace('\r\n', '\n')
        myID = self.me.userid
        myCID = self.me.chanid
        sender = self.nonEmptyNickname(parms.srcuserid)
        dest = ""
        # dest will be changed below for intercepted messages.
        delims = ""
        # delims will be set below per message type - two chars, for before and after sender.
        if mtype == "1":
            # User message.
            # Delims like IRC private messages.
            delims = "**"
            if parms.destuserid != myID:
                # This must come from a user message intercept.
                dest = self.nonEmptyNickname(parms.destuserid)
        elif mtype == "2":
            # Channel message.
            # Delims like IRC channel messages.
            delims = "<>"
            if not myCID or parms.chanid != myCID:
                # This must come from a channel message intercept.
                dest = parms.channel
        elif mtype == "3":
            # Broadcast message.
            # Delims like IRC op messages.
            delims = "!!"
        elif mtype == "4":
            # User typing start/stop message (TT 4.3+ non-Classic), and TTCom PM.
            # Format: typing\r\n{0|1}, 1=typing and 0=stopped.
            # See typeInd setting above.
            if typeInd is None:
                # TTCom PM.
                # Delims like IRC "Direct Client-to-Client" (DCC) messages.
                delims = "=="
            else:
                # Typing indicator.
                # These are only sent for a normal PM; hence, use those delims.
                delims = "**"
                # And there really is no content.
                content = ""
                # But we put the indicator inside the delims to set this off from actual content.
                # The below interpretation of typing 0 is based on experimentation with TT 5.11. [DGL, # 2022-11-15]
                if typeInd: sender = f"{sender} is typing"
                else: sender = f"{sender} has an empty typing area"
            if parms.destuserid != myID:
                # This must come from a typing message intercept.
                dest = self.nonEmptyNickname(parms.destuserid)
        else:
            # Unknown message type, just dump it all.
            msg = ("messagedeliver %s" % (" ".join(
                [k+"="+v for k,v in parms.items()]
            )))
            return msg
        # Final formatting for all known message types.
        # Requires possibly empty dest and content, and non-empty sender and delims.
        inter = ""
        if dest:
            # An intercept of some sort.
            inter = "Intercepted "
            sender = f"{sender} to {dest}"
        msg = f"{inter}{delims[0]}{sender}{delims[1]} {content}"
        # Typing indicators are noisy and thus shut off at this time.
        # [DGL, 2024-01-18]
        if typeInd is not None: return None
        return msg

    def event_joined(self, parms):
        """Sent when this user joins a channel.
        There is a subsequent adduser event for this as well.
        """
        self.outputFromEvent(f"Joined {self.channelname(parms.chanid)}")
        return True

    def event_left(self, parms):
        """Sent when this user leaves a channel.
        There is a subsequent removeuser event for this as well.
        """
        self.outputFromEvent(f"Left channel {self.channelname(parms.chanid)}")
        return True

    def event_addfile(self, parms):
        """Send when a file is offered in a channel.
        """
        fid = f"{parms.chanid}:{parms.filename}"
        self.files.setdefault(fid, AttrDict())
        self.updateParms("Add file", self.files[fid], parms, silent=True)
        if self.state == "loggingIn": return True
        self.outputFromEvent("%s sent to %s file %s (id %s)" % (
            parms.owner,
            self.channelname(parms.chanid),
            parms.filename,
            parms.fileid
        ))
        return True

    def event_removefile(self, parms):
        """Send when a file is removed from a channel's offerings.
        """
        fid = f"{parms.chanid}:{parms.filename}"
        self.outputFromEvent("File %s removed from %s" % (
            parms.filename,
            self.channelname(parms.chanid)
        ))
        del self.files[fid]
        return True

    def event_kicked(self, parms):
        """Sent when this user is kicked off the server.
        """
        # This throws an error if the kicker is another instance of this user.
        # Occurs on servers that forbid simultaneous logins to the same account.
        kicker = self.nonEmptyNickname(parms.kickerid)
        if parms.get("chanid"):
            self.outputFromEvent(f"{kicker} has kicked you from the channel")
            return
        self.outputFromEvent(f"{kicker} has kicked you from the server")
        self.manualCM = (self.autoLogin != 2)
        return True

    def event_stats(self, parms):
        """Sent in response to a querystats command.
        This command requires admin privileges on the server.
        """
        buf = ["Server statistics:"]
        for k in parms:
            buf.append(f"    {k}: {parms[k]}")
        self.outputFromEvent("\n".join(buf))
        return True

    def event_useraccount(self, parms):
        """Sent in response to a ListAccounts command.
        One of these is sent for each account defined.
        """
        parmline = ParmLine("useraccount", parms)
        self.outputFromEvent(parmline)
        return True

    def event_userbanned(self, parms):
        """Sent in response to a ListBans command.
        One of these is sent for each ban that exists.
        """
        parmline = ParmLine("userbanned", parms)
        self.outputFromEvent(parmline)
        return True

    def event_removeuseraccount(self, parms):
        """Sent to admin users in response to a DelAccount command.
        """
        parmline = ParmLine("removeuseraccount", parms)
        self.outputFromEvent(parmline)
        return True

    def event_adduseraccount(self, parms):
        """Sent to admin users in response to a NewAccount command.
        """
        if parms["password"]:
            parms = parms.copy()
            parms["password"] = "..."
        parmline = ParmLine("adduseraccount", parms)
        self.outputFromEvent(parmline)
        return True

    def event_pong(self, parms):
        """Sent in response to a ping command.
        This should only fire if the user sends a ping;
        internally generated pings do not reach this code.
        """
        # Handle in the default manner.
        return False

    def event_error(self, parms):
        """Sent when an error occurs.
        """
        msg = f"Error {parms.number}: {parms.message}"
        # I've never seen more than number and message parms,
        # but I like to be thorough.
        for parm in parms:
            if parm == "number" or parm == "message": continue
            msg += f", {parm}={parms[parm]}"
        self.outputFromEvent("*** " +msg)
        if not self.ev_loggedIn.is_set():
            self.lastError = msg
        # If this was during login, signal failure.
        if self.state == "loggingIn":
            self.state = "loginError"
            self.ev_loggedIn.set()
        return True
