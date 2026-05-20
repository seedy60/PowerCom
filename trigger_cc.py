"""trigger_cc - Load custom plugin/trigger code if it exists.
Also defines a support class for such code.
Hard-codes the legacy Python module file name for this code.

See https://www.dlee.org/tt/ttcom/#plugins for how to create plugins.

As of 2025, TTCom customizes sys.path to let plugins import external libs.
plugins should no longer do this for themselves.
    
Examples of what can be done from Trigger methods:
self.runCommand("system play ...")
self.server.outputFromEvent("Blah that prints only if server isn't silenced.")
self.server.errorFromEvent("blah that prints even for silent servers.")
mycmd_say("Blah") but warning, that might suspend TTCom a while.
time.sleep(0.5)
self.server.send[WithWait]("kick userid=%s" % (event.parms.userid))
(That one can serve to "ban" someone by more than just IP address.)

See also methods in the TriggerBase class in this module.
See also ttapi classes for info on server, user, and other object types.

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

import os, sys, importlib, importlib.util
import traceback

customCode = {}

class TriggerBase:
    def __init__(self, server, event, runCommand):
        self.server = server
        self.event = event
        self.runCommand = runCommand
        # This can be None before login is completed.
        try: self.myIP = self.server.me.ipaddr
        except AttributeError: self.myIP = None

    def serverIsCurrent(self):
        """Returns True if this trigger is from the server that is current.
        """
        return self.server == self.server.parent.curServer

    def nameFromID(self, userid):
        """Return a printable user id string from a userid.
        """
        return self.server.nonEmptyNickname(self.server.users[userid])

def loadPlugins():
    # Trigger code does not load for single-command TTCom invocations.
    if len(sys.argv) > 1:
        return
    reloading = (len(customCode) > 0)
    if reloading:
        for modname in customCode:
            del sys.modules[modname]
        customCode.clear()
    # Set up what we need to make per-plugin import path modifications.
    if getattr(sys, 'frozen', False):
        application_path = os.path.dirname(sys.executable)
    elif __file__:
        application_path = os.path.dirname(__file__)
    # First, legacy top-level ttcom_triggers file.
    fpath = "ttcom_triggers.py"
    modname = "ttcom_triggers"
    if os.path.exists(fpath):
        # Legacy; assumes no subfolder imports.
        spec = importlib.util.spec_from_file_location(modname, fpath)
        mod = importlib.util.module_from_spec(spec)
        try: spec.loader.exec_module(mod)
        except Exception:
            print(f"Error in legacy plugin {modname}:")
            traceback. print_exc()
            print(f"Legacy plugin {modname} failed to load")
            return
        customCode[modname] = mod
        sys.modules[modname] = mod
    # Then the plugins architecture created in February, 2025.
    fpath = "plugins"
    modpath = ""
    if not os.path.exists(fpath):
        return
    for entry in os.listdir(fpath):
        # Plugin names must start with a letter or number.
        # os.listdir skips . and .. but this also skips things like __pycache__
        if not entry[0].isalnum(): continue
        modname = ""
        modpath = os.path.join(fpath, entry)
        popPath = False
        if os.path.isdir(modpath) and os.path.exists(os.path.join(modpath, "__init__.py")):
            # Support for complex plugins with subfolder imports if necessary.
            modname = entry
            modpath = os.path.join(modpath, "__init__.py")
            pth = application_path+f"/plugins/{modname}"
            # insert not append; plugins may need custom versions of common packages.
            # Example: Using platform_utils.paths.app_data_path() won't work without mods.
            # This also makes sure a non-virtualenv setup with pip installs won't break things so easily.
            sys.path.insert(0, pth)
            popPath = True
        elif os.path.isfile(modpath) and entry.lower().endswith(".py"):
            # Simpler; no subfolder imports.
            modname = os.path.splitext(entry)[0]
        if modname and modpath:
            spec = importlib.util.spec_from_file_location(modname, modpath)
            mod = importlib.util.module_from_spec(spec)
            try: spec.loader.exec_module(mod)
            except Exception:
                print(f"Error in plugin {modname}:")
                traceback. print_exc()
                print(f"Plugin {modname} failed to load")
                if popPath:
                    sys.path.pop(0)
                continue
            customCode[modname] = mod
            sys.modules[modname] = mod
        if popPath:
            sys.path.pop(0)
    if len(customCode):
        print(f"Plugins loaded: {', '.join(customCode.keys())}")

def apply(server, parmline, runCommand):
    # Is there custom code at all?
    if len(customCode) == 0: return
    for modname,code in customCode.items():
        # How about a server-specific Trigger_* class?
        func = "Trigger_" +server.shortname
        try: func = getattr(code, func)
        except AttributeError: func = None
        if func: func(server, parmline, runCommand)
        # And finally, an all-server Trigger class?
        try: code.Trigger
        except AttributeError: return
        code.Trigger(server, parmline, runCommand)

loadPlugins()
