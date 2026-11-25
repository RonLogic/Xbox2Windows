"""Translated compute programs with 2D texture views, dispatched through the stand-in's command list (slot 14).

usage: startup_compute_dispatch_texture_check.py <startup_stream_consumer> <dxc> <warp|hardware> [two-mips]

Texture output: the consumer creates the image store program of startup_compute_image_store_d16_check.py as a
device slot 67 pipeline, fills a 32x8 RGBA16_FLOAT texture with sentinel halves, creates an unordered access view of
it at table byte 64 (device slot 19), records Dispatch(2, 1, 1) and copies the texture to a readback buffer. Four
runs share one cache:

1. before translation: refused with "no translation", every half keeps its sentinel;
2. after gfx2hlsl.py --pending with DXC: the 1024 halves equal mip 0 of the fixture's CPU reference (64 changed);
3. texture-store-none (no Dispatch recorded): every half keeps its sentinel;
4. texture-store-denied (a texture without unordered access, whose view the stand-in does not create): refused
   with one logged reason, every half keeps its sentinel.
A fifth run binds the same program under a manifest edited to declare no entry values.

Texture input: case A of startup_gfx2hlsl_compute_check.py with a level-zero sample of a 2D texture XORed into the
stored word (a static sampler, a texture view at table byte 64). The consumer adds a 1x1 RGBA8 texture whose red
is 1.0 and a shader resource view of it (device slot 18). After translation all 320 words equal the case's
expectation XOR the bits of 1.0f; dispatch-sampled-unbound (no view created) is refused with one logged reason and
every word keeps the sentinel.
The two-mips mode writes two different patterns through separate UAVs of the same texture, then swaps the views.
Exit 77 when the adapter is not available.
"""
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

import startup_compute_image_store_d16_check as S
import startup_gfx2hlsl_compute_check as C
from startup_compute_dispatch_check import lines
from startup_compute_request_check import DATA, prepare
from startup_compute_translate_check import state_a
from startup_stream_check import gfx2hlsl as G


def run(directory, consumer, env, mode, layout):
    out = directory / 'out.bin'
    out.unlink(missing_ok=True)
    args = [consumer, directory / 'vertex', directory / 'pixel', directory / 'root', 'slot67',
            directory / 'program', directory / 'data', directory / 'state', mode]
    result = subprocess.run(args, env=dict(env, X2W_TEST_DISPATCH_OUT=str(out)), capture_output=True, text=True)
    if result.returncode == 77:
        print(result.stdout, end='')
        sys.exit(77)
    assert result.returncode == 0, f'{mode}: consumer exit {result.returncode}\n{result.stderr[-3000:]}'
    return list(struct.unpack(layout, out.read_bytes())), result


def translate(cache, dxc, key):
    out = subprocess.run([sys.executable, '-B', G.__file__, '--pending', cache], check=True, capture_output=True,
                         text=True, env=dict(os.environ, X2W_DXC=dxc)).stdout
    assert f'{key:016x}: ok' in out, out


def clean(result):
    assert not lines(result, 'refused'), result.stderr[-3000:]
    errors = [line for line in lines(result, 'x2w-d3d12-debug ') if ' sev=0 ' in line or ' sev=1 ' in line]
    assert not errors, errors[0]
    layer = lines(result, 'x2w-d3d12 debug layer: ')
    return 'enabled, no error message' if layer and '0x00000000' in layer[0] else 'unavailable'


