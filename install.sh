#!/usr/bin/env bash
# Install Counter's committed files into its own release directory.
set -euo pipefail
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if (( EUID != 0 )); then
  exec sudo bash "$source_dir/install.sh" --user "$(id -un)" "$@"
fi
install_user=${SUDO_USER:-}
while (( $# )); do
  case "$1" in
    --user) install_user=${2:?Missing user}; shift 2 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
[[ $install_user =~ ^[a-z_][a-z0-9_-]*[$]?$ && $install_user != root ]] || { echo 'Use --user with a non-root Pi username.' >&2; exit 2; }
id "$install_user" >/dev/null
[[ -f /proc/device-tree/model ]] && grep -q 'Raspberry Pi' /proc/device-tree/model || { echo 'This installer is for Raspberry Pi OS. Use counter --simulate on a PC.' >&2; exit 2; }
exec 9>/run/counter-install.lock
flock -n 9 || { echo 'Another Counter install is running.' >&2; exit 1; }
commit=$(git -C "$source_dir" rev-parse HEAD)
[[ $commit =~ ^[0-9a-f]{40}$ ]] || exit 1
apt-get update
# lgpio's source build needs Python headers and the native -llgpio library.
apt-get install -y python3 python3-venv python3-dev liblgpio-dev libpam0g libpam-modules libpam-runtime git curl i2c-tools swig build-essential
raspi-config nonint do_i2c 0
usermod -a -G i2c,gpio "$install_user"
install -d -m 755 /opt/counter/releases /usr/local/lib/counter
release=$(mktemp -d "/opt/counter/releases/${commit:0:7}-XXXXXX")
git -C "$source_dir" archive HEAD | tar -x -C "$release"
printf '%s\n' "$commit" > "$release/INSTALL_COMMIT"
python3 -m venv "$release/.venv"
"$release/.venv/bin/python" -m pip install "$release[pi]"
PYTHONPATH="$release/core" "$release/.venv/bin/python" -m unittest discover -s "$release/tests" -v
install -d -m 750 -o "$install_user" -g "$(id -gn "$install_user")" /var/lib/counter
# Save the chosen account inside a root-owned helper, not in dashboard input.
sed "s/@INSTALL_USER@/$install_user/g" "$release/installer/update.sh" > /usr/local/sbin/counter-update
chmod 755 /usr/local/sbin/counter-update
chown root:root /usr/local/sbin/counter-update
printf '%s ALL=(root) NOPASSWD: /usr/local/sbin/counter-update ""\n' "$install_user" > /etc/sudoers.d/counter-update
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
ExecStart=/opt/counter/current/.venv/bin/counter --host 0.0.0.0 --port 8080 --config /var/lib/counter/config.json
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
  if curl --fail --silent http://127.0.0.1:8080/health | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin).get("installed_commit") == sys.argv[1] else 1)' "$commit" 2>/dev/null && systemctl is-active --quiet counter.service; then healthy=true; break; fi
  sleep 1
done
if [[ $healthy != true ]]; then
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
echo "Counter installed: http://$(hostname).local:8080"
for pi_address in $(hostname -I); do
  [[ $pi_address == *:* ]] && continue
  echo "Counter IP: http://$pi_address:8080"
done
echo 'Outputs start stopped. Calibration is stored in /var/lib/counter/config.json.'
echo "After boot, open the Pi's current IP address on port 8080 and enter the Pi password for $install_user."
if [[ -z $previous ]]; then
  echo 'Initial install succeeded. Rebooting the Pi now; reconnect after it boots.'
  systemctl reboot
fi
