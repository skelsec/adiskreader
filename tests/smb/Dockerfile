# Samba server sharing the test images read-only, for testing the SMB datasource
FROM debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends samba \
    && rm -rf /var/lib/apt/lists/*
COPY smb.conf /etc/samba/smb.conf
COPY entrypoint.sh /entrypoint.sh
EXPOSE 445
ENTRYPOINT ["/entrypoint.sh"]
