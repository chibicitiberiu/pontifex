# Pontifex disk writer: take over tty1 after Tiny Core's root autologin (boot code "superuser")
if [ "$(tty)" = /dev/tty1 ] && [ -z "$PONTIFEX_WRITER" ]; then
    exec /usr/local/bin/pontifex-writer
fi
