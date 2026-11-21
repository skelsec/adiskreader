
import pathlib
import gzip
import json
import shutil
import zipfile


VFAT_16_TEST_FILE_GZ = 'testfiles/vfat_16_test.img.gz'
VFAT_32_TEST_FILE_GZ = 'testfiles/vfat_32_test.img.gz'
NTFS_TEST_FILE_GZ    = 'testfiles/ntfs_test.img.gz'
# converted from the raw ntfs image with qemu-img, same contents
NTFS_TEST_FILE_VHDX_DYNAMIC_GZ = 'testfiles/ntfs_test_dynamic.vhdx.gz'
NTFS_TEST_FILE_VHDX_FIXED_GZ   = 'testfiles/ntfs_test_fixed.vhdx.gz'
# made by Windows, has a log which needs to be replayed before it can be read
NTFS_TEST_FILE_VHDX_GZ  = 'testfiles/small_ntfs_test.vhdx.gz'
# converted from the raw ntfs image with qemu-img, same contents
NTFS_TEST_FILE_VMDK_SPARSE_GZ = 'testfiles/ntfs_test_monolithicSparse.vmdk.gz'
NTFS_TEST_FILE_VMDK_STREAM_GZ = 'testfiles/ntfs_test_streamOptimized.vmdk.gz'
# snapshot of the sparse vmdk and the snapshot of that snapshot, see VMDK_SNAPSHOT_WRITES
NTFS_TEST_FILE_VMDK_SNAPSHOT_GZ  = 'testfiles/ntfs_test_snapshot.vmdk.gz'
NTFS_TEST_FILE_VMDK_SNAPSHOT2_GZ = 'testfiles/ntfs_test_snapshot2.vmdk.gz'
# made by VMware Workstation: the disk, its snapshot (with VMDK_SNAPSHOT_WRITES) and the disk of the OVA export
NTFS_TEST_FILE_VMDK_VMWARE_GZ          = 'testfiles/ntfs_test_vmware.vmdk.gz'
NTFS_TEST_FILE_VMDK_VMWARE_SNAPSHOT_GZ = 'testfiles/ntfs_test_vmware-000001.vmdk.gz'
NTFS_TEST_FILE_VMDK_VMWARE_OVA_GZ      = 'testfiles/ntfs_test_vmware_ova.vmdk.gz'

# (offset, length, byte) written to the snapshots with qemu-io
VMDK_SNAPSHOT_WRITES = [
	(0x200000, 0x30000, 0xA5),
	(0x2fffe00, 0x1200, 0x5A),
	(0x10001, 0x1ff, 0x77),
]
VMDK_SNAPSHOT2_WRITES = [
	(0x210000, 0x10000, 0x11),
	(0x400000, 0x100000, 0x00),
]

# ntfs image holding diff_vhd_base.vhd and diff_vhd_child1.vhd of the differencing chains, for reading disks stored in a filesystem
NESTED_NTFS_TEST_FILE_GZ = 'testfiles/nested_ntfs.img.gz'

# wim file without compression made by 7z, the xpress and lzx compressed ones are built from it (wim_builder.py)
WIM_TEST_FILE_GZ = 'testfiles/wim_test.wim.gz'

# differencing vhd / vhdx chains made by Windows with make_diff_vhd.ps1, with the contents Windows reads from each disk
DIFF_FIXTURES_ZIP = 'testfiles/adiskreader_diff_fixtures.zip'
DIFF_FOLDER_NAME = 'diff'

# wim files made by Windows (DISM) with make_wim.ps1, with the contents of their images
WIM_FIXTURES_ZIP = 'testfiles/adiskreader_wim_fixtures.zip'
WIM_FOLDER_NAME = 'wimwin'

CURRENT_FILE_PATH = pathlib.Path(__file__).parent.absolute()
TESTFILES_FOLDER_PATH = CURRENT_FILE_PATH.joinpath('testfiles')
MANIFESTS_FOLDER_PATH = CURRENT_FILE_PATH.joinpath('manifests')

# manifest name -> image the manifest is generated from (see make_manifest.py)
MANIFESTS = {
	'vfat_16' : VFAT_16_TEST_FILE_GZ,
	'vfat_32' : VFAT_32_TEST_FILE_GZ,
	'ntfs'    : NTFS_TEST_FILE_GZ,
	'wim'     : WIM_TEST_FILE_GZ,
}

def decompress_gzip(source_path:pathlib.Path, destination_file_name = None):
	if destination_file_name is None:
		destination_file_name = source_path.name.replace('.gz', '')
	dstfilepath = TESTFILES_FOLDER_PATH.joinpath(destination_file_name)
	if dstfilepath.exists():
		return dstfilepath
	with gzip.open(source_path, 'rb') as f_in:
		with open(dstfilepath, 'wb') as f_out:
			shutil.copyfileobj(f_in, f_out)
	return dstfilepath

