"""Base class for bit flag sets (user rights, channel rights, etc.)., and classes of flags.
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

import re, shlex

class BitFlags(int):
    """Base class for all bit-flag classes.
    """
    # Flags: Sequence of tuples of (val,letter,name,desc), where val is the bit value, letter is a unique letter for it or "",
    # name is a one-word name, and desc is a # description. Subclasses must define this.
    # Letter and name fields may also be of the form val0/val1, where val0 represents 0 and val1 represents 1.
    # One value of these may be null (e.g., t/ or /p) but not both.
    # If any letter is "", byLetter() will raise an error.
    flags = ()
    # A default value assigned to bits when a dot is used in an alter() call.
    default = 0

    def __new__(cls, val=None):
        """Create a bitflag value with the given value, or the default value if none is given.
        """
        if val is None:
            val = cls.default
        else: val = int(val)
        self = int.__new__(cls, val)
        return self

    @classmethod
    def mask(cls):
        """The set of all bits listed in the val part of the flags tuple, as an int.
        """
        return sum([t[0] for t in cls.flags])

    @classmethod
    def bitCount(cls):
        """Return the number of flag bits for this class.
        Note that this may be less than the number of bits required for a value in this class.
        """
        return len(cls.flags)

    @classmethod
    def bitWidth(cls):
        """Return the number of bits required in any value of an instance of this class.
        This will be greater than bitCount() if bits are skipped in the mask.
        """
        mask = cls.mask()
        cnt = 0
        while mask:
            cnt += 1
            mask >>= 1
        return cnt

    def __invert__(self):
        """Invert against the mask rather than against all bits available.
        """
        return self.__class__(int.__invert__(self) & self.mask())

    def toString(self, style):
        """Return a string representing this object's flags.
        style is a string consisting of any combination of these:
        c or w or l: Character, word, or long-description versions of flags.
            c uses single characters, w words separated by spaces, and l long descriptions separated by comma and space.
        A or D: Optimize by comparing to the set of all flags, and for D, also default values.
            When closest to all, "all minus" and the differences will print.
            When closest to defaults for D, "default plus" or "default minus" and the differences will print.
        Caveats:
            D is not yet implemented and behaves just like A.
            Incoming bits beyond what the object expects may be handled incorrectly.
        """
        # Make a list of int flag bit values that appear in this int's value.
        # ToDo: This misses any bits in self that are not in self.flags.
        flags = [f for f in self.flags if self & f[0]]
        prefix = ""
        if "A" in style or "D" in style:
            if not len(flags): return "none"
            nflags = [f for f in self.flags if not (self & f[0])]
            if len(nflags) < len(flags):
                flags = nflags
                prefix = "all minus "
                if not len(flags): return "all"
        if "c" in style:
            return prefix + "".join([f[1] for f in flags])
        elif "w" in style:
            return prefix + " ".join([f[2] for f in flags])
        elif "l" in style:
            return prefix + ", ".join([f[3] for f in flags])
        else:
            raise ValueError(f"Unknown string style: {style}")

    @classmethod
    def comm(cls, v1, v2):
        """Return a tuple (v1only,v2only,both) of what changed between values v1 and v2.
        The returned values will be of the same class as v1 and v2, which should share the same class.
        This method's name and tuple element order are based on the comm(1) utility in most Unix variants.
        """
        a,b,c = 0,0,0
        mask = cls.mask()
        n = cls.bitWidth()
        bit = 1
        for i in range(1, n+1):
            if (v1 & bit):
                if (v2 & bit): c += bit
                else: a += bit
            else:
                if (v2 & bit): b += bit
            bit <<= 1
        return (cls(a), cls(b), cls(c))

    def alter(self, specs, style):
        """Set or alter flags by a specification consisting of flag names or partial names with modifiers.
        specs may be a list or a line that will be split at white space.
        Each element of spec is a prefix followed by a flag name or partial name.
        Prefixes: + set, - reset, ^ toggle (xor), . default. + is default if there is no prefix.
        Defaulting with . means use the default value for this bit.
        style determines which column of flags is used to match: 1 letter, 2 word, 3 description.
        Flag name: First exact match, then prefix match, then contain match are tried.
        Case is ignored unless style is 1.
        If style is 1, letters can be concatenated into one element; e.g., +ab -cdE.
        Example spec string to set one bit, toggle another, clear another, and defult a fourth: +a -b ^c .d
        Returns the modified version of flags as an object of the same class.
        """
        if isinstance(specs, str): specs = shlex.split(specs)
        prefixes = "+-^."
        val = int(self)
        if style == 1:
            flags = dict([(f[style],f[0]) for f in self.flags])
        else:
            flags = dict([(f[style].lower(),f[0]) for f in self.flags])
        for i,spec in enumerate(specs):
            # strip would be enough; but for speech dictation, we let things like "op only" and "no record"
            # condense into single words like "oponly" and "norecord."
            # This implies that the speaker dictated quotes around the spaces though.
            spec = re.sub(r'\s+', '', spec)
            if not spec: raise ValueError("Empty bit flag specification element")
            # "+" is the default prefix if none is given.
            if spec[0] not in prefixes: spec = "+" +spec
            prefix,spec = spec[0], spec[1:]
            if not spec:
                # A full initial value.
                if prefix == '.':
                    val = self.default
                elif prefix == '+':
                    val = self.mask()
                elif prefix == '-':
                    val = 0
                else: # '^'
                    # Flip all bits that we manage.
                    # Trivia: A spec of ". ^" results in the inverse of all defaults.
                    val ^= (val & self.mask())
                continue
            # Non-empty spec and we have a prefix to apply to it.
            targets = []
            if style == 1:
                # Case matters and letter strings represent multiple flags rather than just one.
                targets.extend(list(spec))
            else:
                targets = [spec.lower(),]
            for spec in targets:
                # Exact match (ignoreing case except for style 1), then prefix match, then containment.
                # This is here in case one flag name contains another; e.g., "end" and "amend."
                # Otherwise, the shorter flag could never be specified.
                matches = [s[1] for s in self.flags if s[1] == spec]
                if len(matches) > 1: raise ValueError("Identical flag names encountered")
                if not len(matches):
                    # Now a prefix match.
                    matches = [s[1] for s in self.flags if s[1].startswith(spec)]
                    if len(matches) > 1: raise ValueError(f"{spec} matches more than one flag")
                if not len(matches):
                    # Now a containment match.
                    matches = [s[1] for s in self.flags if spec in s[1]]
                    if len(matches) > 1: raise ValueError(f"{spec} matches more than one flag")
                if not len(matches): raise ValueError(f"No flag matches {spec}")
                if len(matches) > 1: raise ValueError(f"More than one flag matches {spec}")
                match = matches[0]
                bit = flags[match]
                pfx = prefix
                if prefix == '.':
                    # Use what the default mask would make equivalent.
                    if self.default & bit: pfx = '+'
                    else: pfx = '-'
                if pfx == '+':
                    val |= bit
                elif pfx == '-':
                    if val & bit: val -= bit
                else:  # '^'
                    val ^= bit
        return self.__class__(val)

    @property
    def byLetter(self):
        """Get or set flags by letter.
        """
        raise RuntimeError("Not implemented yet")

