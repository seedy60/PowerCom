"""Ban request manager module for TTCom.
This module converts user address ban requests into the ban patterns to apply to the server.

Copyright (C) 2024-2026 Doug Lee

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

import re
from mplib.mycmd import CommandError
import geolocator

class BanRequest:
    """Convert a user-provided ban request pattern to the pattern to send to the server.
    This class only handles address bans, not username bans.
    Members:
        pattern: What to send to the server to create the ban.
        type: Printable type of the pattern; e.g., address, pattern, subnet.
        msgs: Empty or a list of messages to print about the ban request.
    msgs is used to handle special cases like subnet bans that may not work.
    """
    def __init__(self, parent, addr):
        self.msgs = []
        self.parent = parent
        # Remove the IPv6 socket prefix from IPv4 addresses when present.
        if addr.lower().startswith("::ffff:"):
            addr = addr[7:]
        self.pattern = ""
        self.type = ""
        if geolocator.is_valid_address(addr, formatOnly=True):
            # Either IPv4 or IPv6 raw addresses go here.
            # IPV4 addresses with the ::ffff: prefix also go here.
            self.type = "address"
            self.pattern = addr
            if geolocator.is_IPv6_format(addr):
                return
            # An IPv4 address.
            # The next bit makes IPv4 addresses more flexible across socket types.
            self.type = "pattern"
            self.pattern = "(::ffff:)?" +addr
            return
        # Not an address; try a subnet specification.
        if self._handleSubnets(addr):
            return
        # Unrecognized format.
        self.pattern = ""
        self.type = ""

    def _handleSubnets(self, addr):
        """Handle IPv4 subnets like 192.168.0.0/16 and 192.168.1.0/24.
        Also handle subnets like 192.168.25 or 192.168 where the address is only partial.
        Also handle user subnet specifications like bob/16 or june/24.
        Warning: Currently only accepts /16 and /24 suffixes.
        Matching specifications become regular expression patterns.
        Returns True if no further probing of addr should be done.
        Raises a CommandError if a user specification matches a user but there is no IP address available.
        """
        if "/" not in addr:
            if "." not in addr: return False
            parts = addr.split(".")
            # All parts must be numeric.
            if [part for part in parts if not part.isdigit()]: return False
            if len(parts) == 2:
                addr += ".0.0/16"
            elif len(parts) == 3:
                addr += ".0/24"
            else:
                return False
        lhs,rhs = addr.rsplit("/", 1)
        if not rhs.isdigit(): return False
        rhsi = int(rhs)
        if rhsi != 16 and rhsi != 24: return False
        suffix = r'\..*'
        if rhsi == 16: suffix *= 2
        if self._handleSubnetHelper(lhs, suffix):
            return True
        # Still could be a user/size specification.
        user = self.parent.userMatch(lhs)
        if not user.ipaddr:
            raise CommandError(f"No IP address available for user {lhs}")
        lhs = user.ipaddr
        if self._handleSubnetHelper(lhs, suffix):
            self.type = f"user {addr} {self.type}"
            return True
        return False

    def _handleSubnetHelper(self, lhs, suffix):
        if lhs.lower().startswith("::ffff:"):
            lhs = lhs[7:]
        if geolocator.is_IPv4_format(lhs):
            # This is a raw IPV4 subnet specification.
            lhs = lhs.rsplit(".", 1)[0]
            if len(suffix) > 4:
                lhs = lhs.rsplit(".", 1)[0]
            lhs += suffix
            self.pattern = "(::ffff:)?" + lhs
            self.type = "subnet pattern"
            return True
        return False

