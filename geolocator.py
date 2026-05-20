"""Implementation for the geolocate command of TTCom.
Author: Ivan Soto, September, 2023
Updated 2023-2026 by Doug Lee as part of TTCom.

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

import json
import re
import urllib.request, urllib.parse
import sys
from socket import gethostbyname, gaierror

# Regular expressions to test validity of IP addresses.
ipv4_pattern = re.compile(r'^(\d{1,3}\.){3}\d{1,3}$')
# This is a bit simplistic but should be adequate for this application.
# Note that ::0, a valid local address, is called valid here despite it making no sense to look it up.
# This is by design because it really is a valid IPV6 address. [DGL, 2023-09-19]
ipv6_pattern = re.compile(r'^[0-9a-fA-F:]+:[0-9a-fA-F:]*[0-9a-fA-F]$')

def is_IPv4_format(addr):
    """Return True if addr is in valid IPV4 address format.
    This includes the ones prefixed with ::ffff: for IPv6 sockets.
    """
    if addr.lower().startswith("::ffff:"):
        addr = addr[7:]
    return ipv4_pattern.match(addr)

def is_IPv6_format(addr):
    """Return True if addr is in valid IPV6 address format.
    """
    return ipv6_pattern.match(addr)

def is_valid_address(addr, formatOnly=False):
    """Check if IP address or domain name is valid."""
    if not addr:
        return False
    try:
        if formatOnly:
            # Skip to second test.
            raise gaierror
        # Try resolving the address to an IP; if successful, it's valid
        gethostbyname(addr)
        return True
    except gaierror:
        # Check if it's a valid IP address (IPv4 or IPv6)
        if addr.lower().startswith("::ffff:"):
            addr = addr[7:]
        if ipv4_pattern.match(addr) or ipv6_pattern.match(addr):
            return True
        return False

class GeolocatorException(Exception):
    pass

class Geolocator:
    """
    geolocator
    Get geolocation information using ipinfo.com.
    A few years back I wrote something like this for BGT, and now decided to port this to python. It is very simple to use, no API keys required, just create an instance of the class and you're good to go!

    Usage:
    geolocator = Geolocator()
    ip_address = "google.com"  # Replace with the IP address or domain name you want to geolocate
    try:
        if geolocator.geolocate(ip_address):
            summary = geolocator.get_geolocation_summary()
            print(summary)
        else:
            print("Geolocation failed.")
    except GeolocatorException as e:
        print(f"Geolocation error: {e}")
    """

    def __init__(self):
        self.inf = {}
        self.web_url = "http://ip-api.com/json"
        self.raw_data = ""
        self.requested_data = False

    def geolocate(self, addr):
        try:
            resp = self.url_get(f"{self.web_url}/{addr}")
            if not resp:
                return False

            self.raw_data = resp
            self.inf = json.loads(resp)  # Use json.loads to parse JSON
            if "status" not in self.inf or self.inf["status"] != "success":
                return False
            else:
                self.requested_data = True
                return True
        except Exception as e:
            raise GeolocatorException(f"An error occurred during geolocation: {e}")

    summary_fmt = [
        # Display name, main field name, extra field name.
        # Extras occur for things like region.
        ["City", "city", ""],
        ["Region", "regionName", "region"],
        ["Zipcode", "zip", ""],
        ["Country", "country", "countryCode"],
        ["Timezone", "timezone", ""],
        ["ISP", "isp", ""],
    ]
    def get_geolocation_summary(self, exclude_any=None, include_header=True):
        if exclude_any is None: exclude_any = []
        ds = []
        if not self.requested_data:
            return ""
        inf = self.inf
        if include_header:
            ds.append(f'Information for IP address: {inf.get("query", "")}')
        for dispname,fldname,extname in self.summary_fmt:
            if fldname in exclude_any or (extname and extname in exclude_any):
                continue
            fldval = inf.get(fldname, "")
            extval = inf.get(extname, "") if extname else ""
            if not fldval:
                # Not sure this ever happens, but this lets abbreviations replace missing full names.
                fldval = extval
                extval = ""
            if not fldval: continue
            if extval:
                ds.append(f"{dispname}: {fldval} ({extval})")
            else:
                ds.append(f"{dispname}: {fldval}")
        res = "\n".join(ds)
        return res

    def get_all(self):
        ds = []
        inf = self.inf
        for k,v in inf.items():
            ds.append(f"{k}: {v}")
        res = "\n".join(ds)
        return res

    def reset(self):
        self.requested_data = False
        self.raw_data = ""
        self.inf.clear()

    def url_get(self, url):
        try:
            with urllib.request.urlopen(url) as stream: response = stream.read().decode("UTF-8")
            return response
        except IOError as e:
            raise GeolocatorException(f"An error occurred during the HTTP request: {e}")

def get_general_geolocation(address, verbose=False):
    geolocator = Geolocator()
    # Avoid IPV6 prefix for an IPV4 address because it can trip up at least MacOS
    # when not configured for IPV6. [DGL, 2023-12-03]
    if address.lower().startswith("::ffff:"):
        address = address[7:]
    try:
        if not is_valid_address(address):
            raise GeolocatorException("Invalid address format: "+address)
        if geolocator.geolocate(address):
            summary = geolocator.get_all() if verbose else geolocator.get_geolocation_summary()
            return summary
        else:
            if verbose and geolocator.raw_data:
                return geolocator.get_all()
            else:
                return "Geolocation failed."
    except GeolocatorException as e:
        return f"Geolocation error: {e}"
