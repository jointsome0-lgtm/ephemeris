# Vera Example invented runner fixture.
import subprocess
import sys
import time

# The descendant inherits the captured pipes. FINISHED therefore proves the
# namespace/process-group kill closed the whole tree, not only this parent.
subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
print("descendant-ready", flush=True)
time.sleep(300)