def store(directory, consumer, dxc, env):
    cache, base = prepare(directory)
    env = dict(base, **env)
    (directory / 'compute_root').write_bytes(C.root_blob(3, table_range=True))
    env['X2W_TEST_COMPUTE_ROOT'] = str(directory / 'compute_root')
    assert S.fixture.GROUP == (4, 2, 1) and S.fixture.GROUPS == 2, 'the consumer hard-codes Dispatch(2, 1, 1)'
    (directory / 'program').write_bytes(S.fixture.WORDS)
    (directory / 'data').write_bytes(DATA)
    (directory / 'state').write_bytes(state_a(S.fixture.GROUP, S.RSRC2))
    reference = S.fixture.reference()
    halves = 32 * 8 * 4  # mip 0 comes first in both halves of the reference
    untouched, expected = reference[:halves], reference[len(reference) // 2:][:halves]
    assert sum(a != b for a, b in zip(untouched, expected)) == 64
    layout = '<%dH' % halves

    got, result = run(directory, consumer, env, 'texture-store', layout)
    print(result.stdout, end='')
    assert got == untouched, 'a Dispatch without a translation wrote the texture'
    assert len(lines(result, 'Dispatch(2, 1, 1) refused: no translation')) == 1, result.stderr[-3000:]
    key = int(lines(result, 'x2w-draw compute request')[0].split()[3], 16)
    translate(cache, dxc, key)

    got, result = run(directory, consumer, env, 'texture-store', layout)
    wrong = [i for i in range(halves) if got[i] != expected[i]]
    assert not wrong, 'half=%d got=%04x expected=%04x (%d halves differ)\n%s' % (
        wrong[0], got[wrong[0]], expected[wrong[0]], len(wrong), result.stderr[-3000:])
    assert len(lines(result, 'Dispatch(2, 1, 1) translated')) == 1, result.stderr[-3000:]
    layer = clean(result)

    got, result = run(directory, consumer, env, 'texture-store-none', layout)
    assert got == untouched and not lines(result, 'Dispatch('), 'the texture changed without a Dispatch'

    got, result = run(directory, consumer, env, 'texture-store-denied', layout)
    assert got == untouched, 'a refused Dispatch wrote the texture'
    refused = lines(result, 'refused: ')
    assert len(refused) == 1 and 'an output texture view was not created on the desktop' in refused[0], result.stderr[-3000:]

    # The same compiled program under a manifest without entry values (the program reads none of its three): the
    # runtime binds no constants. The manifest is edited here; the translator writes `ud 0` for a root without them.
    manifest = cache / f'{key:016x}.txt'
    kept = [line for line in manifest.read_text().splitlines() if not line.startswith('udsrc ')]
    assert len(kept) == 5 and kept[3] == 'ud 3', kept
    manifest.write_bytes(('\n'.join(kept).replace('ud 3', 'ud 0') + '\n').encode())
    got, result = run(directory, consumer, env, 'texture-store', layout)
    assert got == expected and lines(result, 'translated: 0 entry values, 0 inputs, 1 outputs'), result.stderr[-3000:]
    clean(result)
    print(f'texture output: all {halves} halves match after translation (64 changed) and without entry values; '
          f'untouched before translation, without a Dispatch and for a refused view; debug layer: {layer}')


# A second descriptor, at table byte 128, and a different color in v14.
SECOND_STORE = bytes.fromhex(
    '06080cf4 800000fa '  # s_load_dwordx8 s[32:39], s[12:13], 0x80
    '7fc08cbf '           # s_waitcnt lgkmcnt(0)
    'ff021c7e 00bc0044 ' # v_mov_b32_e32 v14, 0x4400bc00
    '080f20f0 080e0880'  # image_store v[14:15], v[8:9], s[32:39] dmask:0xf dim:SQ_RSRC_IMG_2D d16
)
MIPS = ((32, 8), (16, 4))


def mip_reference(colors):
    halves = []
    for mip, ((width, height), color) in enumerate(zip(MIPS, colors, strict=True)):
        for y in range(height):
            for x in range(width):
                sentinel = 0x5000 + 0x400 * mip + 4 * (y * width + x)
                if color is None or x >= 8 or y >= 2:
                    halves.extend(range(sentinel, sentinel + 4))
                else:
                    tag = 8 * y + x
                    rg = (0x3c00 + tag, 0xc000 + tag) if color == 0 else (0xbc00, 0x4400)
                    halves.extend((*rg, tag, 0x8000 + tag))
    return halves


def check_mips(label, got, expected):
    start = 0
    for mip, (width, height) in enumerate(MIPS):
        for index in range(width * height * 4):
            actual, wanted = got[start + index], expected[start + index]
            pixel, channel = divmod(index, 4)
            y, x = divmod(pixel, width)
            assert actual == wanted, (f'{label}: mip={mip} x={x} y={y} channel={channel} '
                                      f'expected={wanted:04x} actual={actual:04x}')
        start += width * height * 4


def store_two_mips(directory, consumer, dxc, env):
    cache, base = prepare(directory)
    root = C.root_blob(3, table_range=True)
    (directory / 'compute_root').write_bytes(root)
    env = dict(base, **env, X2W_TEST_COMPUTE_ROOT=str(directory / 'compute_root'))
    code = S.PREFIX + S.STORE + SECOND_STORE + S.END
    pipeline = G.ComputePipeline(code, root, S.RSRC2, S.fixture.GROUP)
    outputs = [line for line in pipeline.manifest().splitlines() if line.startswith('uav ')]
    assert outputs == [
        'uav 0 tex2d float table 1 64 channels 4',
        'uav 1 tex2d float table 1 128 channels 4',
    ], outputs
    (directory / 'program').write_bytes(code)
    (directory / 'data').write_bytes(DATA)
    (directory / 'state').write_bytes(state_a(S.fixture.GROUP, S.RSRC2))
    untouched = mip_reference((None, None))
    layout = '<%dH' % len(untouched)

    got, result = run(directory, consumer, env, 'texture-store-mips', layout)
    print(result.stdout, end='')
    check_mips('missing translation', got, untouched)
    assert len(lines(result, 'Dispatch(2, 1, 1) refused: no translation')) == 1, result.stderr[-3000:]
    key = int(lines(result, 'x2w-draw compute request')[0].split()[3], 16)
    translate(cache, dxc, key)

    expected = mip_reference((0, 1))
    got, result = run(directory, consumer, env, 'texture-store-mips', layout)
    check_mips('ordered views', got, expected)
    assert len(lines(result, 'translated: 3 entry values, 0 inputs, 2 outputs')) == 1, result.stderr[-3000:]
    layer = clean(result)

    got, result = run(directory, consumer, env, 'texture-store-mips-swapped', layout)
    check_mips('swapped views', got, mip_reference((1, 0)))
    clean(result)
    changed = sum(a != b for a, b in zip(got, expected, strict=True))
    assert changed == 64, changed
    print(f'two mip outputs: {len(got)} halves match; swapping views changes {changed} halves; debug layer: {layer}')


# v_xor_b32 v5, v5, v12: the assembled `v_xor_b32 v5, v5, v6` of case A with its second source register changed
# (not assembled by llvm-mc here; the translation below is checked to read it as written).
XOR = 'v_xor_b32 v5, v5, v12'
SAMPLED = ('s_mov_b64 s[20:21], 0', 's_mov_b64 s[22:23], 0', 's_load_dwordx8 s[24:31], s[10:11], 0x40',
           'image_sample_lz v[12:15], v[0:1], s[24:31], s[20:23] dmask:0xf dim:SQ_RSRC_IMG_2D', XOR)


def sample(directory, consumer, dxc, env):
    cache, base = prepare(directory)
    env = dict(base, **env)
    (directory / 'compute_root').write_bytes(C.root_blob(table_range=True))
    env['X2W_TEST_COMPUTE_ROOT'] = str(directory / 'compute_root')
    _, words, rsrc2, group, groups = C.CASES['A']
    assert groups == (3, 2, 2) and (C.KEY, C.STRIDE) == (0x01234567, 0x9e3779b9), 'the consumer hard-codes case A'
    assert words[96:100].hex() == '050d0a3a', 'case A no longer holds v_xor_b32 v5, v5, v6 at byte 96'
    C.SPLICE[XOR] = '05190a3a'
    code = C.splice(*SAMPLED)
    pipeline = C.translate('A', code=code)
    assert 'v5 = v5 ^ v12;' in pipeline.hlsl_cs() and pipeline.manifest().endswith(
        'srv 0 buf table 1 0\nsrv 1 tex2d table 1 64\nuav 0 table 1 16\nsmp 0 0x0 0x0 0x0 0x0\n'), pipeline.manifest()
    (directory / 'program').write_bytes(code)
    (directory / 'data').write_bytes(DATA)
    (directory / 'state').write_bytes(state_a(group, rsrc2))
    one = struct.unpack('<I', struct.pack('<f', 1.0))[0]
    written = 24 * 3 * 2 * 2  # threads per group times groups; the words past them keep the sentinel
    untouched, plain = [C.SENTINEL] * C.WORDS, C.expected('A')
    assert plain[written:] == untouched[written:]
    expected = [word ^ one for word in plain[:written]] + plain[written:]
    layout = '<%dI' % C.WORDS

    got, result = run(directory, consumer, env, 'dispatch-sampled', layout)
    assert got == untouched, 'a Dispatch without a translation wrote the output'
    key = int(lines(result, 'x2w-draw compute request')[0].split()[3], 16)
    translate(cache, dxc, key)

    got, result = run(directory, consumer, env, 'dispatch-sampled', layout)
    wrong = [i for i in range(C.WORDS) if got[i] != expected[i]]
    assert not wrong, 'word=%d got=%08x expected=%08x (%d words differ)\n%s' % (
        wrong[0], got[wrong[0]], expected[wrong[0]], len(wrong), result.stderr[-3000:])
    assert len(lines(result, 'Dispatch(3, 2, 2) translated')) == 1, result.stderr[-3000:]
    layer = clean(result)

    got, result = run(directory, consumer, env, 'dispatch-sampled-unbound', layout)
    assert got == untouched, 'a refused Dispatch wrote the output'
    refused = lines(result, 'refused: ')
    assert len(refused) == 1 and 'an input texture entry is not the token of a shader resource view' in refused[0], \
        result.stderr[-3000:]
    print(f'texture input: all {C.WORDS} words match after translation; untouched before translation and without a '
          f'view; debug layer: {layer}')


def main(consumer, dxc, adapter, mode=None):
    env = dict(X2W_D3D12_LIST_NOOP='1', X2W_D3D12_DEBUG='1')  # guest lists; debug-layer messages go to the log
    if adapter == 'hardware':
        env['X2W_TEST_DISPATCH_HARDWARE'] = '1'
    with tempfile.TemporaryDirectory() as temporary:
        if mode == 'two-mips':
            store_two_mips(Path(temporary) / 'mips', consumer, dxc, env)
        else:
            store(Path(temporary) / 'store', consumer, dxc, env)
            sample(Path(temporary) / 'sample', consumer, dxc, env)
    print(f'startup_compute_dispatch_texture: PASS on {adapter}')


if __name__ == '__main__':
    if len(sys.argv) not in (4, 5) or sys.argv[3] not in ('warp', 'hardware') or sys.argv[4:] not in ([], ['two-mips']):
        sys.exit(__doc__)
    main(*sys.argv[1:])
