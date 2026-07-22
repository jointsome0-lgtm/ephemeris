# Vera Example invented runner fixture.
import json
import os
from pathlib import Path

REPO = Path("__EPHEMERIS_REPO__")
PRIVATE_SENTINEL = Path("__PRIVATE_SENTINEL__")
OTHER_BUNDLE = Path("__OTHER_BUNDLE__")
BUNDLE = Path("__CURRENT_BUNDLE__")
HOST_NETNS = int("__HOST_NETNS__")


def writable(path: Path) -> bool:
    probe = path / ".invented-runner-write-probe"
    try:
        probe.write_bytes(b"probe")
        probe.unlink()
        return True
    except OSError:
        return False


network_absent = os.stat("/proc/self/ns/net").st_ino != HOST_NETNS

scratch = Path.cwd()
home = Path.home()
module_cache = Path(os.environ["GOMODCACHE"])
gocache = Path(os.environ["GOCACHE"])
gocache.mkdir(parents=True, exist_ok=True)

payload = {
    "repo_absent": not REPO.exists(),
    "home_entries": sorted(entry.name for entry in home.iterdir()),
    "private_sentinel_absent": not PRIVATE_SENTINEL.exists(),
    "run_empty": not any(Path("/run").iterdir()),
    "other_bundle_absent": not OTHER_BUNDLE.exists(),
    "network_absent": network_absent,
    "bundle_readable": BUNDLE.is_dir(),
    "bundle_read_only": not writable(BUNDLE),
    "scratch_writable": writable(scratch),
    "module_cache_read_only": module_cache.exists() and not writable(module_cache),
    "gocache_writable": writable(gocache),
    "snapshot_mode": oct(Path(__file__).stat().st_mode & 0o777),
    "runner_env": sorted(os.environ),
}
print(json.dumps(payload, sort_keys=True))
