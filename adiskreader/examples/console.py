import sys
import os
import asyncio
import traceback
import ntpath
import fnmatch
import datetime
from pathlib import Path
import shlex
import tqdm
import inspect
import typing
from typing import List, Dict
from adiskreader.external.aiocmd.aiocmd import aiocmd
from adiskreader.external.aiocmd.aiocmd.list_completer import ListPathCompleter
from adiskreader import logger
from adiskreader._version import __banner__

from adiskreader.datasource import DataSource
from adiskreader.disks import Disk


class DiskBrowser(aiocmd.PromptToolkitCmd):
	def __init__(self):
		aiocmd.PromptToolkitCmd.__init__(self, ignore_sigint=False) #Setting this to false, since True doesnt work on windows...
		self.__datasource = None
		self.__disk = None
		self.__partitions = []
		self.__partition = None
		self.__partition_id = None
		self.__filesystem = None
		self.__current_directory = None
		self.__buffer_dir_contents = False #True
		self.__subdirs = {}
		self.__files = {}
	
	def _cd_completions(self):
		return ListPathCompleter(get_current_dirs = self.get_current_dirs)

	def _get_completions(self):
		return ListPathCompleter(get_current_dirs = self.get_current_files)
	
	def _getdir_completions(self):
		return ListPathCompleter(get_current_dirs = self.get_current_dirs)
	
	def _sid_completions(self):
		return ListPathCompleter(get_current_dirs = self.get_current_files)
	
	def _dirsid_completions(self):
		return ListPathCompleter(get_current_dirs = self.get_current_dirs)
	
	def get_current_dirs(self):
		return list(self.__subdirs.keys())

	def get_current_files(self):
		return list(self.__files.keys())
	
	#def _mount_completions(self):
	#	return ListPathCompleter(get_current_dirs = self.__partitions.__iter__)
				
	
	def handle_exception(self, e, msg = None):
		#providing a more consistent exception handling
		frame = inspect.stack()[1]
		caller = frame.function
		args, _, _, values = inspect.getargvalues(frame[0])
		caller_args = {arg: values[arg] for arg in args}
		if 'self' in caller_args:
			del caller_args['self']
		if len(caller_args) > 0:
			caller += ' '
			for k,v in caller_args.items():
				caller += '%s=%s ' % (k,v)
			caller = caller[:-1]
		if caller.startswith('do_'):
			caller = caller[3:]
		to_print = 'CMD: "%s" ERR: ' % caller
		to_print += 'Error: %s' % e
		if msg is not None:
			to_print = msg+' '+to_print
		print(to_print)
		
		formatted_exception = "".join(traceback.format_exception(type(e), e, e.__traceback__))
		logger.debug("Traceback:\n%s", formatted_exception)
		return False, e	

	async def _on_close(self):
		pass

	async def do_close(self):
		try:
			if self.__datasource is not None:
				await self.__datasource.close()
			return True, None
		except Exception as e:
			return self.handle_exception(e)

	async def do_partinfo(self, to_print=True):
		try:
			self.__partitions = await self.__disk.list_partitions()
			if to_print is True:
				print('Partitions on current disk:')
				for i, part in enumerate(self.__partitions):
					print(f'[{i}] {part}')
			return True, None
		except Exception as e:
			return self.handle_exception(e)	
	
	async def do_diskinfo(self):
		try:
			print(self.__disk)
			return True, None
		except Exception as e:
			return self.handle_exception(e)

	async def do_fsinfo(self):
		print('TODO!')
		return True, None

	async def do_open(self, filepath:str):
		try:
			self.__datasource = await DataSource.from_url(filepath)
			self.__disk = await Disk.from_datasource(self.__datasource)
			await self.do_partinfo(False)
			if len(self.__partitions) == 0:
				print('No partitions found!')
				return False, None
			if len(self.__partitions) == 1:
				await self.do_mount(0)
				return True, None
			return True, None
		except Exception as e:
			traceback.print_exc()
			return self.handle_exception(e)

	async def do_mount(self, partition_id:int):
		try:
			partition_id = int(partition_id)
			self.__partition = self.__partitions[partition_id]
			self.__filesystem = await self.__partition.mount()
			self.__current_directory = await self.__filesystem.get_root()
			if self.__buffer_dir_contents is True:
				await self.do_refreshcurdir()
			dirpath = await self.__current_directory.resolve_full_path()
			self.__partition_id = partition_id
			self.prompt = '[%s][%s] $ ' % (self.__partition_id, dirpath)
			return True, None
		except Exception as e:
			return self.handle_exception(e)

	async def do_dir(self):
		return await self.do_ls()
	
	async def do_ls(self):
		try:
			if self.__filesystem is None:
				print('No mounted (active) filesystem!')
				return None, Exception('No mounted (active) filesystem!')
			if self.__current_directory is None:
				print('No directory selected!')
				return None, Exception('No directory selected!')
			
			async for entry in self.__current_directory.get_console_output():
				print(entry)
			
			return True, None
		except Exception as e:
			return self.handle_exception(e)
	
	async def do_get(self, file_name:str):
		try:
			if self.__filesystem is None:
				print('No mounted (active) filesystem!')
				return None, Exception('No mounted (active) filesystem!')
			if self.__current_directory is None:
				print('No directory selected!')
				return None, Exception('No directory selected!')
			
			file_obj = await self.__current_directory.get_child(file_name)
			if file_obj is None:
				print('File not found!')
				return False, None
			
			filename = await file_obj.resolve_full_path()
			file_obj = await self.__filesystem.open(filename)
			await file_obj.seek(0,2)
			file_size = await file_obj.tell()
			await file_obj.seek(0,0)
			pbar = tqdm.tqdm(total=file_size, unit='B', unit_scale=True)
			with open(file_name, 'wb') as f:
				while True:
					data = await file_obj.read(1024*1024)
					if data == b'':
						break
					f.write(data)
					pbar.update(len(data))
			return True, None
		except Exception as e:
			return self.handle_exception(e)

	async def do_getdir(self, dir_name:str):
		try:
			if self.__filesystem is None:
				print('No mounted (active) filesystem!')
				return None, Exception('No mounted (active) filesystem!')
			if self.__current_directory is None:
				print('No directory selected!')
				return None, Exception('No directory selected!')
			
			if dir_name not in self.__subdirs and (dir_name.find('/')!= -1 or dir_name.find('\\')!= -1):
				dir_obj = await self.__filesystem.get_record_by_path(dir_name)
			else:
				dir_obj = await self.__current_directory.get_child(dir_name)
			if dir_obj is None:
				print('Directory not found!')
				return False, None
			if dir_obj.is_directory() is False:
				print('Not a directory!')
				return False, None
			
			base_path = Path.cwd()
			rem_dir_path = await dir_obj.resolve_full_path()
			async for root, dirs, files in self.__filesystem.walk(rem_dir_path):
				indep_root = root.replace('\\', '/')
				if indep_root.startswith('/'):
					indep_root = indep_root[1:]
				root_path = base_path / Path(indep_root)
				root_path.mkdir(parents=True, exist_ok=True)
				for dir in dirs:
					dirpath = root_path / dir
					if dirpath.resolve().is_relative_to(base_path) is False:
						print('Skipping %s (unsafe)' % dirpath)
						continue
					dirpath.mkdir(parents=True, exist_ok=True)
				for f in files:
					loc_filepath = root_path / f
					if loc_filepath.resolve().is_relative_to(base_path) is False:
						print('Skipping %s (unsafe)' % loc_filepath)
						continue
					rem_filepath = '\\'.join([root, f])
					file_obj = await self.__filesystem.open(rem_filepath)
					await file_obj.seek(0,2)
					file_size = await file_obj.tell()
					await file_obj.seek(0,0)
					pbar = tqdm.tqdm(total=file_size, unit='B', unit_scale=True)
					with open(loc_filepath, 'wb') as f:
						while True:
							data = await file_obj.read(1024*1024)
							if data == b'':
								break
							f.write(data)
							pbar.update(len(data))
			return True, None
		except Exception as e:
			traceback.print_exc()
			return self.handle_exception(e)
		
	
	async def do_refreshcurdir(self):
		try:
			self.__subdirs = {}
			self.__files = {}
			async for etype, name, entry in self.__current_directory.get_children():
				if etype == 'dir':
					self.__subdirs[name] = entry
				elif etype == 'file':
					self.__files[name] = entry
				# otherwise we skip
			return True, None
		except Exception as e:
			return self.handle_exception(e)	

	async def do_cd(self, directory_name):
		try:
			# a partition must be mounted
			if self.__partition is None:
				print('No partition mounted!')
				return False, None
			
			# we want the previous directory
			if directory_name == '..':
				self.__current_directory = await self.__current_directory.get_parent()
				dirpath = await self.__current_directory.resolve_full_path()
				self.prompt = '[%s][%s] $ ' % (self.__partition_id, dirpath)
				if self.__buffer_dir_contents is True:
					_, err = await self.do_refreshcurdir()
					if err is not None:
						raise err
				return True, None
			
			# we want to go to a specific directory
			if directory_name.find('\\') != -1:
				if self.__current_directory is None:
					print('No directory selected for relative path traversal!')
					return False, None
				curpath = await self.__current_directory.resolve_full_path()
				directory_name = '\\'.join([curpath, directory_name])
					
				# this better be a full path
				newdir = await self.__filesystem.get_record_by_path(directory_name)
				if newdir is None:
					print('Directory not found!')
					return False, None
				self.__current_directory = newdir
				dirpath = await self.__current_directory.resolve_full_path()
				self.prompt = '[%s][%s] $ ' % (self.__partition_id, dirpath)
				if self.__buffer_dir_contents is True:
					_, err = await self.do_refreshcurdir()
					if err is not None:
						raise err
				return True, None
			
			# this is a relative path
			newdir = await self.__current_directory.get_child(directory_name)
			if newdir is None:
				raise Exception('Directory not found!')
			self.__current_directory = newdir
			dirpath = await self.__current_directory.resolve_full_path()
			self.prompt = '[%s][%s] $ ' % (self.__partition_id, dirpath)
			if self.__buffer_dir_contents is True:
				_, err = await self.do_refreshcurdir()
				if err is not None:
					raise err
			return True, None
			
			
		except Exception as e:
			traceback.print_exc()
			return self.handle_exception(e)
	
	async def do_enumperms(self, accessfilter:str, depth:int = 1, outfilename:str = None):
		print('TODO!')
		return True, None

	async def do_getfilesd(self, file_name):
		print('TODO!')
		return True, None

	async def do_getdirsd(self):
		print('TODO!')
		return True, None

