"""Command implementation module for TTCom.

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

import geolocator
import time
from datetime import datetime
import os, sys, re, socket, shlex
from glob import glob
import urllib.request, urllib.parse
from getpass import getpass
import threading
from collections import Counter
from mplib.progressReporter import ProgressReporter
from mplib.attrdict import AttrDict
from ttapi import TeamtalkServer, xmlToDict
import ttflags
from mplib.player import Player
player = Player(delay=0.1, timeout=0.1)
from mplib.mycmd import MyCmd, say as mycmd_say, classproperty, ArgumentParser, CommandError, err, errTrace
from mplib.TableFormatter import TableFormatter
from conf import conf, ConfigError
from banreq import BanRequest
from triggers import Triggers
from powercom_core import features as powercom_features
from parmline import ParmLine, TTParms, KeywordParm, IntParm, StringParm, ListParm
from mplib.textblock import TextBlock
from mplib.dglutils import hrsize, callWithRetry

# Logging support - singleton object.
import logging
class Logger:
    def __init__(self):
        self.logname = "powercom.log"
        self.logpath = self.logname
        doLogging = os.path.exists(self.logpath)
        if not doLogging:
            self.info = lambda msg: msg
            self.flush = lambda: ""
            return
        h = logging.FileHandler(self.logpath)
        logger = logging.getLogger('log')
        logger.setLevel(logging.INFO)
        h.setFormatter(logging.Formatter('%(message)s'))
        h.terminator = ''
        logger.addHandler(h)
        # External methods.
        self.info = logger.info
        self.flush = h.flush

    def logEvent(self, event, server="*PowerCom*"):
        logger.info(f"{datetime.now().ctime()}\n  {server}: {event}\n")
logger = Logger()
logger.logEvent("starting")

class MyTeamtalkServer(TeamtalkServer):
    def __init__(self, parent, *args, **kwargs):
        # This is a TTComCmd object.
        self.parent = parent
        self.silent = 0
        self.hidden = 0
        self.encrypted = False
        # TODO: triggers can't be set here because we don't have a
        # command processor object.
        TeamtalkServer.__init__(self, *args, **kwargs)
        # See hookEvents and outputFromEvent for usage of this one.
        self._evparms = None

    def outputFromEvent(self, line, raw=False):
        """For event output. See output() for details.
        Only outputs for current and non-silenced servers,
        """
        silent = self.silent
        # Lines without a .event attribute are TTCom-generated output.
        try: ev = self._evparms.event
        except AttributeError: ev = ""
        # Events that print under all conditions.
        if ev in [
            "", "error",
            "joined", "left", "kicked",
            "stats",
        ]:
            silent = 0
        if silent > 2:
            # Unconditional silence, even if it's the current server.
            return
        # Exceptions for specific events that are generally wanted.
        # These will print for silent = 0 or 1 regardless of current server.
        # See also the above unconditional-event list.
        if ev in [
            "_connected_", "_disconnected_",
            "messagedeliver", "serverupdate",
        ]:
            silent = 0
        if silent > 1:
            # Silent = 2; nothing prints beyond the above exceptions, regardless of current server.
            return
        if silent and self.shortname != self.parent.curServer.shortname:
            # Silence unless it's the current server.
            return
        TeamtalkServer.outputFromEvent(self, line, raw)

    def hookEvents(self, eventline, afterDispatch):
        """Called on each event with the event's parmline as a parameter.
        This method is called twice per event:
        once before and once after the event is dispatched.
        The afterDispatch parameter indicates which type of call is occurring.
        This method is not reentrant.
        """
        self._evparms = None if afterDispatch else eventline
        TeamtalkServer.hookEvents(self, eventline, afterDispatch)
        if not afterDispatch:
            logger.info("%s\n  %s: %s\n" % (
                datetime.now().ctime(),
                self.shortname,
                eventline.initLine.rstrip()
            ))
            return
        if eventline.event in ["userbanned", "useraccount"]:
            # These events are responses to listing commands and
            # should not trigger activity.
            return
        try: self.triggers.apply(eventline)
        except Exception as e:
            self.output(f"Trigger failure: {err()}")
            self.output("    " +errTrace())

class Servers(dict):
    def __init__(self, parent):
        # This is a TTComCmd object.
        self.parent = parent

    def add(self, newServer):
        """Add a new server.
        """
        shortname = newServer.shortname
        if shortname in self:
            self.remove(shortname)
        self[shortname] = newServer

    def remove(self, shortname):
        """Stop and remove a server connection.
        Shortname may be a shortname or an actual server object.
        """
        if issubclass(type(shortname), TeamtalkServer):
            shortname = shortname.shortname
        server = self[shortname]
        server.disconnect()
        del self[shortname]

class TTComCmd(MyCmd):
    @property
    def prompt(self):
        p = super().prompt
        cs = self._curShortname
        if cs:
            p = p.replace(">", " "+cs+">")
            try: chanid = self.curServer.me.get("chanid")
            except AttributeError: chanid = None
            if chanid:
                channel = self.curServer.channels.get(chanid)
                cname = channel.get("channel")
                if cname:
                    p = p.replace(">", cname+">")
        if self.prefix: p += self.prefix +" "
        return p

    @classproperty
    def speakEvents(cls):
        "Whether to speak events."
        return conf.option("speakEvents")

    def __init__(self, noAutoLogins=False, logins=None):
        super().__init__()
        self.add_option("nickLimit", "Longest nickname printed before it is cut short with ellipses.")
        if logins is None: logins = []
        if logins:
            noAutoLogins = True
        self.noAutoLogins = noAutoLogins
        self.servers = Servers(self)
        self._curShortname = ""
        MyCmd.__init__(self)
        TeamtalkServer.write = self.msg
        TeamtalkServer.writeEvent = self.msgFromEvent
        self.prefix = ""
        try:
            self.readServers(logins)
        except ConfigError as e:
            print(str(e))

    @property
    def curServer(self):
        if not self._curShortname:
            raise CommandError("No current server has been set.")
        try: return self.servers[self._curShortname]
        except KeyError: raise CommandError(f"Server {self._curShortname} is no longer in the server list.")

    def precmd(self, line):
        """Handles >-to-"switch " translation.
        Also handles :/;-to-"summary " translation.
        """
        l = line.strip()
        if l and self.prefix and not l.startswith("/"):
            l = self.prefix +" " +l
            line = l
        elif l.startswith("/"):
            l = l[1:].lstrip()
            line = l
        if l.startswith("?"): l = l[1:].lstrip()
        elif re.match(r'^help\s', l.lower()): l = l.split(None, 1)[1]
        if l and l[0] == ">":
            line = line.replace(">", "switch ", 1)
        if l and l[0] == ":":
            line = line.replace(":", "summary ", 1)
        elif l and l[0] == ";":
            line = line.replace(";", "summary ", 1)
        return MyCmd.precmd(self, line)

    def do_prefix(self, line):
        """Set or clear a prefix to be added to all commands until the prefix is again changed.
        Used primarily for "querying" (an IRC term) someone for text chatting.
        To send a command when a prefix is in effect, put a slash (/) at the start of the line.
        Otherwise your command will be prefixed also.
        Examples:
            prefix umsg bob
            Hi there!      (goes to Bob)
            /whoIs bob   (the slash makes this go to TTCom as a command)
            /prefix   (clears the prefix)
            whoIs bob    (goes to TTCom as a command)
        Warning: Be sure your prefix will work for its lifetime; e.g., "bob" is not a good way to send
        messages if another Bob might appear on server while you are doing it.
        """
        self.prefix = line.strip()

    def readServers(self, logins=None):
        if logins is None: logins = []
        waitFors = []
        curservers = conf.servers()
        curset = set(curservers.keys())
        oldset = set(self.servers.keys())
        anyDel = False
        for oldserver in oldset-curset:
            self.msg("Deleting " +oldserver)
            svr = self.servers[oldserver]
            svr.terminate()
            del svr
            del self.servers[oldserver]
            anyDel = True
        if anyDel and self._curShortname not in self.servers:
            ns = ""
            if len(self.servers): ns = list(self.servers.keys())[0]
            self.msg(f"Current server is {ns}" if ns else "No current server")
            self._curShortname = ns
        for shortname,parms in curservers.items():
            if not self._curShortname:
                # This will cause the final active shortname to be the last one seen here.
                # Near the end of this method though, the last server requesting login will override this.
                self._curShortname = shortname
            host = ""
            tcpport = None
            loginParms = {}
            autoLogin = 0
            silent = 0
            hidden = 0
            encrypted = False
            triggers = Triggers(self.onecmd)
            doLogin = None
            for k,v in parms.items():
                if k.lower() == "host":
                    host = v
                elif k.lower() == "tcpport":
                    tcpport = int(v)
                elif k.lower() == "autologin":
                    if not int(self.noAutoLogins):
                        autoLogin = int(v)
                elif k.lower() == "silent":
                    silent = int(v)
                elif k.lower() == "hidden":
                    hidden = int(v)
                elif k.lower() == "encrypted":
                    if v.lower() in ["1", "true"]: encrypted = True
                    elif v.lower() in ["0", "false"]: encrypted = False
                    else: encrypted  = False
                elif k.lower().startswith("match ") or k.lower().startswith("action "):
                    which,what = k.split(None, 1)
                    if "." in what:
                        triggerName,subname = what.split(".", 1)
                    else:
                        triggerName,subname = what,""
                    if which.lower() == "match":
                        triggers.addMatch(triggerName, ParmLine(v), subname)
                    else:  # action
                        triggers.addAction(triggerName, v, subname)
                elif k.lower() in powercom_features.CONFIG_KEYS:
                    pass
                else:
                    loginParms[k.lower()] = v
            newServer = MyTeamtalkServer(self, host, tcpport, shortname, loginParms)
            reconfig = False
            if autoLogin:
                newServer.autoLogin = autoLogin
            if silent:
                newServer.silent = silent
            if hidden:
                newServer.hidden = hidden
            if encrypted:
                newServer.encrypted = encrypted
            # TODO: This is an odd way to get this link made.
            triggers.server = newServer
            newServer.triggers = triggers
            oldGone = False
            if shortname in self.servers:
                oldServer = self.servers[shortname]
                if (oldServer.host != newServer.host
                or oldServer.tcpport != newServer.tcpport
                or oldServer.encrypted != newServer.encrypted
                ):
                    self.msg("Changing connection information for " +shortname)
                    oldServer.autoLogin = 0
                    oldServer.terminate()
                    oldServer.disconnect()
                    del oldServer
                    oldGone = True
                    self.servers[shortname] = newServer
                    doLogin = int(newServer.autoLogin)
                    reconfig = True
                    if doLogin: doLogin = newServer
                elif oldServer.loginParms != newServer.loginParms:
                    self.msg("Changing login information for " +shortname)
                    oldServer.logout()
                    oldServer.loginParms = newServer.loginParms
                    doLogin = int(newServer.autoLogin)
                    if doLogin: doLogin = oldServer
                elif newServer.autoLogin and not oldServer.autoLogin and oldServer.state != "loggedIn":
                    doLogin = oldServer
                if not oldGone:
                    if oldServer.autoLogin != newServer.autoLogin:
                        if not reconfig: self.msg("autoLogin for %s changing to %d" % (shortname, newServer.autoLogin))
                    oldServer.autoLogin = newServer.autoLogin
                    if oldServer.silent != newServer.silent:
                        if not reconfig: self.msg("silent for %s changing to %d" % (shortname, newServer.silent))
                    oldServer.silent = newServer.silent
                    if oldServer.hidden != newServer.hidden:
                        if not reconfig: self.msg("hidden for %s changing to %d" % (shortname, newServer.hidden))
                    oldServer.hidden = newServer.hidden
                    if oldServer.triggers != newServer.triggers:
                        if not reconfig: self.msg(f"Updating triggers for {shortname}")
                    oldServer.triggers = newServer.triggers
                    # TODO: Again, weird way to set this link up.
                    oldServer.triggers.server = oldServer
            else:
                self.servers.add(newServer)
                doLogin = int(newServer.autoLogin)
                if doLogin: doLogin = newServer
            if doLogin and self.noAutoLogins:
                doLogin = None
            if not doLogin and shortname in logins:
                doLogin = newServer
            if doLogin:
                doLogin.login(True)
                waitFors.append(doLogin)
        halfsecs = 0
        incomplete = False
        while any([server.state not in ["loggedIn", "loginError"] for server in waitFors]):
            halfsecs += 1
            if halfsecs == 20:
                incomplete = True
                break
            time.sleep(0.5)
        time.sleep(0.5)
        Triggers.loadCustomCode()
        #self.do_shortSummary()
        unfinished = []
        for server in waitFors:
            # This will cause the final active shortname to be the last one seen here.
            self._curShortname = server.shortname
            if server.state != "loggedIn":
                unfinished.append(server.shortname)
        if len(unfinished):
            self.msg("Servers that did not connect: " +", ".join(unfinished))

    def userMatch(self, u, checkAll=False, allowMultiple=False):
        """Match a user to what was typed/passed, asking for a
        selection if necessary. Returns a user object.
        If allowMultiple is True, a list is returned instead.
        The passed string is checked for containment in nickname,
        username, and userid fields. To match a userid exactly, use a
        number sign ("#") followed with no spaces by the userid;
        example: #247. If the userid matches a user, that user is used.
        """
        # If checkAll is True, all servers' users are checked (not well tested or used).
        if checkAll:
            users = []
            list(map(lambda s: users.extend(s.users),
                self.servers
            ))
        else:
            users = self.curServer.users
        if u.startswith("#") and u[1:].isdigit():
            users = [u1 for u1 in users.values() if u1.userid == u[1:]]
        else:
            users = [u1 for u1 in users.values() if u.lower() in self.curServer.nonEmptyNickname(u1, "dnc").lower()]
        if checkAll:
            flt = lambda u1: u1.server.shortname +"/" +self.curServer.nonEmptyNickname(u1, "dnc")
        else:
            flt = lambda u1: self.curServer.nonEmptyNickname(u1, "dnc")
        return self.selectMatch(users, "Select a User", flt, allowMultiple=allowMultiple)

    def channelMatch(self, c, noPrompt=False, allowMultiple=False):
        """Match a channel to what was typed/passed, asking for a
        selection if necessary and allowed by the caller. Returns a channel object.
        If the channel spec given includes an equal sign (=), a channel is selected by property; e.g., chanid=5.
        Otherwise the passed string is checked for containment in the channel name.
        If c contains a slash (/), the full name is checked;
        otherwise just the final component of channel names are checked.
        A channel name of "/" always matches just the root channel (chanid 1).
        A channel name starting and ending with "/" must match a
        channel exactly, except for case.
        If noPrompt is passed and True, a KeyError is thrown if more than one channel matches.
        If allowMultiple is passed and True, a list of channels is returned and it may contain zero or more channels.
        It is an error to specify allowMultiple=True if noPrompt is also True.
        """
        channels = self.curServer.channels
        if c == "/":
            return [channels["1"],] if allowMultiple else channels["1"]
        elif c.startswith("/") and c.endswith("/"):
            # Exact match (except for case) required.
            channels = [c1 for c1 in channels.values() if c.lower() == self.curServer.channelname(c1["chanid"]).lower()]
        elif "=" in c:
            # Specific parameter search like chanid=5.
            channels = [chan for chan in channels.values() if self.filterPasses(chan, [c])]
        elif "/" in c:
            # Containment match against full channel paths, case ignored.
            channels = [c1 for c1 in channels.values() if c.lower() in self.curServer.channelname(c1["chanid"]).lower()]
        else:
            # Match against channel names (no paths), case and final / ignored.
            channels = [c1 for c1 in channels.values() if c.lower() in self.curServer.channelname(c1["chanid"])[:-1].rpartition("/")[2].lower()]
        # selectMatch handles the 0 and 1 match cases properly without prompting.
        if not noPrompt or len(channels) <= 1:
            return self.selectMatch(channels, "Select a Channel",
                lambda c1: self.curServer.channelname(c1["chanid"]),
                allowMultiple=allowMultiple
            )
        # Too many matches when we can't prompt for a selection.
        raise CommandError("Error: More than one channel matched")

    def fileMatch(self, fspec, ownerFilter=None, permCheck=False):
        """Obtain a list of files from the server that match a specification.
        The file spec can be "*" to match all files and is otherwise a containment match with case ignored.
        If the spec contains a slash, what comes before it is a channel spec.
        see channelMatch() for rules on channel matching. The channel spec can also be "*" to match all channels.
        Channel specs are only allowed for admins, in order to avoid some security and inconsistency problems that would otherwise occur.
        If ownerFilter is None (default) or an empty list, all matching files regardless of owner are considered.
        Otherwise, it is a list of case-insensitive containment matches, with the following special cases:
            The null string ("") matches the anonymous account.
            None matches the current user.
            * prompts the user to select from all accounts.
        If any list element matches zero or more than one account,
        the user is prompted to select from all accounts matching any list element.
        Accounts to match are chosen from the owners of the files already selected by name and/or channel.
        if permCheck is True and this user is not an admin, only this user's files will match;
        and it will be an error to specify other owners.
        This is a helper for the file_get/delete/check subcommand methods.
        """
        cspec = ""
        chanid = None
        if "/" in fspec:
            if int(self.curServer.me.usertype) != 2:
                raise CommandError("You must be an admin in order to reference non-current channels with this command")
            cspec,fspec = fspec.rsplit("/", 1)
            if not cspec: cspec = "/"
        else:
            chanid = self.curServer.me.get("chanid")
            if not chanid: raise ValueError("Not in a channel")
        # Start with all files matching fspec.
        if not fspec or fspec == "*":
            files = list(self.curServer.files.values())
        else:
            files = [f for f in self.curServer.files.values() if fspec.lower() in f.filename.lower()]
        # Now narrow by channel.
        if chanid:
            files = [f for f in files if f.chanid == chanid]
        elif cspec and cspec != "*":
            channels = self.channelMatch(cspec, allowMultiple=True)
            files = [f for f in files if f.chanid in [c.chanid for c in channels]]
        # This is as close as we can get to most recent last order.
        # uploadtime is added in TeamTalk 5.8.1.
        files.sort(key=lambda f: (float(f.uploadtime or 0), int(f["fileid"])))
        # Now narrow by owner if necessary.
        if ownerFilter is None: ownerFilter = []
        files = self._restrictFilesByOwner(files, ownerFilter, permCheck)
        # Now let the user select from that list.
        return self.selectMatch(files, "Select Files",
            lambda f: self._formattedFile(f),
            allowMultiple=True,
            sort=False,
            promptOnSingle=True
        )

    def _restrictFilesByOwner(self, files, ownerFilter, permCheck):
        """Apply an owner filter to the given file list and return the result.
        See fileMatch() for how ownerFilter and permCheck args work.
        """
        wantedOwners = set()
        if permCheck and int(self.curServer.me.usertype) == 2:
            permCheck = False
        if permCheck:
            wantedOwners.add(self.curServer.me.username)
        if not wantedOwners and (not ownerFilter or len(ownerFilter) == 0):
            return files
        fileOwners = set([f.owner for f in files])
        # select indicates whether to prompt the user for final owner selection.
        select = False
        for spec in ownerFilter:
            if spec is None:
                wantedOwners.add(self.curServer.me.username)
            elif spec == "":
                wantedOwners.add("")
            elif spec == "*":
                # Replace the whole set with all accounts relevant to this command.
                wantedOwners = fileOwners
                select = True
                # No need to check more ownerFilter entries since we already include everything.
                break
            else:
                # A containment match spec, case insensitive.
                # The "" entry was already handled so it won't match everything here.
                matches = [o for o in fileOwners if spec.lower() in o.lower()]
                # Add all matches to wantedOwners but flag user selection prompt if that's not exactly one more account.
                wantedOwners |= set(matches)
                if len(matches) != 1:
                    select = True
        if select:
            wantedOwners = self.selectMatch(list(wantedOwners), "Select One or More File Owners", allowMultiple=True)
        if not len(wantedOwners):
            raise CommandError("No owners matched")
        if permCheck and wantedOwners != set([self.curServer.me.username, ]):
            raise CommandError("You can only access your own files with this command")
        files = [f for f in files if f.owner in wantedOwners]
        return files

    def _formattedFile(self, f):
        """Format a file for presentation in a file list.
        """
        owner = f.owner
        owner = f"username {owner}" if owner else "the anonymous account"
        ch = self.curServer.channelname(f.chanid)
        buf = f"{f.filename} from {owner} in {ch}, size {f.filesize}"
        if f.uploadtime: buf += f", uploaded {self.ctime(float(f.uploadtime))}"
        return buf

    def serverMatch(self, s):
        """Match a server to what was typed/passed, asking for a
        selection if necessary. Returns a server object.
        Matches are for containment, but an exact match takes precedence;
        so "nick" matches "nick" even if "nick1" is also a server.
        """
        servers = list(self.servers.keys())
        servers = [s1 for s1 in servers if s.lower() in s1.lower()]
        try: return self.servers[s]
        except KeyError: pass
        return self.servers[self.selectMatch(servers, "Select a Server")]

    def versionString(self):
        """Return the version string for PowerCom.
        """
        return (
"""PowerCom
Based on TeamTalk Commander (TTCom).
Copyright (c) 2011-2026 Doug Lee.

