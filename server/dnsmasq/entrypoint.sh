#!/bin/sh
# Render the config for this site, then run dnsmasq in the foreground.
set -eu
sed -e "s|@IFACE@|$PONTIFEX_IFACE|g" -e "s|@SUBNET@|$PONTIFEX_SUBNET|g" \
    /dnsmasq.conf.in > /etc/dnsmasq.conf
exec dnsmasq --keep-in-foreground --log-facility=- --conf-file=/etc/dnsmasq.conf
