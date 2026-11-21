#!/bin/sh
set -e
useradd -M -s /usr/sbin/nologin "$SMB_USER"
printf '%s\n%s\n' "$SMB_PASSWORD" "$SMB_PASSWORD" | smbpasswd -s -a "$SMB_USER"
exec smbd --foreground --no-process-group --debug-stdout
