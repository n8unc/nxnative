# Unreal Engine game files for mods: write and patch pak files, list and patch IoStore containers, read Zen packages, unversioned property headers and .locres files.
# scripts/ue_mod.py is the command that uses it; dm/gfx/tools/uepak.py reads the same formats for the shader tools (this module only reads through it, so the shader chain does not change).
# A mod is served by the host's RomFS overlay (DM_ROMFS_OVERLAY, hle_fs.c): a patched copy of a pak or container in the overlay replaces the game's, and everything else is read from the RomFS.
# Patched copies are APFS clones of the originals (cp -c: no space of their own on the same volume) with the new data appended and a new index written after it; the original data stays in
# place, unused. Found on FINAL FANTASY RESONANCE (UE 5.6, pak version 11, IoStore TOC version 8; dm/games/0100606026F12000/README.md, Modding).
import hashlib, os, shutil, struct, subprocess, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'gfx', 'tools'))
import uepak   # noqa: E402

R = uepak.R
MOUNT_ROOT = '../../../'   # a mount point of the RomFS root

def rel(mount, path):   # a file's path from the RomFS root: the mount point (relative to the binaries' folder, three levels below the root) joined with the entry's path
    m = mount[len(MOUNT_ROOT):] if mount.startswith(MOUNT_ROOT) else mount.lstrip('/')
    return (m + path).lstrip('/')

