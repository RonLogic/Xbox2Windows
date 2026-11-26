"""Verify the fixed slot-57 runtime path with the source-built mesh pair and CPU rasterizer."""

import argparse
import os
from pathlib import Path
import re
import subprocess
import tempfile

import startup_mesh57_skeleton_check as fixture


def run_case(directory, consumer, adapter, label, expected, forwarded):
    output = directory / "pixels.bin"
    output.unlink(missing_ok=True)
    env = dict(os.environ, X2W_TEST_MESH57_EXCHANGE=str(directory))
    run = subprocess.run([consumer, adapter, "mesh57-runtime"], env=env, capture_output=True, text=True)
    if run.returncode == 77:
        print(run.stdout, end="")
        return 77
    assert not run.returncode, f"{label}: runtime fixture exited {run.returncode}\n{run.stdout}\n{run.stderr}"
    actual = output.read_bytes()
    assert len(actual) == len(expected), f"{label}: got {len(actual)} bytes, expected {len(expected)}"
    census = re.search(r"^x2w-render indirect: forwarded: ([^;]+);", run.stdout, re.MULTILINE)
    assert census, f"{label}: missing indirect census\n{run.stdout}"
    assert census[1] == ("dispatchmesh 1" if forwarded else "0"), (
        f"{label}: expected {forwarded} forwarded mesh calls, observed {census[1]!r}"
    )
    for index in range(0, len(expected), 4):
        if actual[index:index + 4] != expected[index:index + 4]:
            y, x = divmod(index // 4, fixture.SIDE)
            raise AssertionError(
                f"{label}: pixel ({x}, {y}) expected {expected[index:index + 4].hex()}, "
                f"got {actual[index:index + 4].hex()}"
            )
    print(f"{label}: {len(expected) // 4} pixels matched, {forwarded} forwarded mesh calls, argument guards intact")
    return 0


def check(directory, consumer, adapter):
    status, published = fixture.publish_request(directory, consumer, adapter)
    if status:
        return status
    cache, key, _ = published
    result = fixture.G.translate_request(str(cache), key)
    assert result == "ok", result
    points, colors, indices = fixture.geometry()
    words = [a | b << 10 | c << 20 for a, b, c in indices]
    (directory / "geometry.bin").write_bytes(fixture.buffer(points, colors, words))
    clear = bytes((0, 0, 0, 255)) * (fixture.SIDE * fixture.SIDE)
    pixels = b"".join(bytes(pixel) for pixel in fixture.rasterise(points, colors, indices, 12, 4))
    assert pixels != clear

    for stage in ("ms", "ps"):
        artifact = cache / f"{key:016x}.{stage}57.cso"
        bytecode = artifact.read_bytes()
        try:
            artifact.unlink()
            status = run_case(directory, consumer, adapter, f"missing {stage}", clear, 0)
            if status:
                return status
            artifact.write_bytes(bytecode[:-1])
            status = run_case(directory, consumer, adapter, f"truncated {stage}", clear, 0)
            if status:
                return status
        finally:
            artifact.write_bytes(bytecode)
    return run_case(directory, consumer, adapter, "compiled pair", pixels, 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("consumer", type=Path)
    parser.add_argument("dxc")
    parser.add_argument("adapter", choices=("warp", "hardware"))
    args = parser.parse_args()
    os.environ["X2W_DXC"] = args.dxc
    os.environ.pop("X2W_FXC", None)
    with tempfile.TemporaryDirectory(prefix="mesh57-live-") as work:
        return check(Path(work), args.consumer.resolve(), args.adapter)


if __name__ == "__main__":
    raise SystemExit(main())
