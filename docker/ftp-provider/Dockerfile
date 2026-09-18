# Throwaway mock FTP provider for the Phase 1.5 integration test
# (ckanext/providerharvest/tests/integration/test_e2e_ftp_harvest.py).
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

RUN apk add --no-cache vsftpd \
    && adduser -D -h /home/produser -s /sbin/nologin produser \
    && echo "produser:test-pass" | chpasswd \
    && mkdir -p /home/produser/upload \
    && chown -R produser:produser /home/produser

COPY upload/ /home/produser/upload/
RUN chown -R produser:produser /home/produser/upload
COPY vsftpd.conf /etc/vsftpd/vsftpd.conf

EXPOSE 21 21100-21110
CMD ["vsftpd", "/etc/vsftpd/vsftpd.conf"]
