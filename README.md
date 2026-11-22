![Supported Python versions](https://img.shields.io/badge/python-3.10+-blue.svg) [![Documentation Status](https://readthedocs.org/projects/msldap/badge/?version=latest)](https://msldap.readthedocs.io/en/latest/?badge=latest) [![Twitter](https://img.shields.io/twitter/follow/skelsec?label=skelsec&style=social)](https://twitter.com/intent/follow?screen_name=skelsec)

## :triangular_flag_on_post: Sponsors

If you like this project, consider purchasing licenses of [OctoPwn](https://octopwn.com/), our full pentesting suite that runs in your browser!  
For notifications on new builds/releases and other info, hop on to our [Discord](https://discord.gg/PM8utcNxMS)

# adiskreader
Async Python library to parse local and remote disk images.

## :triangular_flag_on_post: Runs in the browser

This project, alongside with many other pentester tools runs in the browser with the power of OctoPwn!  
Check out the community version at [OctoPwn - Live](https://live.octopwn.com/)

# Description
*The project is still in developement, expect issues*

This is a library with a simple API to read (only read) and recover files from disk images of virtual machines (VHD, VHDX, VMDK, QCOW2, VDI), raw disks/partitions obtained by `dd` or similar command, Windows images (WIM, ESD), optical disc images (ISO) and Veeam backups. Opening a file on the disk image provides an async fileobject so you can perform file operations like `read` `seek` `tell` without extracting the entire file.  
The image does not need to be on your machine: it is read in parts over SMB, SSH or NFS as well, only the blocks needed are fetched.  
Everything is written in Python, without the tools or libraries of the vendors and without mounting anything. It comes with an example console client to demonstrate the core features.

# Features

## Datasource

| File | Gzip | SMB | SSH(SFTP) | NFS (v3, v4) | File inside another image |
|------|------|-----|-----------|-------|---------------------------|
| ✔️    | ✔️    | ✔️   | ✔️         | ✔️     | ✔️                         |

Disks made of several files (snapshots, differencing disks, split extents, backup chains) find their other files next to the one opened, on all of the datasources.

## Disk

| Raw | VHD | VHDX | VMDK | QCOW2 | VDI |
|-----|-----|------|------|-------|-----|
| ✔️   | ✔️   | ✔️    | ✔️    | ✔️     | ✔️   |

The type of the disk is taken from the URL scheme or the extension of the file, then from the signature in the file.  
Differencing VHD / VHDX disks, VMDK snapshots and QCOW2 disks with backing files are read together with their parents.  
VHD: fixed, dynamic and differencing disks.  
VHDX: fixed, dynamic and differencing disks. A disk which was not closed cleanly (copied from a running machine, for example) has a log, it is replayed in memory when the disk is opened, the file is not modified.  
VMDK: descriptor files with any number of flat, sparse or zero extents, monolithic and split disks, stream optimized disks (OVA exports), and the snapshot formats of ESXi (vmfsSparse, seSparse).  
QCOW2: version 2 and 3, compressed clusters, extended L2 entries, external data files. The snapshots stored in the disk are opened with `open_snapshot`. Encrypted disks are not supported.  
VDI: the differencing disks do not store the path of their parent, open the files of the chain with `VDIDisk.from_chain`.  
Disk images stored inside another image can be opened with `FSFileSource`.

## Archive

| WIM (uncompressed, XPRESS, LZX, LZMS) | Split WIM (SWM) | ESD (LZMS, solid) |
|---------------------------------------|-----------------|-------------------|
| ✔️                                     | ✔️               | ✔️                 |

ESD: the solid resources are decompressed in chunks of up to 64 MB, reading a file needs the whole chunk it is stored in. Encrypted ESD files are not supported.

## Backup

| Veeam full backup (VBK) | Veeam incremental backup (VIB) | Veeam backup chain (VBM) |
|-------------------------|--------------------------------|--------------------------|
| ✔️                       | ✔️                              | ✔️                        |

The disks of the backed up machines are read from the backup files of Veeam Backup & Replication, with all of its compression levels (none, dedupe-friendly, optimal, high, extreme) and block sizes.  
An incremental backup needs the backups before it: open the `vbm` file stored next to them (`VBK.from_datasource`, the latest restore point by default) or give the files of the chain (`VBK.from_datasources`).  
Tested with the backups of Hyper-V machines made by Veeam 13. Backups of other sources and of older versions (reverse incremental `vrb` files among them) were not tested.  
Encrypted backups are not supported. `get_password_verifier` gives the data the password is checked with and the key derivation, in the format of hashcat mode 31200. The mode is PBKDF2-HMAC-SHA1 with 10000 iterations as older Veeam versions do it, Veeam 13 uses PBKDF2-HMAC-SHA256 with 600000 iterations.

## Optical disc image (iso)

| ISO9660 | Joliet | Rock Ridge | UDF 1.02 - 2.60 |
|---------|--------|------------|-----------------|
| ✔️       | ✔️      | ✔️          | ✔️               |

UDF: physical, virtual (VAT), sparable and metadata partitions are read.  
An image usually describes its files with more than one of these, each of them can be mounted.

## Partition

| MBR | GPT |
|-----|-----|
| ✔️   | ✔️   |

## Filesystem

| FAT12 | FAT16 | FAT32 | VFAT | NTFS | EXT4 | EXFAT  |
|-------|-------|-------|------|------|------|--------|
| ✔️     | ✔️     | ✔️     | ✔️    | ✔️    | ❌    | ❌     |

NTFS: compressed (LZNT1) and sparse files are read, alternate data streams can be opened. Files compressed by the newer "system compression" (CompactOS, `compact /exe`: XPRESS4K, XPRESS8K, XPRESS16K, LZX) are read as well, the ones backed by a WIM file (WIMBoot) are not. Encrypted (EFS) files are not supported.

# Install
Python 3.10 or newer is needed. `git clone` and `pip install .` should do the trick.  
After install use the `adiskreader-console` executable for the sample client.  
The optional [native decompressors](#native-decompressors) make reading the compressed formats (WIM, ESD, Veeam backups, compressed NTFS files) much faster.

# Usage

## `adiskreader-console`
Interactive sample client that tries to automatically "mount" the disk image specified by the source URL.  
The type of the image is detected, it can be given in the scheme of the URL as well (`+raw`, `+vhd`, `+vhdx`, `+vmdk`, `+qcow2`, `+vdi`).

### Parsing a VHDX file over SMB
`adiskreader-console smb+ntlm-password://TEST\victim@10.10.10.2/sharename/foldername/disk.vhdx`  

### Parsing a VHD file over SSH
`adiskreader-console ssh+password+vhd://test:test@10.10.10.3/mnt/hgfs/vhdxtest/17763.737.amd64fre.rs5_release_svc_refresh.190906-2324_server_serverdatacentereval_en-us_1.vhd`  

### Parsing a VHDX file over NFS
`adiskreader-console nfs://10.10.10.4/srv/exports/images/disk.vhdx`  
The path is the export followed by the path of the file in it. Symbolic links are followed inside of the export.  
The connection is made by [anfs](https://github.com/skelsec/anfs), the URL takes its parameters:  
- the server gets the uid and gid 0 (AUTH_SYS), others can be set: `nfs://10.10.10.4/srv/exports/images/disk.vhdx?uid=1000&gid=1000`
- Kerberos: `nfs+kerberos-password://REALM\user:password@server/srv/exports/images/disk.vhdx?dc=<ip of the KDC>`
- the port in the URL is the one of the NFS service, `mountport` is the one of the mount service: the portmapper is asked for the ones not given
- `privport=1` for the servers which only accept clients connecting from a port below 1024 (needs the rights to use one)

NFSv3 is used by default. With `version=4` (or `4.0`, `4.1`, `4.2`) it is NFSv4: no mount service and no portmapper is needed, and the path is the one in the namespace of the server, which may differ from the path of the export: `nfs://10.10.10.4/images/disk.vhdx?version=4`  
Absolute symbolic links are followed inside the export of the file only, with NFSv4 they are taken as paths in the namespace of the server.

### Parsing a local VHDX file
`adiskreader-console file://C:\Users\test\images\test.vhdx`

### Browsing a WIM file
`adiskreader-console file:///home/user/images/install.wim`  
The images of the WIM file take the place of the partitions, `mount <index>` selects one.

### Browsing a Veeam backup
`adiskreader-console file:///home/user/backups/job/job.vbm`  
The partitions of all the disks in the latest restore point are listed, `mount <index>` selects one. A `vbk` file can be opened on its own.

### Browsing an ISO file
`adiskreader-console file:///home/user/images/install.iso`  
The filesystems of the image (UDF, ISO9660 with Rock Ridge, Joliet) take the place of the partitions, the most descriptive one is mounted, `mount <index>` selects another.

### Parsing a gzipped raw disk image 
`adiskreader-console file+gz:///home/user/images/test.gz.raw`

## Native decompressors
The XPRESS, LZX, LZMS, LZ4, LZNT1 and Veeam RLE decompressors and the CRC32C checksum are written in Python, the optional `adiskreader_native` module (Rust) has the same ones much faster (about 50 times for the first three). The checksums of the Veeam backup files are verified only when the module is installed, unless asked for.
It is used when it is installed, nothing else needs to be changed:  
`pip install ./native/adiskreader_native` (needs a Rust toolchain)  
Setting `ADISKREADER_NO_NATIVE=1` turns it off.

## Using the API
At the moment there is no documentation, so you'll have to rely on the code itself :(  
The console client (`adiskreader/examples/console.py`) and the tests show most of it. The short version:

```python
import asyncio
from adiskreader.datasource import DataSource
from adiskreader.disks import Disk

async def main():
    ds = await DataSource.from_url('file:///home/user/images/test.vhdx')
    disk = await Disk.from_datasource(ds)
    partitions = await disk.list_partitions()
    fs = await partitions[0].mount()

    async for root, dirs, files in fs.walk():
        print(root, dirs, files)

    file = await fs.open('Windows\\System32\\config\\SAM')
    await file.seek(0, 0)
    data = await file.read(4096)

asyncio.run(main())
```

WIM files, ISO images and Veeam backups are opened with their own classes, the rest is the same:

```python
from adiskreader.filesystems.wim import WIM
from adiskreader.filesystems.iso import ISOImage
from adiskreader.backups.vbk import VBK

backup = await VBK.from_datasource(ds) # ds is the vbm file, or a vbk file
partitions = await backup.list_partitions()
```

A disk image stored in a filesystem of another image is a datasource too:

```python
from adiskreader.datasource.fsfile import FSFileSource

inner_ds = await FSFileSource.from_filesystem(fs, '\\backups\\disk.vhd')
inner_disk = await Disk.from_datasource(inner_ds)
```

# Testing
`pip install pytest pytest-asyncio` then `python3 -m pytest tests`

The tests read test images with known contents: disks and archives made by Windows, qemu, VirtualBox, VMware and Veeam (the scripts making them are in `tests`), and compare what is read with what those tools read from them. Nothing is mounted and no root rights are needed. Some of the tests use `7z` and `qemu-img` when they are installed, the SMB and SSH tests need a server (`ADISKREADER_SMB_URL`, `ADISKREADER_SSH_URL`, see `tests/smb`). The NFS tests have their own small server (`tests/nfs_server.py`), they are run against a real one as well when `ADISKREADER_NFS_URL` is set. The `test_nfs_lab` tests (NFSv4, Kerberos, real servers) need the docker lab of [anfs](https://github.com/skelsec/anfs) (its `lab` folder), they are skipped without it.  
They are run for every pull request (`.github/workflows/tests.yaml`), with and without the native module.

The large test files are not kept in this repository: the tests download the ones they need (about 90 MB in total) from the releases of [adiskreader-testfiles](https://github.com/skelsec/adiskreader-testfiles) to `tests/testfiles` and check their SHA-256.  
Set `ADISKREADER_TEST_OFFLINE=1` to skip the tests of the files which are not there instead of downloading them.

# Kudos

### FAT
The FAT filesystem operations are provided by a modifyed verson of PyFATFs.
Original project is licensed under MIT, can be found [here](https://github.com/nathanhi/pyfatfs/)  

### Generic info
This C# library is awesome sauce for all things disk reading/writing!
https://github.com/DiscUtils/DiscUtils