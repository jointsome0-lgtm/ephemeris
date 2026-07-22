# Vera Example invented runner fixture.
import subprocess
import sys
import time

# The descendant starts a separate session but inherits the captured pipes.
# FINISHED therefore proves the cgroup kill closed the whole tree, not only
# this parent's process group.
subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(300)"],
    start_new_session=True,
)
print("descendant-ready", flush=True)
time.sleep(300)
