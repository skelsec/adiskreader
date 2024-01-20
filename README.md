# adiskreader
Async Python library to parse local and remote disk images.

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

| Raw | VHD | VHDX | VMDK |   |
|-----|-----|------|------|---|
| ✔️   | ✔️   | ✔️    | ❌    |   |

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

### Parsing a gzipped raw disk image 
`adiskreader-console file+gz:///home/user/images/test.gz.raw`

## Using the API
At the moment there is no documentation, so you'll have to rely on the code itself :(

# Testing
tests require installing guestmount

# Kudos

### FAT
The FAT filesystem operations are provided by a modifyed verson of PyFATFs.
Original project is licensed under MIT, can be found [here](https://github.com/nathanhi/pyfatfs/)  

### Generic info
This C# library is awesome sauce for all things disk reading/writing!
https://github.com/DiscUtils/DiscUtils