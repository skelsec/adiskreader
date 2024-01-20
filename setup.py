from setuptools import setup, find_packages
import re

VERSIONFILE="adiskreader/_version.py"
verstrline = open(VERSIONFILE, "rt").read()
VSRE = r"^__version__ = ['\"]([^'\"]*)['\"]"
mo = re.search(VSRE, verstrline, re.M)
if mo:
    verstr = mo.group(1)
else:
    raise RuntimeError("Unable to find version string in %s." % (VERSIONFILE,))

setup(
	# Application name:
	name="adiskreader",

	# Version number (initial):
	version=verstr,

	# Application author details:
	author="Tamas Jos",
	author_email="info@skelsec.com",

	# Packages
	packages=find_packages(exclude=["tests*", "devel*"]),

	# Include additional files into the package
	include_package_data=True,


	# Details
	url="https://github.com/skelsec/adiskreader",

	zip_safe=True,
	#
	# license="LICENSE.txt",
	description="",

	# long_description=open("README.txt").read(),
	python_requires='>=3.6',
	classifiers=[
		"Programming Language :: Python :: 3.6",
		"License :: OSI Approved :: MIT License",
		"Operating System :: OS Independent",
	],
	install_requires=[
		'cachetools',
		'winacl>=0.1.8',
		'aiosmb>=0.4.10',
        'amurex>=0.0.4',
        'prompt-toolkit>=3.0.2',
		'tqdm',
	],
    tests_require=[
        'pytest',
        'pytest-cov',
        'pytest-asyncio',
    ],

	entry_points={
		'console_scripts': [
			'adiskreader-console   = adiskreader.examples.console:main',
		],
	}
)
