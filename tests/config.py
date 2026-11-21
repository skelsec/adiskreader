
import pathlib
import gzip
import json
import shutil


VFAT_16_TEST_FILE_GZ = 'testfiles/vfat_16_test.img.gz'
VFAT_32_TEST_FILE_GZ = 'testfiles/vfat_32_test.img.gz'
NTFS_TEST_FILE_GZ    = 'testfiles/ntfs_test.img.gz'
# converted from the raw ntfs image with qemu-img, same contents
NTFS_TEST_FILE_VHDX_DYNAMIC_GZ = 'testfiles/ntfs_test_dynamic.vhdx.gz'
NTFS_TEST_FILE_VHDX_FIXED_GZ   = 'testfiles/ntfs_test_fixed.vhdx.gz'
# made by Windows, has a log which needs to be replayed before it can be read
NTFS_TEST_FILE_VHDX_GZ  = 'testfiles/small_ntfs_test.vhdx.gz'


CURRENT_FILE_PATH = pathlib.Path(__file__).parent.absolute()
TESTFILES_FOLDER_PATH = CURRENT_FILE_PATH.joinpath('testfiles')
MANIFESTS_FOLDER_PATH = CURRENT_FILE_PATH.joinpath('manifests')

# manifest name -> image the manifest is generated from (see make_manifest.py)
MANIFESTS = {
	'vfat_16' : VFAT_16_TEST_FILE_GZ,
	'vfat_32' : VFAT_32_TEST_FILE_GZ,
	'ntfs'    : NTFS_TEST_FILE_GZ,
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

def load_manifest(name):
	"""Returns the expected contents of a test image: {'dirs': [path], 'files': {path: {'size', 'sha256'}}}"""
	with open(MANIFESTS_FOLDER_PATH.joinpath(name + '.json'), 'r') as f:
		return json.load(f)

async def fs_read(file, offset, size):
	await file.seek(offset, 0)
	return await file.read(size)
