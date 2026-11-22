
import pathlib
import gzip
import hashlib
import json
import os
import shutil
import zipfile
import urllib.request


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

# wim file without compression made by 7z, the xpress, lzx and lzms compressed ones are built from it (wim_builder.py)
WIM_TEST_FILE_GZ = 'testfiles/wim_test.wim.gz'

# differencing vhd / vhdx chains made by Windows with make_diff_vhd.ps1, with the contents Windows reads from each disk
DIFF_FIXTURES_ZIP = 'testfiles/adiskreader_diff_fixtures.zip'
DIFF_FOLDER_NAME = 'diff'

# wim files made by Windows (DISM) with make_wim.ps1, with the contents of their images
WIM_FIXTURES_ZIP = 'testfiles/adiskreader_wim_fixtures.zip'
WIM_FOLDER_NAME = 'wimwin'

# backup files made by Veeam Backup & Replication 13 with make_vbk.ps1, with the contents of the disks at each run
VBK_FIXTURES_ZIP = 'testfiles/vbk_fixtures.zip'
VBK_FOLDER_NAME = 'vbk'

# disc images made by IMAPI2 on Windows (make_iso.ps1)
ISO_FIXTURES_ZIP = 'testfiles/adiskreader_iso_fixtures.zip'
ISO_FOLDER_NAME = 'isowin'

# QCOW2 and VDI disks made by qemu and VirtualBox with make_qemu_fixtures.py, with the contents qemu reads from each disk
QEMU_FIXTURES_ZIP = 'testfiles/adiskreader_qemu_fixtures.zip'
QEMU_FOLDER_NAME = 'qemu'

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

# the large test files are not kept in git, they are downloaded from here when a test needs them
# set ADISKREADER_TEST_OFFLINE to skip the tests of the missing ones instead
FIXTURES_URL = 'https://github.com/skelsec/adiskreader-testfiles/releases/download/v1/'
# file name -> sha256
FIXTURES_SHA256 = {
	'adiskreader_diff_fixtures.zip'       : 'a522aca9e6cb50f037d982421f821b03f58eb1502987dfdd0da4d2fe5fa64bd6',
	'adiskreader_iso_fixtures.zip'        : '60ad2d4c3b6bf1fd9b86b6dd76041b78018250195cc96a4f96eebbffd49f8e08',
	'adiskreader_qemu_fixtures.zip'       : '14bfdf9afd2816f6a4134a472b60417d679d0122d30f862cbe3eddcf88fd9888',
	'adiskreader_wim_fixtures.zip'        : '31647f4458f9130712c9d0f525df488484bc7ddaa1733484ecb997a2e2163208',
	'vbk_fixtures.zip'                    : '872918794c4ed613a3660406dad4911b3d2a332e04cc611d1c51dd054574071b',
	'ntfs_test_dynamic.vhdx.gz'           : '9223b3bdacbd5af719377afa15fb1db1860efca1a388afe490cd18e50511f4ef',
	'ntfs_test_fixed.vhdx.gz'             : '326c07a59b2fc232d07ba5b15dc59924dc66ab3a2c685ebdfbcf82f1dce01040',
	'ntfs_test_monolithicSparse.vmdk.gz'  : 'bc3f2a4956a6088d78bf2c4731ac66f7ab94d6ed49c70dd325abff171dfb8aac',
	'ntfs_test_streamOptimized.vmdk.gz'   : '452c28286f85c1eb59567505f0da9cd2742f553e620f38b28e144b042cd3b2a3',
	'ntfs_test_snapshot.vmdk.gz'          : '0a4d09f640604fee343290a2c3145231ef68b23ee2ef53521f2527d6575dc15b',
	'ntfs_test_snapshot2.vmdk.gz'         : '8c8545287c4099bbbdc3b463eb7ff93bca9fa943d4ae3f7494aff37e5747423b',
	'ntfs_test_vmware.vmdk.gz'            : '7d211fff6805fd650e23be6c9234ec1cf938ec81550ec788002786fd779d21bb',
	'ntfs_test_vmware-000001.vmdk.gz'     : '66433d98bb35402b6c22296d3bfce3eb707dc7d604f4b91335296b30101dbcaf',
	'ntfs_test_vmware_ova.vmdk.gz'        : '95bc45a1fe2fe09506ebbe753e454a82de659d97901c06ff7e35b26566f9504f',
}

def get_fixture(path):
	"""
	Returns the path of a test file (path is relative to the tests folder), downloading it when it is not kept in git.
	The test is skipped when the file is not available.
	"""
	filepath = CURRENT_FILE_PATH.joinpath(path)
	if filepath.exists():
		return filepath
	if filepath.name not in FIXTURES_SHA256:
		raise FileNotFoundError(str(filepath))
	import pytest
	if os.environ.get('ADISKREADER_TEST_OFFLINE'):
		pytest.skip('%s is not downloaded' % filepath.name, allow_module_level=True)
	tmpfilepath = filepath.with_name(filepath.name + '.tmp')
	sha256 = hashlib.sha256()
	try:
		with urllib.request.urlopen(FIXTURES_URL + filepath.name, timeout=60) as f_in:
			with open(tmpfilepath, 'wb') as f_out:
				while True:
					data = f_in.read(0x100000)
					if data == b'':
						break
					sha256.update(data)
					f_out.write(data)
	except OSError as e:
		tmpfilepath.unlink(missing_ok=True)
		pytest.skip('%s could not be downloaded: %s' % (filepath.name, e), allow_module_level=True)
	if sha256.hexdigest() != FIXTURES_SHA256[filepath.name]:
		tmpfilepath.unlink()
		raise Exception('%s was downloaded with an unexpected sha256: %s' % (filepath.name, sha256.hexdigest()))
	tmpfilepath.rename(filepath)
	return filepath

