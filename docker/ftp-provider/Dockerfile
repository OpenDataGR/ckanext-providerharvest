# Throwaway mock FTP provider for the Phase 1.5 integration test
# (ckanext/providerharvest/tests/integration/test_e2e_ftp_harvest.py).
#
# ProFTPD, not vsftpd: confirmed via a real CI run that Alpine's vsftpd
# build rejects MLSD ("500 Unknown command") -- FTPTransport.list_entries
# relies on MLSD (RFC 3659's structured directory listing, see
# transport/ftp.py's docstring for why over parsing legacy LIST output).
# ProFTPD ships mod_facts (MLSD/MLST support) enabled by default.
#
# Plain FTP only, deliberately -- FTPTransport defaults to FTPS with a
# real, verifying TLS context (ssl.create_default_context(), see
# transport/ftp.py), which a self-signed cert in a disposable test
# container would correctly FAIL to verify. Standing up a CA the test
# environment also trusts just to exercise the TLS path here would be
# a lot of test-only complexity for something the unit tests
# (test_transport_ftp.py) already cover directly with fakes. This
# container instead exercises the other real path DESIGN.md calls for:
# plain FTP, permitted only with an explicit per-source acknowledgment.
FROM alpine:3.20

RUN apk add --no-cache proftpd \
    && adduser -D -h /home/produser -s /sbin/nologin produser \
    && echo "produser:test-pass" | chpasswd \
    && mkdir -p /home/produser/upload \
    && chown -R produser:produser /home/produser

COPY upload/ /home/produser/upload/
RUN chown -R produser:produser /home/produser/upload
COPY proftpd.conf /etc/proftpd/proftpd.conf

EXPOSE 21 21100-21110
CMD ["proftpd", "--nodaemon"]