This program is covered by version 3 of the GNU General Public License.
This program comes with ABSOLUTELY NO WARRANTY.
This is free software, and you are welcome to redistribute it under
certain conditions.
See the file LICENSE.txt for further information.
The ConfigObj module is under separate license and copyright;
see that file for details.

PowerCom version %ver%
""".strip()).replace("%ver%", conf.version)

    def do_about(self, line=""):
        """Show the copyright and version information for PowerCom.
        """
        self.msg(self.versionString())

    def do_powercom(self, line=""):
        """Manage PowerCom speech output."""
        powercom_features.do_powercom(self, line)

    def help_powercom(self):
        powercom_features.help_powercom(self)

    def do_www(self, subpage=""):
        """Open a web page for this utility or related to it.
        Available pages (type "www <name>", example "www man"):
            home or man or blank (just "www"): The TTCom user guide.
            tt: The TeamTalk home page.
        """
        return self.launchPage(subpage)

    def do_man(self, line=""):
        """Go to the online TTCom manual (users guide).
        Same as "www man."
        """
        return self.launchPage("man")

    def launchPage(self, subpage=""):
        """Launch the indicated page or a default page. If HTML is passed, indicated by a leading <, launch that page from a temporary file.
        Specific page names supported:
            home or man: The TTCom manual / users guide page.
            tt: The home page for TeamTalk.
        If subpage is blank or not given, home is assumed.
        """
        if subpage.startswith("<"):
            html = subpage
            subpage = "ttcom_temp.htm"
            subpage = os.path.join(tempfile.gettempdir(), subpage)
            with open(subpage, "w") as f: f.write(html)
            # Make a URL of this.
            subpage = re.sub(r'^/cygdrive/(.)/', r'\1:/', subpage)
            subpage = re.sub(r'^/mnt/(.)/', r'\1:/', subpage)
            if not subpage.startswith("/"): subpage = "/" +subpage
            subpage = "file://" +subpage
            if not self.launchURL(subpage):
                self.msg("Not supported on this platform.")
            # ToDo: The temporary file remains on disk.
            return
        sp = subpage.lower()
        if sp in ["", "home", "man"]:
            sp = ""
            url = "https://www.dlee.org/ttcom/" +sp
        elif sp == "tt":
            url = "https://bearware.dk"
        else:
            self.msg(subpage +" is not a recognized web page identifier.")
            return
        if not self.launchURL(url):
            self.msg("Not supported on this platform.")
            return

    def do_rel(self, line=""):
        super().rel("https://www.dlee.org/teamtalk/ttcom/ttcom.htm", "https://www.dlee.org/teamtalk/ttcom/beta/ttcom.htm", line, start=75000)
    do_rel.__doc__ = MyCmd.rel.__doc__

    def do_version(self, line=""):
        """Shows the version of the currently selected server when connected.
        With an argument, shows the version of the indicated user's client, and client name if available.
        If multiple users match, a list is presented that allows multiselection.
        """
        line = line.strip()
        server = self.curServer
        if not line:
            try: sver = server.info.version
            except Exception: pass
            if not sver:
                raise CommandError(f"Server {server.shortname} version not available")
            self.msg(f"{server.shortname} server version {sver}")
            if server.state != "loggedIn":
                self.msg("Warning: Not logged in, version information may be out of date.")
            return
        # Client indicated.
        users = self.userMatch(line, allowMultiple=True)
        if not users: return
        for user in users:
            cname = user.get("clientname").strip()
            cver = user.get("version").strip()
            if cname and cver: cname = f"{cname} version {cver}"
            elif cver: cname = f"Version {cver}"
            cname = f"{self.curServer.nonEmptyNickname(user, 'n')}:\n    {cname}"
            self.msg(cname)

    def do_box(self, line=""):
        """Manage box address names, for convenience in server configuration.
        This command works very similarly to the alias command:
        box by itself lists any defined box names.
        box with a name shows that box's address.
        box with a name and an address sets the address for that box name.
        box with a name preceded by a dash (-) removes that box's entry.
        More than one box can be removed at once; e.g., box -me you bill
        Box names can be used in place of addresses on host lines in a server definition.
        A box name may not contain a dot (.) or a colon (:).
        Warning: Box names and addresses are stored in ttcom.ini, not ttcom.conf.
        Keep this in mind when using a server definition file on more than one computer.
        This could be useful; e.g., if one machine can use a local address for a box and another can't.
        """
        boxSect = "Boxes"
        if not line:
            # List all defined boxes.
            try: boxes = conf[boxSect]
            except Exception:
                self.msg("No boxes defined")
                return
            s = "Boxes:"
            found = False
            for box in sorted(boxes.keys()):
                found = True
                val = boxes[box]
                s += f"\n   {box} = {val}"
            if not found:
                self.msg("No boxes defined")
                return
            self.msg(s)
            return
        removing = False
        if line[0] == "-":
            line = line[1:]
            if not line:
                raise SyntaxError("Must specify a box to remove")
            removing = True
        args = shlex.split(line)
        # These characters are forbidden in box names, so they are easier to distinguish from addresses.
        # Removal is allowed to remove such box entries in case they are so defined.
        forbidden = ".:"
        try: boxes = conf[boxSect]
        except Exception: boxes = []
        if removing:
            # Allow multiple boxes to be removed at once.
            # Syntactic leniency: When removing boxes, the leading dash on non-first boxes is optional.
            # Minor side effect: "box --blah" becomes the same as "box -blah" here.
            args = [re.sub(r'^-', '', arg) for arg in args]
            # In case of manual file modification.
            conf.load()
            for arg in args:
                box = arg.lower()
                try: del conf.user_cfg[boxSect][box]
                except KeyError: self.msg(f"No {box} box")
                else: self.msg(f"Box {box} removed")
            conf.user_cfg.write()
            conf.load()
            return
        elif len(args) == 1:
            # Just asking for the address of a box.
            try: val = boxes[args[0].lower()]
            except KeyError:
                self.msg(f"No {args[0].lower()} box")
                return
            self.msg(f"{args[0].lower()} is {val}")
            return
        # Only adding a new box remains.
        conf.load()
        lhs = args[0].lower()
        rhs = line.split(None, 1)[1]
        ucfg = conf.user_cfg
        ucfg.setdefault(boxSect, {})
        ucfg[boxSect][lhs] = rhs
        self.msg(f"{lhs} is now {rhs}")
        ucfg.write()
        conf.load()

    def do_vlist(self, line=""):
        """List users sorted by TeamTalk packet protocol, client name, and client version number.
        -p[<num>] filter: -p0 means text-only clients, -p means voice-capable clients, -p<num> restricts to a particular packetprotocol.
        (TT5 supports only packetprotocol 1 at this time; TT4 supported several.)
        """
        proto = None
        if line:
            if not line.startswith("-p"):
                self.msg("Unknown options: " +line)
                return
            line = line[2:].strip()
            if not line: proto = -1
            elif line.isdigit(): proto = int(line)
            else:
                self.msg("Unknown packet protocol number: " +line)
                return
        server = self.curServer
        server.summarizeVersions(proto)

    def do_cltypes(self, line=""):
        """Summarize clientnames across all logged-in servers.
        This current client is included.
        """
        clnames = {}
        totServers = totClients = 0
        for server in self.servers.values():
            if server.state != "loggedIn": continue
            users = [u for u in server.users.values()]
            if not len(users): continue
            totServers += 1
            for user in users:
                client = user.get("clientname")
                if client is None: client = "<noName>"
                clnames.setdefault(client, 0)
                clnames[client] += 1
                totClients += 1
        self.msg(f"Client names across {totServers:d} logged-in servers ({totClients:d} clients):")
        for clname in sorted(clnames, key=lambda k: k.lower()):
            self.msg(f"{clnames[clname]:8d}  {clname}")

    def do_switch(self, line):
        """Get or change the server to which subsequent commands will apply,
        or switch to a specific server just long enough to run one command.
        Usage: switch [serverName [command]]
        Without arguments, just indicates which server is current.
        With one argument, changes the current server.
        With more arguments, switches to a server, runs a command, and switches back.
        """
        args = shlex.split(line)
        newServer = None
        if len(args) >= 1:
            newServer = self.serverMatch(args.pop(0))
        if len(args) == 0:
            if newServer: self._curShortname = newServer.shortname
            self.msg(f"Current server is {self.curServer.shortname}")
            return
        # A command to run against a specific server (newServer).
        oldname = self._curShortname
        # Reparse to avoid spacing issues.
        tmp,line = line.split(None, 1)
        try:
            self._curShortname = newServer.shortname
            self.onecmd(line)
        finally:
            self._curShortname = oldname

    def do_refresh(self, line=""):
        """Refresh config and server info and update connections as necessary.
        """
        line = line.strip()
        # Reload ttcom.ini in case boxes were added/changed from outside.
        conf.load()
        if not line:
            self.readServers()
            return
        self.msg("Selective server refresh is not yet implemented")
        """
        shortnames = line.split()
        for shortname in shortnames:
            server = self.serverMatch(shortname)
        """

    def help_summary_flags(self):
        """Help for how to select users/clients for a summary command().
        """
        self.msg(self._formatHelp("""
