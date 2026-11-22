# TODO

Ideas for later, in no particular order.

## Datasources
- NFS: the datasource was tested with the server of the tests only (`tests/nfs_server.py`), run the tests against real servers (`ADISKREADER_NFS_URL`).
- NFS: the MOUNT service must be on the port of the url when the url has one.
- NFS: symbolic links pointing to another export of the server are not followed.
- anfs imports `xdrlib`, which is not part of Python since 3.13: adiskreader installs the `standard-xdrlib` backport for it, anfs should not need it.

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
