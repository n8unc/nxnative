#!/usr/bin/env python3
"""Mods for Unreal Engine games: list, extract and repack the game's files, for the host's RomFS overlay (DM_ROMFS_OVERLAY).

The game's files are taken from a build folder the NSP script made (<NSP folder>/<title ID>_build: extract/<version>/RomFS, and for Oodle-compressed files the Mac host
tools/dmhost with the rewritten modules patched_<version>, which run the game's own decoder; any Mac or mac-app build has them). Paths are from the RomFS root, as the game
sees them: FFRS/Config/DefaultEngine.ini (in a pak), FFRS/Content/Datatable/Item/DT_EquipmentItemData.uasset (in an IoStore container).

  ue_mod.py list    <build folder> [regex]                 every file in the paks and containers, with where it is
  ue_mod.py extract <build folder> <regex> <out folder>    the matching files, decoded, at their paths under <out folder>
  ue_mod.py text    <build folder> <culture> <regex>       the localized strings (Localization/*/<culture>/*.locres) whose text or key matches: namespace, key, text
  ue_mod.py pack    <build folder> <mod folder> <overlay>  every file under <mod folder> (at its game path) into patched copies of the paks and containers that hold it, in <overlay>

Run the game with DM_ROMFS_OVERLAY=<overlay> (several overlays: separated by ':', the first wins); the Mac app reads it from env.txt in its Application Support folder
(docs/development.md, Modding Unreal Engine games). pack takes edited files only: a file in no pak or container stops it, unless --add-to <pak path> names the pak that gets it.
Patched copies are APFS clones of the game's files with the changes appended (no space of their own when the overlay is on the same volume as the build)."""
import argparse, os, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'dm/tools'))
import ue_files   # noqa: E402
uepak = ue_files.uepak


