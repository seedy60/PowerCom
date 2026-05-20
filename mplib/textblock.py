"""TextBlock class for simplifying the building of formatted text blocks.
Adds an add method for conditional inclusion of named values.

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

class TextBlock:
    """String with helpers for constructing output text blocks.
    """
    def __init__(self, val=None, ignoreZeros=False):
        if not val: val = ""
        self._buf = val
        self.ignoreZeros = ignoreZeros

    def add(self, name, val, sameLine=False, ignoreZeros=None):
        """Helps construct text blocks.
        Name and val are a value and its name.
        sameLine indicates if this name/val pair goes on this or the next line.
        If name ends with \n, no space will be inserted before val.
        ignoreZeros determines whether str values amounting to 0 will be included.
        Examples: "0", "000", "0.00."
        If not passed or None, self.ignoreZeros is used.
        Note that for str values of val, leading and trailing spaces are ignored and do not print.
        None, int 0, float 0.00, the null string, and anything else where (not val) is True do not print.
        """
        if val is None: val = ""
        elif isinstance(val, str): val = val.strip()
        if ignoreZeros is None: ignoreZeros = self.ignoreZeros
        if ignoreZeros and isinstance(val, str):
            try:
                if "." in val: val = float(val)
                elif val.isdigit(): val = int(val)
            except ValueError: pass
            if not val: val = ""
        val = str(val).strip()
        if not val:
            # Make sure new lines are started when requested,
            # but avoid creating blank ones for missing values.
            if not self._buf.endswith("\n") and not sameLine:
                self._buf += "\n"
            return
        # We have a value to add.
        buf = ""
        if not self._buf: buf = ""
        elif sameLine and not self._buf.endswith("\n"): buf += ", "
        elif not self._buf.endswith("\n"): buf += "\n"
        if name.endswith("\n"):
            buf += f"{name}{val}"
        else:
            buf += f"{name} {val}"
        self._buf += buf

    def __iadd__(self, other):
        """Implements +=.
        """
        self._buf += other
        return TextBlock(self._buf, ignoreZeros=self.ignoreZeros)

    def __str__(self):
        return str(self._buf)

