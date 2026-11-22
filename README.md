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

This is a library with a simple API to read (only read) and recover files from disk images (vhdx) or raw disks/partitions obtained by `dd` or similar command. Opening a file on the disk image provides an async fileobject so you can perform file operations like `read` `seek` `tell` without extracting the entire file.  
It comes with an example console client to demonstrate the core features.

# Features

## Datasource

| File | Gzip | SMB | SSH(SFTP) |   |
|------|------|-----|-----------|---|
| ✔️    | ✔️    | ✔️   | ✔️         |   |

## Disk

| Raw | VHD | VHDX | VMDK | QCOW2 | VDI |
|-----|-----|------|------|-------|-----|
| ✔️   | ✔️   | ✔️    | ✔️    | ✔️     | ✔️   |

Differencing VHD / VHDX disks, VMDK snapshots and QCOW2 disks with backing files are read together with their parents.  
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

# Install
`git clone` and `pip install .` should do the trick.  
After install use the `adiskreader-console` executable for the sample client

# Usage

## `adiskreader-console`
Interactive sample client that tries to automatically "mount" the disk image specified by the source URL like:

### Parsing a VHDX file over SMB
`adiskreader-console smb+ntlm-password://TEST\victim@10.10.10.2/sharename/foldername/disk.vhdx`  

### Parsing a VHD file over SSH
`adiskreader-console ssh+password+vhd://test:test@10.10.10.3/mnt/hgfs/vhdxtest/17763.737.amd64fre.rs5_release_svc_refresh.190906-2324_server_serverdatacentereval_en-us_1.vhd`  

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
The XPRESS, LZX, LZMS, LZ4 and Veeam RLE decompressors and the CRC32C checksum are written in Python, the optional `adiskreader_native` module (Rust) has the same ones much faster (about 50 times for the first three). The checksums of the Veeam backup files are verified only when the module is installed, unless asked for.
It is used when it is installed, nothing else needs to be changed:  
`pip install ./native/adiskreader_native` (needs a Rust toolchain)  
Setting `ADISKREADER_NO_NATIVE=1` turns it off.

## Using the API
At the moment there is no documentation, so you'll have to rely on the code itself :(

# Testing
`python3 -m pytest tests`

The large test files are not kept in this repository: the tests download the ones they need (about 90 MB in total) from the releases of [adiskreader-testfiles](https://github.com/skelsec/adiskreader-testfiles) to `tests/testfiles` and check their SHA-256.  
Set `ADISKREADER_TEST_OFFLINE=1` to skip the tests of the files which are not there instead of downloading them.

# Kudos

### FAT
The FAT filesystem operations are provided by a modifyed verson of PyFATFs.
Original project is licensed under MIT, can be found [here](https://github.com/nathanhi/pyfatfs/)  

### Generic info
This C# library is awesome sauce for all things disk reading/writing!
https://github.com/DiscUtils/DiscUtils