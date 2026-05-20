"""AttrDict, a dictionary that allows d.a and d["a"] to be the same.

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

class AttrDict(dict):
    """
    Dictionary where d.attr == d["attr"].
    Keys are case-insensitive as well.
    Actual attributes may exist but must begin with at least one underscore (_).
    c._a will fail if _a is not an attribute,
    but c.a will return None if a is not defined.
    """
    def __getattr__(self, fieldname):
        fn = fieldname.casefold()
        if fn in self:
            return self.__getitem__(fieldname)
        if fieldname.startswith("_"):
            raise AttributeError(fieldname)
        return None

    def __setattr__(self, fieldname, fieldval):
        # Fields don't begin with underscores, but internal attributes do.
        if fieldname.startswith("_"):
            dict.__setattr__(self, fieldname, fieldval)
        # Anything else sets a field.
        else:
            if fieldval is None:
                if fieldname in self:
                    self.__delitem__(fieldname)
            else:
                self.__setitem__(fieldname, fieldval)

    def __delattr__(self, fieldname):
        if fieldname.casefold() in self:
            return dict.__delitem__(self, fieldname.casefold())
        return dict.__delattr__(self, fieldname.casefold())

    def __getitem__(self, fieldname):
        return dict.__getitem__(self, fieldname.casefold())

    def __setitem__(self, fieldname, fieldval):
        return dict.__setitem__(self, fieldname.casefold(), fieldval)

    def has_key(self, k):
        return k.casefold() in self

