# Throwaway mock SCP provider for the Phase 1.5 integration test
# (ckanext/providerharvest/tests/integration/test_e2e_scp_harvest.py).
# NOT atmoz/sftp (used for the SFTP test server): that image's whole
# design forces `ForceCommand internal-sftp` for every declared user,
# which specifically blocks raw `scp`/exec-channel commands -- exactly
# what ScpTransport needs (its list_entries runs `find` over an exec
# channel, its open_entry uses the real `scp` protocol). A plain OpenSSH
# server with password auth and no forced command is what a real
# SCP-only provider actually looks like, so that's what this builds.
FROM alpine:3.20

RUN apk add --no-cache openssh-server findutils \
    && ssh-keygen -A \
    && adduser -D -h /home/produser -s /bin/sh produser \
    && echo "produser:test-pass" | chpasswd \
    && mkdir -p /home/produser/upload

COPY upload/ /home/produser/upload/
COPY sshd_config /etc/ssh/sshd_config
RUN chown -R produser:produser /home/produser

EXPOSE 22
CMD ["/usr/sbin/sshd", "-D", "-e"]
