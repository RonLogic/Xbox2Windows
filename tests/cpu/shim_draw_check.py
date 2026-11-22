"""Compare raster draws from the linked shim EXE, mapped image and AOT process.

The draw case renders the repository's 64x64 test triangle offscreen from the same
EXE entry that runs the startup shims and the compute dispatch. The provider
publishes the raw RGBA bytes after the startup output; this check compares them
byte for byte across runners and against the CPU pixel rule. The flip case mirrors
X in the vertex shader and must exit 20 with bytes that differ from the rule but
still agree across runners.

The two-frame cases check both import arguments, completed fence values, and
the independent green/red references. A pre-submit failure leaves only frame one.
"""
import os
from pathlib import Path
import subprocess
import sys

from shim_startup_check import EXPECTED, SHIM_IMPORTS, check


SIDE = 64
BYTES = SIDE * SIDE * 4


class FrameMismatch(AssertionError):
    pass


def covered(x, y):
    return x >= 8 and y >= 8 and x + y <= 63


def reference(red=False):
    out = bytearray()
    for y in range(SIDE):
        for x in range(SIDE):
            inside = covered(x, y)
            out += bytes((255 if inside and red else 0, 255 if inside and not red else 0, 0, 255))
    return bytes(out)


def texture_reference(*, updated=False, swap=False):
    colors = ((255, 0, 0, 255), (255, 0, 255, 255) if updated else (0, 255, 0, 255),
              (0, 0, 255, 255), (255, 255, 0, 255))
    out = bytearray()
    for y in range(SIDE):
        for x in range(SIDE):
            r, g, b, a = colors[2 * (y >= 24) + (x >= 24)] if covered(x, y) else (0, 0, 0, 255)
            out += bytes((b, g, r, a) if swap else (r, g, b, a))
    return bytes(out)


def compare_frames(actual, expected):
    check("frame byte count", len(actual), len(expected))
    if actual != expected:
        byte = next(i for i, (a, b) in enumerate(zip(actual, expected)) if a != b)
        frame, offset = divmod(byte, BYTES)
        y, x = divmod(offset // 4, SIDE)
        start = byte - offset % 4
        raise FrameMismatch(f"stage {frame + 1} pixel ({x}, {y}): "
                            f"expected {tuple(expected[start:start+4])}, actual {tuple(actual[start:start+4])}")


def verify(case, result, expected_pixels):
    check("startup output", result.stdout[:len(EXPECTED.output)], EXPECTED.output)
    pixels = result.stdout[len(EXPECTED.output):]
    if case == "bogus":
        check("unknown draw case exits", result.returncode, 21)
        check("unknown draw case publishes nothing", pixels, b"")
        return None
    if case in ("texture", "texture-stale"):
        check(f"{case} exit code", result.returncode, 42)
        first = texture_reference()
        expected = first + texture_reference(updated=True) + texture_reference(updated=True, swap=True)
        if case == "texture":
            compare_frames(pixels, expected)
        else:
            try:
                compare_frames(pixels, expected)
            except FrameMismatch as error:
                check("stale update diagnostic", str(error),
                      "stage 2 pixel (24, 8): expected (255, 0, 255, 255), actual (0, 255, 0, 255)")
                print(f"Stale update rejected: {error}")
            else:
                raise AssertionError("The previous texture pattern escaped detection")
            compare_frames(pixels, first * 2 + texture_reference(swap=True))
        return pixels
    if case in ("two", "stale", "fail-second"):
        frames, status = {
            "two": (expected_pixels + reference(red=True), 42),
            "stale": (expected_pixels * 2, 20),
            "fail-second": (expected_pixels, 24),
        }[case]
        check(f"{case} exit code", result.returncode, status)
        compare_frames(pixels, frames)
        return pixels
    check("draw publishes a full frame", len(pixels), BYTES)
    if case == "flip":
        check("mirrored draw exits", result.returncode, 20)
        if pixels == expected_pixels:
            raise AssertionError("mirrored draw matches the exact rule")
        return pixels
    check("exact draw exits", result.returncode, 42)
    check("exact draw matches the CPU rule", pixels, expected_pixels)
    return pixels


def compare(mapper, exe, lifted, companion, io, xmem, d3d12, *, texture_reuse=False):
    digest = reference()
    green = sum(1 for i in range(0, BYTES, 4) if digest[i + 1] == 255)
    print(f"draw frame: {SIDE}x{SIDE} RGBA ({BYTES} bytes); CPU rule holds {green} green pixels")
    map_command = [sys.executable, "-B", Path(__file__).with_name("shim_startup_check.py"), "--map",
                   mapper, exe, companion, io, xmem, d3d12, ""]
    markers = sorted(f"bound {dll}!{symbol}" for dll, symbols in SHIM_IMPORTS.items() for symbol in symbols)
    cases = ("texture", "texture-stale") if texture_reuse else ("draw", "flip", "bogus", "two", "stale", "fail-second")
    for case in cases:
        env = dict(os.environ, X2W_SHIM_DRAW_CASE="" if case == "bogus" else case,
                   X2W_SHIM_COMPUTE_CASE="")
        if case == "bogus":
            env["X2W_SHIM_DRAW_CASE"] = "no such case"
        outputs = {}
        trace = []
        if case in ("two", "stale", "fail-second"):
            second = {"two": 4, "stale": 5, "fail-second": 6}[case]
            trace = ["draw_frame mode=3", "draw fence completed 1", f"draw_frame mode={second}"]
            if case != "fail-second":
                trace.append("draw fence completed 2")
        elif texture_reuse:
            modes = (7, 8 if case == "texture" else 10, 9)
            trace = [line for i, mode in enumerate(modes, 1)
                     for line in (f"draw_frame mode={mode}", f"draw fence completed {i}")]
        for runner, command in (("native", [exe]), ("pe_map", map_command), ("AOT", [lifted])):
            child = subprocess.run(command, env=env, capture_output=True, timeout=120)
            if case == cases[0] and runner == "native" and child.returncode == 77:
                raise SystemExit(77)
            lines = child.stderr.decode(errors="replace").splitlines()
            if runner == "pe_map":
                if case == "bogus":
                    # ExitProcess(21) kills the mapped child before it prints the
                    # return marker; the six shim bindings are already logged.
                    check("explicit shim bindings", sorted(lines), markers)
                else:
                    check("mapper returned", lines[-1:], ["mapped host returned"])
                    check("explicit shim bindings", sorted(lines[:len(markers)]), markers)
                    check(f"{case} mapped draw trace", lines[len(markers):-1], trace)
            else:
                check(f"{case} {runner} diagnostics", lines, trace)
            outputs[runner] = verify(case, child, digest)
        if case != "bogus":
            check(f"{case} runners agree",
                  (outputs["native"], outputs["AOT"]), (outputs["pe_map"], outputs["pe_map"]))
        print(f"{case}: exits checked; rgba_bytes={len(outputs['native'] or b'')}")


if __name__ == "__main__":
    # The draw provider path is passed so the test depends on its build; the guest
    # locates it next to the EXE, like the compute provider.
    args = sys.argv[1:]
    texture_reuse = args[-1] == "--texture-reuse"
    if texture_reuse:
        args.pop()
    compare(*map(Path, args[:-1]), texture_reuse=texture_reuse)
