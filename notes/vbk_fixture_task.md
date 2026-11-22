# Task: create Veeam backup test files (VBK/VIB/VRB fixtures)

You are running on a Windows 11 lab VM where Veeam Backup & Replication 13 (free Community
Edition) has just been installed. This file is the whole briefing; the person who gave it to you
is the owner of the lab and can answer questions, enable things on the hypervisor and reboot the VM.

## Why

`adiskreader` is an open-source pure-Python library that reads disk images and archives (VHDX, VMDK,
QCOW2, WIM, ...) without the vendor's software. We want to add a reader for Veeam backup files.
There is no public specification. We have a set of *unverified* notes on the format, and need real
files written by Veeam, with known content, to check the notes against and to use as test fixtures
later. The reader is developed on another machine; your job ends with a zip of files and a report.

The fixtures get committed to a git repository, so they have to be **small**: aim for under 60 MB
for the whole zip, and keep every single backup file as small as the scenario allows.

## What a good result looks like

A folder `vbk_fixtures\` (zipped at the end) with:

1. the backup files of each scenario below, every file Veeam wrote for it (`.vbk`, `.vib`, `.vrb`,
   `.vbm`, anything else), in one subfolder per scenario
2. `manifest.json` describing each scenario (see "Manifest")
3. ground truth to compare a reader against (see "Ground truth")
4. `make_vbk.ps1`: the script that produced all this, rerunnable on a fresh VM with the same setup
5. `report.md`: what you did, what did not work, and anything you noticed that was unexpected

## The source to back up

Preferred: a tiny Hyper-V VM on this same machine.

- Nested virtualization has to be exposed to this VM by the hypervisor under it. Check first
  (`Get-CimInstance Win32_Processor` → `VirtualizationFirmwareEnabled`, or `systeminfo`). If it is
  off, stop and ask the owner to enable it.
- Hyper-V needs Windows 11 Pro/Enterprise/Education. Enabling the feature needs a reboot: tell the
  owner before you do it, and make the script able to continue after the reboot.
- Whether Veeam 13 accepts Windows 11 client Hyper-V as a managed host is **not confirmed**. Try to
  add the local machine as a standalone Hyper-V host. If Veeam refuses, say so in the report and
  use the fallback.
- The VM does not need an operating system and never has to boot. Create it powered off, generation
  2, minimal memory, with data disks only.
- Disks: prepare them on the host (create a dynamic VHDX, mount, partition, format, write files,
  dismount), then attach them to the VM.
  - disk 1: 512 MB, GPT, one NTFS volume
  - disk 2: 256 MB, MBR, one FAT32 volume (shows how several disks of one VM are stored)

Fallback, if Hyper-V cannot be used: a Veeam-managed agent backup job (volume level) of a small
volume on this VM, e.g. a mounted 512 MB VHDX. Same content, same scenarios as far as they apply.

### Content of the disks

Deterministic, so the script can recreate it (seeded generator, no timestamps in the data):

- several small text files, some of them in nested folders, one with a non-ASCII name
- one well-compressible file of about 6 MB (repeating but not constant text)
- one file of pseudo-random bytes of about 3 MB (does not compress, crosses block boundaries)
- one 4 MB file of zero bytes, and one sparse file
- one file of about 2.5 MB whose every 4 KiB chunk starts with its own chunk number (to see
  misplaced blocks at once)

Keep the incompressible data small: it is what makes the fixtures big.

### Changes for the incremental runs

Between runs, mount the disk on the host again and make changes that are easy to tell apart:

- run 2: modify the middle of the random file, append to a text file, add a new 1 MB file
- run 3: delete one file, add a folder with files, overwrite the first 64 KiB of the compressible file

Changed block tracking with a powered-off VM whose disk was modified outside of it may misbehave.
If an incremental comes out empty or as large as a full, note it in the report, and try once more
with change tracking disabled in the job.

## Scenarios

Each one in its own repository folder or job so the files do not mix. Names in `code` are the
subfolder names to use.

| Name | What |
|---|---|
| `chain_default` | forward incremental chain: full + 2 incrementals, default settings (compression Optimal, storage optimization "Local target" / 1 MB blocks) |
| `chain_reverse` | reverse incremental: 3 runs, gives a `.vbk` and `.vrb` files |
| `comp_none` | one full, compression None |
| `comp_dedupe` | one full, compression Dedupe-friendly |
| `comp_high` | one full, compression High |
| `comp_extreme` | one full, compression Extreme |
| `block_wan` | one full, smallest block size offered (WAN target) |
| `block_lan` | one full, LAN target |
| `block_large` | one full, largest block size offered |
| `encrypted` | one full, default settings, with a job password |
| `zip` | one VeeamZIP of the VM (`Start-VBRZip`), default settings |

Nice to have if they cost little: a chain with a synthetic or active full after the incrementals;
a full made with "per-machine backup files" switched the other way than the default; a backup with
inline deduplication disabled.

If the Community Edition does not offer a setting, skip the scenario and write that down. Do not
install trial licence keys or change licensing.

For `encrypted`, generate a throwaway password in the script and record it in the manifest. It
protects test data only. Do not use any real password of the lab for it.

## Ground truth

For every restore point (each full and each incremental run), record what the disks contained at
that moment:

- a copy of the source `.vhdx` files as they were when that run was made (they are dynamic and
  mostly empty, so they stay small; if they do not, keep them for `chain_default` only and say so)
- `sha256` of every file on the volumes, with path and size
- the size of each virtual disk in bytes

Then use Veeam itself as the reference reader: for at least `chain_default` (latest point) and
`comp_high`, restore the VM files or the disks with Veeam (file-level or VM files restore, or the
Extract utility that ships with Veeam) to a scratch folder and record names, sizes and `sha256`
of what comes out. This tells us how the disk is represented inside the backup. Do not put the
restored files in the zip unless they are small.

## Manifest

`manifest.json`, one entry per scenario:

- name, kind of job (Hyper-V job / VeeamZIP / agent job)
- settings: compression level, storage optimization, encryption on/off and the test password,
  change tracking on/off, anything else you changed
- for each run in order: time, type (full/incremental/reverse), the list of files that Veeam
  created or modified in that run with size and `sha256`, and which ground truth belongs to it
- for reverse incremental: which file holds which restore point after the last run

Top level: Veeam version and build, Windows version and build, PowerShell version used, date.

## How to work

- Veeam is scripted through its PowerShell module (`Veeam.Backup.PowerShell`). Version 13 may need
  PowerShell 7 instead of Windows PowerShell; check which one loads the module. Cmdlet and parameter
  names changed between Veeam versions: look them up with `Get-Command` / `Get-Help` on the machine
  instead of trusting memory, mine included. Names I expect, unverified: `Connect-VBRServer`,
  `Add-VBRHvHost`, `Find-VBRHvEntity`, `Add-VBRHvJob`, `Set-VBRJobAdvancedStorageOptions`,
  `Start-VBRJob`, `Start-VBRZip`, `Add-VBRBackupRepository`, `Add-VBREncryptionKey`.
- Use a new backup repository in a new folder on a local disk (for example `D:\vbkfix\repo` or
  `C:\vbkfix\repo`), not the default one. Check free disk space first.
- Wait for each job to finish and check its result; a job that ended with a warning or error is not
  a fixture. Put the session result in the manifest.
- Copy the backup files only after the job has finished and released them.
- `make_vbk.ps1` should be one script with clear steps, safe to run again (remove or reuse what it
  created before), and should end by writing the manifest and the zip. Comments only where
  something is not obvious.

## Limits

- Work only on this VM. Do not connect Veeam or anything else to other machines of the lab, and do
  not scan the network.
- Do not send the files anywhere; the owner copies the zip.
- Installing the Hyper-V feature and creating the test VM, repository and jobs is expected. Other
  system changes (firewall, security settings, other software): ask the owner first.
- When finished, list what you created on the VM (the test VM, jobs, repository, folders) so
  the owner can decide what to remove. Do not remove the Veeam installation.

## What to report back

`report.md`, short and concrete:

- which scenarios exist and which were skipped, with the reason
- for each: the files and their sizes
- whether Veeam accepted Windows 11 Hyper-V as a host, or the fallback was needed
- which compression levels and block sizes the product offers, with the exact names shown
- how the restored data from Veeam looked: file names, and whether the stored disk is the `.vhdx`
  file byte for byte or something else
- anything unexpected: errors, settings that were not available, odd file sizes, extra files in the
  repository
- the total size of the zip, and if it is over the budget, what takes the space

If you are blocked (nested virtualization off, Hyper-V host rejected and the agent fallback also
fails, licence limits), stop and report what you tried and the exact error text instead of
working around it with a different product or source.
