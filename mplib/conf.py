"""Application configuration manager.
"""
"""
Copyright (C) 2016-2026 Doug Lee

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
from .configobj import ConfigObj, ConfigObjError, flatten_errors
hasWin32 = True
try: from win32com.shell import shellcon, shell
except ImportError: hasWin32 = False

class Conf:
    """Borg-style class for application configuration information.
    (For info on singleton class types, see https://www.geeksforgeeks.org/singleton-pattern-in-python-a-complete-guide/ )

    Members:
        exefile: Full path to the running app.
        appFolder, appName: Properties returning folder and name parts of exefile.
        confExt: Extension of configuration file; should be .ini.
        confName, confFolder: Properties returning name and folder of configuration file.
        confFile: Property returning full path to configuration file.
        isPortable: True if this is a portable app instance (configs in folder with app itself).
        plat: String indicating host machine type:
            Mac: macos.
            Linux including WSL on Windows: linux.
                (See dglutils.platform_string() for a version that identifies WSL.)
            Windows including Cygwin: windows.
            Anything else: Equal to sys.platform.lower() when not recognized.
            This can be used for decision-making.
        conf: Configobj-style configuration structure.
    Methods:
        load: Load or reload configuration information.
        optGet, optSet, option: Get and set options in the configuration.
    Miscellaneous notes:
        Item references to this class forward to self.conf; so conf["blah"] is the same as conf.conf["blah"].
        conf.option is the recommended interface for getting and setting options.
        Exception: conf.optSet can delete options.
    """

    # This allows subclasses to add members/methods to the single instance.
    _shared_dict = {}
    def __new__(cls, *args, **kwargs):
        obj = super().__new__(cls)
        obj.__dict__ = cls._shared_dict
        return obj

    def __init__(self):
        if not Conf._shared_dict:
            self._bootstrap()

    def _bootstrap(self):
        # First, find ourself.
        self._exefile = sys.argv[0]
        # If that's a symlink, start from its target.
        if os.path.islink(self._exefile):
            self._exefile = os.readlink(self._exefile)
        if not os.path.dirname(self._exefile):
            # Try to figure out where we're running from anyway.
            cwd = os.getcwd()
            # This makes just typing the app name on a command line with no path or extension work against a stand-alone executable.
            if not os.path.splitext(self._exefile)[1]:
                self._exefile += ".exe"
            self._exefile = os.path.join(cwd, self._exefile)
            if not os.path.exists(self._exefile):
                raise ValueError("Run with full path; unable to find myself")
        # Now set up the members that feed the documented properties for parts of that path.
        self._appName = os.path.splitext(os.path.basename(self._exefile))[0]
        self._appFolder = os.path.dirname(self._exefile)
        # Extension used for app config files.
        self.confExt = ".ini"
        # Now for config file path determination -
        # but we need machine type for that.
        self.plat = self._machineType()
        # Full path to user config file if this is a portable app instance.
        portFile = os.path.join(self.appFolder, self.confName)
        self._confFolder = ""
        if os.path.exists(portFile):
            # Portable instance assumed.
            self._confFolder = self.appFolder
            # Userpath not needed.
        else:
            self._setUserPath()
            # That may be the null string.
            if self.userpath: self.userpath = os.path.join(self.userpath, self.appName)
            if self.userpath and not os.path.exists(self.userpath):
                print(f"Creating folder for {self._appNamePrint} user files: {self.userpath}", file=sys.stderr)
                os.mkdir(self.userpath)
            self._confFolder = self.userpath
            # Fall back to portable if we absolutely can't find a userpath to use.
            if not self._confFolder or not os.path.exists(self._confFolder):
                self._confFolder = self.appFolder
        # self._confFolder must now be a valid and existing path.
        # Full path to default config file, maintained with the app.
        # This path is not saved as an instance var because we only load this file once.
        defFile = os.path.join(self.appFolder, self.appName) +"_defaults" +self.confExt
        self.default_cfg = self._confOne(defFile, True)
        self.load()

    def _machineType(self):
        """Returns "macos", "linux, windows," or sys.platform."""
        plat = sys.platform.lower()
        if plat[:3] in ["mac", "dar"]:
            return "macos"
        elif "linux" in plat: # e.g., linux2
            return "linux"
        elif "win" in plat: # windows, win32, cygwin
            return "windows"
        return plat

    def _setUserPath(self):
        """Set self.userpath, null if no value found.
        This is the folder to contain the app user folder but does not include the app's actual subfolder.
        Method:
            * Collect all candidates, in first-preference-first order; see _getUserPathCandidates().
            * Pick the first of these where the app's specific folder already exists.
            * Failing that, pick the first candidate returned.
        Note that the app's folder may actually be a symlink, on platforms that support this.
        """
        self.userpath = ""
        paths = self._getUserPathCandidates()
        if not paths: return
        # First priority: If a path for this specific app exists already, use the first such case.
        pths = [path for path in paths if os.path.exists(os.path.join(path, self.appName))]
        if pths:
            self.userpath = pths[0]
            return
        # Failing that, use the first candidate found.
        return paths[0]

    def _getUserPathCandidates(self):
        """Get candidate folders for _setUserPath.
        A path must exist at app load time to be considered.
        Rules:
            - If APPDATA is set to a path in the environment (usually for Windows and WSL/Cygwin), include that.
            - If this is Windows and we have Win32 code and we can get APPDATA from that, include that.
            - If this is Windows and nothing worked yet, stop.
            - If HOME is not valid in the environment, stop.
            - If the (generally MacOS) folder $HOME/Library/Application Support folder exists, include that.
            - If the (generally Linux) $HOME/.config folder exists, consider that.
            - Return everything that got included.
        """
        paths = []
        path = ""
        # APPDATA on Windows and Cygwin (usually but not always defined).
        path = os.environ.get("APPDATA")
        if path and os.path.exists(path):
            paths.append(path)
        # Windows-specific for when APPDATA is not defined but we have Win32 codebase.
        if hasWin32:
            # APPDATA folder without relying on env['APPDATA'] being present..
            # Does not tend to work under WSL or Cygwin in most cases because win32 code is not loaded there.
            path = ""
            try: path = shell.SHGetFolderPath(0, shellcon.CSIDL_APPDATA, 0, 0)
            except Exception: pass
            if path and os.path.exists(path):
                paths.append(path)
        home = os.environ.get("HOME")
        if not home or not os.path.exists(home):
            return paths
        # Generally MacOS...
        tst = os.path.join(home, "Library", "Application Support")
        if os.path.exists(tst):
            paths.append(tst)
        # Generally Linux...
        tst = os.path.join(home, ".config")
        if os.path.exists(tst):
            paths.append(tst)
        return paths

    # Public properties, then methods.
    appFolder = property(lambda self: self._appFolder, None, None, "Folder containing application")
    appName = property(lambda self: self._appName, None, None, "Basename of application, without extension. This name is sufficient for filesystem folder names, file basenames, etc.")
    _appNamePrint = property(lambda self: self._appName[0].upper() +self._appName[1:], "Preferred version of app name for printing. Don't use for filesystem folder/file names.")
    confName = property(lambda self: self.appName +self.confExt, None, None, "Name of user config file, wherever it is located.")
    confFolder = property(lambda self: self._confFolder, None, None, "Folder containing user config files for this app instance.")
    isPortable = property(lambda self: self.confFolder == self.appFolder, None, None, "True if this is a portable app instance.")
    confFile = property(lambda self: os.path.join(self._confFolder, self.confName), None, None, "Full path to active user configuration file.")

    def load(self):
        """Load or reload user configuration info.
        Default configuration info is not reloaded.
        """
        # Copy of defaults.
        cfg = ConfigObj(self.default_cfg)
        # Merge user config settings into the copy.
        self.user_cfg = self._confOne(self.confFile)
        cfg.merge(self.user_cfg)
        # There is no file for the merged settings.
        cfg.filename = None
        self.conf = cfg

    def __getitem__(self, i): return self.conf.__getitem__(i)
    def __setitem__(self, i, v): return self.conf.__setitem__(i, v)

    def _confOne(self, path, mustExist=False):
        """Create a ConfigObj from one ini file.
        """
        try: cfg = ConfigObj(path,
            configspec=None,
            list_values=False,
            interpolation=False,
            file_error=mustExist,
            create_empty=not mustExist,
            indent_type="",
            encoding="UTF-8",
        )
        except (ConfigObjError, IOError) as e:
            sys.exit(f"Unable to read {path}: {e}")
        return cfg

    def optGet(self, name, sect="Options", default=None):
        """Get an option value or return the default value if not found.
        The default value is None if not given.
        sect is the section to use, Options by default.
        """
        try: return self.conf[sect][name]
        except KeyError: return default

    def optSet(self, name, val, sect="Options"):
        """Set an option value. Writes the update to disk if necessary.
        Note that a value of None is equivalent to deleting the option.
        sect is the section to use, Options by default.
        """
        # Check only user settings, not default settings.
        try: curval = self.user_cfg[sect][name]
        except KeyError: curval = None
        # Even if that was None, do nothing if there is nothing to change.
        if curval == val: return
        if val is None:
            # A deletion.
            try: del self.user_cfg[sect][name]
            except KeyError: return
            self.user_cfg.write()
            self.load()
            return
        # A key update.
        self.user_cfg.setdefault(sect, {})
        self.user_cfg[sect][name] = val
        self.user_cfg.write()
        self.load()

    def option(self, sOpt, newval=None, default=None, section="Options"):
        """Get or set an option in the Options or given section of the ini file.
        Returns the current value whether or not it is first changed.
        When getting and default is passed, default is returned if sOpt is not found.
        Newer interface than optGet/optSet.
        """
        if newval is not None:
            self.optSet(sOpt, newval, sect=section)
        return self.optGet(sOpt, sect=section, default=default)

    def msg(self, line):
        print(line.rstrip())

