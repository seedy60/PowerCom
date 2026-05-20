"""Console-based progress reporter code
Copyright (c) 2019-2026 Doug Lee.

This code is covered by version 3 of the GNU General Public License.
This code comes with ABSOLUTELY NO WARRANTY.
This is free software, and you are welcome to redistribute it under
certain conditions. See the full license for details:
http://www.gnu.org/licenses/gpl-3.0.html
"""

import os, sys, time
from threading import Thread

class ProgressReporter:
    """Reporter of progress percentages for a long operation.
    """
    def __init__(self, desc, percentFunc, interval=5.0):
        """Create a process reporter.
        desc: The printed description of the process - e.g., "Uploading files." May be blank ("").
        percentFunc: The callable that returns the percent complete at call time, as a float between 0.0 and 1.0.
        interval: Float seconds between printed updates, default 5.0 seconds. Must be 0.2 seconds or greater.
        """
        if interval < 0.2: raise ValueError("ProgressReporter interval must be 0.2 seconds or greater")
        self.desc = desc
        self.percentFunc = percentFunc
        self.interval = interval
        self.running = True
        self.th = Thread(target=self._reporter, daemon=True)
        self.th.start()

    def __del__(self):
        self.stop()

    def stop(self):
        """Stop the progress reporting.
        """
        self.running = False
        self.th.join(timeout=1.0)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    def _reporter(self):
        """The reporter of progress. Runs in its own thread.
        """
        if self.desc:
            print(self.desc+" ... ", end="")
            sys.stdout.flush()
        started = False
        isatty = os.isatty(1)
        while self.running:
            # A partially busy loop just to keep the thread from hanging too long when the process completes.
            for tick in range(0, int(5*self.interval)):
                time.sleep(0.2)
                if not self.running: break
            if not self.running: break
            p = 100 * self.percentFunc()
            if not isinstance(p, int): p = round(p)
            if started and isatty:
                # Backspace over previously printed percentage.
                print("\b\b\b\b", end="")
            started = True
            if isatty:
                print(f"{p:3d}%", end="")
                sys.stdout.flush()
        if started and isatty:
            print("\b\b\b\b", end="")
        print("done")
        sys.stdout.flush()

