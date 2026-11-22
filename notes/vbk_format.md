# VBK / VIB / VRB (Veeam backup storage) format notes

Status: started as what a third-party repair tool assumes about the format, then checked against
files written by Veeam Backup & Replication 13.1.2.57 (Hyper-V job and VeeamZIP, format version 13,
see section 13). **[V]** marks what those files confirm; everything else is still the tool's
assumption. Items marked (?) are guesses. Older format versions (v1 vectors, 44/30/37-byte records,
`.vrb`) are not covered by any fixture.

All integers are little endian. `PAGE` = 4096. A page id of -1 means "none".

## 1. File layout

| Offset | Content |
|---|---|
| 0 | storage header (one page) |
| 4096 | metadata slot, copy 1 |
| 4096 + slot size | metadata slot, copy 2 |
| elsewhere | metadata banks and data blocks, located through the slots |

The same container is used for `.vbk` (full), `.vib` (incremental) and `.vrb` (reverse incremental).

## 2. Storage header (offset 0)

| Offset | Type | Field |
|---|---|---|
| 0 | u32 | format version. Seen values 5..64 in the low 16 bits; `0x10008` is treated as a variant of 7/8. Veeam 13 writes 13 [V] |
| 4 | u32 | initialized flag. 0 = the session never finished ("Storage not initialized") |
| 8 | u32 | length of the digest type name (< 64) |
| 12 | chars | digest type name, ASCII (`md5`) |
| 263 | u32 | slot format (9 in Veeam 13) |
| 267 | u32 | block size of the stored files: 262144, 524288, 1048576, 4194304 or 8388608 [V for the first four] |

Derived flags:
- "v7 records" (longer records, with key set id): version 7, `0x10008`, or >= 9
- "metadata vector v2": version >= 12 and not `0x10008`

Assumed defaults when the header is lost: version 9 or 13, block size 1 MiB.

## 3. Metadata slot

Two copies; the valid one with the highest `version` counter is the current one. In the fixtures
both copies are valid and equal, except that **each copy points to its own copy of the banks**
(different offsets, same checksums) [V].

| Offset | Type | Field |
|---|---|---|
| 0 | u32 | checksum of bytes 4 .. 124 + max_banks*16; CRC32C in Veeam 13 [V] (the tool also accepts zlib CRC32) |
| 4 | u32 | always 1 |
| 8 | u64 | version (generation counter) |
| 16 | u64 | storage EOF (expected file size) |
| 24 | u32 | (unknown; equals the bank count in the fixtures) |
| 28 | i64 | directory root page id |
| 36 | u64 | directory record count |
| 44 | i64 | block store root page id |
| 52 | u64 | block store record count |
| 60 | ... | (unknown, 56 bytes) |
| 92 | i64 | -1 in plain files; a page id in the encrypted fixture (key store?). Used to detect encryption (?) |
| 116 | u32 | max_banks (<= 32512; typical 248, 2976, 32512) |
| 120 | u32 | bank count (<= max_banks) |
| 124 | 16 * n | bank references |

Bank reference: `u32 CRC32C of the whole bank`, `i64 file offset`, `u32 size in bytes` [V].

Slot size = `124 + max_banks*16` rounded up to a page (the tool computes it from 116, same result
for the typical values). The second copy is at `4096 + slot size` [V: max_banks 32512, second
copy at 528384].

## 4. Banks and pages

A bank is a run of pages. Its first page is a header:
- u16 @0: number of pages in the bank (power of two, >= 16); bits 17 and up are zero
- from @4: one byte per page, 0 or 1 (free/used) (?)

Page `i` of the bank is at `bank.offset + PAGE * (i + 1)` [V]. Banks of 0x20 and 0x40 pages seen.

Page id (i64) = `(bank index << 32) | page index`.

## 5. Metadata vectors (arrays of fixed-size records)

A vector is given by (root page id, record count, record size).

### v1 (older formats)
Linked list of pages. Each page: `i64 next page id` @0, records from @8.
Records per page = `4088 // record size`.

### v2 (format version >= 12)
The root is a linked list of *table* pages (`i64 next` @0, and the page's own id @8 in the cases
the tool checks). All table pages are read as 512 i64 values each and concatenated; the n-th data
page of the vector is found at this index of the concatenation:
- n < 510: `n + 2`
- otherwise: walk table pages j = 1, 2, 3, ... with capacities 508, 511, 511, 508, 511, 511, ...
  (`[511, 508, 511][j % 3]`), entry = `j*512 + (512 - capacity) + remaining`

The first case is [V] (directory, block store and block tables of all fixtures). The fixtures are
too small to reach the second case; the capacities stay unverified (?).
Data pages hold `4096 // record size` records, starting at offset 0, no header.

## 6. Directory