def fstring(s):   # FString: int32 length with the NUL, then ASCII (or, negative length, UTF-16)
    try: b = s.encode('ascii') + b'\0'; return struct.pack('<i', len(b)) + b
    except UnicodeEncodeError: b = s.encode('utf-16-le') + b'\0\0'; return struct.pack('<i', -(len(b) // 2)) + b

def text_invariant(s):   # FText with no history (-1) holding a culture-invariant string: flags (2 = CultureInvariant), history type, has-string bool (4 bytes), FString; shown as written in every language
    return struct.pack('<I', 2) + b'\xff' + struct.pack('<I', 1) + fstring(s)

def text_none(): return struct.pack('<I', 0) + b'\xff' + struct.pack('<I', 0)   # an empty FText

def clone(src, dst):   # an APFS clone (no space of its own); a plain copy across volumes or file systems
    if os.path.exists(dst): os.remove(dst)
    os.makedirs(os.path.dirname(dst) or '.', exist_ok=True)
    if subprocess.run(['cp', '-c', src, dst], capture_output=True).returncode:
        print(f'note: {dst} is a full copy of {src} ({os.path.getsize(src) >> 20} MB): cloning needs both on the same APFS volume', file=sys.stderr)
        shutil.copyfile(src, dst)

# ---- pak files (version 11, UE 4.26 to 5.x; index not encrypted)
# Footer (the last 0xdd bytes): encryption key GUID, encrypted index flag, magic, version, index offset and size, index SHA-1, 5 compression method names of 32 bytes.
# Index: mount point, entry count, path hash seed, path hash index (flag, offset, size, SHA-1), full directory index (the same), encoded entries, count of unencoded entries.
# Path hash index: count, (FNV-1a 64 of the lowercased path in UTF-16LE from the offset basis plus the seed, offset into the encoded entries) pairs, then a pruned directory index.
# Directory index: count, then per directory (every parent listed, the root as "/") its name and its files (name, offset into the encoded entries). Each file's data follows a 53-byte
# FPakEntry (offset 0, sizes, compression 0, SHA-1, flags, block size); its encoded entry is the u32 flags word (offset, raw and stored sizes fit 32 bits) then offset and size.
PAK_MAGIC, PAK_VERSION, PAK_FOOTER = 0x5A6F12E1, 11, 0xdd
FNV_BASIS, FNV_PRIME, M64 = 0xcbf29ce484222325, 0x100000001b3, (1 << 64) - 1

def path_hash(path, seed):
    h = (FNV_BASIS + seed) & M64
    for c in path.lower().encode('utf-16-le'): h = ((h ^ c) * FNV_PRIME) & M64
    return h

def _pak_head(data): return struct.pack('<qqqi', 0, len(data), len(data), 0) + hashlib.sha1(data).digest() + struct.pack('<BI', 0, 0)

def _pak_encoded(off, size):
    if off >= 1 << 32 or size >= 1 << 32: raise ValueError('a pak entry past 4 GB is not written')
    return struct.pack('<III', 1 << 31 | 1 << 30 | 1 << 29, off, size)

def _parent(d): return d[:-1].rsplit('/', 1)[0] + '/' if '/' in d[:-1] else '/'

def _dir_put(dirs, path, off):
    d, name = path.rsplit('/', 1) if '/' in path else ('', path)
    d = d + '/' if d else '/'
    fl = dirs.setdefault(d, [])
    for i, (n, _) in enumerate(fl):
        if n == name: fl[i] = (name, off); break
    else: fl.append((name, off))
    while d != '/': d = _parent(d); dirs.setdefault(d, [])

def _dirs_read(r):
    dirs = {}
    for _ in range(r.take('<i')):
        d = r.fstr(); dirs[d] = [(r.fstr(), r.take('<i')) for _ in range(r.take('<i'))]
    return dirs

def _dirs_bytes(dirs):
    return struct.pack('<i', len(dirs)) + b''.join(fstring(d) + struct.pack('<i', len(fl)) + b''.join(fstring(n) + struct.pack('<i', o) for n, o in fl) for d, fl in dirs.items())

def _pak_tail(f, data_end, mount, count, seed, hashes, pruned, full, entries, methods=()):
    phash = struct.pack('<i', len(hashes)) + b''.join(struct.pack('<Qi', h, e) for h, e in hashes.items()) + _dirs_bytes(pruned)
    dirs = _dirs_bytes(full)
    ph_off = data_end; fd_off = ph_off + len(phash); idx_off = fd_off + len(dirs)
    index = (fstring(mount) + struct.pack('<iQ', count, seed) + struct.pack('<iQQ', 1, ph_off, len(phash)) + hashlib.sha1(phash).digest()
             + struct.pack('<iQQ', 1, fd_off, len(dirs)) + hashlib.sha1(dirs).digest() + struct.pack('<i', len(entries)) + bytes(entries) + struct.pack('<i', 0))
    names = b''.join(m.encode().ljust(32, b'\0')[:32] for m in methods).ljust(5 * 32, b'\0')
    f.write(phash + dirs + index + bytes(16) + struct.pack('<BIiqq', 0, PAK_MAGIC, PAK_VERSION, idx_off, len(index)) + hashlib.sha1(index).digest() + names)

def pak_write(out, files, mount=MOUNT_ROOT):
    """A new pak of {path below the mount point: bytes}, stored without compression"""
    seed = int.from_bytes(hashlib.sha1('\n'.join(sorted(files)).encode()).digest()[:4], 'little')
    entries, hashes, full = bytearray(), {}, {'/': []}
    with open(out, 'wb') as f:
        pos = 0
        for path in sorted(files):
            data = files[path]; e = len(entries); entries += _pak_encoded(pos, len(data))
            blob = _pak_head(data) + data; f.write(blob); pos += len(blob)
            hashes[path_hash(path, seed)] = e; _dir_put(full, path, e)
        _pak_tail(f, pos, mount, len(files), seed, hashes, full, full, entries)

def pak_patch(orig, out, files):
    """A copy of the pak orig with {path below its mount point: bytes} replaced or added. Returns [(path, 'replaced' / 'added')]"""
    with open(orig, 'rb') as f:
        enc, ioff, isz, methods, ver = uepak.footer(f)
        if enc or ver != PAK_VERSION: raise ValueError(f'{orig}: pak version {ver}{", encrypted index" if enc else ""}: only unencrypted version {PAK_VERSION} paks are patched')
        f.seek(ioff); r = R(f.read(isz))
        mount = r.fstr(); count = r.take('<i'); seed = r.take('<Q')
        if not r.take('<i'): raise ValueError(f'{orig}: no path hash index')
        ph_off, ph_sz = r.take('<QQ'); r.raw(20)
        if not r.take('<i'): raise ValueError(f'{orig}: no full directory index')
        fd_off, fd_sz = r.take('<QQ'); r.raw(20)
        entries = bytearray(r.raw(r.take('<i')))
        if r.take('<i'): raise ValueError(f'{orig}: unencoded entries are not handled')
        f.seek(ph_off); p = R(f.read(ph_sz)); hashes = dict(p.take('<Qi') for _ in range(p.take('<i'))); pruned = _dirs_read(p)
        f.seek(fd_off); full = _dirs_read(R(f.read(fd_sz)))
    end = os.path.getsize(orig) - PAK_FOOTER
    clone(orig, out); done = []
    with open(out, 'r+b') as o:
        o.seek(end); o.truncate(); pos = end
        for path in sorted(files):
            data = files[path]; e = len(entries); entries += _pak_encoded(pos, len(data))
            blob = _pak_head(data) + data; o.write(blob); pos += len(blob)
            h = path_hash(path, seed); new = h not in hashes
            hashes[h] = e; _dir_put(full, path, e)
            d, name = (path.rsplit('/', 1)[0] + '/', path.rsplit('/', 1)[1]) if '/' in path else ('/', path)
            if new or any(n == name for n, _ in pruned.get(d, [])): _dir_put(pruned, path, e)
            count += new; done.append((path, 'added' if new else 'replaced'))
        _pak_tail(o, pos, mount, count, seed, hashes, pruned, full, entries, [m for m in methods])
    return done

def pak_files(path):   # {path from the RomFS root: path below the pak's mount point}
    p = uepak.Pak(path)
    try: return {rel(p.mount, f): f for f in p.files}
    finally: p.f.close()

# ---- IoStore containers (.utoc / .ucas; one partition, not encrypted or signed)
# TOC: FIoStoreTocHeader (144 bytes: magic, version, header size, chunk count, block count, block entry size 12, method count and name length, block size, directory index size,
# partition count, container ID, key GUID, flags at 80 (1 compressed, 2 encrypted, 4 signed, 8 indexed), perfect hash seed count at 84, partition size at 88, chunks without a perfect hash at 96),
# then chunk IDs (12 each), offsets and lengths (5 + 5 bytes, big-endian, in the uncompressed stream, which the blocks cut into pieces of the block size), perfect hash seeds and the
# chunks without one (u32 each), compression blocks (5-byte offset in the .ucas, 3-byte stored size, 3-byte raw size, method; 0 = stored), method names, the directory index, then one
# metadata entry per chunk (version 8: 20-byte hash, flags, 3 pad = 24 bytes; earlier: 32-byte hash, flags = 33). Flags bit 0: compressed.
# Directory index: mount point, directories {name, first child, next sibling, first file}, files {name, next file, chunk index}, string table (u32 indices; ~0 = none).
NONE32 = 0xffffffff

class Toc:
    def __init__(self, path):
        self.path = path; b = self.b = open(path, 'rb').read()
        if b[:16] != uepak.TOC_MAGIC: raise ValueError(f'{path}: not an IoStore TOC')
        self.version = b[16]
        hsz, n, nblk, bsz, nmeth, mlen, self.block_size, dsz, nparts = struct.unpack_from('<9I', b, 20)
        self.flags = b[80]; nseeds = struct.unpack_from('<I', b, 84)[0] if self.version >= 4 else 0; nover = struct.unpack_from('<I', b, 96)[0] if self.version >= 5 else 0
        if bsz != 12: raise ValueError(f'{path}: compression block entries of {bsz} bytes')
        self.n, self.nparts = n, nparts
        self.o_rng = hsz + 12 * n; self.o_blk = self.o_rng + 10 * n + 4 * nseeds + 4 * nover; self.nblk = nblk
        self.o_dir = self.o_blk + 12 * nblk + nmeth * mlen; self.dsz = dsz
        self.o_meta = self.o_dir + dsz
        self.meta = (len(b) - self.o_meta) // n if n else 0
        if self.flags & 4: raise ValueError(f'{path}: signed containers are not handled')
        if n and (len(b) - self.o_meta) % n or self.meta not in (24, 33): raise ValueError(f'{path}: {len(b) - self.o_meta} bytes of chunk metadata for {n} chunks')

    def files(self):   # {path from the RomFS root: chunk index}, from the directory index
        if not self.flags & 8 or not self.dsz: return {}
        if self.flags & 2: raise ValueError(f'{self.path}: the directory index is encrypted')
        r = R(self.b[self.o_dir:self.o_dir + self.dsz]); mount = r.fstr()
        dirs = [r.take('<IIII') for _ in range(r.take('<i'))]; fil = [r.take('<III') for _ in range(r.take('<i'))]; strs = [r.fstr() for _ in range(r.take('<i'))]
        out, todo = {}, [(0, '')] if dirs else []
        while todo:
            d, prefix = todo.pop(); name, child, sib, f = dirs[d]
            p = prefix + (strs[name] + '/' if name != NONE32 else '')
            while f != NONE32: fn, f, ud = fil[f]; out[rel(mount, p + strs[fn])] = ud
            while child != NONE32: todo.append((child, p)); child = dirs[child][2]
        return out

    def patch(self, out, chunks):
        """A copy of this container (out = the new .utoc; its .ucas beside it) with {chunk index: bytes} replaced: stored 16-aligned blocks appended to a clone of the .ucas, placed at the end
        of the uncompressed stream; the chunk's metadata loses the compressed flag and keeps its hash (the engine reads chunks without checking it; FINAL FANTASY RESONANCE)"""
        if self.flags & 2 or self.nparts != 1: raise ValueError(f'{self.path}: encrypted or partitioned containers are not handled')
        b = bytearray(self.b); blocks = bytearray(b[self.o_blk:self.o_blk + 12 * self.nblk]); blk = self.block_size
        clone(self.path[:-5] + '.ucas', out[:-5] + '.ucas')
        with open(out[:-5] + '.ucas', 'r+b') as u:
            u.seek(0, 2); pos = u.tell()
            for i, data in sorted(chunks.items()):
                stream = (len(blocks) // 12) * blk
                for k in range(0, max(len(data), 1), blk):
                    piece = data[k:k + blk]; pad = -pos % 16; u.write(b'\0' * pad); pos += pad
                    blocks += pos.to_bytes(5, 'little') + len(piece).to_bytes(3, 'little') + len(piece).to_bytes(3, 'little') + b'\0'
                    u.write(piece); pos += len(piece)
                b[self.o_rng + 10 * i:self.o_rng + 10 * i + 10] = stream.to_bytes(5, 'big') + len(data).to_bytes(5, 'big')
                b[self.o_meta + self.meta * i + (20 if self.meta == 24 else 32)] &= ~1
        struct.pack_into('<I', b, 28, len(blocks) // 12)
        b[self.o_blk:self.o_blk + 12 * self.nblk] = blocks
        open(out, 'wb').write(b)

# ---- Zen packages (UE 5 cooked packages in IoStore containers)
# FZenPackageSummary: bHasVersioningInfo, HeaderSize, Name (FMappedName), PackageFlags, CookedHeaderSize, then section offsets: imported public export hashes, import map, export map,
# export bundle entries, dependency bundle headers and entries, imported package names, and from UE 5.4 or so two more (60 bytes in all; 52 before). The name map follows: count, string
# bytes, hash algorithm 0xC1640000, CityHash64 of each lowercased name (Latin-1 or UTF-16LE bytes), 2-byte big-endian headers (bit 15 UTF-16, low 15 bits the length), the strings, unpadded.
# Export map entries are 72 bytes: serial offset and size (u64, the offset from the end of the header), object name (FMappedName), outer, class, super and template indices, public export hash,
# flags, filter flags. Export data starts at HeaderSize, in export order.
NAME_HASH_ALG = 0xC1640000
SECTIONS = ['imported_hashes', 'import_map', 'export_map', 'export_bundles', 'dep_headers', 'dep_entries', 'imported_package_names', 'section_7', 'section_8']

def name_hash(s):
    lo = s.lower()
    return uepak.city64(lo.encode('latin1') if all(ord(c) < 256 for c in lo) else lo.encode('utf-16-le'))

class Package:
    def __init__(self, b):
        self.b = b
        has_ver, self.header_size, ni, nn, self.flags, self.cooked_header_size = struct.unpack_from('<6I', b, 0)
        if has_ver: raise ValueError('a package with versioning info is not read')
        for size in (60, 52):   # the summary's size: the name map's hash algorithm sits 8 bytes after it
            if len(b) >= size + 16 and struct.unpack_from('<Q', b, size + 8)[0] == NAME_HASH_ALG: break
        else: raise ValueError('not a Zen package (no name map after a 52 or 60 byte summary)')
        self.summary_size = size
        self.sections = dict(zip(SECTIONS, struct.unpack_from(f'<{(size - 24) // 4}i', b, 24)))
        o = size; n = struct.unpack_from('<i', b, o)[0]; o += 16
        self.hashes = list(struct.unpack_from(f'<{n}Q', b, o)); o += 8 * n
        heads = struct.unpack_from(f'>{n}H', b, o); o += 2 * n; self.names = []
        for h in heads:
            L = h & 0x7fff
            if h & 0x8000: self.names.append(b[o:o + 2 * L].decode('utf-16-le')); o += 2 * L
            else: self.names.append(b[o:o + L].decode('latin1')); o += L
        self.name = self.names[ni]
        e = self.sections['export_map']; end = min(v for v in self.sections.values() if v > e)
        self.exports = []
        for i in range((end - e) // 72):
            off, size_, oi, on = struct.unpack_from('<QQII', b, e + 72 * i)
            self.exports.append(dict(offset=off, size=size_, name=self.names[oi] + (f'_{on - 1}' if on else ''), at=e + 72 * i))

    def names_ok(self): return all(name_hash(s) == h for s, h in zip(self.names, self.hashes))
    def fname(self, i, n): return self.names[i] + (f'_{n - 1}' if n else '')
    def export(self, k=0):   # export k's bytes
        x = self.exports[k]; s = self.header_size + x['offset']; return self.b[s:s + x['size']]

    def with_export(self, k, data):
        """The package with export k's bytes replaced; the exports after it move and their offsets follow"""
        x = self.exports[k]; s = self.header_size + x['offset']; d = len(data) - x['size']
        out = bytearray(self.b[:s]) + data + self.b[s + x['size']:]
        struct.pack_into('<Q', out, x['at'] + 8, len(data))
        for y in self.exports:
            if y['offset'] > x['offset']: struct.pack_into('<Q', out, y['at'], y['offset'] + d)
        return bytes(out)

# ---- unversioned property serialization (cooked UE 4.26+ / 5): each struct starts with u16 fragments (skip count bits 0-6, has-zeroes bit 7, last bit 8, value count bits 9-15),
# then, when any fragment has zeroes, a zero mask over the values of those fragments only (u8 up to 8 values, u16 up to 16, else u32 words); a masked value is zero and not written.
def unversioned_header(r):
    """[(property index, stored)] in order; the values of the stored ones follow"""
    frags = []
    while True:
        h = r.take('<H'); frags.append((h & 0x7f, h >> 7 & 1, h >> 9))
        if h >> 8 & 1: break
    nz = sum(n for _, z, n in frags if z); mask = 0
    if nz:
        if nz <= 8: mask = r.take('<B')
        elif nz <= 16: mask = r.take('<H')
        else: mask = sum(r.take('<I') << (32 * k) for k in range((nz + 31) // 32))
    out, idx, zi = [], 0, 0
    for skip, z, n in frags:
        idx += skip
        for _ in range(n):
            stored = True
            if z: stored = not (mask >> zi & 1); zi += 1
            out.append((idx, stored)); idx += 1
    return out

# ---- .locres. Versions 1 to 3 start with a 16-byte magic, the version and the string array's offset (i64); version 2 and later then hold an entry count (u32) and a hash (u32) before
# each namespace and key. Namespaces: name, keys {key, source string hash, string index}; the string array: count, then each FString (and from version 2 a reference count).
# Version 0 (no magic): namespaces {name, keys {key, source string hash, FString}}.
LOCRES_MAGIC = bytes.fromhex('0e14747567 4a03fc4a15909dc3377f1b'.replace(' ', ''))

def locres(b):
    """{namespace: {key: localized string}}"""
    r = R(b)
    if b[:16] != LOCRES_MAGIC:   # version 0
        out = {}
        for _ in range(r.take('<I')):
            name = r.fstr(); out[name] = {}
            for _ in range(r.take('<I')): k = r.fstr(); r.take('<I'); out[name][k] = r.fstr()
        return out
    r.raw(16); ver = r.take('<B')
    if ver > 3: raise ValueError(f'.locres version {ver} is not read')
    soff = r.take('<q'); hashed = ver >= 2
    if hashed: r.take('<I')
    ns = []
    for _ in range(r.take('<I')):
        if hashed: r.take('<I')
        name = r.fstr(); keys = []
        for _ in range(r.take('<I')):
            if hashed: r.take('<I')
            k = r.fstr(); r.take('<I'); keys.append((k, r.take('<i')))
        ns.append((name, keys))
    r.o = soff; strs = []
    for _ in range(r.take('<i')):
        strs.append(r.fstr())
        if hashed: r.take('<i')
    return {name: {k: strs[i] for k, i in keys} for name, keys in ns}
