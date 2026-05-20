"""Sound player with queue support.

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

import os, shlex
from time import sleep
from subprocess import call, PIPE
from shutil import which
import threading
from queue import SimpleQueue, Empty

class FileEntry:
    """A file queue entry.
    """
    def __init__(self, fname):
        self.cargo = fname

class EffectEntry:
    """An effect queue entry.
    """
    def __init__(self, effect):
        if isinstance(effect, str):
            effect = shlex.split(effect)
        self.cargo = effect

class Player:
    """A queue for playing files and/or effects via SoX.
    Members (also __init__ parameters):
        delay (default 0.3 sec): How long to wait from first entry until processing begins.
        timeout (default 0.3 sec): How long to pend for further additions while new ones are arriving.
        maxsize (default 50): Largest queue may grow before processing is forced to begin.
    """
    def __init__(self, delay=0.3, timeout=0.3, maxsize=50):
        self.delay = delay
        self.timeout = timeout
        self.maxsize = maxsize
        self.queue = SimpleQueue()
        self.held = None
        self.th = threading.Thread(target=self.consumer)
        # Let the play queue die quietly on program exit.
        self.th.daemon = True
        self.th.start()

    def consumer(self):
        """The consumer for the queue. Runs in its own thread.
        """
        playPath = which("play")
        while (True):
            lst = []
            # Wait for an entry.
            if self.held is None:
                lst.append(self.queue.get())
            else:
                lst.append(self.held)
                self.held = None
            firstType = type(lst[0])
            # Pause if requested so further entries can appear if this is a blast of them.
            if self.delay > 0.0: sleep(self.delay)
            # Collect as many as are now available after that first one.
            # A change in entry type stops the retrieval.
            while True:
                try:
                    if self.maxsize > 0 and self.queue.qsize() >= self.maxsize:
                        break
                    if self.timeout <= 0.0:
                        entry = self.queue.get(block=False)
                    else:
                        entry = self.queue.get(block=True, timeout=self.timeout)
                    if type(entry) == firstType:
                        lst.append(entry)
                    else:
                        self.held = entry
                        break
                except Empty: break
            # lst is now a uniform list of one entry type, length 1 or more.
            # Make sure we have a player app to use.
            if not playPath or not os.path.exists(playPath):
                playPath = which("play")
            if not playPath:
                # Skip these but keep waiting for entries in case someone fixes the missing app issue.
                continue
            if firstType is FileEntry:
                cmd = ["play", "-q", "-v0.5"]
                cmd.extend([f.cargo for f in lst])
            elif firstType is EffectEntry:
                cmd = ["play", "-q", "-v0.5", "-n"]
                while lst:
                    fx = lst.pop(0)
                    cmd.extend(fx.cargo)
                    if lst: cmd.extend(":")
            else:
                print(f"Player Warning: Discarded {len(lst)} entries of unknown type {str(firstType)}")
                continue
            try: call(cmd, stdin=PIPE, stdout=PIPE, text=True)
            except Exception as e: print(str(e))

    def sendFile(self, fname):
        """Add a file to the play queue.
        """
        self.queue.put(FileEntry(fname))

    def sendEffect(self, effect):
        """Add an effect to the play queue.
        """
        self.queue.put(EffectEntry(effect))