Vector of 192-byte records from the slot's directory root. Nesting up to 8 levels. Layout [V] for
types 1, 3 and 5.

| Offset | Type | Field |
|---|---|---|
| 0 | u32 | type: 1 folder, 2 external file, 3 file, 4 patch, 5 increment |
| 4 | u32 | name length (<= 128) |
| 8 | chars | name, UTF-8 |
| 136 | i64 | properties page id |
| folder: 148 | i64 | root page id of the children vector |
| folder: 156 | u32 | number of children |
| file/increment: 152 | i64 | root page id of the block table |
| file/increment: 160 | u64 | number of blocks (file) or of changed blocks (increment) |
| file/increment: 168 | u64 | file size in bytes |

Top level: one folder per machine (name holds `vm-123` or a GUID). Inside: `summary.xml`,
`*.vmx`, `*.nvram`, descriptor `*.vmdk`, the disk as `*-flat.vmdk` (VMware) or `*.vhd(x)`/`*.avhdx`
(Hyper-V), `BackupComponents.xml` (agent backups?).

Hyper-V backup made by Veeam 13 [V]: one top folder `<guid> (<vm guid>)` with `summary.xml`,
`config.wmi`, a folder `Config` (`.vmcx`, `.vmrs`, `.vmgs`), one folder per controller slot
(`Scsi0-0`, `Scsi0-1`) holding `disk1.vhdx` / `disk2.vhdx`, and files named `FsAwareMeta:<guid>:<hex>`.
**The entry called `diskN.vhdx` is the raw content of the virtual disk** (its size is the virtual
size), not a VHDX file; Veeam writes a new VHDX around it on restore.

`summary.xml` attributes used: `OIB/@VmName`, `Backup/@JobName`, `Point|Backup|OIB/@CreationTime`
(`MM/dd/yyyy HH:mm:ss`), `OIB/@IsCorrupted`, `OIB/@IsConsistent`, `Backup/@EncryptionState` (0 = not encrypted).

## 7. Block table of a file (type 3)

Two levels.

Level 1: vector at the file's root, record size 24, `ceil(blocks / 1088)` records:
- i64 @0: root page id of the level 2 vector, -1 = all 1088 blocks of this range are sparse
- u64 @8: block size (?)
- i64 @16: number of records in the level 2 vector [V]

Level 2: vector of block references, record size 46 (v7 records) or 30:

