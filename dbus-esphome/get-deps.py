#!/usr/bin/env python3
"""
get-deps.py  –  Install Python dependencies for dbus-esphome on Venus OS.

Uses only Python stdlib (urllib, zipfile, json) because Venus OS ships a
stripped Python that cannot run pip.

Usage:  python3 get-deps.py <target-dir>
"""
import json
import os
import platform
import re
import sys
import zipfile
from urllib.request import urlopen

PYPI = "https://pypi.org/pypi/{}/json"

# Full dependency list for aioesphomeapi (transitive deps included).
# Update this list if aioesphomeapi adds or removes dependencies.
PACKAGES = [
    "aioesphomeapi",
    "aiohappyeyeballs",
    "async-interrupt",
    "protobuf",
    "zeroconf",
    "ifaddr",
    "chacha20poly1305-reuseable",
    "cryptography",
    "noiseprotocol",
    "tzdata",
    "tzlocal",
]


def glibc_minor():
    """Return the minor version of the system glibc (e.g. 31 for glibc 2.31)."""
    try:
        lib, ver = platform.libc_ver()
        if lib == "glibc" and ver:
            return int(ver.split(".")[1])
    except Exception:
        pass
    return 17  # conservative fallback


def plat_tags():
    """Return ordered platform compatibility tags for the running system.

    Generates all manylinux_2_X tags from the system glibc version down to 2.17
    so that wheels built against any supported glibc baseline are accepted.
    """
    arch_map = {
        "armv7l":  "armv7l",
        "aarch64": "aarch64",
        "x86_64":  "x86_64",
    }
    arch = arch_map.get(platform.machine())
    if not arch:
        return ["any"]

    minor = glibc_minor()
    tags = [f"linux_{arch}"]
    for v in range(minor, 16, -1):          # e.g. 31 → 30 → … → 17
        tags.append(f"manylinux_2_{v}_{arch}")
    tags.append(f"manylinux2014_{arch}")    # legacy alias for manylinux_2_17
    tags.append("any")
    return tags


def py_tags():
    v = sys.version_info
    return [f"cp{v.major}{v.minor}", f"cp{v.major}", f"py{v.major}{v.minor}", f"py{v.major}", "py3"]


def abi_tags():
    v = sys.version_info
    return [f"cp{v.major}{v.minor}", "abi3", "none"]


def tag_score(tag_str, candidates):
    """Score a dot-separated tag field against an ordered candidate list.
    Returns (len - index) for the first match, or -1 if no match."""
    for tag in tag_str.split("."):
        for i, c in enumerate(candidates):
            if tag == c:
                return len(candidates) - i
    return -1


def score_wheel(fname):
    """Return compatibility score for a wheel filename, or -1 if incompatible."""
    if not fname.endswith(".whl"):
        return -1
    parts = fname[:-4].rsplit("-", 4)
    if len(parts) != 5:
        return -1
    _, _, py, abi, plat = parts

    # abi3 wheels carry a *minimum* CPython version tag (e.g. cp39-abi3 means
    # CPython >= 3.9 with the stable ABI).  They are NOT an exact-version match,
    # so we handle them separately instead of running through py_tags().
    if abi == "abi3":
        m = re.match(r"cp3(\d+)$", py)
        if not m or sys.version_info < (3, int(m.group(1))):
            return -1
        ps = 1  # valid, but lower priority than an exact cp312 binary wheel
    else:
        ps = tag_score(py, py_tags())
        if ps < 0:
            return -1

    as_ = tag_score(abi, abi_tags())
    ls = tag_score(plat, plat_tags())
    if as_ < 0 or ls < 0:
        return -1
    return ps * 10000 + as_ * 100 + ls


def ver_key(v):
    return tuple(int(x) for x in re.findall(r"\d+", v))


def best_wheel_for(package):
    """Query PyPI and return (version, url, filename) for the best matching wheel."""
    with urlopen(PYPI.format(package), timeout=30) as r:
        data = json.loads(r.read().decode())

    # Newest stable versions first
    versions = sorted(
        (v for v in data["releases"] if not re.search(r"(a|b|rc|dev)\d", v)),
        key=ver_key,
        reverse=True,
    )

    for ver in versions:
        best = (-1, None, None)
        for f in data["releases"].get(ver, []):
            if f.get("yanked"):
                continue
            s = score_wheel(f["filename"])
            if s > best[0]:
                best = (s, f["url"], f["filename"])
        if best[0] >= 0:
            return ver, best[1], best[2]

    v = sys.version_info
    sys.exit(
        f"ERROR: No compatible wheel for '{package}' "
        f"(arch={platform.machine()}, python={v.major}.{v.minor})"
    )


def install(package, target):
    print(f"  {package}", end=" … ", flush=True)
    ver, url, fname = best_wheel_for(package)
    print(f"{ver}", flush=True)

    tmp = f"/tmp/{fname}"
    with urlopen(url, timeout=120) as r, open(tmp, "wb") as f:
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            f.write(chunk)

    os.makedirs(target, exist_ok=True)
    with zipfile.ZipFile(tmp) as z:
        z.extractall(target)
    os.unlink(tmp)


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "./vendor"
    v = sys.version_info
    print(f"Installing {len(PACKAGES)} packages to {target}/")
    print(f"  arch={platform.machine()}  python={v.major}.{v.minor}")
    for pkg in PACKAGES:
        install(pkg, target)
    print(f"\nDone.")


if __name__ == "__main__":
    main()
