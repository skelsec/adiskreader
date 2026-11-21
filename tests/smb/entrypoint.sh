#!/bin/sh
set -e
useradd -M -s /usr/sbin/nologin "$SMB_USER"
printf '%s\n%s\n' "$SMB_PASSWORD" "$SMB_PASSWORD" | smbpasswd -s -a "$SMB_USER"
printf '%s:%s\n' "$SMB_USER" "$SMB_PASSWORD" | chpasswd
mkdir -p /run/sshd
/usr/sbin/sshd -o PasswordAuthentication=yes -o ForceCommand=internal-sftp
exec smbd --foreground --no-process-group --debug-stdout
