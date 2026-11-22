# Read-only probe of the Veeam lab VM: changes nothing, only reports.
# Run in an elevated PowerShell on the VM where Veeam Backup & Replication is installed:
#   powershell -ExecutionPolicy Bypass -File vbk_probe.ps1
# (if the Veeam module does not load, try the same with pwsh, PowerShell 7)
# Result: vbk_probe.txt next to the script.

$ErrorActionPreference = 'Continue'
$out = Join-Path $PSScriptRoot 'vbk_probe.txt'

& {
	'== PowerShell'
	$PSVersionTable.PSVersion.ToString()

	'== Windows'
	Get-CimInstance Win32_OperatingSystem | Format-List Caption, Version, BuildNumber, OSArchitecture

	'== Virtualization'
	Get-CimInstance Win32_Processor | Format-List Name, VirtualizationFirmwareEnabled, SecondLevelAddressTranslationExtensions, VMMonitorModeExtensions
	Get-CimInstance Win32_ComputerSystem | Format-List Model, HypervisorPresent, TotalPhysicalMemory

	'== Hyper-V features'
	try {
		Get-WindowsOptionalFeature -Online -FeatureName 'Microsoft-Hyper-V*' | Format-Table FeatureName, State -AutoSize
	} catch { "not available: $_" }
	'Hyper-V cmdlets: ' + [bool](Get-Command New-VM -ErrorAction SilentlyContinue)

	'== Disks'
	Get-Volume | Where-Object DriveLetter | Format-Table DriveLetter, FileSystem, Size, SizeRemaining -AutoSize

	'== Veeam module'
	try {
		Import-Module Veeam.Backup.PowerShell -ErrorAction Stop -WarningAction SilentlyContinue
		(Get-Module Veeam.Backup.PowerShell).Version.ToString()
	} catch { "import failed: $_" }

	'== Veeam cmdlets needed by the fixture script'
	$names = 'Connect-VBRServer', 'Get-VBRServer', 'Add-VBRHvHost', 'Find-VBRHvEntity', 'Start-VBRZip',
		'Add-VBRHvJob', 'Start-VBRJob', 'Get-VBRJob', 'Set-VBRJobAdvancedStorageOptions', 'Get-VBRBackupRepository',
		'Add-VBRBackupRepository', 'Get-VBRBackup', 'Get-VBRRestorePoint', 'Add-VBREncryptionKey',
		'Add-VBRProtectionGroup', 'Add-VBRComputerBackupJob'
	foreach ($name in $names) {
		'{0,-36} {1}' -f $name, [bool](Get-Command $name -ErrorAction SilentlyContinue)
	}

	'== Veeam server'
	try {
		Connect-VBRServer -ErrorAction Stop
		Get-VBRServer | Format-Table Name, Type -AutoSize
		Get-VBRBackupRepository | Format-Table Name, Type, Path -AutoSize
		try { Get-VBRInstalledLicense | Format-List Status, Edition, Type } catch { "licence: $_" }
	} catch { "connect failed: $_" }

	'== Parameters of the cmdlets (they differ between Veeam versions)'
	foreach ($name in 'Start-VBRZip', 'Add-VBRHvJob', 'Set-VBRJobAdvancedStorageOptions', 'Add-VBRHvHost') {
		$cmd = Get-Command $name -ErrorAction SilentlyContinue
		if ($cmd) { "$name : " + (($cmd.Parameters.Keys | Sort-Object) -join ', ') }
	}
} *>&1 | Out-File -FilePath $out -Encoding utf8 -Width 250

"written: $out"
