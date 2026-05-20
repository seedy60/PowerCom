"""Utilities used by various of Doug Lee's Python programs.

Copyright (C) 2008-2026 Doug Lee

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

import os, sys, re, shutil, shlex, platform
from subprocess import call, run, PIPE, STDOUT, DEVNULL

def safeCall(*args, **qwargs):
    """Call that retries on OSError 11, which happens on Cygwin 1.8.
    safeCall and callWithRetry are both provided here for historical reasons.
    """
    maxtries = 20
    while maxtries:
        maxtries -= 1
        try:
            return call(*args, **qwargs)
        except OSError as e:
            if e.errno != 11:
                raise

def callWithRetry(func, *args, **kwargs):
    """For Cygwin 1.8 on Windows:
    Forks can ffail randomly in the presence of things like antivirus software,
    because DLLs attaching to the process can cause address mapping problems.
    This function retries such calls so they don't fail.
    safeCall and callWithRetry are both provided here for historical reasons.
    """
    i = 1
    while i <= 50:
        try:
            return func(*args, **kwargs)
        except OSError as e:
            i += 1
            print("Retrying, attempt #" +str(i))
    print("Retry count exceeded.")

class PathCache(dict):
    """A class for caching executable paths, and also absences of them.
    """
    _cache = {}
    @classmethod
    def getPath(cls, name):
        """Get a path for an executable by its name, or return the null string if it is not found. Cache the result for the next call with the same name.
        """
        p = cls._cache.get(name)
        if p is not None: return p
        p = shutil.which(name)
        cls._cache[name] = p
        return p

def hrsize(size, flags="d"):
    """Return a 10- to 12-character string representing the given size succinctly in human-readable form:
        0-1023 bytes: Size suffixed with " bytes.
        0-1023 kb, meg, gig, tb, etc.: Size with two decimal places with the appropriate suffix.
    All values are aligned so that the first four characters are always thousands through ones for any unit.
    Flags (default is "d"):
        b: Binary (multiplier 1024, IEC units used, field width 11).
        d: Decimal (multiplier 1000, field width 10).
        s: Speed (multiplier 1000, units end in "bps", field width 12).
        t: Transfer rate (multiplier 1000, units end in "B/s", field width 12).
    Meanings of first letter: kilo, mega, giga, tera, peta, exa, zetta, yotta, ronna, quetta.
    If a value exceeds the highest supported unit, the value will be longer and the format will break.
    At this writing, that only happens at 10**34for decimal and a bit later for binary scaling.
    Intended standards adherence:
    * IEC 80000-13.
    * IEEE 1541-2002.
    """
    unit_info = {
        # Format: flag: (multiplier, field_width, plain_unit, k_unit, meg_unit, ...)
        "b": (1024, 11, "bytes", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB", "ZiB", "YiB", "RiB", "QiB"),
        "d": (1000, 10, "bytes", "kB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB", "RB", "QB"),
        "s": (1000, 12, "bps", "kbps", "Mbps", "Gbps", "Tbps", "Pbps", "Ebps", "Zbps", "Ybps", "Rbps", "Qbps"),
        "t": (1000, 12, "B/s", "kB/s", "MB/s", "GB/s", "TB/s", "PB/s", "EB/s", "ZB/s", "YB/s", "RB/s", "QB/s"),
    }
    if isinstance(size, str): size = int(size)
    units = list(unit_info[flags]).copy()
    scale = units.pop(0)
    fwidth = units.pop(0)
    uidx = 0
    val = size
    while val > scale and uidx+1 < len(units):
        val /= float(scale)
        uidx += 1
    unit = units[uidx]
    # 4 places for digits plus space before unit.
    uwidth = fwidth - 5
    if unit == "bytes":
        val = f"{val:4d}"
    else:
        val = f"{val:7.2f}"
        # Now we need space for a decimal and two more digits.
        uwidth -= 3
    val = f"{val} {unit:>{uwidth}s}"
    return val

def timeToSecs(tm):
    """Return the number of seconds represented by the given possibly incomplete or improper time,
    like 2: or 76:88.  Negative values are allowed as well.
    Returns float. A given null string returns 0.0.
    Given times like 14 or 14.5 become seconds directly.
    Needed for sending start times to ffmpeg.
    """
    if not tm:
        return float(0)
    if not isinstance(tm, str) or ":" not in tm:
        return float(tm)
    sign = 1
    if tm[0] == "-":
        tm = tm[1:]
        sign = -1
    parts = tm.split(":")
    # Fix null segments. This is for things like 33: and 4:22: but technically also handles things like 5::22.
    parts = [p if p else "0" for p in parts]
    secs = 0
    try:
        # The first one to fail stops this.
        secs += float(parts.pop())
        secs += float(parts.pop()) * 60.0
        secs += float(parts.pop()) * 3600.0
    # Unlikely but supported, days.
        secs += float(parts.pop()) * (3600.0 *24.0)
    except IndexError: pass
    return sign * secs

def secsToTime(secs):
    "Convert seconds to hh:mm:ss."
    mm,ss = divmod(secs, 60)
    hh,mm = divmod(mm, 60)
    return "%02d:%02d:%02d" % (hh, mm, ss)

def copyTimes(path1, path2):
    """Copy the access and modification times of path1 to path2.
    """
    st = os.stat(path1)
    nstimes = (st.st_atime_ns, st.st_mtime_ns)
    os.utime(path2, ns=nstimes)

def crtime(f):
    """Return the birth time of a file as a timestamp.
    May be slow; uses stat(1) when os.stat doesn't include st_birthtime.
    """
    try:
        # This works on at least MacOS Monterey (and surely later and maybe earlier).
        # Tested on Python 3.13.2.
        # If the file doesn't exist, a different error will be raised here.
        return os.stat(f).st_birthtime
    except AttributeError:
        pass
    # This happens on at least Ubuntu 24.04 (noble) under Python 3.12.3.
    # By this point though, we know the file exists.
    stpath = shutil.which("stat")
    if not stpath:
        raise OSError("Birth time not available")
    proc = run(
        [stpath, "-c", "%W", f],
        stdin=DEVNULL, stdout=PIPE, stderr=PIPE,
        text=True
    )
    result = float(proc.stdout.strip())
    if result != 0.0: return result
    raise OSError("Birth time not available")

def isOggOpusFile(soundfile):
    """Returns True if soundfile appears to be an Ogg Opus file.
    """
    try:
        with open(soundfile, "rb") as f:
            bytes = f.read(512)
            if re.search(b"OpusHead", bytes): return True
    except Exception: return False
    return False

def soxFileTypes(path=None):
    """Return a set of extensions (with leading dot) supported by the local SoX version.
    If path is given, it is the SoX instance to use; otherwise the environment PATH is used to find it.
    "Supported" here means able to decode.
    """
    if not path: path = PathCache.getPath("sox")
    if not path: path = PathCache.getPath("sox.exe")
    proc = run(path, stdin=DEVNULL, stdout=PIPE, stderr=STDOUT, encoding="utf-8")
    stdout = proc.stdout
    formats = stdout.split("AUDIO FILE FORMATS:", 1)[1].split("\n", 1)[0].strip().split(None)
    formats = ["."+f for f in formats]
    if ".opus" in formats:
        # A format that is Opus but with a different extension, used by Unigram/Telegram.
        formats.append(".oga")
    return set(formats)

def ffmpegFileTypes(path=None):
    """Return a set of extensions (with leading dot) supported by the local ffmpeg version.
    If path is given, it is the ffmpeg instance to use; otherwise the environment PATH is used to find it.
    If ffmpeg is not found or there is an error, the empty set is returned.
    "Supported" here means able to decode (demux, as ffmpeg puts it).
    """
    if not path: path = PathCache.getPath("ffmpeg")
    if not path: path = PathCache.getPath("ffmpeg.exe")
    if not path: return set()
    proc = run([path, "-formats"], stdin=DEVNULL, stdout=PIPE, stderr=DEVNULL, encoding="utf-8")
    lines = proc.stdout.splitlines()
    formats = set()
    started = False
    for line in lines:
        line = line.strip()
        # Ignore header material.
        if not line: continue
        if not started and "--" in line:
            started = True
            continue
        if not started: continue
        # An actual format line. Format: flags fmt[,fmt...] description.
        # Flags: D can decode, E can encode.
        try: flags,fmts,rest = line.split(None, 2)
        except ValueError: flags,fmts = line.split(None, 1)
        if "d" not in flags.lower(): continue
        fmts = fmts.split(",")
        [formats.add(f".{fmt}") for fmt in fmts]
    if ".opus" in formats:
        # A format that is Opus but with a different extension, used by Unigram/Telegram.
        formats.append(".oga")
    return formats

def platform_string(flags=""):
    """Return a string representing the platform on which this app is running.
    Warning: This call may take from 0.1 to 0.5 seconds.
    flags:
        e: Print error messages. Intended for debugging this function.
    The returned string is meant more for printing than for application decision-making;
    see sys.platform() for that, or lower-case this method's result first.
    Using .startswith(...) for matching is recommended; e.g., plat.lower().startswith("windows ")
    In multiplatform cases like WSL, the immediate containing OS is returned first.
    Results are NOT all in lower case.
    Valid result components, in order, at this writing (August, 2025):
        * Base OS (MacOS, Linux, Windows, Cygwin, etc.).
          For unrecognized platforms, this will likely be sys.platform (lower case).
        * For Windows, version and edition; e.g., 11 Professional.
        * For Linux, distro and release name; e.g., Ubuntu Noble or Debian Bookworm (case may vary).
        * For MacOS, release name; e.g., Monterey.
        * Containing VM technology; e.g., vmware, WSL/WSL1/WSL2. WSL may be in upper or lower case.
          This is currently only detected from Linux with systemd installed.
    """
    plat = sys.platform.lower()
    if plat[:3] in ["mac", "dar"]:
        # Add release name.
        relname = ""
        lname = '/System/Library/CoreServices/Setup Assistant.app/Contents/Resources/en.lproj/OSXSoftwareLicense.rtf'
        try:
            with open(lname, "r", encoding="utf-8", errors="replace") as f:
                relname = f.read().split('SOFTWARE LICENSE AGREEMENT FOR macOS', 1)[1].split("\n", 1)[0]
                relname = re.sub(r'\W*$', r'', relname)
                if relname:
                    relname = " " + relname
        except Exception as e:
            if "e" in flags:
                print(f"While retrieving MacOS release name: {str(e)}")
            pass
        return _include_vm_info(f"MacOS{relname}", flags)
    if "linux" in plat: # e.g., linux2
        plat = platform.system()
        # Add distro and release friendly names if possible.
        dct = None
        try:
            dct = platform.freedesktop_os_release()
        except Exception as e:
            if "e" in flags:
                print(f"While requesting freedesktop_os_release info: {str(e)}")
        if not dct:
            # This happened on Debian Buster; read it for ourselves.
            try:
                with open('/etc/os-release', "r", encoding="utf-8", errors="replace") as f:
                    # That's a shell script of key=value pairs, with sometimes quoted values.
                    lines = f.read().replace('"','').replace("'","").splitlines()
                    dct = dict([line.split("=", 1) for line in lines if "=" in line])
            except Exception as e:
                if "e" in flags:
                    print(f"While reading/parsing /etc/os-release: {str(e)}")
        if not dct:
            return _include_vm_info(plat, flags)
        try:
            relname = dct["VERSION"].split("(", 1)[1].split(")", 1)[0].split(None, 1)[0]
        except Exception as e:
            if "e" in flags:
                print(f"While splitting freedesktop_os_release VERSION info: {str(e)}")
            relname = dct.get("VERSION_CODENAME", "").title()
        plat += f" {dct['NAME'].split(None, 1)[0]}"
        if relname:
            plat += f" {relname}"
        return _include_vm_info(plat, flags)
    if "win" in plat: # windows, win32, cygwin
        # These give things like Windows, 11, Professional.
        # ToDo: Verify what happens on Cygwin here.
        plat = f"{platform.system()} {platform.release()} {platform.win32_edition()}"
        # ToDo: Find a way to add things like " 24h2"
        if plat == "cygwin" or os.path.exists("/cygdrive"):
            plat += " Cygwin"
        return plat
    return _include_vm_info(plat, flags)

def _include_vm_info(plat, flags):
    """Return plat with any additional information for an OS running in a VM.
    The word "in" will separate any finding here from the rest of the platform info.
    """
    pl = platform.release()
    if "WSL" in pl:
        # This tends to add "2" and hopefully "1" as appropriate.
        plat += f' in WSL{pl.split("WSL", 1)[1]}'
        # ToDo: Consider reaching for more Windows info here.
        return plat
    try:
        proc = run(["systemd-detect-virt"], stdin=DEVNULL, stdout=PIPE, stderr=PIPE, text=True)
        vmtype = proc.stdout.strip()
        if vmtype and vmtype.lower() != "none":
            plat += f" in {vmtype}"
    except Exception as e:
        if "e" in flags:
            print(f"While looking for VM info: {str(e)}")
        return plat
    return plat

def SoXDev(ln, io="o"):
    """Return the platform-specific full-name translation of an abbreviated SoX-compatible device identifier.
    The optional second argument, "i" or "o" (default), means input versus output device.
    Currently only these platforms are supported: Windows, Cygwin and WSL (on Windows), MacOS (Darwin).
    Warning: These mappings are author-specific, though the VAC mappings may be of use to others.
    The SoX options -d, -n, and -p are returned unchanged.
    For an unrecognized device, i/o value, combination, or platform, the given value is simply returned unchanged.
    """
    if ln in ["-d", "-n", "-p"]:
        return ln
    io = io.lower()
    if not ln:
        io = "input" if io=="i" else "output"
        raise ValueError(f"Null {io} specification")
    if io not in "io":
        return ln
    isIn = (io == "i")
    # We need platform_string's ability to detect wsl here.
    plat = platform_string().lower()
    if "windows" in plat or "cygwin" in plat or "wsl" in plat:
        if ln[0] in "lL" and ln[1:].isdigit():
            # str(int()) removes any leading zeros.
            return f"Line {int(ln[1:])!s} (Virtual Audio Cable)"
        if ln.lower() == "a":
            return "Microphone (Andrea PureAudio US" if isIn else "Speakers (Andrea PureAudio USB-"
        if ln.lower() == "c":
            return "Microphone (Arctis Nova 7)" if isIn else "Headphones (Arctis Nova 7)"
        if ln.lower() == "g":
            # No stereo input available on this headset.
            if isIn:
                return ln
            return "Headphones (Arctis Nova 7)"
        if ln.lower() == "h":
            return "Microphone (Logi H800 Headset)" if isIn else "Speakers (Logi H800 Headset)"
        if ln.lower() == "r":
            return "Microphone Array (Realtek(R) Au" if isIn else "Speakers/Headphones (Realtek(R)"
        if ln.lower() == "l":
            return "Line In (Realtek(R) Audio)" if isIn else "Speakers/Headphones (Realtek(R)"
        if ln.lower() == "u":
            return "Line (USB Audio Device)" if isIn else "Speakers (USB Audio Device)"
        if ln.lower() == "z":
            return "Microphone (ZOOM H1essential)" if isIn else "Headphones (ZOOM H1essential)"
        return ln
    elif plat.startswith("darwin"):
        if ln[0] in "lL" and ln[1:].isdigit():
            # str(int()) removes any leading zeros.
            # ToDo: Replace with standard Loopback names.
            return f"Line {int(ln[1:])!s} (Virtual Audio Cable)"
        if ln.lower() == "h":
            return "Microphone (Logitech Wireless Headset)" if isIn else "Speakers (Logitech Wireless Headset)"
        if ln.lower() == "i":
            return "Built-in Input" if isIn else "Built-in Outpu"
        return ln
    return ln

class Opts(list):
    """Fairly simple command-line argument parser assistant.
    This parser is position-ignorant, meaning options can appear in any order with other arguments.
    If you need position sensitivity, parse fragments of the argument list separately.
    The standard "--" argument is honored for keeping options from being recognized after it.
    Option stacking (-abc for -a -b -c) is allowed, as are options with values.
    You must extract value-taking arguments first, as a result.
    Be very careful if you allow options like -2 and also values!
    Stacking can be disabled after object creation with opts.allow_stacking = False.
    A value-taking option MUST have a value, even if it is ""
    For instance, you can't support both -o with nothing after and -oOutFile / -o outFile.
    A value-taking option eats either the rest of its arg or the whole next one, whichever comes first.
    The list value of an Opts object at any moment is the args not yet taken out.
    The philosophy of this parser is, just handle parsing. The caller handles
        * Documentation of options.
        * Storage of option states and values.
        * Errors associated with domain-specific option requirements.
    This class raises a ValueError for issues with options being sought,
    and also if the user provides an option that requires a value but at the end of the arg list.
    Example usage:
        opts = Opts(sys.argv[1:][)
        if opts.get(["-a", "--all"]): print("-a found (at least once)")
        # Returns how many found, 0 or more.
        vals = opts.get(["-o", "--output"], value=True)
        if len(vals) > 1: sys.exit("can't specify multiple output targets at once")
    """
    def __init__(self, args):
        super().__init__(args)
        self.allow_stacking = True

    def get(self, opt, value=False):
        """Return how many times opt occurs in args (zero or more) and remove them all.
        opt can be an option (str) or a list of alternatives.
        If value is passed and True, instead returns a list of values or [] if none.
        Raises a ValueError if a passed permitted option is not properly formed.
        Also passes back a ValueError if the user specifies an allowed value-taking option at the end of the arg list.
        """
        opts = [opt, ] if isinstance(opt, str) else opt
        tally = [] if value else 0
        for opt in opts:
            # Sanity checks on each developer-specified option.
            if not opt.startswith("-"):
                if len(opt) == 1:
                    raise ValueError(f"Option {opt} must start with one dash")
                else:
                    raise ValueError(f"Option {opt} must start with two dashes")
            if opt.startswith("--"):
                if len(opt) <= 3:
                    raise ValueError(f"Option {opt} must contain more than one character after the dashes")
            elif len(opt) != 2:
                raise ValueError(f"Option {opt} must have exactly one character after the dash")
            while True:
                result = self._getOne(opt, value)
                if not result: break
                if value:
                    if result: tally.append(result)
                else:
                    if result: tally += 1
        return tally

    def _getOne(self, opt, value=False):
        """Return True and remove opt if found, and False if not.
        If value=True is passed, returns the value for the option if found or None if not.
        This removes both option and value, whether that's two args or one or part of one.
        Raises a ValueError if the user specifies an allowed value-taking option at the end of the arg list.
        """
        stopIndex = 32767
        if "--" in self: stopIndex = self.index("--")
        if not value:
            if opt in self and self.index(opt) < stopIndex:
                self.remove(opt)
                return True
            if not self.allow_stacking: return False
            # Can't stack long-form options.
            if opt.startswith("--"): return False
            opt = opt[1:]
            for i,arg in enumerate(self):
                if i >= stopIndex: break
                # No stealing short-forms out of long ones or non-option args.
                if arg.startswith("--") or not arg.startswith("-"): continue
                if opt not in arg: continue
                self[i] = arg.replace(opt, "", 1)
                # If there was only one, it would have matched before this loop.
                return True
            return False
        # Now for value-taking cases.
        if opt in self and self.index(opt) < stopIndex:
            idx = self.index(opt)
            self.remove(opt)
            # idx now points to the next one.
            if len(self) < idx:
                # This means the user did not give a value.
                raise ValueError(f"{opt} requires a value")
            return self.pop(idx)
        if not self.allow_stacking: return None
        # Can't stack long-form options.
        if opt.startswith("--"): return None
        opt = opt[1:]
        for i,arg in enumerate(self):
            if i >= stopIndex: break
            # No stealing short-forms out of long ones or non-option args.
            if arg.startswith("--") or not arg.startswith("-"): continue
            if opt not in arg: continue
            # If there was no value here, the option would have matched before this loop.
            self[i],val = arg.split(opt, 1)
            return val
        return None

class SoXFX:
    """Handler of custom-defined SoX effect shortcut conversion into full SoX effect chains.
    Initialize with an effect chain that might include expandable elements.
    After this, the following properties will exist:
        given: The originally given effect chain, with any shortcuts unchanged.
        expanded: The expanded and SoX-ready effect chain.
        profEffects: Any effects required for a separate initial SoX run to create a noise profile. "" if not needed.
    All shortcuts defined here contain no spaces.
    Supported shortcut patterns, where <f> and <n> are a float and an int, respectively:
    h<f> or l<f> becomes highpass <f> or lowpass <f>.
    comp<f>s becomes compand .01,.01,.01,.01 -<f>,-20,0,-10
    comp<f> becomes compand .01,.01 -<f>,-20,0,-10
        (Those two allow for quick and fast-reacting compressions that do and don't allow channels to vary with respect to each other.)
    dlbac and dlbad implement Dolby A compression and decompression, respectively.
    eqs<f1>[-<f2>][/<width>] becomes a range of equalizer effects; examples:
        eqs60-300 becomes equalizers 60/120/180/240/300 .0005h -180
        eqs60-300/.005h becomes the same with slightly wider cutout bands.
        If a width does not end with a letter (h/q/o), h is assumed.
        These are very effective means to remove harmonics such as from a 60Hz hum.
    fmr and fms: FM sound simulation and FM broadcast signal simulation, respectively.
    sil<f> becomes silence 1 0 <f>% -1 0 <f>%
        These are noise gates to trim out periods below a sound level threshhold.
        For historical reasons, sil0<n> becomes silence 1 0 .0<n>% -1 0 .0<n>% (equivalent to specifying sil0.0<n>).
        For example, sil03 and sil.03 are the same. Omitting the dot is deprecated.
    nv<n>[@<f1>[/<f2>]] Cut out sound at and above <n>dB, possibly centering around <f1>Hz and possibly with a width of <f2>Hz. <f2> defaults to 400Hz if not given when <f1> is given.
        This is a way to keep only the background sound from a file based on sound level threshhold and possibly frequency range of non-background sound.
        It can also be used to trim out very loud sounds in a quiet file as if they never happened.
        "nv" originally stood for "no vocals" as the author used this shortcut to test individual vocal tracks for unwanted background before mixing.
    ov<n>[@<f1>[/<f2>]] Cut out sound at and below <n>dB, possibly centering around <f1>Hz and possibly with a width of <f2>Hz. <f2> defaults to 400Hz if not given when <f1> is given.
        This is the inverse of nv and originally meant "only vocal." It preserves the described sound and trims out the rest.
    nr<start>,<duration>[,<factor>]: Noise reduction with on-the-fly profile.
        start and duration are time specifications like 3:0 and 0:1, or 3.1 (seconds) and 0:0.5.
        Without factor or with a factor of 1[.0], noisered uses 0.0. If factor is less than 1, noisered uses that.
        If factor is above 1, noisered uses 0.0 and a vol <factor> is prepended to the noisered effect.
        Warning: This effect creates self.profEffects for an extra SoX process to pipe the profile into this one;
        hence, it will not work in a live input stream setting.
        The creation and piping of this process is left to the caller.
    dtmf<string>[/[<duration>][/[<delay>][/[<fade>][/[<end>]]]]]: Dial DTMF tones.
        <string> is any sequence of 0-9 # * ABCD (case insensitive).
        <duration> and <delay> are in milliseconds and specify tone and inter-tone gap duration, defaults 200 and 100.
        <fade> is the millisecond duration of the start and end fades on tones, default 20 ms.
        <end> is the duration of the final delay after the tone sequence, default 1 sec (1000 ms).
        Making this too short can cause a "ValueError: SoX process run failure" message to print.
        This effect makes the most sense with the source being -n.
    """
    def __init__(self, given):
        self.given = given
        self.profEffects = ""
        parts = given.split()
        for i in range(0, len(parts)):
            p = parts[i]
            # h/l<f>
            p = re.sub(r'^h([\d.]+)$', r'highpass \1', p)
            p = re.sub(r'^l([\d.]+)$', r'lowpass \1', p)
            # comp<f>[s]
            p = re.sub(r'^comp([\d.]+)$', r'compand .01,.01 -\1,-20,0,-10', p)
            p = re.sub(r'^comp([\d.]+)s$', r'compand .01,.01,.01,.01 -\1,-20,0,-10', p)
            # dlbac and dlbad
            p = re.sub(r'^dlbac$', r'mcompand ".1,.1 4:-56,-46,-36,-26,-26,-20,-17,-15,-9,-9" 80 ".1,.1 4:-56,-46,-36,-26,-26,-20,-17,-15,-9,-9" 3k ".1,.1 4:-56,-46,-36,-26,-26,-20,-17,-15,-9,-9" 9k ".1,.1 4:-56,-42,-36,-23,-26,-18,-17,-14,-9,-9"', p)
            p = re.sub(r'^dlbad$', r'mcompand ".1,.1 4:-46,-56,-26,-36,-20,-26,-15,-17,-9,-9" 80 ".1,.1 4:-46,-56,-26,-36,-20,-26,-15,-17,-9,-9" 3k ".1,.1 4:-46,-56,-26,-36,-20,-26,-15,-17,-9,-9" 9k ".1,.1 4:-42,-56,-23,-36,-18,-26,-14,-17,-9,-9"', p)
            # eqs ranges.
            match = re.match(r'^eqs([\d.]+)-([\d.]+)/([\d.]+[a-zA-Z]?)$', p)
            if not match:
                match = re.match(r'^eqs([\d.]+)-([\d.]+)$', p)
            if not match:
                match = re.match(r'^eqs([\d.]+)$', p)
            if match:
                gr = match.groups()
                baseFreq = float(gr[0])
                if len(gr) > 1:
                    endFreq = float(gr[1])
                else:
                    endFreq = baseFreq
                if len(gr) > 2:
                    width = gr[2]
                    if not width[-1].isalpha():
                        width += "h"
                else:
                    width = ".0005h"
                freqMult = 1
                freq = baseFreq
                p = ""
                while freq <= endFreq:
                    if p: p += " "
                    p += f"equalizer {freq!s} {width} -180"
                    freqMult = freqMult +1
                    freq = freqMult *baseFreq
            # fmr and fms
            if p.lower() == "fmr" or p.lower() == "fms":
                p = (
                    # ToDo: The treble effect here and the gain after it replace the original "filter 8000- 29 100" and is my guess.
                    'gain -3 treble -12 8000 .4 gain -15 mcompand '
                    +'"0.005,0.1 -47,-40,-34,-34,-17,-33" 100 '
                    +'"0.003,0.05 -47,-40,-34,-34,-17,-33" 400 '
                    +'"0.000625,0.0125 -47,-40,-34,-34,-15,-33" 1600 '
                    +'"0.0001,0.025 -47,-40,-34,-34,-31,-31,-0,-30" 6400 '
                    +'"0,0.025 -38,-31,-28,-28,-0,-25" '
                    # The "sinc -n 255 -b 16 -17500 is a replacement in the SoX man page for a now lost original "filter" effect.
                    +'gain 12 highpass 22 highpass 22 sinc -n 255 -b 16 -17500 '
                    +'gain 9'
                    # This next one makes the difference between FM radio sound and broadcast signal condition simulation according to the SoX man page.
                    +(' lowpass -1 17801' if p=="fmr" else '')
                )
            # sil0<n> and sil<f>
            p = re.sub(r'^sil(0\d+)$', r'silence 1 0 .\1% -1 0 .\1%', p)
            p = re.sub(r'^sil([\d.]+)$', r'silence 1 0 \1% -1 0 \1%', p)
            # nv, no-vocal, number is dB at and above which to cut them out.
            # This effect also trims out the resulting silence and any other very quiet parts.
            # The first version allows for an arbitrary band to be removed preferentially.
            # The second version allows for a 400h-wide band to be removed preferentially.
            sil = "silence 1 0 .001% -1 0 .001%"
            p = re.sub(r'^nv(\d+)@([\d.]+)/([\d.]+)$', r'vol .01 equalizer \2 \3h 55 compand .0001,.05 6:-\1.0001,-\1,-inf,0,-inf 0 0 .0015 '+sil+r' -1 .022 .001% equalizer \2 \3h -55 vol 100', p)
            p = re.sub(r'^nv(\d+)@([\d.]+)$', r'vol .01 equalizer \2 400h 55 compand .0001,.05 6:-\1.0001,-\1,-inf,0,-inf 0 0 .0015 '+sil+r' -1 .022 .001% equalizer \2 400h -55 vol 100', p)
            p = re.sub(r'^nv(\d+)$', r'compand .0001,.05 6:-\1.0001,-\1,-inf,0,-inf 0 0 .0015 '+sil, p)
            # ov, only-vocal, number is dB at and below which to cut out other noise.
            # This effect also trims out the resulting silence.
            # The first version allows for an arbitrary band to be preserved preferentially.
            # The second version allows for a 400h-wide band to be preserved preferentially.
            p = re.sub(r'^ov(\d+)@([\d.]+)/([\d.]+)$', r'vol .01 equalizer \2 \3h 55 compand .01,.1 -\1.01,-inf,-\1,-\1 0 0 .012 silence -l 1 .022 .001% -1 .7 .001% equalizer \2 \3h -55 vol 100', p)
            p = re.sub(r'^ov(\d+)@([\d.]+)$', r'vol .01 equalizer \2 400h 55 compand .01,.1 -\1.01,-inf,-\1,-\1 0 0 .012 silence -l 1 .022 .001% -1 .7 .001% equalizer \2 400h -55 vol 100', p)
            p = re.sub(r'^ov(\d+)$', r'compand .01,.1 -\1.01,-inf,-\1,-\1 0 0 .012 silence -l 1 .022 .01% -1 .7 .01%', p)
            # dtmf<string>
            p = re.sub(r'(?i)dtmf([0-9#*abcd/.]+)', self._dtmf, p)
            parts[i] = p
            # nr<start>,<duration>[,<factor>]: Noise reduction with on-the-fly profile.
            # Without factor or with a factor of 1[.0], noisered uses 0.0. If factor is less than 1, noisered uses that.
            # If factor is above 1, noisered uses 0.0 and a vol <factor> is prepended to the noisered effect.
            match = re.match(r'^nr([\d.:=]+),([\d.:=]+)(.*)$', p)
            if match:
                profstart = match.groups()[0]
                proflen = match.groups()[1]
                rest = match.groups()[2]
                fac = 0.0
                if rest: fac = float(rest[1:])
                vol = 1.0
                if fac >= 1.0:
                    vol = fac
                    fac = 0.0
                p = f"noisered - {fac}"
                if vol != 1.0: p = f"vol {vol} {p}"
                # Now for the noiseprof command.
                prof = f"trim {profstart} {proflen} {' '.join(parts[:i])} noiseprof -"
                self.profEffects = prof
            parts[i] = p
        v = " ".join(parts)
        self.expanded = v

    @classmethod
    def _dtmf(cls, m):
        """Return a SoX effect chain that would produce the string of DTMF digits given.
        Digits 1-9, 0, and A-D are supported.
        Each produced tone is about 200 ms long excluding tiny fade-in and fade-out times to prevent clicks.
        There is a 100 ms pause between digits, again excluding start and end fade times.
        The fade time at each end of each tone is 20 ms.
        There is also one second of silence after the end, produced by a separate effect chain separated from the main one by a colon.
        If this is not desirable, something like result.split(":", 1)[0].rstrip() will remove it.
        These four defaults can be changed by trailing specs separated by slashes in the given order; e.g.,
        /300/200/30/5, or //100 to only make tones closer together.
        """
        rows = (697, 770, 852, 941)
        cols = (1209, 1336, 1477, 1633)
        order = "123a456b789c*0#d"
        parms = m.group(1).lower().split("/")
        seq = parms.pop(0)
        # This is the duration of the full-volume portion of a digit (excluding the fades at both ends).
        # This is long by modern standards but safer across fuzzy connections, such as the cell network.
        dur = cls._dtmfDefault(parms, 0.2)
        # This is the inter-digit space. Due to fading at both ends of each digit, it may seem longer to the ear than is shown here.
        spacing = cls._dtmfDefault(parms, 0.1)
        # This is the length of a hyperbolic fade applied at both start and end of each digit.
        # The actual length of a dialed digit is the above duration plus two times this fade length.
        fade = cls._dtmfDefault(parms, 0.02)
        # This is the duration of a final silence after all digits.
        end = cls._dtmfDefault(parms, 1)
        results = []
        for ch in seq:
            idx = order.index(ch)
            row,col = rows[idx//4], cols[idx%4]
            fullDur = str( float(dur) + 2*float(fade))
            # vol .9 below prevents clipping while maintaining a high output volume.
            results.append("synth {0} sine {1} sine {2} channels 1 vol .9 fade {3} {4} {3} : trim 0:0 0:{5}".format(
                fullDur, row, col,
                fade, dur, spacing
            ))
        buf = " : ".join(results)
        # This prevents a ValueError caused by SoX exiting too fast on a single dialed digit.
        if buf: buf += f" : trim 0 {end}"
        return buf

    @classmethod
    def _dtmfDefault(cls, parms, dfl):
        val = dfl
        if not parms: return val
        parm = parms.pop(0)
        if parm == '': return val
        val = f"{float(parm) / 1000:f}"
        return val

    @staticmethod
    def supported(path="sox"):
        """Return the supported effects for the default or given SoX or sox_ng program path.
        This method calls the program to get this information.
        "*" (deprecated) and "+" (experimental) marks are removed from returned effects.
        "#" (lib-only) effects are not included in the returned effects.
        Effects are returned as a list, lower-case as SoX returns it.
        """
        p = str(path)
        if not (":" in p or "/" in p or "\\" in p):
            p = shutil.which(p)
            if not p and os.path.splitext(p)[1].lower() != ".exe":
                p = shutil.which(f"{p}.exe")
        if not p:
            raise ValueError(f"{path} not found")
        proc = run_command(p, "")
        # Find EFFECTS line and its end, and grab what's inbetween.
        sfx = proc.stdout.split("EFFECTS:", 1)[1].lstrip().split("\n", 1)[0]
        # Remove deprecated and experimental markers.
        sfx = sfx.replace("*", "").replace("+", "")
        # Remove effects marked with "#" because they're lib-only; no command can use them.
        sfx = [s for s in sfx if not s.endswith("#")]
        return sfx

def windows_environ(use_cmd=True):
    """Get the Windows environment under WSL.
    Uses cmd.exe by default because it is 5-6 times faster than using the wslvar utility at this writing (July, 2025).
    cmd.exe also should work under Cygwin whereas wslvar would not.
    (Under Cygwin though, os.environ should be good enough.)
    Raises an IOError if cmd.exe is not found.
    Note that the results are similar but not exactly identical to wslvar -S.
    If use_cmd is False, uses wslvar -S and raises an IOError if wslvar is not found.
    """
    if use_cmd:
        cmd = shutil.which("cmd.exe")
        if not cmd:
            raise IOError("cmd.exe not found!")
        # stderr hidden because of the three-line "UNC paths are not supported" complaint.
        proc = run([cmd, "/c", "set"], stdin=DEVNULL, stdout=PIPE, stderr=DEVNULL, text=True)
        lines = proc.stdout.splitlines()
        sep = "="
    else:
        wslvar = shutil.which("wslvar")
        if not wslvar:
            raise IOError("wslvar utility not found; run 'apt install wslu' or similar?")
        proc = run([wslvar, "-S"], stdin=DEVNULL, stdout=PIPE, text=True)
        lines = proc.stdout.splitlines()
        # Pop off column headings.
        lines.pop(0)
        while not lines[0].lstrip()[0].isalpha():
            lines.pop(0)
        sep = None
    env = {}
    for line in lines:
        line = line.strip()
        k,v = line.split(sep, 1)
        env[k] = v
    return env

def universal_basename(path):
    """Return the basename of the given path regardless of whether this, or the path's origin, is Windows or Posix.
    """
    path = path.replace("/", os.sep).replace("\\", os.sep)
    return os.path.basename(path)

def run_command(cmd, check_flags="", env=None):
    """Helper to run commands and return results or handle errors.
    cmd can be a list, or an str which is here run through shlex.split().
    The shell argument to run() will always be False here.
    check_flags (default none):
    r: Abort app on non-zero returncode.
    e: Abort app on non-empty stderr.
    o: Abort app on non-empty stdout.
    Pass env for special environment requirements.
    On success, returns the CompletedProcess object after calling strip() on stdout and stderr members.
    Any abort is done by calling sys.exit() with proc.returncode or -1 if that's 0.
    Before exiting, the return code and any stdout and stderr text are printed.
    Also exits with an error if the command can't be found.
    """
    if isinstance(cmd, str):
        cmd = shlex.split(cmd)
    try:
        proc = run(cmd, shell=False, stdin=DEVNULL, stdout=PIPE, stderr=PIPE, text=True, check=False, env=env)
        proc.stdout = proc.stdout.strip()
        proc.stderr = proc.stderr.strip()
        if (
        ("r" in check_flags and proc.returncode != 0)
        or ("e" in check_flags and proc.stderr)
        or ("o" in check_flags and proc.stdout)
        ):
            print(f"Error: {shlex.join(cmd)} failed (check_flags {check_flags}):")
            print(f"Return code {proc.returncode}")
            if proc.stdout:
                print(f"stdout:\n{proc.stdout}")
            if proc.stderr:
                print(f"stderr:\n{proc.stderr}")
            sys.exit(proc.returncode or -1)
        return proc
    except FileNotFoundError:
        print("Error: '{cmd[0]}' command not found. Ensure that it is installed and in your PATH.")
        sys.exit(1)

def remote_path(args, ssh_args=None):
    """Translates a "system reference" first arg to a remote-redirection command prefix list, if appropriate.
    If the first arg in args is a system reference, pop it and return the appropriate ssh command.
    If not, make no changes and return the null string.
    (Do not pass sys.argv directly because index 0 fetches the command path, not the first parameter.)
    System references are recognized by containing an at sign (@).
    (In other words, prepend "@" if it doesn't already contain one; otherwise this is optional.)
    System reference formats supported, and how they translate:
    @sys: ssh sys
    userid@sys: ssh userid@sys
    spec1,spec2,...,specN: ssh -J spec1,spec2... specN
        Connects to specN by first going through spec1, then spec2, ... as proxies
        The Nth system authenticates against your host.
    spec1+spec2+...+specN: ssh spec1 ssh spec2... ssh specN
        Connects to specN by tunneling I/O through spec1, then spec2...
        The Nth system authenticates against the previous system in the chain.
    s1,s2,s3+s4,s5: ssh -J s1,s2 s3 ssh -J s4 s5
    Syntax origins:
    * The comma (,) is used by ssh -J for multiple proxy specification.
    * The plus (+) mnemonically means "additional I/O detour."
    If ssh_args is given, these args are added to each ssh command in the returned result.
    """
    if not args or "@" not in args[0]:
        return ""
    arg = args[0]
    if arg.startswith("@"):
        arg = arg[1:]
    cmd = []
    parts = arg.split("+")
    while parts:
        part = parts.pop(0)
        cmd.append("ssh")
        if ssh_args:
            cmd.extend(ssh_args)
        if "," in part:
            Jspec,part = part.rsplit(",", 1)
            cmd.extend(["-J", Jspec])
        cmd.append(part)
    args.pop(0)
    return cmd

# This helps debug platform_string() on new systems.
if __name__ == "__main__":
    print(platform_string("e"))
