import io
from adiskreader.filesystems.ntfs import logger

def apply_fixups(buffer:io.BytesIO, usa_offset:int, usa_count:int, start_pos:int=0, bytes_per_sector:int=512, validate_checksum:bool=True):
    """Apply fixups to buffer.

    Args:
        buffer (io.BytesIO): Buffer to apply fixups to.
        usa_offset (int): Offset of update sequence array.
        usa_count (int): Number of entries in update sequence array.
    """
    # http://inform.pucp.edu.pe/~inf232/Ntfs/ntfs_doc_v0.5/concepts/fixup.html
    
    update_seq = []
    buffer.seek(usa_offset + start_pos, 0)
    for _ in range(usa_count):
        update_seq.append(buffer.read(2))
    
    buffer.seek(start_pos, 0)
    for i, actual_data in enumerate(update_seq[1:]):
        correct_pos = start_pos + ((i+1)*bytes_per_sector) - 2
        buffer.seek(correct_pos, 0)
        if validate_checksum is True:
            checksum = buffer.read(2)
            if checksum != update_seq[0]:
                logger.debug('[MISMATCH] i: {} Expected: {} Actual: {}'.format(buffer.tell()-2, update_seq[0], checksum))
                #print('[MISMATCH] i: {}'.format(buffer.tell()-2))
                #print('[MISMATCH] SEQ: {}'.format(update_seq))
                #input('Update sequence mismatch. Expected: {} Actual: {}'.format(update_seq[0], checksum))
                continue
            buffer.seek(correct_pos, 0)
        buffer.write(actual_data)
            
    return update_seq



# https://gist.github.com/ImmortalPC/c340564823f283fe530b
def hexdump(src, length=16, sep='.'):
    """
    Pretty printing binary data blobs
    :param src: Binary blob
    :type src: bytearray
    :param length: Size of data in each row
    :type length: int
    :param sep: Character to print when data byte is non-printable ASCII
    :type sep: str(char)
    :return: str
    """
    result = []

    for i in range(0, len(src), length):
        subSrc = src[i:i+length]
        hexa = ''
        isMiddle = False
        for h in range(0,len(subSrc)):
            if h == length/2:
                hexa += ' '
            h = subSrc[h]
            if not isinstance(h, int):
                h = ord(h)
            h = hex(h).replace('0x', '')
            if len(h) == 1:
                h = '0'+h
            hexa += h+' '
        hexa = hexa.strip(' ')
        text = ''
        for c in subSrc:
            if not isinstance(c, int):
                c = ord(c)
            if 0x20 <= c < 0x7F:
                text += chr(c)
            else:
                text += sep
        result.append(('%08X:  %-'+str(length*(2+1)+1)+'s  |%s|') % (i, hexa, text))

    return '\n'.join(result)