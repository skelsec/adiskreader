import ntpath
from adiskreader import logger

# helpers of the disk formats which store only the changes made to a parent disk (differencing VHD and VHDX)

def parent_candidates(names:list):
    """
    Returns the paths to try when opening the parent disk, relative to the directory of the child.
    names are the paths stored in the child, the relative ones are tried as they are,
    then the file names of all of them are searched in the directory of the child.
    """
    names = [name.replace('/', '\\') for name in names if name is not None and name != '']
    candidates = []
    for name in names:
        if name.startswith('\\') is False and ntpath.splitdrive(name)[0] == '':
            candidates.append(ntpath.normpath(name))
    for name in names:
        candidates.append(ntpath.basename(name))

    res = []
    for candidate in candidates:
        if candidate not in ['', '.', '..'] and candidate not in res:
            res.append(candidate)
    return res

async def open_parent_disk(stream, names:list, disktype, is_parent):
    """
    Opens the parent disk using the datasource of the child.
    is_parent(disk) tells if the disk is the one the child was created from. If none of the files found
    is that disk, then the first one is used and a warning is logged.
    Returns the disk and its datasource, both to be closed by the caller.
    """
    fallback = None
    error = None
    candidates = parent_candidates(names)
    for candidate in candidates:
        try:
            ds = await stream.open_sibling(candidate)
        except Exception as e:
            error = e
            continue
        try:
            disk = await disktype.from_datasource(ds)
        except Exception as e:
            error = e
            await ds.close()
            continue
        if is_parent(disk) is True:
            if fallback is not None:
                await fallback[0].close()
                await fallback[1].close()
            return disk, ds
        if fallback is None:
            fallback = (disk, ds, candidate)
        else:
            await disk.close()
            await ds.close()

    if fallback is not None:
        logger.warning('The parent disk %s is not the one the disk was created from, data might be inconsistent' % fallback[2])
        return fallback[0], fallback[1]
    raise Exception('Parent disk not found, tried: %s. Last error: %s' % (', '.join(candidates), error))

def bitmap_runs(bitmap:bytes, start:int, count:int, msb_first:bool):
    """Returns the runs of the same bits as list of (number of bits, True if the bits are set)"""
    runs = []
    current = None
    size = 0
    pos = start
    end = start + count
    while pos < end:
        byte = bitmap[pos >> 3]
        if pos & 7 == 0 and end - pos >= 8 and byte in (0, 0xFF):
            bit = byte == 0xFF
            step = 8
        else:
            shift = 7 - (pos & 7) if msb_first is True else pos & 7
            bit = (byte >> shift) & 1 == 1
            step = 1
        if bit != current:
            if size > 0:
                runs.append((size, current))
            current = bit
            size = 0
        size += step
        pos += step
    if size > 0:
        runs.append((size, current))
    return runs
