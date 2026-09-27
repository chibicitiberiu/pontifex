/* Pontifex iPXE build options (copied to src/config/local/general.h by build.sh) */

/* HTTPS on every platform, BIOS too, so "chain https://boot.netboot.xyz" works */
#define DOWNLOAD_PROTO_HTTPS

/* Handy shell commands when debugging a machine */
#define NSLOOKUP_CMD
#define PING_CMD
#define NTP_CMD		/* retro boxes with a dead CMOS battery fail HTTPS without a sane clock */
#define REBOOT_CMD
#define POWEROFF_CMD
#define CONSOLE_CMD
