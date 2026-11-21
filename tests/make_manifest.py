"""
Generates the manifests the tests compare against, so the tests don't need to mount the test images.
The expected contents are extracted with 7z (no root needed), not with adiskreader itself.

Usage: python make_manifest.py
"""

import hashlib
import json
import os
import subprocess
import tempfile

from config import MANIFESTS, MANIFESTS_FOLDER_PATH, CURRENT_FILE_PATH, decompress_gzip


def sha256_file(path):
	h = hashlib.sha256()
	with open(path, 'rb') as f:
		for chunk in iter(lambda: f.read(1024*1024), b''):
			h.update(chunk)
	return h.hexdigest()

def make_manifest(image_path):
	dirs = []
	files = {}
	with tempfile.TemporaryDirectory() as tmpdir:
		# -sns- skips alternate data streams, [SYSTEM] holds the NTFS metafiles ($MFT etc.)
		# 7z returns nonzero for the truncated fat32 image, the extracted files are still complete
		subprocess.run(
			['7z', 'x', '-y', '-sns-', '-x![SYSTEM]', '-o' + tmpdir, str(image_path)],
			stdout=subprocess.DEVNULL, check=False
		)
		for root, dirnames, filenames in os.walk(tmpdir):
			rel = os.path.relpath(root, tmpdir)
			parts = [] if rel == '.' else rel.split(os.sep)
			for name in dirnames:
				dirs.append('\\'.join(parts + [name]))
			for name in filenames:
				path = os.path.join(root, name)
				files['\\'.join(parts + [name])] = {
					'size': os.path.getsize(path),
					'sha256': sha256_file(path),
				}
	if len(files) == 0:
		raise Exception('7z extracted nothing from %s' % image_path)
	return {'dirs': sorted(dirs), 'files': dict(sorted(files.items()))}

def main():
	MANIFESTS_FOLDER_PATH.mkdir(exist_ok=True)
	for name, image_gz in MANIFESTS.items():
		image_path = decompress_gzip(CURRENT_FILE_PATH.joinpath(image_gz))
		manifest = make_manifest(image_path)
		outpath = MANIFESTS_FOLDER_PATH.joinpath(name + '.json')
		with open(outpath, 'w') as f:
			json.dump(manifest, f, indent='\t')
			f.write('\n')
		print('%s: %s dirs, %s files' % (outpath.name, len(manifest['dirs']), len(manifest['files'])))

if __name__ == '__main__':
	main()