| Offset | Type | Field |
|---|---|---|
| 0 | u32 | block size (same as in the header) |
| 4 | u8 | type: 0x10 = stored, 0x01 = zero block [V]. (The tool's "0 = stored" is wrong for version 13.) |
| 5 | 16 bytes | digest, equal to the one in the block store record [V]. For zero blocks: MD5 of `block size` zero bytes |
| 21 | u64 | block id (index into the block store) |
| 29 | ... | (unknown; 16 bytes more in v7 records) |

Block `n` of the file covers bytes `n * block size ...`. The last block can be short.
Small files (configs) are stored as one short block.

## 8. Block table of an increment (type 5)

One vector at the root, one record per *changed* block, record size 53 (v7) or 37:
the block reference as above, plus `u64 @29`: index of the block in the file [V].

## 9. Block store

Vector from the slot's store root, record size 60 (v7) or 44. Index = block id.

| Offset | Type | Field |
|---|---|---|
| 0 | u8 | 4 in all records |
| 1 | u32 | reference count (?): 0 in empty records, mostly 1, 3 seen |
| 5 | i64 | file offset of the stored block |
| 13 | u32 | allocated size |
| 17 | u8 | 1 for deduplicated disk blocks, 0 for small files and with inline deduplication off |
| 18 | 16 bytes | digest of the block (all zero = empty record). **Not the plain MD5** of the stored or of the uncompressed bytes, although the header says `md5`; the same content has the same digest in every file |
| 34 | i8 | compression: -1 none, 2 RLE, 7 LZ4, 8 and 9 zstd [V]; 3 zlib high, 4 zlib low (tool, not seen) |
| 35 | u8 | 1 in used records |
| 36 | u32 | stored (compressed) size |
| 40 | u32 | source size (<= 64 MiB) |
| 44 | 16 bytes | v7 only: key set id, non-zero = block is encrypted |

The block store is shared by all the files of the storage (deduplication).

## 10. Stored blocks

What the job's compression level writes in Veeam 13 [V]:

| Level (PowerShell value) | Id | Stored block |
|---|---|---|
| None (0) | -1 | the raw data |
| Dedupe-friendly (4) | 2 | `u32 source size`, then a byte-wise RLE stream |
| Optimal (5, default) | 7 | 12-byte header, then one raw LZ4 block (no frame): `u32 magic 0xF800000F`, `u32 CRC32C of the uncompressed data`, `u32 source size` |
| High (6) | 8 | a zstd frame (magic `28 b5 2f fd`), no extra header |
| Extreme (9) | 9 | a zstd frame, no extra header |

RLE: bytes are copied as they are; when two equal bytes follow each other, the next byte is the
total length of the run (2..255), the pair included. Longer runs repeat the triple
(`00 00 ff 00 00 ff ...`).

3, 4: not handled by the tool, not written by Veeam 13; layout unknown.

The tool verifies the data by MD5 against the block store record; that does not hold for Veeam 13
(see the digest remark above). For LZ4 the CRC32C in the block header is usable.

Veeam leaves out blocks of deleted files: the area of a file deleted before the run reads as zeros
from the backup while the source disk still has the stale data [V]. A reader cannot be compared
with the source disk byte for byte outside of allocated file data.

## 11. Chains

A restore point = the full (`.vbk`) plus the later increments, ordered by `CreationTime`.
For a disk: start with the block table of the file in the full, then for each later storage look up
the same folder/file name: a type 3 entry replaces the whole map, a type 5 entry replaces the listed
blocks (and can change the disk size). Each block is read from the storage that owns it.
[V] for full + 2 increments and for full, increment, active full.
Reverse incrementals (`.vrb`) apply in the other direction, from the full backwards (not analysed).
Veeam 13 no longer creates them, nor single-file (not per-machine) backups.

The `.vbm` next to the backup files is XML metadata of the chain, rewritten on every run. It is not
needed to read a storage. VeeamZIP writes none.

## 12. Not known

- Encryption (key sets, encrypted metadata). The tool refuses these files. In the encrypted
  fixture the header and slots are plain and valid, there is one more bank, most banks are
  encrypted pages, and slot offset 92 holds a page id instead of -1.
- Compression 3/4 block layout.
- The digest algorithm of the blocks.
- Types 2 (external file) and 4 (patch).
- Properties pages.
- Per-machine storages / `.vbm`, tape and object-storage layouts.

## 12a. Password verifier of encrypted storages

Source: hashcat issue 3623 (mode 31200), checked against the encrypted fixture.

Find the 12 bytes `60 00 00 00 10 00 00 00 40 00 00 00` (three u32 lengths). They are followed by
a 96-byte blob (unknown), a 16-byte ciphertext and a 64-byte salt. In the fixture the structure sits
twice, in the one bank that is not encrypted.

Check of a password: `PBKDF2(password as UTF-16LE, salt, iterations, 48 bytes)` gives the AES-256
key (32 bytes) and the CBC IV (16 bytes); the decrypted ciphertext is 4 bytes of data and twelve
`0c` padding bytes.

- per the issue (files of 2023): HMAC-SHA1, 10000 iterations; hash line `$vbk$*salt*10000*ct`
- Veeam 13.1 fixture [V]: **HMAC-SHA256, 600000 iterations**. Mode 31200 does not fit these files.

What tells the two generations apart inside the file is not known (?).

## 13. Fixtures used for the check

Made by `make_vbk.ps1` on Windows 11 with Veeam Backup & Replication 13.1.2.57 (Community), source:
a powered-off Hyper-V VM with a 512 MB GPT/NTFS disk and a 256 MB MBR/FAT32 disk.

| Scenario | Checked |
|---|---|
| `chain_default` (full + 2 `.vib`), `chain_active_full` | every restore point equals the source disks, except the zeroed area of the deleted file |
| `comp_none`, `block_wan` (256K), `block_lan` (512K), `block_large` (4M), `dedup_off`, `zip` | both disks equal the source |
| `comp_dedupe` | RLE blocks equal the blocks of `comp_none` |
| `comp_high`, `comp_extreme` | both disks equal the source (zstd via the `zstandard` package) |
| `encrypted` | header, slots and the password verifier |

Still open: vector v2 beyond 510 data pages, formats older than 13, `.vrb`, VMware and agent
backups, block sizes above 4 MiB.

## 12b. Telling the key derivation of the password verifier

The sample of the hashcat issue (`backup_password_hashcat.vbk`, password "hashcat", format version 13 as well, made in 2023)
and the Veeam 13.1 fixture have the same layout around the verifier. The record before the marker
(`.. e6 00 00 00 .. 01 00 00 00 10 00 00 00 <16 byte key id> c6 00 00 00 <6 bytes> <4 zero bytes>` then the marker)
differs in one byte, 8 bytes before the marker: 0 in the 2023 file (PBKDF2-HMAC-SHA1, 10000), 2 in the 13.1 file
(PBKDF2-HMAC-SHA256, 600000). One file of each, so this is a guess; the reader tries both derivations when a password is checked.
The marker is found twice in both files, the second one is outside of the banks of the valid slot (an older copy of the bank).
