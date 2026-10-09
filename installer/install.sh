#!/usr/bin/env bash
# Install Counter's committed files into its own release directory.
set -Eeuo pipefail
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
install_user=${SUDO_USER:-}
install_ref=main
while (( $# )); do
  case "$1" in
    --user) install_user=${2:?Missing user}; shift 2 ;;
    --source) source_dir=${2:?Missing source}; shift 2 ;;
    --branch) install_ref=${2:?Missing branch}; shift 2 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
[[ $install_ref == main || $install_ref == testing ]] || { echo 'Choose main or testing.' >&2; exit 2; }
if [[ $install_ref == testing ]]; then
  echo 'WARNING: testing contains experimental changes and can break installation or servo behavior.'
fi
source_dir=$(cd -- "$source_dir" && pwd)
if (( EUID != 0 )); then
  exec sudo bash "$source_dir/installer/install.sh" --source "$source_dir" --branch "$install_ref" --user "${install_user:-$(id -un)}"
fi
step='checking the Pi'
trap 'status=$?; printf "Counter install failed while %s (line %s).\n" "$step" "$LINENO" >&2; exit "$status"' ERR
[[ $install_user =~ ^[a-z_][a-z0-9_-]*[$]?$ && $install_user != root ]] || { echo 'Use --user with a non-root Pi username.' >&2; exit 2; }
id "$install_user" >/dev/null
[[ -f /proc/device-tree/model ]] && grep -q 'Raspberry Pi' /proc/device-tree/model || { echo 'This installer is for Raspberry Pi OS. Use counter --simulate on a PC.' >&2; exit 2; }
umask 022
exec 9>/run/counter-install.lock
flock -n 9 || { echo 'Another Counter install is running.' >&2; exit 1; }
commit=$(git -c safe.directory="$source_dir" -C "$source_dir" rev-parse HEAD)
[[ $commit =~ ^[0-9a-f]{40}$ ]] || exit 1
step='installing Raspberry Pi OS dependencies'
export DEBIAN_FRONTEND=noninteractive
apt-get -o DPkg::Lock::Timeout=60 update
# Use the OS's lgpio binary built for /usr/bin/python3, including Python 3.13.
apt-get -o DPkg::Lock::Timeout=60 install -y python3 python3-venv python3-lgpio libpam0g libpam-modules libpam-runtime git curl ca-certificates i2c-tools
/usr/bin/python3 -c 'import sys; assert sys.version_info >= (3, 11), "Counter requires Python 3.11 or newer"; import lgpio; from importlib.metadata import version; print("OS lgpio:", version("lgpio"))'
step='enabling I²C and GPIO access'
raspi-config nonint do_i2c 0
usermod -a -G i2c,gpio "$install_user"
install -d -m 755 /opt/counter/releases /usr/local/lib/counter
release=$(mktemp -d "/opt/counter/releases/${commit:0:7}-XXXXXX")
# mktemp creates 0700 directories; the service user must be able to enter the release.
chmod 755 "$release"
step='building the Counter release'
git -c safe.directory="$source_dir" -C "$source_dir" archive HEAD | tar -x -C "$release"
printf '%s\n' "$commit" > "$release/INSTALL_COMMIT"
printf '%s\n' "$install_ref" > "$release/INSTALL_REF"
/usr/bin/python3 -m venv --system-site-packages "$release/.venv"
"$release/.venv/bin/python" -m pip install "$release[pi]"
PYTHONPATH="$release/core" "$release/.venv/bin/python" -m unittest discover -s "$release/tests" -v
step="checking that $install_user can run the release"
runuser -u "$install_user" -- test -x "$release/.venv/bin/counter"
step='configuring the Counter service'
install -d -m 750 -o "$install_user" -g "$(id -gn "$install_user")" /var/lib/counter
# Save the chosen account inside a root-owned helper, not in dashboard input.
sed "s/@INSTALL_USER@/$install_user/g" "$release/installer/update.sh" > /usr/local/sbin/counter-update
chmod 755 /usr/local/sbin/counter-update
chown root:root /usr/local/sbin/counter-update
printf '%s ALL=(root) NOPASSWD: /usr/local/sbin/counter-update "", /usr/local/sbin/counter-update main, /usr/local/sbin/counter-update testing\n' "$install_user" > /etc/sudoers.d/counter-update
chmod 440 /etc/sudoers.d/counter-update
visudo -cf /etc/sudoers.d/counter-update
cat > /etc/pam.d/counter <<'PAM'
# Check this Pi's current account password; no Counter password database.
auth required pam_unix.so
account required pam_unix.so
PAM
chmod 644 /etc/pam.d/counter
cat > /etc/systemd/system/counter.service <<UNIT
[Unit]
Description=Counter mechanical digit display
After=network.target
[Service]
Type=simple
User=$install_user
SupplementaryGroups=i2c gpio
WorkingDirectory=/opt/counter/current
Environment=COUNTER_RELEASE=/opt/counter/current
Environment=GPIOZERO_PIN_FACTORY=lgpio
Environment=COUNTER_LOGIN_USER=$install_user
# Port 80 lets a browser open the bare Pi address; 8080 keeps older links working.
AmbientCapabilities=CAP_NET_BIND_SERVICE
ExecStart=/opt/counter/current/.venv/bin/counter --host 0.0.0.0 --port 80 --port 8080 --config /var/lib/counter/config.json
Restart=on-failure
RestartSec=3
TimeoutStopSec=10
NoNewPrivileges=false
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/var/lib/counter
[Install]
WantedBy=multi-user.target
UNIT
step='starting Counter'
previous=''
if [[ -d /opt/counter/current ]]; then
  previous=$(readlink -f /opt/counter/current)
fi
ln -sfn "$release" /opt/counter/current
systemctl daemon-reload
systemctl enable counter.service
systemctl restart counter.service
healthy=false
for attempt in {1..20}; do
  if curl --fail --silent --max-time 3 http://127.0.0.1/health | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin).get("installed_commit") == sys.argv[1] else 1)' "$commit" 2>/dev/null && systemctl is-active --quiet counter.service; then healthy=true; break; fi
  sleep 1
done
if [[ $healthy != true ]]; then
  journalctl -u counter.service -n 30 --no-pager >&2 || true
  echo 'Counter did not start; restoring the previous release.' >&2
  if [[ -n $previous && -d $previous ]]; then
    ln -sfn "$previous" /opt/counter/current
    systemctl restart counter.service
  else
    systemctl stop counter.service
    unlink /opt/counter/current
  fi
  exit 1
fi
echo "Counter installed / $install_ref: http://$(hostname).local"
for pi_address in $(hostname -I); do
  [[ $pi_address == *:* ]] && continue
  echo "Counter IP: http://$pi_address"
done
echo 'Outputs start stopped. Calibration is stored in /var/lib/counter/config.json.'
echo "After boot, open the Pi's current IP address in a browser and enter the Pi password for $install_user."
if [[ -z $previous ]]; then
  echo 'Initial install succeeded. Rebooting the Pi now; reconnect after it boots.'
  systemctl reboot
fi