async def amain(file_path:str, commands:List[str] = [], continue_on_error:bool = False, no_interactive:bool=False):
	client = DiskBrowser()
	_, err = await client._run_single_command('open', [file_path])
	if err is not None:
		sys.exit(1)
	if len(commands) == 0:
		if no_interactive is True:
			print('Not starting interactive!')
			sys.exit(1)
		await client.run()
	else:
		try:
			for command in commands:
				if command == 'i':
					await client.run()
					sys.exit(0)
				
				cmd = shlex.split(command)
				if cmd[0] == 'login':
					_, err = await client.do_login()
					if err is not None:
						sys.exit(1)
					continue
				
				print('>>> %s' % command)
				_, err = await client._run_single_command(cmd[0], cmd[1:])
				if err is not None and continue_on_error is False:
					print('Batch execution stopped early, because a command failed!')
					sys.exit(1)
			sys.exit(0)
		finally:
			await client.do_close()

def main():
	import argparse
	import platform
	import logging
	
	parser = argparse.ArgumentParser(description='Interactive SMB client')
	parser.add_argument('-v', '--verbose', action='count', default=0)
	parser.add_argument('-n', '--no-interactive', action='store_true')
	parser.add_argument('-c', '--continue-on-error', action='store_true', help='When in batch execution mode, execute all commands even if one fails')
	parser.add_argument('file_path', help = 'File path')
	parser.add_argument('commands', nargs='*')
	
	args = parser.parse_args()
	print(__banner__)

	if args.verbose >=1:
		logger.setLevel(logging.DEBUG)

	if args.verbose > 2:
		print('setting deepdebug')
		logger.setLevel(1) #enabling deep debug
		asyncio.get_event_loop().set_debug(True)
		logging.basicConfig(level=logging.DEBUG)

	asyncio.run(
		amain(
			args.file_path,
			args.commands, 
			args.continue_on_error, 
			args.no_interactive
		)
	)

if __name__ == '__main__':
	main()
	
	

	