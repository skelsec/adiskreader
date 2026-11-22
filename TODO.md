# TODO

Ideas for later, in no particular order.

## Datasources
- NFS: symbolic links pointing to another export of the server are not followed over NFSv3.
- NFS: the files are read with the anonymous stateid over NFSv4, they are not opened: a server with mandatory locks or share reservations may refuse it.
- NFS: the tests against real servers need the docker lab of anfs, the CI does not have it (the kernel server needs the `nfsd` module and a privileged container, NFS-Ganesha alone may do).

## Filesystems
- ext4
- exFAT
- NTFS: encrypted (EFS) files. `tests/make_ntfs_compressed.ps1 -WithEfs` already makes a test file for it.
- NTFS: files backed by a WIM file (WIMBoot), they raise `NotImplementedError` now.

## Backups
- Veeam: decryption of encrypted backups (only the password verifier is extracted now, `get_password_verifier`).
- Veeam: backups of other sources and of older versions (reverse incremental `vrb` files) were not tested.

## Disks
- QCOW2: encrypted disks.

## Packaging
- Release on PyPI, with wheels of the native module (`native/adiskreader_native`).
- A shared Rust crate for the decompressors, to be used by the other projects as well.

## Tests / CI
- A Windows job in the CI workflow.
- A licence file for the [adiskreader-testfiles](https://github.com/skelsec/adiskreader-testfiles) repository.
- The "Task was destroyed but it is pending" log lines of the NTFS tests: an `async for` over `read_sequential_clusters` is left before it ends.
