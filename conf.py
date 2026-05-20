"""TTCom configuration class.
Extends the normal Conf class for my projects with support for a TeamTalk server definition file, ttcom.conf.
This is in addition to the regular ttcom.ini file that manages options, aliases, etc.
The name of the published class here is still Conf, for compatibility with older TTCom plugins.

Copyright (C) 2011-2026 Doug Lee

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

import os, sys
from mplib.conf import Conf as OrgConf

class ConfigError(Exception): pass

class Servers(dict):
    """A dict of defined servers, with possible other internal data kept about them and their definition file.
    Server file structure notes:
    General:
        Files consist of blank lines, comments starting with ; or #, section headers, and key/value pairs.
        There are no inline comments officially, but see the Section headers rules below.
        In the below description, "space" and "spaces" include Space and Tab.
        Don't rely on this; use Space for spacing, at least outside of comments.
    Section headers:
        Sections are bracketed; valid sections are [server name] and [include name].
        At least for now, material after the ] on the same line is ignored. Don't rely on this.
        The words "server" and "include" should be in lower case.
        Currently their case is ignored, but don't rely on this.
        Server name case is preserved, but they must be unique ignoring case.
        This is the same for include names.
        Name case is preserved so that it may be used to improve pronunciation of names in TTCom output.
    Key/value pairs:
        Keys and values are separated by =
        Spaces on either side of the = sign is ignored.
        Keys and values may contain spaces but may not start or end with them.
        Quoting keys or values is not supported; ' and " are valid characters in either.
        Keys may not begin with ; or # because that would make a comment line.
        Key names should be in lower case.
        Currently case in key names is ignored, but don't rely on this.
        Duplicate keys are not an error; the last one prevails.
        This is especially important when include sections are used.
    The include= key:
        include=a,b,c - Include lines from [include a], [include b], and [include c].
        The lines from the included section(s) replace the include= line, in order of occurrence.
        An include section may include others, but recursion is an error.
        Multiple include= lines may be present in one server or include block.
        Remember that the last instance of a key prevails.
    The [server defaults] section:
        This section defines defaults for all servers that do not override them.
        The section is optional but recommended for things like nickname.
        See DEFAULTS below for default server key values where not overridden.
    """

    # Default values for server keys, when not overridden by an explicit or [server defaults] value.
    KEY_DEFAULTS = {
        "nickname": "PowerCom User",
        "gender": "n",
        "statusmsg": "PowerCom text client.",
        "autologin": 0,
        "silent": 0,
        "hidden": 0,
    }

    # Known server keys and their types:
    # c connection, l login, p post-login, t TTCom.
    #    f means forbidden (not allowed in a config file).
    #    x means known external; currently this covers Mason's Windows sound plugin keys.
    # A "*" in a key below matches any key starting with what comes before it.
    # This is used to catch old trigger match/action keys, whose key names are dynamic.
    KEY_TYPES = {
        # These require a disconnect/reconnect to change.
        "host": "c",
        "tcpport": "c",
        "udpport": "c",
        "encrypted": "c",
        # These require a logout/login but not a reconnect to change.
        "username": "l",
        "password": "l",
        # Nickname is a hybrid: It's sent with login but would not require a logout to change...
        # unless nickname lock is active, so we call it a login item here.
        "nickname": "l",
        # These are sent right after login but are not part of the login sequence itself.
        "gender": "p",
        "statusmode": "p",
        "statusmsg": "p",
        "channel": "p",
        "chanid": "p",
        "chanpassword": "p",
        # These change TTCom operation but are not sent to a TeamTalk server.
        "autologin": "t",
        "silent": "t",
        "hidden": "t",
        # These are trigger definition entries.
        "match *": "t",
        "action *": "t",
        # These are not changeable during a connection and represent this client.
        "clientname": "f",
        "version": "f",
        # Built-in PowerCom feature options.
        "speech": "x",
        "speechengine": "x",
        "speechinterrupt": "x",
        "speechdmodule": "x",
        "speechvoice": "x",
        "speechrate": "x",
        "speechvolume": "x",
        "speechpitch": "x",
        "nospeak": "x",
        "sounds": "x",
        "soundpack": "x",
        "soundvolume": "x",
        "playbacktype": "x",
        "nosound": "x",
        "notifyloginout": "x",
        "notifymessage": "x",
        "ntfy": "x",
        "ntfytopic": "x",
        "ntfyurl": "x",
        "ntfyuser": "x",
        "ntfypassword": "x",
        "prowl": "x",
        "prowlkey": "x",
        "mgnotify": "x",
        "mgnotifykey": "x",
        "pushover": "x",
        "pushoveruser": "x",
        "pushovertoken": "x",
        "pushoverdevice": "x",
        "pushoversound": "x",
        "pushoverpriority": "x",
        "pushoverretry": "x",
        "pushoverexpire": "x",
        "systemnotify": "x",
        "log": "x",
        "maxlogsize": "x",
        "maxlogfiles": "x",
    }

    def __init__(self, svfpath):
        super().__init__()
        self.svfpath = svfpath
        self.raw_text = None
        self.line = self.lineno = None
        try:
            self.__read()
        finally:
            del self.line, self.lineno

    def _err(self, msg, warn=False):
        """Print a warning or error.
        if warn is True, call it a warning and don't throw an error.
        """
        tp = "Warning" if warn else "Error"
        msg = f"{tp} at {self.svfpath} line {self.lineno:d}: {msg}\n    {self.line}"
        print(msg)
        if not warn:
            raise ConfigError("Server information not loaded. Fix the error and type Refresh or reload TTCom")

    def _sectInfo(self, line):
        """Return a (type,name) tuple for a section header.
        type is "i" or "s" for include or server, and name is the exact name from the line.
        If the line is not recognized, both values returned are None.
        Assumes line is a section name with brackets (and any trailing material) removed.
        """
        # 2 in order to catch server and include names that contain spaces, which is not allowed.
        parts = line.split(None, 2)
        if len(parts) != 2:
            return (None, None)
        tp,name = parts
        tp = tp.lower()
        # ToDo: Could warn of upper case "server" or "include" here.
        if tp not in ("server", "include"):
            return (None, None)
        tp = "i" if tp == "include" else "s"
        return (tp, name)

    def keyInfo(self, k):
        """Return a (type,key) tuple indicating key type and standardized value.
        See the KEY_TYPES class constant for valid key types.
        If the key is improperly formed, both values returned are None.
        If the key is properly formed but not known, it is standardized and return with a null-string type.
        """
        k = k.strip()
        # 2 in order to catch disallowed spaces in keys.
        parts = k.split(None, 2)
        if len(parts) > 2:
            return (None, None)
        elif len(parts) == 2:
            parts[0] = parts[0].lower()
            k = f"{parts[0]} {parts[1]}"
            tp = self.KEY_TYPES.get(f"{parts[0]} *")
            if tp:
                return (tp, k)
            return ("", k)
        # No spaces.
        k = k.lower()
        tp = self.KEY_TYPES.get(k)
        if tp:
            return (tp, k)
        return ("", k)

    def __read(self):
        """Read server info from the file.
        Internal and only called once, during init.
        Populates: servers, raw_text.
        Uses boxes from ttcom.ini if found, for dotless, colonless host addresses.
        """
        self.clear()
        if not os.path.exists(self.svfpath):
            print(f"Warning: {self.svfpath} not found. See ttcom.conf.sample for guidance in creating this file.")
            return
        # Step 1: Read in all server and include blocks and bail out on any errors.
        # lIncludes and lServers (dicts of lists) are populated here.
        # self.raw_text is also set.
        lIncludes = {}
        lServers = {}
        sect = None
        with open(self.svfpath, "r", encoding="utf-8-sig") as f:
            self.raw_text = f.read()
        for i,line in enumerate(self.raw_text.splitlines()):
            self.line = line
            # Users expect 1-based line numbering.
            self.lineno = i + 1
            line = line.strip()
            if not line or line[0] in ";#": continue
            if line.startswith("[") and "]" in line:
                # A section.
                tp,name = self._sectInfo(line[1:].split("]", 1)[0])
                if not tp:
                    self._err("Unrecognized section", warn=True)
                    # Eat this quietly after that warning.
                    sect = []
                    continue
                # A server or include section.
                lg = lIncludes.keys() if tp == "i" else lServers.keys()
                if [e for e in lg if e.lower() == name.lower()]:
                    self._err("Duplicate section")
                # Valid section.
                sect = []
                if tp == "i":
                    lIncludes[name] = sect
                else:
                    lServers[name] = sect
                continue
            # A key/value pair.
            if "=" not in line:
                self._err("Unrecognized line")
            k,v = line.split("=", 1)
            tp,k = self.keyInfo(k)
            if tp is None:
                # This means an ill-formed key, not a key we don't know about that is otherwise fine.
                self._err("Unrecognized key format")
            elif tp == "f":
                self._err(f"{k} is not allowed in a server definition")
            v = v.strip()
            # Special box handling for host lines.
            if k == "host" and "." not in v and ":" not in v:
                boxSect = "Boxes"
                try: addr = conf[boxSect][v]
                except KeyError: addr = None
                if addr: v = addr
            sect.append( (k,v), )
        # We should now have self.raw_text, and complete lIncludes and lServers lists.
        # Scratchpads for recursion handling here.
        self.shortname = ""
        self.includes = lIncludes
        try:
            for shortname,parms in lServers.items():
                self.shortname = shortname
                self.setdefault(shortname, {})
                server = self[shortname]
                self.doneIncludes = set()
                self._applyParms(server, parms)
        finally:
            del self.includes, self.doneIncludes, self.shortname
        # Apply and remove any defaults list.
        # Also apply TTCom defaults where not overridden.
        defaults = self.pop("defaults", {})
        [defaults.setdefault(k,v) for k,v in self.KEY_DEFAULTS.items()]
        for shortname,sd in self.items():
            [sd.setdefault(k, v) for k,v in defaults.items()]

    def _applyParms(self, server, parms):
        """Apply parms to a server.
        Internal helper for __read().
        Uses the scratchpad ivars set up by that method.
        """
        for k,v in parms:
            if k == "include":
                for inc in v.split(","):
                    inc = inc.strip()
                    if inc in self.doneIncludes:
                        raise ConfigError(f"Server {self.shortname} definition error: {inc} is included more than once")
                    self.doneIncludes.add(inc)
                    try: parms1 = self.includes[inc]
                    except KeyError:
                        raise ConfigError(f"Server {self.shortname} definition error: {inc} is not a valid include section name")
                    self._applyParms(server, parms1)
                continue
            # A normal parameter.
            server[k] = v

class Conf(OrgConf):
    """
    Configuration object. See Conf() for normal behavior information.
    This subclass adds
        conf = Conf("ttcom.conf")
        conf.svfname for name of server file managed by this class.
        conf.svfpath for the full path to that file.
    """
    def __init__(self, svfname=None):
        super().__init__()
        # If self.svfname already exists, this is not the first call and svfname is ignored.
        # If it doesn't exist and svfname is not passed, the object is returned without setting self.svfname;
        # so things will be fine unless somebody tries to examine servers before a svfname is passed.
        # If self.svfname doesn't exist and self.svfname is passed, either this is the first call,
        # or this call fixes the above case.
        if hasattr(self, "svfname") or svfname is None:
            return
        self.svfname = svfname
        self.svfpath = self.svfname

    def servers(self):
        """Return a dict of the servers defined in the server config file.
        These are shortname -> parms mappings, where parms is a dict of the server's parameters.
        Note that this reads the config file; this is not an efficient way to examine defined servers.
        Throws a ConfigError on any error reading the servers.
        May also print warnings without throwing an error.
        """
        servers = Servers(self.svfpath)
        if not servers:
            print("Warning: No servers defined")
        return servers

conf = Conf("ttcom.conf")