def has_fixture(path):
	"""False when the test file is missing and is not to be downloaded"""
	return CURRENT_FILE_PATH.joinpath(path).exists() or not os.environ.get('ADISKREADER_TEST_OFFLINE')

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
	if compressed is True:
		return get_fixture(image_gz)
	dstfilepath = TESTFILES_FOLDER_PATH.joinpath(pathlib.Path(image_gz).name.replace('.gz', ''))
	if dstfilepath.exists():
		return dstfilepath
	return decompress_gzip(get_fixture(image_gz))

def get_wim_image(compression = None):
	"""Returns the path of the test wim file, compression is None, 'xpress', 'lzx' or 'lzms'"""
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
	return has_fixture(WIM_FIXTURES_ZIP)

def get_wim_fixtures():
	"""Extracts the WIM files made by Windows to testfiles/wimwin, returns the folder and the manifest"""
	folder = TESTFILES_FOLDER_PATH.joinpath(WIM_FOLDER_NAME)
	with zipfile.ZipFile(get_fixture(WIM_FIXTURES_ZIP)) as zf:
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

def has_vbk_fixtures():
	return has_fixture(VBK_FIXTURES_ZIP)

def get_vbk_fixtures():
	"""Extracts the Veeam backup files to testfiles/vbk, returns the folder and the manifest"""
	folder = TESTFILES_FOLDER_PATH.joinpath(VBK_FOLDER_NAME)
	zippath = get_fixture(VBK_FIXTURES_ZIP)
	marker = folder.joinpath('extracted')
	# the zip is extracted again when it is replaced
	stamp = str(zippath.stat().st_mtime_ns)
	if marker.exists() is False or marker.read_text() != stamp:
		shutil.rmtree(folder, ignore_errors=True)
		with zipfile.ZipFile(zippath) as zf:
			zf.extractall(folder)
		marker.write_text(stamp)
	folder = folder.joinpath('vbk_fixtures')
	return folder, json.loads(folder.joinpath('manifest.json').read_text(encoding='utf-8-sig'))

def has_iso_fixtures():
	return has_fixture(ISO_FIXTURES_ZIP)

def get_iso_fixtures():
	"""Extracts the disc images made by Windows to testfiles/isowin, returns the folder and the manifest"""
	folder = TESTFILES_FOLDER_PATH.joinpath(ISO_FOLDER_NAME)
	with zipfile.ZipFile(get_fixture(ISO_FIXTURES_ZIP)) as zf:
		with zf.open('manifest.json') as f:
			manifest = json.loads(f.read().decode('utf-8-sig'))
		for image in manifest['images']:
			dstfilepath = folder.joinpath(image['file'])
			if dstfilepath.exists():
				continue
			dstfilepath.parent.mkdir(parents=True, exist_ok=True)
			tmpfilepath = dstfilepath.with_name(dstfilepath.name + '.tmp')
			with zf.open(image['file']) as f_in:
				with open(tmpfilepath, 'wb') as f_out:
					shutil.copyfileobj(f_in, f_out)
			tmpfilepath.rename(dstfilepath)
	return folder, manifest

def get_qemu_fixtures():
	"""Extracts the QCOW2 and VDI disks to testfiles/qemu, returns the folder and the images of the manifest by their names"""
	folder = TESTFILES_FOLDER_PATH.joinpath(QEMU_FOLDER_NAME)
	zippath = get_fixture(QEMU_FIXTURES_ZIP)
	marker = folder.joinpath('extracted')
	with zipfile.ZipFile(zippath) as zf:
		with zf.open('manifest.json') as f:
			images = json.load(f)['images']
		# the zip is extracted again when it is replaced
		stamp = str(zippath.stat().st_mtime_ns)
		if marker.exists() is False or marker.read_text() != stamp:
			shutil.rmtree(folder, ignore_errors=True)
			for info in zf.infolist():
				if info.filename.startswith('expected/') or info.filename == 'manifest.json':
					continue
				zf.extract(info, folder)
			marker.write_text(stamp)
	return folder, {image['name']: image for image in images}

def get_qemu_expected(sha256):
	"""Returns what qemu read from a disk"""
	with zipfile.ZipFile(get_fixture(QEMU_FIXTURES_ZIP)) as zf:
		return zf.read('expected/%s.raw' % sha256)

def get_diff_fixtures():
	"""
	Extracts the differencing disks to testfiles/diff, returns the folder and the images of the manifest.
	The disks are decompressed, their expected contents (image['raw']) are left gzipped.
	"""
	folder = TESTFILES_FOLDER_PATH.joinpath(DIFF_FOLDER_NAME)
	with zipfile.ZipFile(get_fixture(DIFF_FIXTURES_ZIP)) as zf:
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
