#!/usr/bin/env python3
# ==================================================================
#  CrowPi Lessons - SETUP
#
#  Open this file in Thonny and click Run (or right-click >
#  Open With > Python 3). It downloads the newest installer from
#  github.com/palkipl/crowpi-lessons and opens it:
#  ADD or REMOVE CrowPi Lessons, and lock the games or not.
#
#  This file never goes out of date - it always gets the newest version.
# ==================================================================
import os
import subprocess
import sys
import tempfile
import urllib.request

INSTALLER = "https://raw.githubusercontent.com/palkipl/crowpi-lessons/main/install_crowpi.py"


def main():
    print("Downloading the CrowPi Lessons installer...")
    target = os.path.join(tempfile.mkdtemp(prefix="crowpi_setup_"), "install_crowpi.py")
    try:
        request = urllib.request.Request(INSTALLER, headers={"User-Agent": "CrowPi-Setup"})
        with urllib.request.urlopen(request, timeout=30) as response, open(target, "wb") as out:
            out.write(response.read())
    except Exception as problem:
        here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "install_crowpi.py")
        if not os.path.exists(here):
            print("Couldn't download the installer (%s)." % problem)
            print("Check the internet connection, or put install_crowpi.py next to this file.")
            return 1
        print("No internet - using the installer next to this file.")
        target = here
    print("Opening the installer window...")
    return subprocess.call([sys.executable, target] + sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