Selecting users/clients for a summary command:

The summary, allSummary, and shortSummary commands that summarize who is where on servers allow flags that change their default behavior.
Summary and allSummary by default include all clients (-a) except "me" (current user);
and by default, shortSummary only includes voice-capable clients that are in channels (-cv).
To alter which users/clients are included, use a dash followed, without spaces, by one or more of the following:
    a: Include all clients (equivalent to -cntv).
    c: Include clients that are in a channel.
    m: Include me; must be done explicitly or the current user is omitted.
    n: Include clients that are not in a channel.
    t: Include text clients such as TTCom.
    v: Include voice-capable clients.
Special conveniences:
    -c by itself is equivalent to -ctv (all clients in a channel).
    -n is similarly equivalent to -ntv.
    -t is equivalent to -tcn (all text clients whether in a channel or not).
    -v is similarly equivalent to -vcn.
Examples beyond the listed conveniences:
    To get all voice clients that are not in a channel, use -vn
    To get all text clients in a channel, use -ct
    To get all clients including me / yourself, use -m or -am.
""".strip()))

    @staticmethod
    def _getSummaryFlags(line, flags="a"):
        """Get channel summary flags, or defaults when none found, and return them with the remaining part of the given line.
        """
        if line.startswith("-"):
            parts = line.split(None, 1)
            if len(parts) == 1: parts.append("")
            flags, line = parts
            flags = flags[1:]
        flags = TeamtalkServer.standardizeSummaryFlags(flags)
        return flags,line

    def do_summary(self, line=""):
        """Summarize the users and active channels on this or a given server.
        By default, all users/clients are included.
        For details on what flags can be included to change this, type Help Summary_flags.
        """
        flags,line = self._getSummaryFlags(line)
        if line:
            server = self.serverMatch(line)
        else:
            server = self.curServer
        server.summarizeChannels(flags)

    def do_allSummary(self, line=""):
        """Summarize user/channel info on all connected servers.
        Servers marked hidden in the config file are omitted.
        By default, all users/clients are included.
        For details on what flags can be included to change this, type Help Summary_flags.
        """
        flags,line = self._getSummaryFlags(line)
        if len(self.servers) == 0:
            self.msg("No servers.")
            return
        offs = {}
        empties = []
        sums = []
        serverCount = 0
        stateCounts = {}
        for shortname in sorted(self.servers):
            server = self.servers[shortname]
            stateCounts.setdefault(server.state, 0)
            stateCounts[server.state] += 1
            serverCount += 1
            if server.hidden: continue
            if server.state != "loggedIn":
                offs.setdefault(server.state, [])
                offs[server.state].append(shortname)
            elif len(server.users) <= (0 if "m" in flags else 1):
                # 1 allows for this user.
                empties.append(shortname)
            else:
                sums.append(shortname)
        if len(offs):
            for k in sorted(offs.keys()):
                self.msg(f"{k}: {', '.join(offs[k])}")
        if len(empties):
            self.msg("No users: " +", ".join(empties))
        for shortname in sums:
            server = self.servers[shortname]
            server.summarizeChannels(flags)
        self.msg("Server count {0:d}: {1}".format(
            serverCount,
            ", ".join(["{0:d} {1}".format(stateCounts[state], state) for state in stateCounts])
        ))

    def do_shortSummary(self, line=""):
        """Short summary of who's on all logged-in servers with people.
        Servers marked hidden in the config file are omitted.
        By default, only voice-capable clients that are in channels are considered.
        For details on what flags can be included to change this, type Help Summary_flags.
        """
        flags,line = self._getSummaryFlags(line, "cv")
        if len(self.servers) == 0:
            self.msg("No servers.")
            return
        offs = {}
        sums = []
        serverCount = 0
        stateCounts = {}
        for shortname in sorted(self.servers):
            server = self.servers[shortname]
            stateCounts.setdefault(server.state, 0)
            stateCounts[server.state] += 1
            serverCount += 1
            if server.hidden: continue
            if server.state != "loggedIn":
                if server.state == "disconnected" and not server.autoLogin:
                    continue
                state = server.state
                if server.conn and server.conn.state and server.conn.state != state:
                    state += "/" +server.conn.state
                offs.setdefault(state, [])
                offs[state].append(shortname)
            elif len(server.users) <= (0 if "m" in flags else 1):
                # 1 allows for this user.
                continue
            else:
                sums.append(shortname)
        if len(offs):
            for k in sorted(offs.keys()):
                self.msg(f"{k}: {', '.join(offs[k])}")
        for shortname in sums:
            server = self.servers[shortname]
            self.oneShortSum(server, flags)
        self.msg("Server count {0:d}: {1}".format(
            serverCount,
            ", ".join(["{0:d} {1}".format(stateCounts[state], state) for state in stateCounts])
        ))

    def oneShortSum(self, server, flags):
        """Short-form summary for one server.
        """
        users = server.applySummaryFlags(server.users.values(), flags)
        if not len(users):
            return
        users = [server.nonEmptyNickname(u) for u in users]
        users.sort(key=lambda u: u.lower())
        tuc = len(users)
        ucounts = {}
        for u in users:
            ucounts.setdefault(u, 0)
            ucounts[u] += 1
        users = []
        for u,uc in ucounts.items():
            if uc > 1:
                u = f"{uc} x {u}"
            users.append(u)
        line = "%s (%d): %s" % (
            server.shortname,
            tuc,
            ", ".join(users)
        )
        self.msg(line)

    def do_join(self, line):
        """Join a channel.
        Usage: join channelname [password]
        channelname and/or password can contain spaces if quoted.
        If no password is given and the requested channel has one, it will be prompted for.
        Channel / always refers to the root channel.
        A channel starting and ending with / must match exactly except for letter casing.
        A channel containing a / is matched against all full channel names (path included).
        Otherwise the channel is matched against only the actual channel names, without paths.
        A channel may also be specified by property; e.g., chanid=5.
        This command will not create temporary channels.
        """
        args = shlex.split(line)
        channel,password = "",""
        if args: channel = args.pop(0)
        if args: password = args.pop(0)
        channel = self.channelMatch(channel)
        if int(channel.protected) and not password:
            password = getpass("Channel password: ")
        self.do_send(f'join chanid={channel.chanid} password="{password}"')

    def do_leave(self, line):
        """Leave a channel.
        Usage: leave [channelname]
        channelname can be multiple words and can optionally be quoted.
        channelname can also be omitted to leave the current channel.
        """
        if not line.strip():
            self.do_send("leave")
            return
        line = self.dequote(line)
        ch = self.channelMatch(line)
        self.do_send('leave channel=' +ch.chanid)

    def do_nickname(self, line):
        """Set a new nickname or check the current one.
        """
        if line:
            line = self.dequote(line)
            self.do_send(f"changenick nickname=\"{line}\"")
            return
        nick = self.curServer.me.nickname
        self.msg("You are now %s" % (
            self.curServer.nonEmptyNickname(self.curServer.me, "n")
        ))

    def do_connect(self, line=""):
        """Connect to a server without logging in.
        """
        self.curServer.connect()

    def do_disconnect(self, line=""):
        """Disconnect from a server.
        """
        # Sending "quit" can make other clients notice the disconnect sooner.
        self.curServer.send("quit")
        time.sleep(0.5)
        if self.curServer.state != "disconnected":
            self.curServer.disconnect()

    def do_login(self, line=""):
        """Log into a server, connecting first if necessary.
        This also clears the "manual connection management" flag raised by being kicked off.
        """
        # The manualCM flag is set to True if autoLogin is not 2 when this instance is kicked from a server.
        self.curServer.manualCM = False
        self.curServer.login()

    def do_logout(self, line=""):
        """Log out of a server.
        """
        self.curServer.logout()

    def do_auth(self, line):
        """Handle login authentication via the BearWare website.
        See the TTCom user guide for details on this process.
        """
        id,token = None,None
        try:
            id = conf.option("username", section="bearwareid")
            token = conf.option("token", section="bearwareid")
        except Exception: pass
        if id and token and not self.confirm("Your BearWare account is already set up. Replace or revalidate (y/n)?"): return
        if not self.confirm("Have you already created a BearWare login account (y/n)?"):
            url = "https://bearware.dk/wordpress/wp-login.php?action=register"
            self.msg(f"Bearware accounts are created by visiting {url}")
            if self.confirm("Launch browser now (y/n)?"):
                if not self.launchURL(url):
                    self.msg("Not supported on this platform. Visit the above URL manually.")
            else:
                self.msg("To create an account, visit the above URL and fill out the form.")
            self.msg("Once you have an account, enter the login and password at the following prompts.")
        id = input("Enter your BearWare username: ")
        if id: id = id.strip()
        if id.lower().endswith("@bearware.dk"): id = id.rsplit("@", 1)[0].rstrip()
        if not id:
            self.msg("Account setup aborted")
            return
        pw = getpass("Enter your BearWare password (this will not be stored anywhere by TTCom): ")
        if pw: pw = pw.strip()
        if not pw:
            self.msg("Account setup aborted")
            return
        self.msg("Validating ...")
        url = "https://www.bearware.dk/teamtalk/weblogin.php?" +urllib.parse.urlencode((
            ("client", conf.name),
            ("version", f"0.0.0.{conf.version}"),
            ("dllversion", "0.0.0.0000"),
            ("os", sys.platform),
            ("service", "bearware"),
            ("action", "auth"),
            ("username", id),
            ("password", pw),
        ))
        with urllib.request.urlopen(url) as stream: xml = stream.read().decode("UTF-8")
        # On auth failure, that throws an error that prints: HTTPError: HTTP Error 401: Authentication Failed
        d = xmlToDict(xml)
        if not d.get("username"): raise ValueError("No username found in BearWare website authentication response")
        if not d.get("token"): raise ValueError("No token found in BearWare website authentication response")
        id = d["username"]
        token = d["token"]
        conf.option("username", id, section="bearwareid")
        conf.option("token", token, section="bearwareid")
        for k,v in d.items():
            if k == "token": continue
            self.msg(f"{k} {v}")
        self.msg("Account validated for use in TTCom")

    def do_broadcast(self, line):
        """Send a broadcast message to all people on a server,
        even those who are currently not in a channel. The message
        shows up in the main message window for each user.
        If no message is given, one is prompted for; and these may contain more than one line of text.
        Example usage: Broadcast Server going down in five minutes.
        This command requires the Broadcast user right.
        """
        if not line:
            line = self.getMultilineValue()
        if not line:
            self.msg("No broadcast message specified.")
            return
        parms = TTParms([KeywordParm("message"),
            IntParm("type", 3),
        ])
        self._sendMessage(parms, line)

    def do_move(self, line):
        """Move one or more users to a new channel.
        Usage: move user1[, user2 ...] channel
        Users and channels can be ids or partial names.
        A user can also be @channelName, which means all users in that channel.
        Example: move doug "bill cosby" @main" away
        means move doug, Bill Cosby, and everyone in main to away,
        where "main" and "away" are contained in channel names on the server.
        This command requires the Move user right.
        """
        args = shlex.split(line)
        if not args: raise SyntaxError("No user(s) or channel specified")
        if len(args) < 2: raise SyntaxError("At least one user and a channel are required")
        users = []
        channel = None
        for u in args[:-1]:
            if u.startswith("@"):
                chan = self.channelMatch(u[1:])
                cid = self.curServer.channels[chan["chanid"]]["chanid"]
                for u1 in self.curServer.users.values():
                    if u1.get("chanid") == cid:
                        users.append(u1)
            else:
                users.extend(self.userMatch(u, allowMultiple=True))
        channel = self.channelMatch(args[-1])
        for u in users:
            self.do_send(f"moveuser userid={u['userid']} chanid={channel['chanid']}")

    def do_cmsg(self, line):
        """Send a message to the current channel or, for admins, another channel.
        Usage: cmsg [@<channelname>] <message>
        Sends to the current channel unless another channel name is given.
        If no message is given, one is prompted for; and these may contain more than one line of text.
        Examples:
            cmsg Hello to everyone in my current channel...
            cmsg @blah Hello to the people in the blah channel.
        """
        if line.startswith("@"):
            try: channel,msg = line.split(None, 1)
            except ValueError: channel,msg = line,""
            channel = self.channelMatch(channel[1:])
        else:
            msg = line
            try: channel = self.curServer.channels[self.curServer.me.chanid]
            except (KeyError, AttributeError):
                raise CommandError("You are not in a channel")
        msg = msg.strip()
        if not msg:
            msg = self.getMultilineValue()
        if not msg:
            raise SyntaxError("A message must be specified")
        parms = TTParms([KeywordParm("message"),
            IntParm("type", 2),
            IntParm("chanid", channel.chanid),
        ])
        self._sendMessage(parms, msg)

    def do_subscribe(self, line):
        """Change or just display subscriptions and intercepts for a user.
        To display only, include no arguments.
        To add subscriptions or intercepts, specify a plus sign (+) followed by the letters to change.
        To remove, use a dash (-) instead. To toggle, use a caret (^).
        To use the default value for TeamTalk, use a dot (.).
        If there is no prefix, a plus sign is assumed.
        Letters can be grouped; example, subscribe dlee cb -u
        (That would subscribe to channel messages and broadcasts but not user messages).
        Intercepts are the upper-case versions of subscriptions: subscribe dlee U intercepts user messages.
        A dot by itself sets subscriptions to default values and turns off any intercepts; example, subscribe dlee .
        Letters available for use, "the user" meaning the one specified in the command:
            u, user messages: Messages sent by the user to you.
            c, channel messages: Messages sent by the user to your current channel.
            b, broadcast messages: Messages sent by the user to the entire server.
            t, typing notifications: TeamTalk notifications indicating when the user is typing to you.
                This subscription is also used for TTCom pmsg messages sent to you.
        Letters available but that do not cause TTCom to receive or display any more data:
            a, audio: Sound sent by the user.
            v, video: Video sent by the user.
            d, desktop: The user's shared desktop.
            x, desktop control: Desktop control data from the user.
            s, stream: Media streams from the user.
        Letters k through q are also honored and represent subscriptions and intercepts that are currently
        not used by TeamTalk.
        WARNING: If you try to intercept but you are not an admin, the server will reject your request.
        If you tried to alter subscriptions in the same command, some or all of those may fail to take effect as well.
        Check the final printed subscription value to see what is in effect after the command.
        """
        args = shlex.split(line)
        if len(args) < 1:
            raise SyntaxError("A user must be specified")
        user = self.userMatch(args.pop(0))
        curSubs = ttflags.Subscriptions(int(user.sublocal))
        nextSubs = curSubs.alter(args, 1)
        removed,added = ttflags.Subscriptions.comm(curSubs, nextSubs)[0:2]
        # Issue any unsubscribes, then any subscribes.
        if removed:
            self.do_send(f"unsubscribe userid={user.userid} sublocal={removed!s}")
        if added:
            self.do_send(f"subscribe userid={user.userid} sublocal={added!s}")
        # Then list what remains active.
        curSubs = ttflags.Subscriptions(int(user.sublocal))
        self.msg(f"Subscriptions: {curSubs.toString('c')}")

    def do_pmsg(self, line):
        """Send a private message to another TTCom user.
        The message is sent in a way that does not appear in TeamTalk server logs and that
        appears not possible for official admin clients to intercept.
        Warning: This feature is not officially supported by TeamTalk itself and may not work on some servers.
        Sending a message to a regular TeamTalk client with this command will cause no effect or display.
        Neither the TeamTalk author nor the TTCom author can guarantee privacy with this feature,
        nor be sure that future or old TeamTalk server versions will treat these messages in the manner described here.
        A warning will print if the user is not set up to see the message being sent.
        """
        return self.do_umsg(line, True)

    def do_umsg(self, line, invisible=False):
        """Send a message to a user.
        Usage: umsg <user> <message>
        <user> can be anything that matches a user; e.g., full or partial nickname or username,
        or full or partial IP address when those are visible to this user.
        An exact userid can be indicated with a number sign: umsg #332 Hi there.
        If no message is given, one is prompted for; and these may contain more than one line of text.
        A warning will print if the user is not set up to see the message being sent.
        """
        args = line.split(None, 1)
        if len(args) < 1:
            raise SyntaxError("A user must be specified")
        try: userid,content = args
        except ValueError:
            userid = args[0]
            content = self.getMultilineValue()
        if not content: raise SyntaxError("A message must be given")
        if userid[0] == "#" and userid[1:].isdigit():
            userid = userid[1:]
        else:
            userid = self.userMatch(userid).userid
        mtype = 1
        if invisible: mtype = 4
        user = self.curServer.users.get(userid)
        subpeer = None
        if user:
                subpeer = user.get("subpeer")
        if subpeer:
            subpeer = ttflags.Subscriptions(subpeer)
            mask = ttflags.Subscriptions(0)
            # This checks for both subscriptions and intercepts.
            mask = mask.alter("+tT" if invisible else "+uU", 1)
            if not (subpeer & mask):
                    self.msg("Warning: This user is not set up to see this message")
        parms = TTParms([KeywordParm("message"),
            IntParm("type", mtype),
            IntParm("destuserid", userid),
        ])
        self._sendMessage(parms, content)

    def do_stats(self, line=""):
        """Show statistics for a server.
        This requires admin privileges on the server.
        The results print in much the same manner as for the TeamTalk client's Server Statistics option,
        except that data transfer statistics are printed in more human-friendly units.
        The server uptime also shows days instead of large numbers of hours and includes milliseconds.
        """
        stats = self.request("querystats")
        # Remove the final Ok event.
        resp = stats.pop()
        if (resp.event != "ok"
        or len(stats) != 1
        or stats[0].event != "stats"
        ):
            raise CommandError(resp)
        # Dict of actual stats returned.
        stats = stats[0].parms
        uptime = stats.pop("uptime", None)
        # That's in milliseconds - not so human-friendly.
        # This cuts off the milliseconds without math.
        uptime,ms = uptime[:-3], uptime[-3:]
        # This makes hh:mm:ss or dd:hh:mm:ss.
        uptime = self.curServer.secsToTime(uptime, days=True)
        # This makes it look more like the times printed by the Linux+ uptime(1) command.
        if uptime.count(":") == 3:
            # That implies non-zero days also.
            dd,uptime = uptime.split(":", 1)
            dd = f"{dd} day" if dd == 1 else f"{dd} days"
            uptime = f"{dd} + {uptime}"
        uptime += f".{ms}"
        self.msg(f"Server {self.curServer.shortname} up {uptime}")
        tbl = TableFormatter("Data Transfer Statistics", ["Data Type", "Received (RX)", "Sent (Tx"], noRowCount=True)
        for name,rx,tx in self.do_stats._tbldata:
            tbl.addRow((name, hrsize(stats.pop(rx, 0)), hrsize(stats.pop(tx, 0))))
        self.msg(tbl.format(2))
        self.msg(f"Total login count {stats.pop('usersserved', 0)}   Peak {stats.pop('userspeak', 0)}")
        # Anything that prints below is new since the above code was last updated.
        for k,v in stats.items():
            self.msg(f"{k}: {v}")
    do_stats._tbldata = [
        ("Voice", "voicerx", "voicetx"),
        ("Video Capture", "videocaprx", "videocaptx"),
        ("Media Files", "mediafilerx", "mediafiletx"),
        ("Desktops", "desktoprx", "desktoptx"),
        ("Total", "totalrx", "totaltx"),
        ("File Transfers", "filesrx", "filestx"),
    ]

    def do_server(self, line):
        """Server management.
        Run without arguments for a list of subcommands, or type a subcommand and -h for help with that subcommand.
        Example: server info -h.
        """
        args = shlex.split(line)
        self.dispatchSubcommand("server_", args)

    def server_info(self, args):
        "Use server info -h to get a full syntax description for this subcommand."
        parser = ArgumentParser(prog="server info", description="Print server information (properties).", epilog="Example: server info")
        opts = parser.parse_args(args)
        buf = TextBlock(ignoreZeros=True)
        if not self.curServer.conn:
            self.msg("Not connected")
            return
        inf = AttrDict(self.curServer.info.copy())
        buf.add("Server name", inf.pop("servername", None))
        mr = inf.pop("motdraw", None)
        if mr is None: mr = ""
        mr = mr.replace(r'\r\n', "\n").replace(r'\n', "\n")
        buf.add("Raw MOTD:\n", mr)
        tp = inf.pop("tcpport", None)
        up = inf.pop("udpport", None)
        if tp == up:
            buf.add("TCP and UDP ports", tp)
        else:
            buf.add("TCP port", tp)
            buf.add("UDP port", up)
        del tp, up
        buf.add("Server version", inf.pop("version", None))
        buf.add("Protocol version", inf.pop("protocol", None), True)
        buf.add("Max users", inf.pop("maxusers", None))
        buf.add("User timeout (seconds)", inf.pop("usertimeout", None), True)
        buf.add("Autosave", inf.pop("autosave", None), True)
        buf2 = TextBlock(ignoreZeros=True)
        buf2.add("Video", inf.pop("videotxlimit", None))
        buf2.add("Voice", inf.pop("voicetxlimit", None), True)
        buf2.add("Desktop", inf.pop("desktoptxlimit", None), True)
        buf2.add("Media file", inf.pop("mediafiletxlimit", None))
        buf2.add("Total", inf.pop("totaltxlimit", None), True)
        if str(buf2).strip():
            buf2 = str(buf2)
            if str(buf): buf += "\n"
            buf += "Bandwidth transmit limits (KBPS):\n"
            for line in buf2.splitlines():
                buf += f"   {line}\n"
        buf.add("Max login attempts from one IP before ban:", inf.pop("maxloginattempts", None))
        buf.add("Max simultaneous logins per IP:", inf.pop("maxiplogins", None))
        buf.add("Required delay between logins from one IP (seconds):", inf.pop("logindelay", None))
        levents = int(inf.pop("logevents", 0))
        if levents:
            buf.add("Event logging flags:", F"{hex(levents)} ({ttflags.LogEvents(levents).toString('wA')})")
        # Anything non-empty value not handled above goes here.
        # There are exceptions, such as motd (we print motdraw), and userid (see WhoIs for that).
        for k in sorted(inf):
            if k in ["motd", "userid"]: continue
            v = inf[k]
            buf.add(k, v)
        self.msg(buf)

    def userAction(self, cmd, user, useChannel=False, useChannelPath=False):
        """Perform an action on one or more users that just require a userid or a userid and a channel id or channel path.
        If useChannel is True, the user parameter must start with @channelSpec. Otherwise this client's current channel is used.
        If useChannel is False, the user parameter is just a user specification.
        If channelPath is True, the sent server command will specify the channel by path rather than by chanid.
        """
        if useChannel:
            if user.startswith("@"):
                channel,user = user.split(None, 1)
                channel = self.channelMatch(channel[1:])
            else:
                try: channel = self.curServer.channels[self.curServer.me.chanid]
                except (KeyError, AttributeError):
                    raise CommandError("You are not in a channel")
            if useChannelPath: chan = f' channel="{channel.channel}"'
            else: chan = f' chanid={channel.chanid}'
        else: chan = ""
        user = user.strip()
        if not user:
            raise SyntaxError("A user name or partial name must be specified")
        users = self.userMatch(user, allowMultiple=True)
        if not users: return
        for user in users:
            self.do_send(f'{cmd} userid="{user.userid}"{chan}')

    def do_kick(self, line):
        """Kick a user by name or ID from the server.
        This command requires the Kick user right on TT5 servers.
        If -c is used, kicks from channel rather than server.
        This usage requires the Kick user right or channel op status in the affected channel.
        """
        isChannel = False
        if line.split(None, 1)[0] == "-c":
            isChannel = True
            line = line[2:].lstrip()
        self.userAction("kick", line, isChannel)

    def do_ckick(self, line):
        """Kick a user by name or ID from the channel.
        This command requires the Kick user right or channel op status in the affected channel.
        """
        self.userAction("kick", line, True)

    def do_ban(self, line):
        """Ban management. Requires the Ban right.
        Run without arguments for a list of subcommands, or type a subcommand and -h for help with that subcommand.
        Example: ban list -h.
        """
        args = shlex.split(line)
        self.dispatchSubcommand("ban_", args)

    def ban_list(self, args):
        "Use ban list -h to get a full syntax description for this subcommand."
        parser = ArgumentParser(prog="ban list", description="List all or selected bans sorted (by default) by creation time.", epilog="Examples: ban list, ban li -sc, ban li bob, ban li 24.114., ban li channel=/, ban li !nickname=Bob")
        parser.add_argument("-a", "--all", action="store_true", help="Include all channel bans. WARNING: May take a while and may fail with flood limiting errors.")
        parser.add_argument("-C", "--channel", action="store_true", help="List all channel bans but not server bans. WARNING: May take a while and may fail with flood limiting errors.")
        parser.add_argument("-c", "--count", action="store_true", help="Show count only.")
        parser.add_argument("-s", "--sort",
            choices=("c","C","i","I","n","N","o","O","s","S","t","T","u","U","-"),
            default="t",
            help="Sort order: c channel, i IP address, o owner (ban creator), n nickname, s server order, t ban time (default), u username, upper case to reverse order."
        )
        parser.add_argument("filter", nargs="*", help='fieldname=value to match exactly against a specific field, or just value to match against any field. Fields include bantime, username, nickname, ipaddr, and channel. More than one filter can be given. Prefix fieldname with "!" to select mismatches instead of matches. Quote any values that contain spaces.')
        opts = parser.parse_args(args)
        bans = []
        if not opts.channel:
            bans.extend(self.getBans())
        if opts.all or opts.channel:
            self.msg(f"Collecting bans for {len(self.curServer.channels)} channels")
            # Unexpectedly, the next line added [] entries for each channel with no bans!
            # The below loop therefore replaces it. [DGL, 2024-04-02, Python 3.12]
            #[bans.extend(self.getBans(channel) for channel in self.curServer.channels.values())]
            for channel in self.curServer.channels.values():
                b = self.getBans(channel)
                if not b: continue
                bans.extend(b)
        if opts.filter: ttl = "Matching Bans"
        else: ttl = "Bans"
        parmsets = []
        for ban in bans:
            parms = ban.parms
            if not self.filterPasses(parms, opts.filter, True): continue
            parmsets.append(parms)
        if opts.count:
            self.msg(len(parmsets))
            return
        buf = []
        if not parmsets:
            self.msg(f"{ttl}: {len(parmsets)}")
            return
        buf.append(f"{ttl} ({len(parmsets)}):")
        if opts.sort in "cC":
            parmsets.sort(key=lambda p: p.channel or "", reverse=(opts.sort=="C"))
        elif opts.sort in "iI":
            parmsets.sort(key=lambda p: p.ipaddr or "", reverse=(opts.sort=="I"))
        elif opts.sort in "nN":
            parmsets.sort(key=lambda p: p.nickname or "", reverse=(opts.sort=="N"))
        elif opts.sort in "oO":
            parmsets.sort(key=lambda p: p.owner or "", reverse=(opts.sort=="O"))
        elif opts.sort == "S":
            # "s" is unchanged order.
            parmsets.reverse()
        elif opts.sort in "tT":
            parmsets.sort(key=lambda p: p.bantime or 0, reverse=(opts.sort=="T"))
        elif opts.sort in "uU":
            parmsets.sort(key=lambda p: p.username or "", reverse=(opts.sort=="U"))
        for i,parms in enumerate(parmsets):
            # I've seen over 100 bans but not over 1000; but if it happens, the time just shifts to the right.
            buf.append(f"{i+1:3d} {self.ctime(float(parms.bantime))}")
            indent = " " * (1 + max(3, len(str(i))))
            if parms.owner:
                hdr = f"User {parms.owner} banned "
            else:
                hdr = "Banned "
            hdr = indent + hdr
            # TeamTalk at least officially does not allow both username and IP to be banned in one record;
            # but due to the ambiguity of how ban types are handled, TTCom will show it if it ever happens.
            btype = ttflags.BanTypes(int(parms.type)).toString("c")
            if "u" in btype:
                hdr += f'user "{parms.username}" '
            if "i" in btype:
                hdr += f'address "{parms.ipaddr}" '
            if "c" in btype:
                hdr += f'from channel "{parms.channel}"'
            buf.append(hdr)
            if "u" not in btype and parms.username:
                buf.append(f'{indent}Username was "{parms.username}"')
            if "i" not in btype and parms.ipaddr:
                buf.append(f'{indent}Address was "{parms.ipaddr}"')
            if parms.nickname:
                buf.append(f'{indent}Nickname was "{parms.nickname}"')
            if "c" not in btype and parms.channel:
                buf.append(f"{indent}Channel was {parms.channel}")
        self.msg("\n".join(buf))

    @staticmethod
    def ctime(secs):
        """A time.ctime wrapper that handles type conversion (str/int to float)
        and odd stamp values that TeamTalk can present.
        """
        if not isinstance(secs, float):
            secs = float(secs or 0)
        iv = int(secs)
        secs = time.ctime(secs)
        if iv < 1100000000:
            # Before or in 2004, which predates TeamTalk; so this is invalid and we show it raw.
            secs = f"Invalid timestamp {str(iv)}"
        return secs

    def ban_count(self, args):
        "Use ban count -h to get a full syntax description for this subcommand."
        parser = ArgumentParser(prog="ban count", description="Count all or selected bans.", epilog="Examples: ban count, ban co bob, ban co 24.114., ban co channel=/, ban co !nickname=Bob")
        parser.add_argument("filter", nargs="*", help='fieldname=value to match exactly against a specific field, or just value to match against any field. Fields include bantime, username, nickname, ipaddr, and channel. More than one filter can be given. Prefix fieldname with "!" to select mismatches instead of matches. Quote any values that contain spaces.')
        opts = parser.parse_args(args)
        bans = self.getBans()
        parmsets = []
        for ban in bans:
            parms = ban.parms
            if not self.filterPasses(parms, opts.filter, True): continue
            parmsets.append(parms)
        self.msg(f"{len(parmsets)}")

    def ban_add(self, args):
        "Use ban add -h to get a full syntax description for this subcommand."
        parser = ArgumentParser(prog="ban add", description="Add a new ban (does not also kick; see the -k option or the kb command for this)", epilog="Examples: ban add bob, ban add nickname=Bob, ban add 24.114., ban add -k 295")
        parser.add_argument("-a", "--address", action="store_true", help="Ban a raw IP address, subnet, or pattern. Does not work with k or filters.")
        parser.add_argument("-k", "--kick", action="store_true", help="Also kick the user(s) being banned.  Does not work with -a")
        parser.add_argument("filter", nargs="*", help='fieldname=value to match exactly against a specific user field, or just value to match against any field. Fields include userid, username, usertype, userdata, nickname, ipaddr, udpaddr, clientname, version, packetprotocol, statusmode, statusmsg, sublocal, and subpeer (not all of these are likely to prove useful).  More than one filter can be given. Prefix fieldname with "!" to select mismatches instead of matches. Quote any values that contain spaces. As a special case, a plain integer like 295 matches an exact userid.')
        opts = parser.parse_args(args)
        if opts.address:
            filts = opts.filter
            if not filts:
                raise CommandError("No address specified")
            elif len(filts) > 1:
                raise CommandError("Only one address, subnet, or pattern can be banned at a time")
            elif opts.kick:
                raise CommandError("-k and -a cannot be used at the same time")
            req = BanRequest(self, filts[0])
            if not req.pattern:
                raise CommandError(f"Invalid address ban request: {filts[0]}")
            self.msg(f"Banning {req.type} {req.pattern}")
            parms = TTParms([KeywordParm("ban"),
                IntParm("type", 2),
                StringParm("ipaddr", req.pattern),
            ])
            self.do_send(parms)
            return
        users = self.curServer.users
        parmsets = []
        for user in users:
            parms = users[user]
            if not self.filterPasses(parms, opts.filter, True): continue
            parmsets.append(parms)
        flt = lambda u1: self.curServer.nonEmptyNickname(u1, "dnc")
        parmsets = self.selectMatch(parmsets, "Select One or More Users", flt, allowMultiple=True)
        for user in parmsets:
            parms = TTParms([KeywordParm("ban"),
                IntParm("userid", user.userid)
            ])
            self.do_send(parms)
            if not opts.kick: continue
            parms = TTParms([KeywordParm("kick"),
                IntParm("userid", user.userid)
            ])
            self.do_send(parms)

    def ban_delete(self, args):
        "Use ban delete -h to get a full syntax description for this subcommand."
        parser = ArgumentParser(prog="ban delete", description="Delete all or selected bans.", epilog="Examples: ban delete, ban del bob, ban del 24.114., ban del !nickname=Bob")
        parser.add_argument("filter", nargs="*", help='fieldname=value to match exactly against a specific field, or just value to match against any field. Fields include bantime, username, nickname, ipaddr, and channel. More than one filter can be given. Prefix fieldname with "!" to select mismatches instead of matches. Quote any values that contain spaces.')
        opts = parser.parse_args(args)
        bans0 = self.getBans()
        bans = []
        for ban in bans0:
            if not self.filterPasses(ban.parms, opts.filter, True): continue
            bans.append(ban.parms)
        if not bans:
            raise CommandError("No matching bans")
        # Select from remaining candidates exactly which ban(s) to delete.
        bans = self.selectMatch(bans, "Select One or More Bans To Remove", allowMultiple=True, promptOnSingle=True, ftran=lambda m: ", ".join([f"{str(el[0])} {str(el[1])}" for el in m.items()]))
        if not bans: raise CommandError("No bans selected")
        if not self.confirm(f"Delete {len(bans)} bans (y/n)?"): return
        for ban in bans:
            self.do_send(TTParms([
                KeywordParm("unban"),
                StringParm("ipaddr", ban.ipaddr)
            ]))

    def do_kb(self, line):
        """Kick and ban a user by name or ID.
        This command requires the Kick and Ban user rights.
        This command is a shortcut for ban add -k.  See the Ban Add subcommand for further information on how to select users (type ban add -h).
        """
        self.do_ban("add -k "+str(line))

    def getAccounts(self):
        """Return the set of accounts on this server.
        Returns a dict of ParmLines, one for each account.
        The keys are the usernames.
        """
        accts = self.request("listaccounts")
        # Remove the final Ok event.
        resp = accts.pop()
        if resp.event != "ok":
            # TODO: This ignores any but the last response line.
            raise CommandError(resp)
        d = {}
        for acct in accts:
            if acct.event == "ok": continue
            d[acct.parms.username] = acct
        return d

    def getBans(self, chan=None):
        """Returns bans on this server as a list of ParmLine objects.
        If chan is not None, it should be the channel object for the channel where this list should be sought.
        The lines are the responses to the "listbans" command, one line per ban.
        """
        if chan is not None:
            chan = f" chanid={chan.chanid}"
        else:
            chan = ""
        bans = self.request(f"listbans{chan}")
        resp = bans.pop()
        if resp.event != "ok":
            # TODO: This ignores any but the last response line.
            raise CommandError(resp)
        return bans

    def getAllChannelBans(self):
        """Return a list of all channel-level bans on the entire server.
        Warning: This may take a while on a server with hundreds of channels.
        """
        bans = []
        [bans.extend(self.getBans(channel)) for channel in self.curServer.channels]
        return bans

    def do_account(self, line):
        """Account management. Requires admin privileges.
        Run without arguments for a list of subcommands, or type a subcommand and -h for help with that subcommand.
        Example: account list -h.
        """
        args = shlex.split(line)
        self.dispatchSubcommand("account_", args)

    def filterPasses(self, parms, filters, nullIsAnonymousAccount=False):
        """Returns True if the given parameter set passes the given filter list and False if not.
        """
        if not filters: return True
        if isinstance(parms, dict): vals = list(parms.values())
        else: vals = parms
        try: vals = ", ".join(vals)
        except TypeError:
            vals1 = []
            for val in vals:
                val1 = None
                try: val1 = str(val)
                except Exception: pass
                if val1 is None: continue
                vals1.append(val1)
            vals = ", ".join(vals1)
        for filter in filters:
            if "=" in filter:
                fname,fvalWanted = filter.split("=", 1)
                invert = False
                if fname.startswith("!"):
                    fname = fname[1:]
                    invert = True
                try: fvalActual = parms[fname]
                except KeyError: fvalActual = ""
                if fvalWanted.lower().startswith("0x"):
                    # The actual value will be in decimal from TT, but the user specified hex.
                    try:
                        ivalActual = int(fvalActual)
                        fvalWanted = int(fvalWanted, 16)
                        fvalActual = ivalActual
                    except ValueError: pass
                if not invert and fvalActual != fvalWanted: return False
                elif invert and fvalActual == fvalWanted: return False
            elif filter == "" and nullIsAnonymousAccount:
                # Special case for matching the anonymous account in an account list.
                if parms["username"] != "": return False
            else:
                if filter.lower() not in repr(vals).lower(): return False
        return True

    def account_list(self, args):
        "Use account list -h to get a full syntax description for this subcommand."
        parser = ArgumentParser(prog="account list", description="List all or selected accounts.", epilog="Examples: account list, acc li --sort=t, acc li -sU, acc li -a, acc li usertype=1, acc li -v doug, acc li !userrights=0x3f607")
        parser.add_argument("-a", "--admin", action="store_true", help="List only admin accounts (usertype=2).")
        parser.add_argument("-c", "--count", action="store_true", help="Show count only; incompatible with -v, -p, and -r.")
        parser.add_argument("-d", "--disabled", action="store_true", help="List only disabled accounts (usertype=0).")
        parser.add_argument("-n", "--normal", action="store_true", help="List only normal accounts (usertype=1).")
        parser.add_argument("-s", "--sort",
            choices=("l","L","m","M","s","S","u","U"),
            default="u",
            help="Sort order: l last login, m last modified, s server order, u user/account name (default), upper case to reverse."
        )
        parser.add_argument("-v", "--verbose", action="count", default=0, help="Verbose listing; include all non-empty fields except passwords. Specify twice to include empty fields. Useful for determining what fields exist.")
        parser.add_argument("-p", "--passwords", action="store_true", help="Include passwords in output.")
        parser.add_argument("-r", "--rights", action="store_true", help="List accounts by rights; incompatible with -v and -p.")
        parser.add_argument("filter", nargs="*", help='fieldname=value to match exactly against a specific field, or just value to match against any field. Fields include username, password, usertype, userdata, userrights, note, initchan, opchannels, and audiocodeclimit. More than one filter can be given. Prefix fieldname with "!" to select mismatches instead of matches. Quote any values that contain spaces. As a special case, "" matches the anonymous account.')
        opts = parser.parse_args(args)
        self._handleAccountUserTypeOptions(opts)
        accts = self.getAccounts()
        if opts.filter: ttl = "Matching User Accounts"
        else: ttl = "User Accounts"
        if opts.rights: ttl += " By Rights"
        parmsets = []
        for username in accts:
            acct = accts[username]
            parms = acct.parms
            if not self.filterPasses(parms, opts.filter, True): continue
            parmsets.append(parms)
        if opts.count:
            self.msg(len(parmsets))
            return
        if opts.rights:
            # Listing organized by rights rather than username.
            total = len(parmsets)
            rsets = {}
            for parms in parmsets:
                # The ZZ will make admin and disabled sort below default shortly.
                rights = self.curServer.adjustedUserRights(parms.userrights)
                r = ("ZZAdmin" if parms.usertype == "2" else "ZZDisabled" if parms.usertype == "0" else "Default", int(rights))
                rsets.setdefault(r, [])
                rsets[r].append(parms)
            #kl = lambda item: item[0][0] +str(bin(item[0][1]).count("1"))
            kl = lambda item: item[0][0] +str(item[0][1]).zfill(16)
            for r,parmsets in sorted(rsets.items(), key=lambda item: item[0][0] +str(bin(item[0][1]).count("1"))):
                usertype,rights = r
                usertype = usertype.replace("ZZAdmin", "Admin").replace("ZZDisabled", "Disabled")
                parmsets.sort(key=lambda p: p.username)
                lst = ", ".join([parms.username for parms in parmsets])
                self.msg(f"{len(parmsets)} {usertype} users with {hex(rights)} ({ttflags.UserRights(rights).toString('wA')}):\n    {lst}")
            self.msg(f"Total accounts: {total}")
            return
        # This field appears starting in protocol version 5.14.
        doLasts = any([p for p in parmsets if p.lastlogin])
        if opts.sort in "lL":
            if not doLasts and len(parmsets):
                raise CommandError("Last-login field not available on this server")
            parmsets.sort(key=lambda parms: float(parms.lastlogin or 0), reverse=(opts.sort=="L"))
        elif opts.sort in "mM":
            parmsets.sort(key=lambda parms: float(parms.modifiedtime or 0), reverse=(opts.sort=="M"))
        elif opts.sort == "S":
            # "s" is unchanged order.
            parmsets.reverse()
        elif opts.sort in "uU":
            parmsets.sort(key=lambda parms: parms.username, reverse=(opts.sort=="U"))
        if not opts.verbose:
            # Short, tabular listing.
            cols = ["Username", "Type", "Rights", "Last Modified"]
            if doLasts: cols.append("Last Used")
            if opts.passwords: cols.append("Password")
            tbl = TableFormatter(ttl, cols)
            for parms in parmsets:
                row = ([
                    parms.username,
                    ["Disabled","Default","Admin","3","4","5"][int(parms.usertype)],
                    "0x"+hex(int(self.curServer.adjustedUserRights(parms.userrights)))[2:].zfill(8),
                    self.ctime(float(parms.modifiedtime)),
                ])
                if doLasts:
                    if parms.lastlogin:
                        row.append(self.ctime(float(parms.lastlogin)))
                    else:
                        row.append(" ")
                if opts.passwords:
                    row.append(parms.password)
                tbl.addRow(row)
                if parms.note and parms.note.strip():
                    tbl.addRow("        " +parms.note.strip())
            self.msg(tbl.format(2))
            return
        # Verbose, multiline listing showing all or all non-empty fields.
        if not parmsets:
            self.msg(f"{ttl}:  0")
            return
        buf = f"{ttl} ({len(parmsets)!s}):\n"
        for parms in parmsets:
            if opts.passwords:
                buf += f'Account username "{parms.username}" type {parms.usertype} password "{parms.password}"\n'
            else:
                buf += f'Account username "{parms.username}" type {parms.usertype}\n'
            for k,v in sorted(parms.items()):
                if k.lower() in ["username", "usertype", "password", "note"]: continue
                if opts.verbose < 2 and (not v or (v.isdigit() and not int(v))): continue
                if opts.verbose < 2 and "chan" in k.lower() and v == "[]": continue
                if k == "userrights":
                    v = self.curServer.adjustedUserRights(v)
                    v = f"0x{hex(int(v))[2:].zfill(8)} ({ttflags.UserRights(int(v)).toString('wA')})"
                elif k == "modifiedtime": v = self.ctime(float(parms.modifiedtime))
                elif k == "lastlogin": v = self.ctime(float(parms.lastlogin))
                buf += f"    {k} {v}\n"
            if opts.verbose >= 2 or parms.note: buf += f'Note: "{parms.note}"\n'
        self.msg(buf)

    def account_add(self, args):
        "Use account add -h to get a full syntax description for this subcommand."
        parser = ArgumentParser(prog="account add", description="Add a new account", epilog="Examples: account add "" "" 1 (makes anonymous account), acc add Bill B1llPw 2, acc add Doug DougsPassword "" (uses the annonymous account for Doug's user rights)")
        parser.add_argument("username", help='The username of the new account. Use "" to make the anonymous account. Use quotes if the name contains spaces.')
        parser.add_argument("password", help='The password for the new account. Use "" to make an account with no password. Use quotes if the password contains spaces.')
        parser.add_argument("usertype", help="1 for regular account, 2 for admin account, 0 for disabled account, or the username of an account to use for user rights.")
        parser.add_argument("field", nargs="*", help="fieldname=value pairs to set other fields for the account. More than one pair may be specified. Example fields include note and initchan. Use quotes if a field value contains spaces. Warning: If you specify an invalid field name, such as by misspelling a field name, the field value will be ignored and will not be set on the account.")
        opts = parser.parse_args(args)
        acctDict = self.getAccounts()
        pat = r'''[\s.,?/;:@#$%^&*'"!+=_-]+'''
        u0 = re.sub(pat, '', opts.username.lower())
        for username in acctDict.keys():
            if username == opts.username: raise CommandError(f'Account "{opts.username}" already exists')
            if username.lower() == opts.username.lower():
                if not self.confirm(f'Warning: There is already an account named "{username}" (same name but different letter casing). Proceed anyway (y/n)?'):
                    return
                else: continue
            u1 = re.sub(pat, '', username.lower())
            if u1 == u0:
                if not self.confirm(f'Warning: There is already an account similarly named "{username}". Proceed anyway (y/n)?'):
                    return
                else: continue
        # Default user rights as of TeamTalk5Classic 5.2.1.4781. [DGL, 2017-04-04
        userRights = 0x0003F607  # decimal 259591
        if self.curServer.comparableVersion(self.curServer.info.version) >= self.curServer.comparableVersion("5.15"):
            userRights |= 0x00C00000
        utype = opts.usertype
        if utype not in ["1", "2"]:
            if utype == "" and "" in acctDict:
                acct = acctDict[""]
            else:
                accts = [a for a in acctDict if utype.lower() in a.lower()]
                rightsAcct = self.selectMatch(accts, "Select an Account For User Rights")
                acct = acctDict[rightsAcct]
            utype = acct.parms.userType
            if int(utype) == 2 and not self.confirm(f"{acct.parms.username} is an admin account. Make {opts.username} an admin account also (y/n)?"):
                utype = "1"
            elif int(utype) == 0 and not self.confirm(f"{acct.parms.username} is a disabled account. Make {opts.username} a disabled account also (y/n)?"):
                utype = "1"
            userRights = acct.parms.userRights
        # Username, password, and any other string values are assumed to be raw values; see the StringParm class.
        parms = TTParms([KeywordParm("newaccount"),
            StringParm("username", opts.username, True),
            StringParm("password", opts.password, True),
            IntParm("usertype", int(utype))
        ])
        if userRights is not None:
            parms.append(IntParm("userrights", userRights))
        for field in opts.field:
            field = TTParms(field)[0]
            if field.name.lower() in ["username", "password", "usertype"]:
                raise CommandError("username, password, and usertype may not be repeated as fieldname=value pairs")
            parms.append(field)
        self.do_send(parms)

    def account_delete(self, args):
        "Use account delete -h to get a full syntax description for this subcommand."
        parser = ArgumentParser(prog="account delete", description="Delete one or more existing accounts, with confirmation", epilog="Examples: account delete, acc del -a, acc del usertype=1, acc del !userrights=0x3f607")
        parser.add_argument("-a", "--admin", action="store_true", help="Consider only admin accounts (usertype=1).")
        parser.add_argument("-d", "--disabled", action="store_true", help="Consider only disabled accounts (usertype=1).")
        parser.add_argument("-n", "--normal", action="store_true", help="Consider only normal accounts (usertype=1).")
        parser.add_argument("filter", nargs="*", help='fieldname=value to match exactly against a specific field, or just value to match against any field. More than one filter can be given. Prefix fieldname with "!" to select mismatches instead of matches. Quote any values that contain spaces. As a special case, "" matches the anonymous account.')
        opts = parser.parse_args(args)
        self._handleAccountUserTypeOptions(opts)
        accts = self.getAccounts()
        acctDict = {}
        for username in sorted(accts):
            acct = accts[username]
            parms = acct.parms
            if not self.filterPasses(parms, opts.filter, True): continue
            acctDict[username] = parms
        if not acctDict:
            raise CommandError("No matching accounts")
        # Select from remaining candidates exactly which account(s) to delete.
        dels = self.selectMatch(list(acctDict.keys()), "Select One or More Accounts To Delete", allowMultiple=True)
        if not dels: raise CommandError("No accounts selected")
        if not self.confirm("Delete {0} (y/n)?".format(", ".join(['"'+d+'"' for d in dels]))):
            return
        for username in dels:
            self.do_send(f'delaccount username="{username}"')

    def account_modify(self, args):
        "Use account modify -h to get a full syntax description for this subcommand."
        parser = ArgumentParser(prog="account modify", description="Modify an existing account", epilog="Examples: account modify Doug password=blah, acc mod Doug usertype=2 (make admin).")
        parser.add_argument("username", help='The username of the existing account. Use "" to modify the anonymous account. Use quotes if the name contains spaces.')
        parser.add_argument("field", nargs="*", help="fieldname=value pairs to set other fields for the account. More than one pair may be specified. Example fields include note and userdata. Use quotes if a field value contains spaces. Warning: If you specify an invalid field name, such as by misspelling a field name, the field value will be ignored and will not be set on the account.")
        opts = parser.parse_args(args)
        acctDict = self.getAccounts()
        try: acct = acctDict[opts.username]
        except KeyError: raise CommandError(f'Account "{opts.username}" does not exist.')
        # Get TTParms interpretation of the account parameters.
        acctParms = TTParms(acct.initLine.strip())
        # Remove the Useraccount keyword.
        acctParms.pop(0)
        parmDict = {}
        for parm in acctParms:
            # ToDo: Hack for ParmLine's mishandling of list types.
            if parm.name.lower() == "opchannels" and isinstance(parm, StringParm):
                parm = ListParm(parm.name, parm.value)
            parmDict[parm.name] = parm
        for field in opts.field:
            field = TTParms(field)[0]
            if field.name.lower() == "username":
                raise CommandError("username may not be repeated as a fieldname=value pair")
            parmDict[field.name] = field
        parms = TTParms([KeywordParm("newaccount"),
            StringParm("username", opts.username, True)
        ])
        for k,v in parmDict.items():
            if k.lower() == "username": continue
            parms.append(v)
        self.do_send(parms)

    def account_users(self, args):
        "Use account users -h to get a full syntax description for this subcommand."
        parser = ArgumentParser(prog="account users", description="List all or selected accounts currently in use by logged-in users. Admin rights are not required.", epilog="Examples: account users, acc u -a, acc u doug")
        parser.add_argument("-a", "--admin", action="store_true", help="List only admin accounts (usertype=2).")
        parser.add_argument("-d", "--disabled", action="store_true", help="List only disabled accounts (usertype=0); possible if the account was disabled while a user remains logged in.")
        parser.add_argument("-n", "--normal", action="store_true", help="List only normal accounts (usertype=1).")
        parser.add_argument("filter", nargs="*", help='value to match against account names. More than one value can be given. Quote any values that contain spaces. As a special case, "" matches the anonymous account.')
        opts = parser.parse_args(args)
        utype = self._handleAccountUserTypeOptions(opts, True)
        users = list(self.curServer.users.values())
        if utype is not None:
            utype = str(utype)
            users = [user for user in users if user.usertype==utype]
        if opts.filter:
            flt = [f.lower() for f in opts.filter]
            users0 = users
            users = []
            for user in users0:
                name = user.username.lower() if user.username else ""
                if name == "" and "" in flt:
                    users.append(user)
                elif name == "": continue
                elif len([f for f in flt if f and f in name]):
                    users.append(user)
            del users0
        accts = {}
        for user in users:
            accts.setdefault(user.username, [])
            accts[user.username].append(user.nickname)
        if opts.admin or opts.filter: ttl = "Matching user accounts"
        else: ttl = "User accounts"
        self.msg(ttl +":")
        for acct,lst in accts.items():
            if not acct: acct = '""'
            self.msg(f"{acct} ({len(lst)}): {', '.join(sorted(lst))}")

    def _handleAccountUserTypeOptions(self, opts, returnOnly=False):
        """Process -a, -d, and -n options for account subcommands that accept them.
        returns the int usertype requested or None if none is requested.
        If returnOnly is not passed and True, also adds the appropriate usertype=n element to the opts.filter list.
        Raises a SyntaxError if more than one of the processed options has been specified by the user.
        """
        cnt = 0
        utype = None
        if opts.admin:
            if not returnOnly: opts.filter.append("usertype=2")
            utype = 2
            cnt += 1
        if opts.disabled:
            if not returnOnly: opts.filter.append("usertype=0")
            utype = 0
            cnt += 1
        if opts.normal:
            if not returnOnly: opts.filter.append("usertype=1")
            utype = 1
            cnt += 1
        if cnt > 1:
            raise SyntaxError("Only one of -a, -d, or -n may be specified in one command")
        return utype

    def do_channel(self, line):
        """Channel management.
        Run without arguments for a list of subcommands, or type a subcommand and -h for help with that subcommand.
        Example: channel list -h.
        """
        args = shlex.split(line)
        self.dispatchSubcommand("channel_", args)

    def channel_list(self, args):
        "Use channel list -h to get a full syntax description for this subcommand."
        parser = ArgumentParser(prog="channel list", description="List all or selected channels.", epilog="Examples: channel list, chan li protected=1, chan li !type=1")
        parser.add_argument("-c", "--count", action="store_true", help="Show count only; incompatible with -v and -p.")
        parser.add_argument("-v", "--verbose", action="count", default=0, help="Verbose listing; include all non-empty fields except passwords. Specify twice to include empty fields. Useful for determining what fields exist.")
        parser.add_argument("-p", "--passwords", action="store_true", help="Include passwords in output.")
        parser.add_argument("filter", nargs="*", help='fieldname=value to match exactly against a specific field, or just value to match against any field. Useful fields include name, topic, protected, maxusers, and type. More than one filter can be given. Prefix fieldname with "!" to select mismatches instead of matches. Quote any values that contain spaces.')
        opts = parser.parse_args(args)
        chans = self.curServer.channels
        if opts.filter: ttl = "Matching Channels"
        else: ttl = "Channels"
        parmsets = []
        # This sort order is meant to mirror that of the TeamTalk channel tree when fully expanded.
        for chanid,parms in sorted(list(chans.items()), key=lambda id_p: id_p[1].channel.lower()):
            if not self.filterPasses(parms, opts.filter): continue
            parmsets.append(parms)
        if opts.count:
            self.msg(len(parmsets))
            return
        if not opts.verbose:
            # Short, tabular listing.
            if opts.passwords:
                cols = ["chanid", "HasPW", "Password", "Type", "MaxUsers", "Channel"]
            else:
                cols = ["chanid", "HasPW", "Type", "MaxUsers", "Channel"]
            tbl = TableFormatter(ttl, cols)
            for parms in parmsets:
                tp = f"{hex(int(parms.type))} ({ttflags.ChannelTypes(parms.type).toString('c')})"
                row = ([
                    f"{int(parms.chanid):5d}",
                    ["No", "Yes"][int(parms.protected)],
                    tp,
                    f"{int(parms.maxusers):6d}",
                    self.curServer.channelname(parms.chanid, False, True)
                ])
                if opts.passwords: row.insert(2, parms.password)
                tbl.addRow(row)
                if parms.topic and parms.topic.strip():
                    tbl.addRow("        " +parms.topic.strip())
            self.msg(tbl.format(2))
            return
        # Verbose, multiline listing showing all or all non-empty fields.
        if not parmsets:
            self.msg(f"{ttl}:  0")
            return
        buf = f"{ttl} ({len(parmsets)!s}):\n"
        for parms in parmsets:
            tp = f"{hex(int(parms.type))} ({ttflags.ChannelTypes(parms.type).toString('wA')})"
            buf += f'Chanid {parms.chanid} {self.curServer.channelname(parms.chanid, False, True)} type {tp}\n'
            for k,v in sorted(parms.items()):
                if k.lower() in ["chanid", "channel", "name", "type", "topic"]: continue
                if opts.verbose < 2 and (not v or (v.isdigit() and not int(v))): continue
                if opts.verbose < 2 and not "topic" in k.lower() and v == "[]": continue
                if not opts.passwords and "password" in k.lower(): continue
                buf += f"    {k} {v}\n"
            if opts.verbose >= 2 or parms.topic: buf += f'Topic: "{parms.topic}"\n'
        self.msg(buf)

    def handleSubHelp(self, args, docText):
        """Handle a request via -h for help on a subcommand.
        """
        if not (len(args) == 1 and args[0] == "-h"): return False
        self.msg(self._formatHelp(docText))
        return True

    def channel_flags(self, args):
        """Show, set, clear, or toggle flags on a channel.
        Usage: channel flags [@<channelname>] [flagSpecs]
        Without arguments, prints the flags that are active on the current channel.
        For a specific channel, use @ followed with no spaces by something that identifies it, like part of its name.
        Flags can be prefixed with + or nothing to set, - to clear, or ^ to toggle.
        Specify no flags to just see what is currently set. Specify one or more flags to change their state.
        Supported flags:
            c - Classroom mode (users must be voiced in channel to speak).
            h - Hidden channel (non-admins can't see it and need its exact name to join).
            n - No recording allowed (except by admins and those with record-everywhere permission).
            o - Op-only (only operators see and hear).
            p (lower case) - Push to talk (constant transmit denied).
            P (upper case) - Permanent channel (won't disappear when the last person leaves).
            x - Exclusive (one transmitter at a time).
        Note that channels can not be hidden or unhidden once created; the server will generate an error message.
        Examples: channel flags, chan fl c, chan fl @chanid=1 -c ^p
        """
        if self.handleSubHelp(args, self.channel_flags.__doc__): return
        chanspec = ""
        for spec in args:
            if spec.startswith("@"):
                chanspec = spec[1:]
            elif "/" in spec:
                chanspec = spec
            if chanspec:
                args.remove(spec)
                break
        if chanspec:
            channel = self.channelMatch(chanspec)
        else:
            try: channel = self.curServer.channels[self.curServer.me.chanid]
            except (KeyError, AttributeError):
                raise CommandError("You are not in a channel")
        if args:
            curFlags = ttflags.ChannelTypes(channel.type)
            nextFlags = curFlags.alter(args, 1)
            if nextFlags == curFlags:
                self.msg("No change")
            else:
                parms = TTParms([KeywordParm("updatechannel"),
                    IntParm("chanid", channel.chanid),
                    IntParm("type", nextFlags),
                ])
                self.do_send(parms)
        flags = ttflags.ChannelTypes(channel.type)
        self.msg(f"Flags for {self.curServer.channelname(channel.chanid)}: {flags.toString('c')}")

    def do_file(self, line):
        """File management.
        Run without arguments for a list of subcommands, or type a subcommand and -h for help with that subcommand.
        Example: file get -h.
        """
        args = shlex.split(line)
        self.dispatchSubcommand("file_", args)

    def file_get(self, args):
        "Use file get -h to get a full syntax description for this subcommand."
        parser = ArgumentParser(prog="file get", description="Get all or selected files from the current channel. See the TTCom User Guide for more advanced administrative usage.", epilog="Examples: file get rules.txt ., file get rules.txt ~/downloads")
        parser.add_argument("-o", "--owner", action="append", help='* to select from all file owners, "" for the anonymous account, or a string to match in owner names. This option may appear more than once to select more than one owner.')
        parser.add_argument("fileSpec", help='File name or partial name to match, or "*" to match all files. Quote if spaces are included.')
        parser.add_argument("localPath", help='Path to the folder where the file should be placed. Use "." for the TTCom folder and "~" for your home folder. Quote if spaces are included.')
        opts = parser.parse_args(args)
        opts.localPath = os.path.expanduser(os.path.expandvars(opts.localPath))
        if not os.path.exists(opts.localPath):
            raise CommandError(f"{opts.localPath} does not exist")
        if not os.path.isdir(opts.localPath):
            raise CommandError(f"{opts.localPath} is not a folder")
        files = self.fileMatch(opts.fileSpec, opts.owner, permCheck=False)
        if not files: raise CommandError("No files selected")
        for f in files:
            self.curServer.getFile(f, opts.localPath)

    def file_check(self, args):
        "Use file check -h to get a full syntax description for this subcommand."
        parser = ArgumentParser(prog="file ", description="Check all or selected files for availability. See the TTCom User Guide for more usage details.", epilog="Examples: file check rules.txt, file check *")
        parser.add_argument("-o", "--owner", action="append", help='* to select from all file owners, "" for the anonymous account, or a string to match in owner names. This option may appear more than once to select more than one owner.')
        parser.add_argument("fileSpec", help='File name or partial name to match, or "*" to match all files. Quote if spaces are included.')
        opts = parser.parse_args(args)
        files = self.fileMatch(opts.fileSpec, opts.owner, permCheck=True)
        if not files: raise CommandError("No files selected")
        good = []
        bad = []
        n = len(files)
        i = 0
        with ProgressReporter(f"Checking {n:d} files", lambda: i/n) as pr:
            for f in files:
                result = self.curServer.getFile(f, None)
                if result: good.append(f)
                else: bad.append(f)
                i += 1
        self.msg(f"{len(files)} files, {len(good)} available for download, {len(bad)} missing.")
        if not len(bad): return
        if not self.confirm(f"Delete the TeamTalk records for the {len(bad)} missing files (y/n)?"): return
        for f in bad:
            results = self.request(f'deletefile chanid={f.chanid} filename="{f.filename}"')
            for line in results:
                if line.event == "ok": continue
                if line.event == "removefile":
                    self.curServer.event_removefile(line.parms)
                    continue
                self.msg(str(line))

    def file_Send(self, args):
        "Use file send -h to get a full syntax description for this subcommand."
        if len(args) == 1: args.append(".")
        parser = ArgumentParser(prog="file send", description="Send all or selected files to the current channel or, for admins, any channel.", epilog="Examples: file send c:/tt/rules.txt, file send ~/tt/rules.txt")
        parser.add_argument("fileSpec", help='File path that may contain wildcards, or folder path for whole folder of files. Quote if spaces are included.')
        parser.add_argument("channel", help='Name or partial name of channel to upload to. Omit this argument or use a dot (.) for the current channel. Only admins can send to another channel.')
        opts = parser.parse_args(args)
        givenChan = opts.channel
        if not givenChan or givenChan == ".":
            chanid = self.curServer.me.get("chanid")
        elif int(self.curServer.me.usertype) != 2:
            raise CommandError("You must be an admin in order to reference non-current channels with this command")
        else:
            chanid = self.channelMatch(givenChan).chanid
        if not chanid: raise CommandError("Need a destination channel to send files")
        localPath = opts.fileSpec
        if not localPath: raise ValueError("Empty local path")
        if os.path.isdir(localPath): localPath = os.path.join(localPath, "*")
        localPath = os.path.expanduser(os.path.expandvars(localPath))
        paths = [f for f in glob(localPath) if os.path.isfile(f)]
        if not len(paths):
            raise CommandError("No files matched")
        paths = self.selectMatch(paths, "Select Files",
            allowMultiple=True,
            promptOnSingle=True
        )
        if not len(paths):
            raise CommandError("No files selected")
        tot = sum([os.stat(p).st_size for p in paths])
        if not self.confirm(f"Send {len(paths):d} files totaling {tot:d} bytes to {self.curServer.channelname(chanid)} (y/n)?"):
            return
        for p in paths:
            self.curServer.sendFile(p, chanid=chanid)

    def file_delete(self, args):
        "Use file delete -h to get a full syntax description for this subcommand."
        parser = ArgumentParser(prog="file delete", description="Delete all or selected files from the current channel. See the TTCom User Guide for more advanced administrative usage.", epilog="Examples: file delete rules.txt, file del .exe (NOT *.exe)")
        parser.add_argument("-o", "--owner", action="append", help='* to select from all file owners, "" for the anonymous account, or a string to match in owner names. This option may appear more than once to select more than one owner.')
        parser.add_argument("fileSpec", help='File name or partial name to match, or "*" to match all files. Quote if spaces are included.')
        opts = parser.parse_args(args)
        files = self.fileMatch(opts.fileSpec, opts.owner, permCheck=True)
        if not files: raise CommandError("No files matched")
        nchans = len(set([f.chanid for f in files]))
        if nchans == 1: nchans = ""
        else: nchans = f" from {nchans} channels"
        if not self.confirm(f"Delete {len(files)} files{nchans} (y/n)?"): return
        for f in files:
            results = self.request(TTParms([
                KeywordParm("deletefile"),
                IntParm("chanid", f.chanid),
                StringParm("filename", f.filename, rawValue=True),
            ]))
            for line in results:
                if line.event == "ok": continue
                if line.event == "removefile":
                    self.curServer.event_removefile(line.parms)
                    continue
                self.msg(str(line))

    def file_counts(self, args):
        "Use file counts -h to get a full syntax description for this subcommand."
        parser = ArgumentParser(prog="file counts", description="Show counts of files per channel and user with totals. Requires admin rights to show files not in the current channel.")
        opts = parser.parse_args(args)
        # This will only include current-channel files without admin rights.
        files = list(self.curServer.files.values())
        c_channels = Counter([f.chanid for f in files])
        c_users = Counter([f.owner for f in files])
        self.msg("File counts by channel, fewest files first:")
        for cid,cnt in reversed(c_channels.most_common()):
            us = Counter([f.owner for f in files if f.chanid == cid])
            self.msg(f'{self.curServer.channelname(cid)}: total {c_channels[cid]:d}, {", ".join([k+" "+str(v) for k,v in us.most_common()])}')
        self.msg(f'Totals by user: {", ".join([k+" "+str(v) for k,v in c_users.most_common()])}')
        self.msg(f'{len(files)} files from {len(c_users)} users in {len(c_channels)} channels')

    def file_sizes(self, args):
        "Use file sizes -h to get a full syntax description for this subcommand."
        parser = ArgumentParser(prog="file sizes", description="List files in order of increasing size. Requires admin rights to show files not in the current channel.")
        opts = parser.parse_args(args)
        # This will only include current-channel files without admin rights.
        files = list(self.curServer.files.values())
        tot = sum([int(f.filesize) for f in files])
        self.msg("Files by increasing size:")
        for f in sorted(files, key=lambda f: int(f.filesize)):
            self.msg(f'{hrsize(f.filesize)}   {f.filename} from {f.owner} in {self.curServer.channelname(f.chanid)}')
        self.msg(f"{len(files):d} files totaling {hrsize(tot)}")

    def do_tt(self, line):
        """Create a .tt file for a user account.
        Usage: tt [clientVersion] ttFileName [userName [channelToJoin]]
        If no clientVersion is given, the current server's version is used.
        Adding a userName includes the user's login and password credentials in the generated file. Requires admin privileges.
        Adding a channelToJoin makes the tt file cause the user to land in the given channel on login.
        """
        if self.curServer.state != "loggedIn":
            raise CommandError("Not logged in")
        args = shlex.split(line)
        verGiven = None
        try:
            verGiven = (float(args[0]) > 0.0)
            if verGiven: verGiven = args.pop(0)
        except IndexError: verGiven = None
        except ValueError: verGiven = None
        if not args:
            raise SyntaxError("Must specify a .tt file name to generate")
        fname = os.path.expanduser(os.path.expandvars(args.pop(0)))
        if not fname.lower().endswith(".tt"):
            fname += ".tt"
        if (os.path.exists(fname)
        and not self.confirm(f"File {fname} already exists. Replace it (y/n)?")):
            return
        if not args:
            acct = ParmLine("fakeEvent username=\"\" password=\"\"")
        else:
            acct = args.pop(0)
            acctDict = self.getAccounts()
            accts = [a for a in acctDict if acct.lower() in a.lower()]
            acct = self.selectMatch(accts, "Select an Account")
            acct = acctDict[acct]
        if args: channel = args.pop(0)
        else: channel = None
        if channel:
            cid = self.channelMatch(channel).chanid
        else:
            cid = None
        tt = self.curServer.makeTTString(acct.parms, cid, verGiven)
        with open(fname, "w", encoding="utf-8") as f:
            f.write(tt)

    def do_url(self, line):
        """Create a tt:// URL for a user account.
        Usage: url [userName [channelToJoin]]
        Adding a userName includes the user's login and password credentials in the generated URL. Requires admin privileges.
        Adding a channelToJoin makes the URL cause the user to land in the given channel on login.
        """
        if self.curServer.state != "loggedIn":
            raise CommandError("Not logged in")
        args = shlex.split(line)
        if not args:
            acct = ParmLine("fakeEvent username=\"\" password=\"\"")
        else:
            acct = args.pop(0)
            acctDict = self.getAccounts()
            accts = [a for a in acctDict if acct.lower() in a.lower()]
            acct = self.selectMatch(accts, "Select an Account")
            acct = acctDict[acct]
        if args: channel = args.pop(0)
        else: channel = None
        if channel:
            cid = self.channelMatch(channel).chanid
        else:
            cid = None
        url = self.curServer.makeURLString(acct.parms, cid)
        self.msg(url)

    def do_say(self, line):
        """Say the given line if possible.
        Quoting is not necessary or desirable.
        Requires MacOS's say command or a similar provision on other platforms.
        If no such provision is found, this command does nothing.
        """
        mycmd_say(line)

    def do_play(self, line):
        """Play a sound file via the SoX play command. Requires the play command to be on the path.
        Files are played in the order received, in their own thread to avoid delaying the entire application.
        When the queue of files to play grows, files will be batched onto multi-file play commands for speed.
        Generating sounds via synth effects instead of playing files is also supported.
        A play command with one argument is a file play; more arguments indicate an effect.
        """
        args = shlex.split(line)
        if len(args) == 1:
            player.sendFile(line)
            return
        player.sendEffect(args)

    def do_beep(self, line):
        """Send a bell character to the TTCom window.
        This can be used to make a sound from a remote ssh session for example.
        """
        self.msg("\7", end="")

    def do_system(self, line):
        """Run a system command in a subshell.
        """
        task = lambda: callWithRetry(os.system, line)
        thr = threading.Thread(target=task)
        thr.daemon = True
        thr.start()

    def do_motd(self, line=""):
        """Show the message of the day (motd) for the current server.
        """
        # Raw value from server.
        motd = self.curServer.info.motd
        # Trick to reconstruct its printable format.
        motd = f'motd="{motd}"'
        motd = TTParms(motd, True).pop(0).value
        self.msg(motd)

    def do_whoIs(self, line=""):
        """Show information about a user.
        Syntax: whoIs <name>, where <name> can be a full or partial user name.
        If name is omitted, this current user is used.
        """
        if not self.curServer.conn:
            self.msg("Not connected")
            return
        line = line.strip()
        isMe = False
        if line:
            user = self.userMatch(line)
            if not user: return
        else:
            user = self.curServer.me
            isMe = True
        u = AttrDict(user.copy())
        # The Accepted event on this user's login sends this user's password into this object.
        # Remove it now so it won't print.
        u.pop("password", None)
        buf = TextBlock()
        userid = u.pop("userid", None)
        buf += f"UserId {userid}"
        if not u.get("username") and not u.get("nickname"):
            buf += ", no nickname or username"
        else:
            buf.add("Username", u.get("username"), True)
            buf.add("Nickname", u.get("nickname"), True)
        u.pop("username", "")
        u.pop("nickname", "")
        buf.add("UserType", u.get("usertype"))
        u.pop("usertype", "")
        buf.add("StatusMode", u.get("statusmode"), True)
        statusmsg = u.get("statusmsg")
        if statusmsg: statusmsg = statusmsg.strip()
        if statusmsg: buf += " (" +statusmsg +")"
        statustime = u.get("statustime")
        if statustime:
            diff = time.monotonic() -statustime
            diff = self.curServer.secsToTime(diff)
            statustime = f"for {diff}"
            if self.curServer.logintime is not None and abs(u.get("statustime") - self.curServer.logintime) < 5.0:
                statustime += " (since your login)"
            buf += " " +statustime
        u.pop("statustime", "")
        u.pop("statusmode", "")
        u.pop("statusmsg", "")
        ipaddr = u.get("ipaddr", "") or ""
        # This fixes IPV6-format versions of IPV4 addresses into a straight IPV4 address.
        if ipaddr.lower().startswith("::ffff:"): ipaddr = ipaddr[7:]
        buf.add("IP Address", self.formattedAddress(ipaddr))
        u.pop("ipaddr", "")
        cname = u.pop("clientname", "").strip()
        cver = u.pop("version", "").strip()
        if cname and cver: cname = f"{cname} version {cver}"
        elif cver: cname = f"Version {cver}"
        buf.add("Client", cname)
        buf.add("Packet Protocol", u.pop("packetprotocol", ""), True)
        chanid = u.pop("chanid", "")
        channel = u.pop("channel", "")
        if chanid or channel:
            if not channel:
                channel = self.curServer.channels[chanid].channel
            buf += f"\nOn channel {chanid} ({channel})"
        val = u.pop("lastlogin", "")
        if val:
            buf.add("Last login", self.ctime(val), False, True)
        val = u.pop("modifiedtime", "")
        if val:
            buf.add("Account last modified", self.ctime(val), False, True)
        server = u.pop("server", None)
        if server:
            channels = list(server.channels.values())
        else:
            channels = []
        for which in [
            ("voiceusers", "Can speak in"),
            ("videousers", "Can share video in"),
            ("mediafileusers", "Can share media files in"),
            ("desktopusers", "Can share desktop in"),
            ("operators", "Operator in"),
            ("opchannels", "Automatically operator in")
        ]:
            k,name = which
            matches = [c for c in channels if userid in (c.get(k) or [])]
            # Substring match is not enough though; false positives like 4095 for userid 9 are possible.
            matches = [c for c in matches if userid in ListParm(name, c[k]).value]
            matches = ", ".join([c.channel for c in matches])
            buf.add(name, matches)
            u.pop(k, None)
        sublocal = u.pop("sublocal", "")
        subpeer = u.pop("subpeer", "")
        if sublocal: sublocal = f"{hex(int(sublocal))} ({ttflags.Subscriptions(sublocal).toString('c')})"
        if subpeer: subpeer = f"{hex(int(subpeer))} ({ttflags.Subscriptions(subpeer).toString('c')})"
        buf.add("Local subscriptions", sublocal)
        buf.add("peer subscriptions", subpeer, True)
        userdata = u.pop("userdata", "")
        if userdata == "0": userdata = ""
        buf.add("Userdata", userdata)
        buf.add("Note", u.pop("note", ""), True)
        # Anything non-empty value not handled above goes here.
        for k in sorted(u):
            # Except for TTCom-internal stuff.
            if k == "temporary": continue
            v = u[k]
            if k == "userrights":
                v = self.curServer.adjustedUserRights(v)
                v = f"0x{hex(int(v))[2:].zfill(8)} ({ttflags.UserRights(int(v)).toString('wA')})"
            elif k in ("lastlogin", "modifiedtime"):
                v = self.ctime(float(v))
            buf.add(k, v)
        self.msg(str(buf))
        if isMe: self.curServer.reportRightsIssues()

    def formattedAddress(self, addr):
        """Return the given address with FQDN where possible.
        Assumes addr is a numeric address (IPV4 or IPV6).
        """
        if not addr: return addr
        fqdn = socket.getfqdn(addr)
        if (fqdn == addr
            or fqdn.endswith(".in-addr.arpa")
        ): return addr
        return f"{fqdn} ({addr})"

    def do_address(self, line=""):
        """Show IP address for a user when available.
        Syntax: address <name>, where <name> can be a full or partial user name.
        If name is omitted, this current user is used.
        """
        line = line.strip()
        if line:
            user = self.userMatch(line)
            if not user: return
        else:
            user = self.curServer.me
        u = AttrDict(user.copy())
        buf = TextBlock()
        ipaddr = u.get("ipaddr") or ""
        buf.add("IP Address", self.formattedAddress(ipaddr))
        self.msg(str(buf))

    def do_geolocate(self, line=""):
        """Show Geolocation information for a user or server IP address when available, or for a given address.
        Syntax: geolocate [ -v ] <name>, where <name> can be a full or partial user name.
        If name is omitted, the current server's address is used.
        An IPV4 or IPV6 address can also be given in place of a user name.
        -v: Verbose; show all fields returned from the web query.
        """
        ipaddrs = []
        line = line.strip()
        verbose = False
        if line.startswith("-v"):
            line = line[2:].lstrip()
            verbose = True
        if line:
            # Take care of IPV6 prefixes on IPV4 addresses first.
            if line.lower().startswith("::ffff:") and line[7].isdigit():
                line = line[7:]
            if geolocator.is_valid_address(line, formatOnly=True):
                ipaddrs.append(line)
            else:
                user = self.userMatch(line)
                if not user: return
                ipaddr = user.get("ipaddr") or ""
                if ipaddr: ipaddrs.append(ipaddr)
        else:  # blank line
            try: ipaddr = self.curServer.conn.sock.getpeername()[0]
            except (AttributeError,IndexError): ipaddr = ""
            if ipaddr:
                ipaddrs.append(ipaddr)
            else:
                self.msg("Server not connected, using a host lookup")
                info = socket.getaddrinfo(self.curServer.host, None)
                # Could just make addrs a set here, but that would lose the returned address order.
                addrs = [rec[4][0] for rec in info]
                for addr in addrs:
                    if addr in ipaddrs: continue
                    ipaddrs.append(addr)
                if len(ipaddrs) != 1:
                    self.msg(f"{len(ipaddrs)} results:")
        if not ipaddrs:
            self.msg("No address to locate")
            return
        for ipaddr in ipaddrs:
            buf = geolocator.get_general_geolocation(ipaddr, verbose=verbose)
            if not buf: buf = "No information returned"
            self.msg(str(buf))

    def do_op(self, line):
        """Op or deop a user in one or more channels or check ops.
        Syntax: op [-a|-d] [<user> [<channel> ...]]
        Op with no arguments lists all ops on the server.
        Op with just a user lists that user's ops.
        Op with -a or -d and a user adds or deletes that user's ops from channels.
        If no channel is specified, the user's current channel is used.
        Otherwise, the command affects all channels listed.
        Changing ops requires admin rights or ops in the affected channel(s).
        Note that this command deals with active ops, not ops set as part of user accounts; see the Account command for those.
        """
        server = self.curServer
        k = "operators"
        line = line.strip()
        if not line:
            # List all ops on server.
            for u in sorted(list(server.users.values()), key=lambda u1: server.nonEmptyNickname(u1, "n")):
                userid = u.userid
                matches = [c for c in server.channels.values() if userid in (c.get(k) or [])]
                matches = ", ".join([c.channel for c in matches])
                if matches:
                    self.msg(f"{server.nonEmptyNickname(u, 'n')}: {matches}")
            return
        # Add, delete, or just show ops for a user.
        act = ""
        if line.startswith("-"):
            if line.startswith("-a"):
                act = "add"
            elif line.startswith("-d"):
                act = "del"
            else:
                raise SyntaxError(f"Unknown option: {line[:2]}")
        args = shlex.split(line)
        # Get rid of -a or -d.
        if act: args.pop(0)
        if not args: raise SyntaxError("Must specify a user.")
        u = self.userMatch(args.pop(0))
        if args and not act:
            raise SyntaxError("No channels needed when just listing ops")
        if not args and u.get("channel") and act:
            # When no channel is given and ops are being changed,
            # use the user's current channel as the target.
            args.append(u.channel)
        opstatus = 0
        if act == "add": opstatus = 1
        # This loop is skipped if not act because we didn't allow that
        # case above.
        for chanName in args:
            c = self.channelMatch(chanName)
            chspec = f"chanid={c.chanid}"
            self.do_send(f'op userid={u.userid} {chspec} opstatus={opstatus}')
            # Let the op list print after those modifications.
        # List ops for just this user.
        userid = u.userid
        matches = [c for c in server.channels.values() if userid in (c.get(k) or [])]
        matches = ", ".join([c.channel for c in matches])
        if matches:
            self.msg(f"{server.nonEmptyNickname(u, 'n')}: {matches}")

    def do_admins(self, line=""):
        """List the admins currently on server and where they are and come from.
        """
        channelname = self.curServer.channelname
        for u in self.curServer.users.values():
            if not u.usertype or int(u.usertype) != 2: continue
            ch = None
            if u.chanid: ch = channelname(u.chanid)
            self.msg(f"{self.curServer.nonEmptyNickname(u)}: {u.ipaddr}, {ch}")

    def do_ping(self, line=""):
        """Send a ping to the server. A pong should come back.
        This command will print the number of milliseconds it takes to receive the response.
        This can be useful for measuring lag time between client and server.
        """
        tm1 = time.monotonic()
        results = self.request("ping")
        if not results: return
        tm2 = time.monotonic()
        tmdiff = tm2 - tm1
        ms = tmdiff * 1000
        ev = results[0].event
        self.msg(f"{ev} {ms:1.2f} ms")

    def do_run(self, fname):
        """Run, or replay, a file of raw TeamTalk API commands at the current server.
        """
        if not fname:
            self.msg("No file name specified.")
            return
        fname = self.dequote(fname)
        fname = os.path.expanduser(os.path.expandvars(fname))
        # TODO: Consider security of unrestricted filesystem access here.
        if not os.path.exists(fname):
            self.msg(f"File {fname} not found.")
            return
        with open(fname, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("addchannel"):
                    line = line.replace("addchannel", "makechannel", 1)
                elif line.startswith("serverupdate"):
                    line = line.replace("serverupdate", "updateserver", 1)
                elif line.startswith("useraccount"):
                    line = line.replace("useraccount", "newaccount", 1)
                if (line.startswith("updateserver")
                and "userrights=" in line
                ):
                    # Some userrights bits can throw out the whole updateserver request.
                    i = line.find("userrights=")
                    line1,sep,rest = line[i:].partition(" ")
                    line = line[:i-1] +rest
                    line1 = "updateserver " +line1
                    self.rawSend(line1)
                self.rawSend(line)

    def rawSend(self, line):
        """Send a raw line to the server.
        """
        self.curServer.conn.send(line)

    def _sendMessage(self, parms, msg):
        """Send a message command to the current server.
        parms must be a TTParms object.
        msg is the raw message content to send, of arbitrary length.
        """
        if not msg: raise ValueError("No content to send")
        while len(msg):
            more = int(len(msg) > 511)
            p = parms.copy()
            p.append(StringParm("content", msg[:511], rawValue=True))
            p.append(IntParm("more", more))
            self.do_send(TTParms(p))
            msg = msg[511:]

    def do_send(self, line):
        """Send a raw command to the current server.
        """
        # Line can be a text line or a ParmLine or TTParms object.
        logger.info("%s\n  %s: %s\n" % (
            datetime.now().ctime(),
            self.curServer.shortname,
            "_send_ " +str(line)
        ))
        self.curServer.sendWithWait(line)

    def request(self, line):
        """Send a command and return its results as a list of ParmLines.
        Line can be a text line or a ParmLine object.
        """
        return self.curServer.sendWithWait(line, True)

    def do_flags(self, line=""):
        """Translate hex or decimal flags into their meanings.
        Usage: flags [-c|-w|-l] type value
        -c/-w/-l: Use character, word (default), or long flags when printing results.
        type: One of the available flag types, or a unique prefix of the type's name.
        Type names: banTypes, channelTypes, logEvents, subscriptions, userRights.
        value: Decimal value, or 0x and the hex value.
        """
        fnames = ("banTypes", "channelTypes", "logEvents", "subscriptions", "userRights")
        fclasses = (ttflags.BanTypes, ttflags.ChannelTypes, ttflags.LogEvents, ttflags.Subscriptions, ttflags.UserRights)
        args = shlex.split(line)
        style = "w"
        if "-c" in args:
            args.remove("-c")
            style = "c"
        elif "-w" in args:
            args.remove("-w")
            style = "w"
        elif "-l" in args:
            args.remove("-l")
            style = "l"
        try: fname = args.pop(0)
        except IndexError: raise CommandError("Please specify a flag type")
        matches = [e for e in fnames if e.lower().startswith(fname.lower())]
        if not matches: raise CommandError(f"Unrecognized flag type: {fname}")
        if len(matches) > 1: raise CommandError(f"{fname} matches {len(matches)} flag types")
        fname = matches[0]
        fclass = fclasses[fnames.index(fname)]
        try: val = args.pop(0)
        except IndexError: raise CommandError("Please specify a flag value")
        try: val = int(val, 0)
        except ValueError: raise CommandError(f"Unrecognized flag value: {val}")
        self.msg(fclass(val).toString(style))

    def do_hrsize(self, line=""):
        """Translate a raw byte count into a human-readable size.
        Usage: hrsize byteCount [unitType]
        Unit types, default is decimal:
            d: Decimal; 1 megabyte is 1 million bytes.
            b: Binary; 1 megabyte is 1,048,576 bytes.
        """
        args = shlex.split(line)
        try: bytes = int(args.pop(0))
        except ValueError:
            raise CommandError("Please specify a byte count")
        ut = args.pop(0).lower() if args else "d"
        if ut not in ["b", "d"]:
            raise CommandError(f"Unknown unit type: {ut}")
        self.msg(hrsize(bytes, ut))

    def run(self):
        if self.extras:
            self.msg("Extra module(s) loaded:")
            for mod in self.extras:
                self.msg(f"    {mod}")
        MyCmd.run(self)

    # This loads supplemental per-user code if it exists.
    # This is done after all the def's so that such code can override things.
    from glob import glob
    extras = []
    paths = []
    # This would allow custom code files anywhere on the path.
    # The path is searched in reverse so earlier findings override later ones.
    # This is disabled out of security concerns though.
    #paths.extend([p for p in reversed(sys.path)])
    p1 = os.path.dirname(sys.argv[0])
    if p1 not in paths:
        paths.append(p1)
    for p in paths:
        files = glob(os.path.join(p, "TTComCmd_*.py"))
        for f in files:
            #fp = os.path.join(p, f)
            fp = f
            #print "Importing supplemental code from " +fp
            with open(fp) as f:
                exec(f.read().replace('\r\n', '\n'))
            extras.append(fp)
