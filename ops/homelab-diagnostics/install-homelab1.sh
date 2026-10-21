#!/bin/bash
# Run as root in the host's mount/PID/network namespaces on homelab1.
set -euo pipefail
[ "$(hostname)" = homelab1 ] || { echo 'This sender configuration is for homelab1.' >&2; exit 1; }
[ "$(id -u)" = 0 ] || exit 1
command -v kdump-config >/dev/null || { echo 'Install kdump-tools and makedumpfile first.' >&2; exit 1; }
install -d -m 0755 /etc/modules-load.d /etc/modprobe.d /etc/default/grub.d /etc/sysctl.d
cat > /etc/modules-load.d/homelab-netconsole.conf <<'CONF'
netconsole
CONF
cat > /etc/modprobe.d/homelab-netconsole.conf <<'CONF'
# Wired LAN receiver on zerotwo. Explicit destination MAC avoids broadcast.
options netconsole netconsole=+6665@192.168.2.46/enp2s0,6666@192.168.2.98/38:05:25:39:33:56
CONF
modprobe netconsole
# 512 MiB is reserved for the dump kernel at the next boot. The ext4 root
# filesystem has sufficient space for dumps and remains independent of tank.
# Remove the superseded file from the initial installation.
rm -f /etc/default/grub.d/99-homelab-crashkernel.cfg
cat > /etc/default/grub.d/zz-homelab-crashkernel.cfg <<'CONF'
GRUB_CMDLINE_LINUX_DEFAULT="$(printf '%s\n' "$GRUB_CMDLINE_LINUX_DEFAULT" | sed -E 's/(^|[[:space:]])crashkernel=[^[:space:]]+//g') crashkernel=512M"
CONF
# Preserve distribution settings and ensure capture is enabled.
sed -i -E 's/^#?USE_KDUMP=.*/USE_KDUMP=1/' /etc/default/kdump-tools
grep -q '^USE_KDUMP=' /etc/default/kdump-tools || echo USE_KDUMP=1 >> /etc/default/kdump-tools
# A 16 GiB host can produce sizeable dumps. Exclude free/cache pages and compress.
sed -i '/^# BEGIN homelab-diagnostics$/,/^# END homelab-diagnostics$/d' /etc/default/kdump-tools
cat >> /etc/default/kdump-tools <<'CONF'

# BEGIN homelab-diagnostics
# Keep dumps off ZFS and bound capture-kernel memory.
KDUMP_COREDIR="/var/crash"
MAKEDUMP_ARGS="-c -d 31"
# END homelab-diagnostics
CONF
cat > /etc/sysctl.d/90-homelab-crash-capture.conf <<'CONF'
# Applied at the next boot, alongside the reserved crash kernel.
kernel.nmi_watchdog = 1
kernel.watchdog = 1
kernel.softlockup_panic = 1
kernel.hardlockup_panic = 1
kernel.panic_on_oops = 1
kernel.panic = 60
CONF
# Do not activate panic-on-lockup before a capture kernel can be loaded.
if [ "$(cat /sys/kernel/kexec_crash_loaded)" = 1 ]; then
    sysctl -p /etc/sysctl.d/90-homelab-crash-capture.conf
fi
update-grub
systemctl enable kdump-tools
printf '<3>homelab-diagnostics: intentional netconsole delivery test %s\n' "$(date -Is)" > /dev/kmsg
printf '\nCrash capture status (a reboot is needed if no crash memory is reserved):\n'
kdump-config show
cat /sys/kernel/kexec_crash_size /sys/kernel/kexec_crash_loaded
