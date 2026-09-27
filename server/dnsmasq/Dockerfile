FROM alpine:latest
RUN apk add --no-cache dnsmasq
COPY dnsmasq.conf.in entrypoint.sh /
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
