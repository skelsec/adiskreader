
import pathlib
import os
import gzip
import shutil
from functools import wraps


MOUNTPOINT_TEST   = pathlib.Path('/mnt/diskreadtest')
VFAT_16_TEST_FILE_GZ = 'testfiles/vfat_16_test.img.gz'
VFAT_32_TEST_FILE_GZ = 'testfiles/vfat_32_test.img.gz'
NTFS_TEST_FILE_GZ    = 'testfiles/ntfs_test.img.gz'
NTFS_TEST_FILE_VHDX_GZ  = 'testfiles/small_ntfs_test.vhdx.gz'


CURRENT_FILE_PATH = pathlib.Path(__file__).parent.absolute()
TESTFILES_FOLDER_PATH = CURRENT_FILE_PATH.joinpath('testfiles')

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

########################################## RAW ##########################################
def setup_vfat_16():
	vfile_path = CURRENT_FILE_PATH.joinpath(VFAT_16_TEST_FILE_GZ)
	vfile_path = decompress_gzip(vfile_path)
	MOUNTPOINT_TEST.mkdir(parents=True, exist_ok=True)
	os.system(f'sudo mount -o loop {vfile_path} {MOUNTPOINT_TEST}')
	return vfile_path, MOUNTPOINT_TEST

def setup_vfat_32():
	vfile_path = CURRENT_FILE_PATH.joinpath(VFAT_32_TEST_FILE_GZ)
	vfile_path = decompress_gzip(vfile_path)
	MOUNTPOINT_TEST.mkdir(parents=True, exist_ok=True)
	os.system(f'sudo mount -o loop {vfile_path} {MOUNTPOINT_TEST}')
	return vfile_path, MOUNTPOINT_TEST

def setup_ntfs():
	vfile_path = CURRENT_FILE_PATH.joinpath(NTFS_TEST_FILE_GZ)
	vfile_path = decompress_gzip(vfile_path)
	MOUNTPOINT_TEST.mkdir(parents=True, exist_ok=True)
	os.system(f'sudo mount -o loop {vfile_path} {MOUNTPOINT_TEST}')
	return vfile_path, MOUNTPOINT_TEST

def setup_ntfs_vhdx():
	vfile_path = CURRENT_FILE_PATH.joinpath(NTFS_TEST_FILE_VHDX_GZ)
	vfile_path = decompress_gzip(vfile_path)
	MOUNTPOINT_TEST.mkdir(parents=True, exist_ok=True)
	os.system(f'sudo guestmount -a {vfile_path} -m /dev/sda1 {MOUNTPOINT_TEST}')
	return vfile_path, MOUNTPOINT_TEST

########################################## GZ ##########################################
def setup_vfat_16_gz():
	vfile_path_gz = CURRENT_FILE_PATH.joinpath(VFAT_16_TEST_FILE_GZ)
	vfile_path = decompress_gzip(vfile_path_gz)
	MOUNTPOINT_TEST.mkdir(parents=True, exist_ok=True)
	os.system(f'sudo mount -o loop {vfile_path} {MOUNTPOINT_TEST}')
	return vfile_path_gz, MOUNTPOINT_TEST

def setup_vfat_32_gz():
	vfile_path_gz = CURRENT_FILE_PATH.joinpath(VFAT_32_TEST_FILE_GZ)
	vfile_path = decompress_gzip(vfile_path_gz)
	MOUNTPOINT_TEST.mkdir(parents=True, exist_ok=True)
	os.system(f'sudo mount -o loop {vfile_path} {MOUNTPOINT_TEST}')
	return vfile_path_gz, MOUNTPOINT_TEST

def setup_ntfs_gz():
	vfile_path_gz = CURRENT_FILE_PATH.joinpath(NTFS_TEST_FILE_GZ)
	vfile_path = decompress_gzip(vfile_path_gz)
	MOUNTPOINT_TEST.mkdir(parents=True, exist_ok=True)
	os.system(f'sudo mount -o loop {vfile_path} {MOUNTPOINT_TEST}')
	return vfile_path_gz, MOUNTPOINT_TEST

def setup_ntfs_vhdx_gz():
	vfile_path_gz = CURRENT_FILE_PATH.joinpath(NTFS_TEST_FILE_VHDX_GZ)
	vfile_path = decompress_gzip(vfile_path_gz)
	MOUNTPOINT_TEST.mkdir(parents=True, exist_ok=True)
	os.system(f'sudo guestmount -a {vfile_path} -m /dev/sda1 {MOUNTPOINT_TEST}')
	return vfile_path_gz, MOUNTPOINT_TEST


async def fs_read(file, offset, size):
	await file.seek(offset, 0)
	return await file.read(size)

def file_read(file, offset, size):
	file.seek(offset, 0)
	return file.read(size)


def unmount_on_finish():
	def decorator(func):
		@wraps(func)
		async def wrapper(*args, **kwargs):
			try:
				return await func(*args, **kwargs)
			finally:
				# Run the command asynchronously
				os.system(f'sudo umount {MOUNTPOINT_TEST}')
		return wrapper
	return decorator
