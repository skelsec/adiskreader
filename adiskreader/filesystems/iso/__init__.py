from adiskreader import logger
from adiskreader.filesystems.iso.iso9660 import ISO9660, SECTOR_SIZE, FIRST_DESCRIPTOR_SECTOR, ISO_MAGIC, VD_PRIMARY

# Optical disc images (iso). The same image can describe its files more than once: with UDF, with ISO9660
# (optionally extended with Rock Ridge) and with Joliet. Each of these is a filesystem that can be mounted,
# they take the place of the partitions of the disks.

UDF_RECOGNITION_IDS = (b'BEA01', b'NSR02', b'NSR03', b'TEA01', b'BOOT2')
MAX_RECOGNITION_SECTORS = 32

VIEW_UDF = 'UDF'
VIEW_ROCKRIDGE = 'ISO9660 + Rock Ridge'
VIEW_JOLIET = 'Joliet'
VIEW_ISO9660 = 'ISO9660'


class ISOView:
    """One of the filesystems describing the contents of the image, mount() opens it"""
    def __init__(self, image, index:int, kind:str, volume_name:str = ''):
        self.image = image
        self.index = index # starts with 1
        self.kind = kind
        self.volume_name = volume_name

    async def mount(self):
        return await self.image.mount(self.index)

    def __str__(self):
        return 'Filesystem %s: %s (%s)' % (self.index, self.kind, self.volume_name)


class ISOImage:
    """
    Optical disc image. list_partitions() gives the filesystems found in it, the first one is the most descriptive.
    """
    def __init__(self):
        self.__stream = None
        self.views = []

    @staticmethod
    async def from_datasource(ds):
        image = ISOImage()
        await image.setup(ds)
        return image

    @staticmethod
    async def read_recognition_ids(ds):
        """The identifiers of the volume recognition sequence, restores the position of the datasource"""
        res = []
        pos = await ds.tell()
        try:
            await ds.seek(FIRST_DESCRIPTOR_SECTOR * SECTOR_SIZE, 0)
            data = await ds.read(MAX_RECOGNITION_SECTORS * SECTOR_SIZE)
        finally:
            await ds.seek(pos, 0)
        # the descriptors are one sector each, the sectors of UDF can be larger than 2048 bytes
        for offset in range(0, len(data) - 6, SECTOR_SIZE):
            ident = data[offset+1:offset+6]
            if ident == ISO_MAGIC or ident in UDF_RECOGNITION_IDS:
                res.append((data[offset], ident))
        return res

    @staticmethod
    async def is_iso(ds):
        """Checks if the datasource has an ISO9660 or UDF volume recognition sequence"""
        ids = await ISOImage.read_recognition_ids(ds)
        for vdtype, ident in ids:
            if ident == ISO_MAGIC and vdtype == VD_PRIMARY:
                return True
            if ident in (b'NSR02', b'NSR03'):
                return True
        return False

    async def setup(self, ds):
        from adiskreader.filesystems.udf import UDF

        self.__stream = ds
        found = []
        try:
            fs = await UDF.from_datasource(ds)
            found.append((VIEW_UDF, fs.volume_name))
        except Exception as e:
            logger.debug('ISO: no usable UDF filesystem: %s' % e)

        descriptors = await ISO9660.read_descriptors(ds)
        if any(vdtype == VD_PRIMARY for vdtype, _ in descriptors):
            try:
                fs = await ISO9660.from_datasource(ds)
                rockridge = fs.rock_ridge
                if rockridge is True:
                    found.append((VIEW_ROCKRIDGE, fs.volume_name))
                if any(ISO9660.is_joliet(data) for _, data in descriptors):
                    try:
                        joliet = await ISO9660.from_datasource(ds, joliet=True)
                        found.append((VIEW_JOLIET, joliet.volume_name))
                    except Exception as e:
                        logger.debug('ISO: no usable Joliet filesystem: %s' % e)
                if rockridge is False:
                    found.append((VIEW_ISO9660, fs.volume_name))
            except Exception as e:
                logger.debug('ISO: no usable ISO9660 filesystem: %s' % e)

        if len(found) == 0:
            raise Exception('No ISO9660 or UDF filesystem found in the image')
        self.views = [ISOView(self, i + 1, kind, name) for i, (kind, name) in enumerate(found)]

    async def mount(self, index:int = 1):
        """Opens a filesystem of the image, index starts with 1"""
        from adiskreader.filesystems.udf import UDF

        if index < 1 or index > len(self.views):
            raise Exception('No such filesystem in the image: %s' % index)
        kind = self.views[index - 1].kind
        if kind == VIEW_UDF:
            return await UDF.from_datasource(self.__stream)
        if kind == VIEW_JOLIET:
            return await ISO9660.from_datasource(self.__stream, joliet=True)
        return await ISO9660.from_datasource(self.__stream, rock_ridge=(kind == VIEW_ROCKRIDGE))

    async def list_partitions(self):
        # the filesystems take the place of the partitions of the disks
        return self.views

    async def close(self):
        """Nothing to do, the datasource is to be closed by the caller"""
        return

    def __str__(self):
        t = []
        t.append('Type         : ISO')
        t.append('Datasource   : %s' % self.__stream)
        for view in self.views:
            t.append(str(view))
        return '\n'.join(t)
