

# https://fejlesztek.hu/create-a-fat-file-system-image-on-linux/
dd if=/dev/zero of=fat32_test.img count=50 bs=1M

```
fdisk test.img

Command (m for help): o
Building a new DOS disklabel with disk identifier 0x46ac6035.

Command (m for help): n
Partition type:
  p primary (0 primary, 0 extended, 4 free)
  e extended
Select (default p): <Enter>
Using default response p
Partition number (1-4, default 1): <Enter>
First sector (2048-99999, default 2048):
Using default value 2048
Last sector, +sectors or +size{K,M,G} (2048-99999, default 99999): <Enter>
Using default value 99999
Partition 1 of type Linux and of size 47.8 MiB is set

Command (m for help): t
Selected partition 1
Hex code (type L to list all codes): c

Changed type of partition 'Linux' to 'W95 FAT32 (LBA)'

Command (m for help): w
The partition table has been altered!

Syncing disks
```

```
mkfs.vfat test.img
```



FOR NTFS:
https://gist.github.com/kumbasar/49906cb704ce9213c972a3e008c74c0c
```
#!/bin/bash

set -x

image="test.img"
label="test"
mntdir=`mktemp -d`

sudo dd status=progress if=/dev/zero of=$image bs=6M count=1000 && sync
echo 'type=7' | sudo sfdisk $image

LOOPMOUNT=`sudo losetup --partscan --show --find ${image}`
echo $LOOPMOUNT
sudo mkfs.ntfs -Q -v -F -L ${label} ${LOOPMOUNT}p1
sudo mount ${LOOPMOUNT}p1 ${mntdir}
# Now you can put some files to ${mntdir}
sudo umount ${mntdir}
sudo losetup -d ${LOOPMOUNT}
```


VHDX (converted from the raw NTFS image):
```
qemu-img convert -f raw -O vhdx -o subformat=dynamic ntfs_test.img ntfs_test_dynamic.vhdx
qemu-img convert -f raw -O vhdx -o subformat=fixed ntfs_test.img ntfs_test_fixed.vhdx
```

VMDK:
The vmdk images are converted from the raw ntfs image, the snapshots are written with qemu-io (the writes are listed in `config.py`):
```
qemu-img convert -f raw -O vmdk -o subformat=monolithicSparse ntfs_test.img ntfs_test_monolithicSparse.vmdk
qemu-img convert -f raw -O vmdk -o subformat=streamOptimized ntfs_test.img ntfs_test_streamOptimized.vmdk
qemu-img create -f vmdk -b ntfs_test_monolithicSparse.vmdk -F vmdk ntfs_test_snapshot.vmdk
qemu-io -f vmdk -c "write -P 0xA5 0x200000 0x30000" -c "write -P 0x5A 0x2fffe00 0x1200" -c "write -P 0x77 0x10001 0x1ff" ntfs_test_snapshot.vmdk
qemu-img create -f vmdk -b ntfs_test_snapshot.vmdk -F vmdk ntfs_test_snapshot2.vmdk
qemu-io -f vmdk -c "write -P 0x11 0x210000 0x10000" -c "write -z 0x400000 0x100000" ntfs_test_snapshot2.vmdk
```
The `ntfs_test_vmware*` images are made by VMware Workstation, `t.vmx` is a minimal VM with `ntfs_test_vmware.vmdk` as its only disk:
```
vmware-vdiskmanager -r built_flat.vmdk -t 0 ntfs_test_vmware.vmdk
vmrun -T ws snapshot t.vmx snap1    # creates ntfs_test_vmware-000001.vmdk, then the same qemu-io writes as above
ovftool t.vmx t.ova                 # before the snapshot, ntfs_test_vmware_ova.vmdk is the disk in the ova
```
The disks qemu-img can not create (multiple extents, ESXi snapshots, stream optimized with footer) are built by `vmdk_builder.py` when the tests run.
If qemu-img is installed, `test_vmdk.py` also checks that qemu reads the same data from them.

Differencing VHD / VHDX:
The chains (base, two children and one more child in a subdirectory, for dynamic and fixed bases) are made by Windows.
Run `make_diff_vhd.ps1` in an elevated PowerShell on a Windows host and copy the zip it creates to `testfiles/adiskreader_diff_fixtures.zip`.
The zip holds the disks, what Windows reads from each of them and a manifest; the tests extract it to `testfiles/diff` (about 820 MB).

MANIFESTS:
The tests compare the images against the expected contents stored in `manifests/`.
After changing an image, regenerate them (needs 7z, no root):
```
python make_manifest.py
```

SMB and SSH:
`test_smb.py` and `test_ssh.py` are skipped unless `ADISKREADER_SMB_URL` / `ADISKREADER_SSH_URL` point to a share / directory holding the decompressed test images.
To use a local samba and sshd container (needs docker, listens on 127.0.0.1:4445 and 127.0.0.1:2222 only):
```
eval "$(smb/start.sh)"
pytest test_smb.py test_ssh.py
docker stop adiskreader-smb
```
