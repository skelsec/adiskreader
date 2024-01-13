

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
