"""Utilities for console apps to prevent them from closing before the user has a chance to read the last
printed message.

Author: Doug Lee
"""

import os, sys
builtinInput = input

def input(prompt):
    """input() replacement that adds a few features:
        Sends the prompt to both stdout and stderr if stdout is not a tty.
        Adds the user's response to stdout if stdout is not a tty, on the assumption that stdout is a log of sorts.
        Catches EOFError and translates it to an exit.
    """
    if not os.isatty(1):
        print(prompt, file=sys.stderr, end="")
        print(prompt, file=sys.stdout, end="")
        prompt = ""
    try: ans = builtinInput(prompt)
    except EOFError: sys.exit(0)
    finally:
        if not os.isatty(1): print(ans)
    return ans

def pauseAndExit(code=0):
    """sys.exit() replacement that pauses for Enter if stdout is a tty.
    """
    if not isinstance(code, int):
        print(code)
        code = 1
    if os.isatty(1):
        input("Press Enter to exit. ")
    sys.exit(code)

import __main__
def usageExit(code=0):
    """Prints usage (__doc__) and exits with an optional exit code via pauseAndExit().
    """
    print(__main__.__doc__)
    pauseAndExit()

def confirm(prompt):
    """Get permission for an action with a y/n prompt.
    Returns True if "y" is typed and False if "n" is typed.
    Repeats request until one or the other is provided.
    KeyboardInterrupt signals equate to "n"
    """
    if not prompt.endswith(" "): prompt += " "
    l = ""
    while not l:
        try: l = input(prompt)
        except KeyboardInterrupt: l = "n"
        l = l.strip()
        l = l.lower()
        if l in ["n", "no"]: return False
        elif l in ["y", "yes"]: return True
        print("Please enter y or n.")
        l = ""

