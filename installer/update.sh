#!/usr/bin/env bash
# Root-owned helper. Sudo permits only no arguments, main, or testing.
set -Eeuo pipefail
(( EUID == 0 )) || { echo 'The update helper must run as root.' >&2; exit 1; }
if (( $# <= 1 )); then
  branch=${1:-}
  if [[ -z $branch ]]; then branch=$(cat /opt/counter/current/INSTALL_REF 2>/dev/null || echo main); fi
  [[ $branch == main || $branch == testing ]] || { echo 'Choose main or testing.' >&2; exit 2; }
  exec 8>/run/counter-update-start.lock
  flock -n 8 || { echo 'An update is already starting.' >&2; exit 1; }
  if systemctl is-active --quiet counter-update.service; then
    echo 'An update is already running.' >&2; exit 1
  fi
  log=/var/log/counter-update.log
  echo "Starting Counter / $branch." > "$log"
  chmod 644 "$log"
  # Run a root-owned snapshot: installing a release replaces the public helper.
  job_script=$(mktemp /run/counter-update-XXXXXX)
  cp -- "${BASH_SOURCE[0]}" "$job_script"
  chmod 700 "$job_script"
  systemd-run --unit=counter-update --collect --property=StandardOutput=append:"$log" --property=StandardError=append:"$log" bash "$job_script" --run "$branch"
  echo "Installing Counter / $branch. Progress: journalctl -u counter-update"
  exit 0
fi
[[ $# == 2 && $1 == --run && ( $2 == main || $2 == testing ) ]] || { echo 'Unsupported update request.' >&2; exit 2; }
branch=$2
trap 'status=$?; if (( status == 0 )); then echo "Counter update completed."; else echo "Counter update failed (exit $status)."; fi' EXIT
work=$(mktemp -d /var/tmp/counter-update-XXXXXX)
echo "==> downloading Counter / $branch"
export GIT_TERMINAL_PROMPT=0
git clone --depth 1 --branch "$branch" https://github.com/AloeVeraZ/Counter.git "$work/source"
[[ -f $work/source/installer/install.sh ]] || { echo "The $branch branch does not contain the new installer yet. Use testing until it is merged into main." >&2; exit 1; }
bash "$work/source/installer/install.sh" --source "$work/source" --branch "$branch" --user '@INSTALL_USER@'
