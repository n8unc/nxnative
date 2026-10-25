"""The Unreal mod tools (dm/tools/ue_files.py, scripts/ue_mod.py) on synthetic files: a pak written and patched reads back through dm/gfx/tools/uepak.py (the shader tools' reader),
its path hashes are the FNV-1a 64 of FINAL FANTASY RESONANCE's pak; an IoStore container with a directory index lists its files and a patched copy reads back with the replaced chunks
and every other chunk unchanged; a Zen package's name map and export resizing; unversioned headers; .locres; and the pack command end to end. No game files needed (temporary files only)."""
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import warnings

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / 'dm/tools'))
import ue_files   # noqa: E402
uepak = ue_files.uepak


def toc_write(path, files, block=0x40):
    """A version 8 IoStore container (.utoc and .ucas) of {path from the RomFS root: bytes}, stored blocks of `block` bytes, with a directory index (one directory level per path part)"""
    names = sorted(files); ids, ranges, blocks, data, stream = [], [], [], bytearray(), 0
    for i, p in enumerate(names):
        d = files[p]; ids.append(struct.pack('<QHBB', 0x1000 + i, 0, 0, 2)); ranges.append((stream, len(d)))
        for k in range(0, max(len(d), 1), block):
            piece = d[k:k + block]; data += b'\0' * (-len(data) % 16); blocks.append((len(data), len(piece))); data += piece
        stream += -(-max(len(d), 1) // block) * block
    strs, dirs, fil = [], [[0xffffffff, 0xffffffff, 0xffffffff, 0xffffffff]], []
    def s(x):
        if x not in strs: strs.append(x)
        return strs.index(x)
    def child(d, name):
        c = dirs[d][1]
        while c != 0xffffffff:
            if strs[dirs[c][0]] == name: return c
            c = dirs[c][2]
        dirs.append([s(name), 0xffffffff, dirs[d][1], 0xffffffff]); dirs[d][1] = len(dirs) - 1; return len(dirs) - 1
    for i, p in enumerate(names):
        parts = p.split('/'); d = 0
        for part in parts[:-1]: d = child(d, part)
        fil.append([s(parts[-1]), dirs[d][3], i]); dirs[d][3] = len(fil) - 1
    di = ue_files.fstring('../../../') + struct.pack('<i', len(dirs)) + b''.join(struct.pack('<IIII', *x) for x in dirs) \
        + struct.pack('<i', len(fil)) + b''.join(struct.pack('<III', *x) for x in fil) + struct.pack('<i', len(strs)) + b''.join(ue_files.fstring(x) for x in strs)
    n = len(names); meth = b'Oodle'.ljust(32, b'\0')
    head = bytearray(144); head[:16] = uepak.TOC_MAGIC; head[16] = 8
    struct.pack_into('<9I', head, 20, 144, n, len(blocks), 12, 1, 32, block, len(di), 1); head[80] = 8   # indexed
    body = b''.join(ids) + b''.join(o.to_bytes(5, 'big') + l.to_bytes(5, 'big') for o, l in ranges) \
        + b''.join(o.to_bytes(5, 'little') + l.to_bytes(3, 'little') + l.to_bytes(3, 'little') + b'\0' for o, l in blocks) + meth + di + bytes(24 * n)
    Path(path).write_bytes(bytes(head) + body); Path(path[:-5] + '.ucas').write_bytes(bytes(data))


def zen_write(names, export, summary=60):
    """A Zen package: summary, name map, an export map of one entry, the export"""
    hdr = [(len(n.encode('utf-16-le')) // 2 | 0x8000) if any(ord(c) > 255 for c in n) else len(n) for n in names]
    sb = b''.join(n.encode('utf-16-le') if h & 0x8000 else n.encode('latin1') for n, h in zip(names, hdr))
    nm = struct.pack('<iIQ', len(names), len(sb), ue_files.NAME_HASH_ALG) + b''.join(struct.pack('<Q', ue_files.name_hash(n)) for n in names) + struct.pack(f'>{len(names)}H', *hdr) + sb
    em = summary + len(nm); em += -em % 8; end = em + 72; nsec = (summary - 24) // 4
    head = bytearray(struct.pack('<6I', 0, end, 0, 0, 0, end) + struct.pack(f'<{nsec}i', em, em, em, *([end] * (nsec - 3))))
    head += nm; head += b'\0' * (em - len(head)); head += struct.pack('<QQII', 0, len(export), 0, 0) + bytes(48)
    return bytes(head) + export


def setUpModule(): warnings.simplefilter('ignore', ResourceWarning)   # uepak.Pak and Toc keep their files open; the readers are the shader tools', unchanged here


class Pak(unittest.TestCase):
    def test_write_and_patch(self):
        with tempfile.TemporaryDirectory() as t:
            a = f'{t}/a.pak'; files = {'Game/Config/A.ini': b'[x]\r\ny=1\r\n', 'Game/Content/B.bin': bytes(range(256)) * 3, 'C.txt': b'c'}
            ue_files.pak_write(a, files)
            p = uepak.Pak(a)
            self.assertEqual(p.mount, '../../../')
            for k, v in files.items(): self.assertEqual(p.read(k), v)
            self.assertEqual(ue_files.pak_files(a), {k: k for k in files})
            b = f'{t}/b.pak'
            done = ue_files.pak_patch(a, b, {'Game/Config/A.ini': b'[x]\r\ny=2\r\n', 'Game/Config/New.ini': b'new'})
            self.assertEqual(sorted(done), [('Game/Config/A.ini', 'replaced'), ('Game/Config/New.ini', 'added')])
            q = uepak.Pak(b)
            self.assertEqual(q.read('Game/Config/A.ini'), b'[x]\r\ny=2\r\n'); self.assertEqual(q.read('Game/Config/New.ini'), b'new')
            self.assertEqual(q.read('Game/Content/B.bin'), files['Game/Content/B.bin'])
            self.assertEqual(uepak.Pak(a).read('Game/Config/A.ini'), files['Game/Config/A.ini'])   # the original is untouched

    def test_path_hash(self):   # an entry of FINAL FANTASY RESONANCE's FFRS-Switch.pak (seed 0x1283072a)
        self.assertEqual(ue_files.path_hash('Engine/GlobalShaderCache-SP_GLSL_SWITCH.bin', 0x1283072a), 0xa205fe87f94f6723)

    def test_text(self):
        self.assertEqual(ue_files.text_invariant('Ab'), b'\x02\0\0\0\xff\x01\0\0\0\x03\0\0\0Ab\0')
        self.assertEqual(ue_files.text_none(), b'\0\0\0\0\xff\0\0\0\0')
        self.assertEqual(ue_files.fstring('ア'), struct.pack('<i', -2) + 'ア'.encode('utf-16-le') + b'\0\0')


class Container(unittest.TestCase):
    def test_list_and_patch(self):
        with tempfile.TemporaryDirectory() as t:
            files = {'Game/Content/Data/T.uasset': bytes(range(200)), 'Game/Content/Data/U.uasset': b'u' * 70, 'Game/Content/Other.uasset': b'o' * 10}
            toc_write(f'{t}/g.utoc', files)
            c = ue_files.Toc(f'{t}/g.utoc'); idx = c.files()
            self.assertEqual(sorted(idx), sorted(files))
            c.patch(f'{t}/p.utoc', {idx['Game/Content/Data/T.uasset']: b'replaced' * 30})
            r = uepak.Toc(f'{t}/p.utoc')
            self.assertEqual(r.read(idx['Game/Content/Data/T.uasset']), b'replaced' * 30)
            for k in ('Game/Content/Data/U.uasset', 'Game/Content/Other.uasset'): self.assertEqual(r.read(idx[k]), files[k])
            self.assertEqual(ue_files.Toc(f'{t}/p.utoc').files(), idx)   # chunk IDs and the directory index are kept
            self.assertEqual(uepak.Toc(f'{t}/g.utoc').read(idx['Game/Content/Data/T.uasset']), files['Game/Content/Data/T.uasset'])


class Zen(unittest.TestCase):
    def test_package(self):
        names = ['/Game/Data/T', 'T', 'Row', 'クリムゾン', 'eUnitParameterType::PhysicalAttack']
        for summary in (60, 52):
            b = zen_write(names, b'E' * 40, summary)
            p = ue_files.Package(b)
            self.assertEqual((p.summary_size, p.names), (summary, names)); self.assertTrue(p.names_ok())
            self.assertEqual(p.export(), b'E' * 40)
            q = ue_files.Package(p.with_export(0, b'F' * 50))
            self.assertEqual((q.export(), q.exports[0]['size']), (b'F' * 50, 50))

    def test_name_hash(self):   # names of FINAL FANTASY RESONANCE's DT_EquipmentItemData (their hashes as the package stores them)
        self.assertEqual(ue_files.name_hash('ST_EquipmentName'), 0x9cf035d6b5db001c)
        self.assertEqual(ue_files.name_hash('クリムゾンセイバー'), 0xe0d86cdcd2f90620)   # クリムゾンセイバー (UTF-16)

    def test_unversioned_header(self):
        # DT_EquipmentItemData's rows: 17 values, zeroes at 2 and 16 (mask 0x00010004); DT_ShopList's entries: 4 values, none zero
        r = uepak.R(struct.pack('<HI', 0x2380, 0x00010004))
        self.assertEqual(ue_files.unversioned_header(r), [(i, i not in (2, 16)) for i in range(17)])
        self.assertEqual(ue_files.unversioned_header(uepak.R(struct.pack('<H', 0x0900))), [(i, True) for i in range(4)])
        # skip 3, then two values; then a fragment with zeroes (skip 1, three values, the middle one zero)
        r = uepak.R(struct.pack('<HHB', 3 | 2 << 9, 1 | 1 << 7 | 1 << 8 | 3 << 9, 0b010))
        self.assertEqual(ue_files.unversioned_header(r), [(3, True), (4, True), (6, True), (7, False), (8, True)])


class Locres(unittest.TestCase):
    def test_read(self):
        strs = ['Crimson Saber', 'Excalibur']
        ns = struct.pack('<I', 0) + ue_files.fstring('ST_EquipmentName') + struct.pack('<I', 2) \
            + struct.pack('<I', 0) + ue_files.fstring('EquipmentName10100') + struct.pack('<Ii', 0, 0) + struct.pack('<I', 0) + ue_files.fstring('EquipmentName10195') + struct.pack('<Ii', 0, 1)
        head = ue_files.LOCRES_MAGIC + bytes([3]); body = struct.pack('<I', 2) + struct.pack('<I', 1) + ns
        off = len(head) + 8 + len(body)
        b = head + struct.pack('<q', off) + body + struct.pack('<i', 2) + b''.join(ue_files.fstring(s) + struct.pack('<i', 1) for s in strs)
        want = {'ST_EquipmentName': {'EquipmentName10100': 'Crimson Saber', 'EquipmentName10195': 'Excalibur'}}
        self.assertEqual(ue_files.locres(b), want)
        v0 = struct.pack('<I', 1) + ue_files.fstring('ST_EquipmentName') + struct.pack('<I', 2) \
            + ue_files.fstring('EquipmentName10100') + struct.pack('<I', 0) + ue_files.fstring(strs[0]) + ue_files.fstring('EquipmentName10195') + struct.pack('<I', 0) + ue_files.fstring(strs[1])
        self.assertEqual(ue_files.locres(v0), want)


class Pack(unittest.TestCase):
    def test_pack(self):
        lib = ue_files
        import importlib.util
        spec = importlib.util.spec_from_file_location('ue_mod_cli', REPO / 'scripts/ue_mod.py'); cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
        with tempfile.TemporaryDirectory() as t:
            romfs = Path(t) / 'build/extract/v0/RomFS'; paks = romfs / 'Game/Content/Paks'; paks.mkdir(parents=True)
            lib.pak_write(str(paks / 'Game-Switch.pak'), {'Game/Config/DefaultEngine.ini': b'[SystemSettings]\r\n'})
            toc_write(str(paks / 'Game-Switch.utoc'), {'Game/Content/Data/T.uasset': b't' * 100, 'Game/Content/Data/U.uasset': b'u' * 100})
            mod = Path(t) / 'mod'; (mod / 'Game/Config').mkdir(parents=True); (mod / 'Game/Content/Data').mkdir(parents=True)
            (mod / 'Game/Config/DefaultEngine.ini').write_bytes(b'[SystemSettings]\r\nr.X=1\r\n'); (mod / 'Game/Content/Data/T.uasset').write_bytes(b'T' * 150)
            cli.main(['pack', str(Path(t) / 'build'), str(mod), str(Path(t) / 'overlay')])
            o = Path(t) / 'overlay/Game/Content/Paks'
            self.assertEqual(uepak.Pak(str(o / 'Game-Switch.pak')).read('Game/Config/DefaultEngine.ini'), b'[SystemSettings]\r\nr.X=1\r\n')
            c = lib.Toc(str(o / 'Game-Switch.utoc')); idx = c.files(); r = uepak.Toc(str(o / 'Game-Switch.utoc'))
            self.assertEqual((r.read(idx['Game/Content/Data/T.uasset']), r.read(idx['Game/Content/Data/U.uasset'])), (b'T' * 150, b'u' * 100))
            (mod / 'Game/Config/Missing.ini').write_bytes(b'x')
            with self.assertRaises(SystemExit): cli.main(['pack', str(Path(t) / 'build'), str(mod), str(Path(t) / 'overlay2')])
            cli.main(['pack', '--add-to', 'Game/Content/Paks/Game-Switch.pak', str(Path(t) / 'build'), str(mod), str(Path(t) / 'overlay3')])
            self.assertEqual(uepak.Pak(str(Path(t) / 'overlay3/Game/Content/Paks/Game-Switch.pak')).read('Game/Config/Missing.ini'), b'x')


if __name__ == '__main__':
    unittest.main()
