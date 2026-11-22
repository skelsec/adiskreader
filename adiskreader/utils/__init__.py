import datetime
import math

def filetime_to_dt(filetime):
    # Define the FILETIME base: January 1, 1601
    base = datetime.datetime(1601, 1, 1)

    # Convert FILETIME to microseconds
    microseconds = filetime / 10

    try:
        # Return the calculated datetime
        return base + datetime.timedelta(microseconds=microseconds)
    except OverflowError:
        # Return the maximum datetime
        input('Datetime error! %s' % filetime)
        return base
    
def round_up(x, y):
    return math.ceil(x / y) * y

def ceil_d(x, y):
    return (x + (y-1)) // y

CRC32C_TABLE = []
for _i in range(256):
    _crc = _i
    for _ in range(8):
        _crc = (_crc >> 1) ^ 0x82F63B78 if _crc & 1 else _crc >> 1
    CRC32C_TABLE.append(_crc)

def crc32c(data:bytes, crc:int = 0):
    """CRC-32C (Castagnoli), used by the VHDX format"""
    crc ^= 0xFFFFFFFF
    table = CRC32C_TABLE
    for b in data:
        crc = table[(crc ^ b) & 0xFF] ^ (crc >> 8)
    return crc ^ 0xFFFFFFFF
