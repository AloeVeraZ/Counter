#!/usr/bin/env bash
# Root-owned helper. Sudo permits only no arguments, main, or testing.
set -Eeuo pipefail
(( EUID == 0 )) || { echo 'The update helper must run as root.' >&2; exit 1; }
log=/var/log/counter-update.log
if (( $# <= 1 )); then
  branch=${1:-}
  if [[ -z $branch ]]; then branch=$(cat /opt/counter/current/INSTALL_REF 2>/dev/null || echo main); fi
  [[ $branch == main || $branch == testing ]] || { echo 'Choose main or testing.' >&2; exit 2; }
  if systemctl is-active --quiet counter-update.service; then
    echo 'An update is already running.' >&2; exit 1
  fi
  # The dashboard runs this inside Counter's read-only service sandbox, so write
  # nothing here: systemd starts the job outside it, and its unit name allows
  # only one update at a time. The job's output starts a fresh log.
  systemctl reset-failed counter-update.service >/dev/null 2>&1 || true
  systemd-run --unit=counter-update --collect --property=StandardOutput=truncate:"$log" --property=StandardError=inherit /bin/bash "${BASH_SOURCE[0]}" --run "$branch"
  echo "Installing Counter / $branch. Progress: journalctl -u counter-update"
  exit 0
fi
[[ $# == 2 && $1 == --run && ( $2 == main || $2 == testing ) ]] || { echo 'Unsupported update request.' >&2; exit 2; }
branch=$2
echo "Starting Counter / $branch."
chmod 644 "$log" 2>/dev/null || true
trap 'status=$?; if (( status == 0 )); then echo "Counter update completed."; else echo "Counter update failed (exit $status)."; fi' EXIT
work=$(mktemp -d /var/tmp/counter-update-XXXXXX)
echo "==> downloading Counter / $branch"
export GIT_TERMINAL_PROMPT=0
git clone --depth 1 --branch "$branch" https://github.com/AloeVeraZ/Counter.git "$work/source"
[[ -f $work/source/installer/install.sh ]] || { echo "The $branch branch does not contain the new installer yet. Use testing until it is merged into main." >&2; exit 1; }
bash "$work/source/installer/install.sh" --source "$work/source" --branch "$branch" --user '@INSTALL_USER@'
