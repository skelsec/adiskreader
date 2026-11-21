# Samba and SSH (SFTP) server sharing the test images read-only, for testing the SMB and SSH datasources
FROM debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends samba openssh-server \
    && rm -rf /var/lib/apt/lists/*
COPY smb.conf /etc/samba/smb.conf
COPY entrypoint.sh /entrypoint.sh
EXPOSE 445 22
ENTRYPOINT ["/entrypoint.sh"]
