# Vera Example invented runner fixture.
import os

chunk = b"invented-output-block\n" * 4096
while True:
    os.write(1, chunk)
