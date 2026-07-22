# Vera Example invented runner fixture.
with open("invented-large-file.bin", "wb") as stream:
    stream.write(b"x" * (40 * 1024 * 1024))
