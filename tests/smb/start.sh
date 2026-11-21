#!/bin/sh
# Starts a container sharing tests/testfiles over SMB (127.0.0.1:4445) and SSH (127.0.0.1:2222)
# for test_smb.py and test_ssh.py
# Stop it with: docker stop adiskreader-smb
set -e
cd "$(dirname "$0")"
. ./smb.env
docker build -q -t adiskreader-smb . > /dev/null
docker rm -f adiskreader-smb > /dev/null 2>&1 || true
docker run -d --rm --name adiskreader-smb \
    -p 127.0.0.1:4445:445 \
    -p 127.0.0.1:2222:22 \
    --env-file smb.env \
    -v "$(pwd)/../testfiles:/share:ro" \
    adiskreader-smb > /dev/null
echo "export ADISKREADER_SMB_URL='smb2+ntlm-password://$SMB_USER:$SMB_PASSWORD@127.0.0.1:4445/images'"
echo "export ADISKREADER_SSH_URL='ssh+password://$SMB_USER:$SMB_PASSWORD@127.0.0.1:2222/share?verify=false'"
