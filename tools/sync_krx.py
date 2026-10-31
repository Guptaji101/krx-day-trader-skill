#!/usr/bin/env python3
"""Copy common/krx.py into every skill folder, so each skill installs on its own.

    python3 tools/sync_krx.py          # copy
    python3 tools/sync_krx.py --check  # exit 1 if any copy differs (the tests run this)
"""

import glob
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "common", "krx.py")


def skills():
    return sorted(d for d in glob.glob(os.path.join(ROOT, "krx-*")) if os.path.isdir(d))


def main(argv):
    src = open(SRC, "rb").read()
    bad = []
    for d in skills():
        dst = os.path.join(d, "krx.py")
        same = os.path.exists(dst) and open(dst, "rb").read() == src
        if "--check" in argv:
            if not same:
                bad.append(dst)
        elif not same:
            shutil.copyfile(SRC, dst)
            print("updated %s" % os.path.relpath(dst, ROOT))
    if bad:
        print("out of date: %s (run python3 tools/sync_krx.py)" % ", ".join(os.path.relpath(b, ROOT) for b in bad))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