class Game:
    def __init__(self, build, version=None, romfs=None, host=None, modules=None):
        build = Path(build)
        if romfs: self.romfs = Path(romfs)
        else:
            vers = sorted(p.parent.name for p in (build / 'extract').glob('*/RomFS'))
            if version: vers = [v for v in vers if v == version]
            if len(vers) != 1: sys.exit(f'{build}: {"no" if not vers else "several"} extract/<version>/RomFS ({", ".join(vers)}): name one with --version, or --romfs')
            self.romfs = build / 'extract' / vers[0] / 'RomFS'; version = vers[0]
        self.host = Path(host) if host else build / 'tools/dmhost'
        self.modules = Path(modules) if modules else build / f'patched_{version}'
        self.dec = None
        self.paks = sorted(self.romfs.rglob('*.pak')); self.tocs = sorted(self.romfs.rglob('*.utoc'))
        self.index = {}   # path → [(kind, container file, key)]
        for p in self.paks:
            for path, key in ue_files.pak_files(p).items(): self.index.setdefault(path, []).append(('pak', p, key))
        for t in self.tocs:
            for path, i in ue_files.Toc(t).files().items(): self.index.setdefault(path, []).append(('toc', t, i))

    def decoder(self):
        if not self.dec:
            if not self.host.is_file() or not self.modules.is_dir(): sys.exit(f'Oodle-compressed files need the Mac host ({self.host}) and the rewritten modules ({self.modules}): --host / --modules')
            self.dec = uepak.OodleHost(self.host, self.modules)
        return self.dec

    def read(self, kind, f, key):
        if kind == 'pak':
            p = uepak.Pak(str(f)); e = p.entry(key)
            try: return p.read(key, self.decoder() if e['method'] not in ('', 'Zlib', 'LZ4') else None)
            finally: p.f.close()
        t = uepak.Toc(str(f)); meth = {t.blocks[k][3] for k in range(t.ranges[key][0] // t.block_size, (sum(t.ranges[key]) - 1) // t.block_size + 1)} if t.ranges[key][1] else set()
        need = any(m and t.methods[m - 1] == 'Oodle' for m in meth)
        try: return t.read(key, self.decoder() if need else None)
        finally:
            for h in t.files.values(): h.close()

    def close(self):
        if self.dec: self.dec.close()


def cmd_list(g, a):
    pat = re.compile(a.regex) if a.regex else None
    for path in sorted(g.index):
        if pat and not pat.search(path): continue
        for kind, f, key in g.index[path]: print(f'{path}\t{os.path.relpath(f, g.romfs)}' + (f'\tchunk {key}' if kind == 'toc' else ''))


def cmd_extract(g, a):
    pat = re.compile(a.regex); n = 0
    for path in sorted(p for p in g.index if pat.search(p)):
        kind, f, key = g.index[path][-1]
        out = Path(a.out) / path; out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(g.read(kind, f, key)); n += 1
        print(f'{path}\t{out.stat().st_size}')
    if not n: sys.exit(f'no file matches {a.regex}')


def cmd_text(g, a):
    pat = re.compile(a.regex); hits = 0
    files = [p for p in g.index if re.search(rf'/Localization/[^/]+/{re.escape(a.culture)}/[^/]+\.locres$', p)]
    if not files: sys.exit(f'no .locres for culture {a.culture}')
    for path in sorted(files):
        for ns, keys in ue_files.locres(g.read(*g.index[path][-1])).items():
            for k, s in keys.items():
                if pat.search(s) or pat.search(k): print(f'{ns}\t{k}\t{s}'); hits += 1
    print(f'{hits} strings', file=sys.stderr)


def cmd_pack(g, a):
    mod = Path(a.mod); files = sorted(str(p.relative_to(mod)).replace(os.sep, '/') for p in mod.rglob('*') if p.is_file() and not p.name.startswith('.'))
    if not files: sys.exit(f'no files under {mod}')
    paks, tocs = {}, {}
    add_to = (g.romfs / a.add_to) if a.add_to else None
    for path in files:
        data = (mod / path).read_bytes()
        homes = g.index.get(path)
        if not homes:
            if not add_to: sys.exit(f'{path} is in no pak or container of {g.romfs}: --add-to <pak> puts new files into a pak')
            ap = uepak.Pak(str(add_to)); mount = ap.mount; ap.f.close(); m = ue_files.rel(mount, '')
            if not path.startswith(m): sys.exit(f'{path} is outside {a.add_to}\'s mount point ({mount})')
            paks.setdefault(add_to, {})[path[len(m):]] = data; continue
        for kind, f, key in homes:   # every copy (a patch pak may hold one too), so whichever the engine prefers has the edit
            (paks if kind == 'pak' else tocs).setdefault(f, {})[key] = data
    overlay = Path(a.overlay)
    for f, edits in paks.items():
        out = overlay / f.relative_to(g.romfs)
        for path, what in ue_files.pak_patch(str(f), str(out), edits): print(f'{f.relative_to(g.romfs)}: {what} {path}')
    for f, edits in tocs.items():
        out = overlay / f.relative_to(g.romfs); out.parent.mkdir(parents=True, exist_ok=True)
        ue_files.Toc(str(f)).patch(str(out), edits)
        for i in edits: print(f'{f.relative_to(g.romfs)}: replaced chunk {i}')
    print(f'overlay: {overlay} (DM_ROMFS_OVERLAY={overlay.resolve()})')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0], epilog=__doc__.split('\n', 2)[2], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--version', help='the extract/<version> folder to use, when the build has several')
    ap.add_argument('--romfs', help='the RomFS folder (default: the build\'s extract/<version>/RomFS)')
    ap.add_argument('--host', help='the Mac host that decodes Oodle (default: the build\'s tools/dmhost)')
    ap.add_argument('--modules', help='the rewritten modules for it (default: the build\'s patched_<version>)')
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('list'); p.add_argument('build'); p.add_argument('regex', nargs='?')
    p = sub.add_parser('extract'); p.add_argument('build'); p.add_argument('regex'); p.add_argument('out')
    p = sub.add_parser('text'); p.add_argument('build'); p.add_argument('culture'); p.add_argument('regex')
    p = sub.add_parser('pack'); p.add_argument('build'); p.add_argument('mod'); p.add_argument('overlay'); p.add_argument('--add-to', help='the pak (path from the RomFS root) that takes files no pak or container holds')
    a = ap.parse_args(argv)
    g = Game(a.build, a.version, a.romfs, a.host, a.modules)
    try:
        try: {'list': cmd_list, 'extract': cmd_extract, 'text': cmd_text, 'pack': cmd_pack}[a.cmd](g, a)
        except ValueError as e: sys.exit(str(e))
    finally: g.close()


if __name__ == '__main__':
    main()