def get_image(image_gz, compressed = False):
	"""Returns the path of the test image, either the gz file or the decompressed one"""
	image_gz_path = CURRENT_FILE_PATH.joinpath(image_gz)
	if compressed is True:
		return image_gz_path
	return decompress_gzip(image_gz_path)

def get_wim_image(compression = None):
	"""Returns the path of the test wim file, compression is None, 'xpress' or 'lzx'"""
	path = get_image(WIM_TEST_FILE_GZ)
	if compression is None:
		return path
	from wim_builder import build_compressed_wim
	dstfilepath = TESTFILES_FOLDER_PATH.joinpath('wim_test_%s.wim' % compression)
	if dstfilepath.exists() is False:
		tmpfilepath = dstfilepath.with_name(dstfilepath.name + '.tmp')
		tmpfilepath.write_bytes(build_compressed_wim(path.read_bytes(), compression))
		tmpfilepath.rename(dstfilepath)
	return dstfilepath

def has_wim_fixtures():
	return CURRENT_FILE_PATH.joinpath(WIM_FIXTURES_ZIP).exists()

def get_wim_fixtures():
	"""Extracts the WIM files made by Windows to testfiles/wimwin, returns the folder and the manifest"""
	folder = TESTFILES_FOLDER_PATH.joinpath(WIM_FOLDER_NAME)
	with zipfile.ZipFile(CURRENT_FILE_PATH.joinpath(WIM_FIXTURES_ZIP)) as zf:
		with zf.open('manifest.json') as f:
			manifest = json.loads(f.read().decode('utf-8-sig'))
		for wim in manifest['wims']:
			for part in wim['parts']:
				dstfilepath = folder.joinpath(part)
				if dstfilepath.exists():
					continue
				dstfilepath.parent.mkdir(parents=True, exist_ok=True)
				tmpfilepath = dstfilepath.with_name(dstfilepath.name + '.tmp')
				with zf.open(part) as f_in:
					with open(tmpfilepath, 'wb') as f_out:
						shutil.copyfileobj(f_in, f_out)
				tmpfilepath.rename(dstfilepath)
	return folder, manifest

def get_diff_fixtures():
	"""
	Extracts the differencing disks to testfiles/diff, returns the folder and the images of the manifest.
	The disks are decompressed, their expected contents (image['raw']) are left gzipped.
	"""
	folder = TESTFILES_FOLDER_PATH.joinpath(DIFF_FOLDER_NAME)
	with zipfile.ZipFile(CURRENT_FILE_PATH.joinpath(DIFF_FIXTURES_ZIP)) as zf:
		with zf.open('manifest.json') as f:
			images = json.load(f)['images']
		for info in zf.infolist():
			# the zip is made on Windows
			name = info.filename.replace('\\', '/')
			if name.endswith('/') or name == 'manifest.json':
				continue
			decompress = name.endswith('.raw.gz') is False
			dstfilepath = folder.joinpath(name[:-3] if decompress is True else name)
			if dstfilepath.exists():
				continue
			dstfilepath.parent.mkdir(parents=True, exist_ok=True)
			tmpfilepath = dstfilepath.with_name(dstfilepath.name + '.tmp')
			with zf.open(info) as f_in:
				with open(tmpfilepath, 'wb') as f_out:
					shutil.copyfileobj(gzip.open(f_in, 'rb') if decompress is True else f_in, f_out)
			tmpfilepath.rename(dstfilepath)
	return folder, images

def get_diff_image(name):
	"""Returns the path of the differencing disk (<chain>_<level>) and its entry in the manifest"""
	folder, images = get_diff_fixtures()
	chain, level = name.split('_')
	for image in images:
		if image['chain'] == chain and image['level'] == level:
			return folder.joinpath(image['file']), image
	raise Exception('No such image: %s' % name)

def get_diff_expected(name):
	"""Returns what Windows read from the differencing disk"""
	folder, _ = get_diff_fixtures()
	_, image = get_diff_image(name)
	with gzip.open(folder.joinpath(image['raw']), 'rb') as f:
		return f.read()

def load_manifest(name):
	"""Returns the expected contents of a test image: {'dirs': [path], 'files': {path: {'size', 'sha256'}}}"""
	with open(MANIFESTS_FOLDER_PATH.joinpath(name + '.json'), 'r') as f:
		return json.load(f)

async def fs_read(file, offset, size):
	await file.seek(offset, 0)
	return await file.read(size)
