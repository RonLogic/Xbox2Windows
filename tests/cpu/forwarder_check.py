"""Resolve a forwarded export through the host resolver and compare it with GetProcAddress."""

import ctypes
from pathlib import Path
import struct
import shutil
import sys
from tempfile import TemporaryDirectory

import pefile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_map_check import LOAD_SEARCH, Mapper, Resolver, STATUS, kernel  # noqa: E402


def check_forwarder_bounds(mapper, original, directory):
    data = original.read_bytes()
    with pefile.PE(data=data) as pe:
        entry = next(e for e in pe.DIRECTORY_ENTRY_EXPORT.symbols if e.name == b"mix_args")
        offset = pe.get_offset_from_rva(entry.address)
        exports = pe.OPTIONAL_HEADER.DATA_DIRECTORY[0]
        size_field = exports.get_field_absolute_offset("Size")
        distance = entry.address - exports.VirtualAddress
        capacity = len(entry.forwarder) + 1

    cases = [(b"Host.#1", 1), (b"Host.#65535", 65535), (b"Host.#0", None),
             (b"Host.#65536", None), (b"Host.#+1", None), (b"Host.# 1", None)]
    for text, expected in cases + [(b"Host.Name", None)]:
        variant = bytearray(data)
        assert len(text) + 1 <= capacity
        variant[offset:offset + len(text) + 1] = text + b"\0"
        if text == b"Host.Name":
            # Keep the terminator in the image, immediately outside the directory.
            struct.pack_into("<I", variant, size_field, distance + len(text))
        path = directory / "forwarder_variant.dll"
        path.write_bytes(variant)
        calls = []
        def record(module, name, ordinal):
            calls.append((module, name, ordinal))
            return 12345
        mapped, status, detail, _ = mapper(path, {})
        if not mapped:
            raise AssertionError(f"cannot map {text!r}: {status}, {detail}")
        try:
            result = mapper.resolved(mapped, b"mix_args", Resolver(record))
            wanted = [] if expected is None else [(b"Host", None, expected)]
            assert calls == wanted and result == (12345 if wanted else None), (text, calls, result)
            assert mapper.resolved(mapped, b"mix_args", Resolver()) is None
        finally:
            mapper.release(mapped)


def check_windows_ordinal(mapper, original, provider, directory):
    target = directory / provider.name
    shutil.copyfile(provider, target)
    module = ctypes.CDLL(str(target))
    expected = kernel.GetProcAddress(module._handle, 77)
    assert expected and not kernel.GetProcAddress(module._handle, b"mix_args")

    image = bytearray(original.read_bytes())
    with pefile.PE(data=bytes(image)) as pe:
        entry = next(e for e in pe.DIRECTORY_ENTRY_EXPORT.symbols if e.name == b"mix_args")
        offset = pe.get_offset_from_rva(entry.address)
        capacity = len(entry.forwarder) + 1
    module_name = provider.stem.encode("ascii")
    forwarder = module_name + b".#77\0"
    assert len(forwarder) <= capacity
    image[offset:offset + capacity] = forwarder.ljust(capacity, b"\0")
    ordinal_copy = directory / "ordinal_forwarder.dll"
    ordinal_copy.write_bytes(image)

    native = kernel.LoadLibraryExW(str(ordinal_copy), None, LOAD_SEARCH)
    if not native:
        raise AssertionError(f"ordinal LoadLibraryExW failed with {ctypes.get_last_error()}")
    try:
        assert kernel.GetProcAddress(native, b"mix_args") == expected
    finally:
        kernel.FreeLibrary(native)

    calls = []

    @Resolver
    def resolve(dll, name, ordinal):
        calls.append((dll, name, ordinal))
        return kernel.GetProcAddress(module._handle, ordinal) if dll == module_name and name is None else None

    base, status, detail, _ = mapper(ordinal_copy, {})
    if not base or status != STATUS["ok"]:
        raise AssertionError(f"ordinal map failed with {status}: {detail}")
    try:
        assert mapper.resolved(base, b"mix_args", resolve) == expected
        assert calls == [(module_name, None, 77)], calls
    finally:
        mapper.release(base)
    print(f"{module_name.decode()}.#77: Windows and pe_map resolved the same NONAME export")


def main(mapper_path, forwarder, provider, ordinal_provider):
    mapper = Mapper(mapper_path)
    # The provider stays loaded until the process exits, so its copy cannot be deleted here.
    with TemporaryDirectory(ignore_cleanup_errors=True) as temp:
        forwarder_copy = Path(temp) / forwarder.name
        provider_copy = Path(temp) / provider.name
        shutil.copyfile(forwarder, forwarder_copy)
        shutil.copyfile(provider, provider_copy)
        # Load the provider first so Windows reuses this module when it resolves the forwarder.
        windows_provider = ctypes.CDLL(str(provider_copy))
        windows_forwarder = kernel.LoadLibraryExW(str(forwarder_copy), None, LOAD_SEARCH)
        if not windows_forwarder:
            raise AssertionError(f"LoadLibraryExW failed with {ctypes.get_last_error()}")
        try:
            forwarded_expected = kernel.GetProcAddress(windows_forwarder, b"mix_args")
            local_expected = kernel.GetProcAddress(windows_forwarder, b"local_value")
        finally:
            kernel.FreeLibrary(windows_forwarder)
        if not forwarded_expected or not local_expected:
            raise AssertionError("GetProcAddress did not resolve the forwarder or the local export")

        requests = []

        def resolve(dll, name, ordinal):
            key = dll.decode().casefold()
            requests.append((key, name.decode() if name else ordinal))
            target = {"cpu_import_provider": windows_provider,
                      "cpu_import_provider.dll": windows_provider}.get(key)
            return kernel.GetProcAddress(target._handle, name if name else ordinal) if target else None

        check_forwarder_bounds(mapper, forwarder_copy, Path(temp))
        check_windows_ordinal(mapper, forwarder_copy, ordinal_provider, Path(temp))
        callback = Resolver(resolve)
        base, status, detail, _ = mapper(forwarder_copy, {})
        if not base or status != STATUS["ok"]:
            raise AssertionError(f"map failed with {status}: {detail}")
        try:
            forwarded = mapper.resolved(base, b"mix_args", callback)
            local = mapper.resolved(base, b"local_value", callback)
            direct = mapper.export(base, b"mix_args")
            missing = mapper.resolved(base, b"absent", callback)
            refused = mapper.resolved(base, b"mix_args", Resolver(lambda dll, name, ordinal: None))
        finally:
            mapper.release(base)

    if direct is not None:
        raise AssertionError(f"pe_map_export returned {direct:#x} for a forwarded export")
    if forwarded != forwarded_expected:
        raise AssertionError(f"forwarded {forwarded:#x} != Windows {forwarded_expected:#x}")
    if local - base != local_expected - windows_forwarder:
        raise AssertionError(f"local export RVA {local - base:#x} != Windows {local_expected - windows_forwarder:#x}")
    if missing is not None or refused is not None:
        raise AssertionError(f"absent={missing} refused={refused}")
    if requests != [("cpu_import_provider", "mix_args")]:
        raise AssertionError(f"unexpected resolver requests {requests}")
    print(f"{forwarder.name}: forwarded mix_args matches GetProcAddress at {forwarded:#x}; "
          "local_value RVA matches; an absent name and a refusing resolver return null")


if __name__ == "__main__":
    sys.exit(main(*map(Path, sys.argv[1:])))
