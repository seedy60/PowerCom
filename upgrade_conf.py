"""upgrade - Upgrade configuration file structure to the one starting at TTCom revisions after 1473.
This module acts immediately on import and provides no other function.
This module only acts if the active app ini file does not already exist.

Actions:
* Leave all blank lines and comments (; and # types) in ttcom.conf, regardless of where they are found.
* Leave [server ...] and [include ...] sections in ttcom.conf.
* Move all else to the active app ini file, creating it in the process, in the same folder with ttcom.conf.

Users are advised to clean out any now irrelevant leftover comments in ttcom.conf after this upgrade.

Author: Doug Lee
"""

import os, sys, re, time
from shutil import copyfile

def isIniBlockHeader(line):
    """Return True if this is a section header for something we're moving to the .ini file.
    Only server and include lines stay in the .conf file.
    """
    ll = line.strip().lower()
    if not ll.startswith("[") or "]" not in ll:
        return False
    ll = ll[1:].split("]", 1)[0]
    words = ll.split()
    if len(words) < 2:
        return True
    kw = words.pop(0)
    if kw not in ["server", "include"]:
        return True
    return False

def upgrade_conf():
    """Migrate parts of ttcom.conf to the active app ini file.
    All sections are migrated except server and include sections.
    Comments and blank lines are not migrated and will remain in the original file.
    Warning: This is true even of comments inside migrated sections!
    Such comments are however not expected.
    """
    basePath = "."
    confPath = os.path.join(basePath, "ttcom.conf")
    appName = os.path.splitext(os.path.basename(sys.argv[0]))[0].lower()
    iniName = "powercom.ini" if appName == "powercom" else "ttcom.ini"
    iniPath = os.path.join(basePath, iniName)
    legacyIniPath = os.path.join(basePath, "ttcom.ini")
    if os.path.exists(iniPath): return
    if iniName == "powercom.ini" and os.path.exists(legacyIniPath):
        copyfile(legacyIniPath, iniPath)
        return
    if not os.path.exists(confPath): return
    print("Upgrading configuration ...")
    with open(confPath) as f: lines = f.readlines()
    confLines = []
    iniLines = []
    iniBlock = False
    for line in lines:
        if not line or line.startswith("#") or line.startswith(";"):
            confLines.append(line)
        elif not iniBlock:
            if isIniBlockHeader(line):
                iniLines.append(line)
                iniBlock = True
            else:
                confLines.append(line)
        else:
            if line.lstrip().startswith("[") and "]" in line and not isIniBlockHeader(line):
                confLines.append(line)
                iniBlock = False
            else:
                iniLines.append(line)
    try:
        with open(iniPath, "w") as f:
            f.write("".join(iniLines))
    except Exception as e:
        print(f"Upgrade failed; {str(e)}")
        pauseAndExit(1)
    try:
        with open(confPath, "w") as f:
            f.write("".join(confLines))
    except Exception as e:
        print("Warning: Original configuration update failed; some material may remain but will be ignored")
        time.sleep(3.0)

upgrade_conf()
