#!/usr/bin/env bash
# This installed file belongs to root. Dashboard may invoke it with NO args.
set -euo pipefail
(( EUID == 0 )) || { echo 'The update helper must run as root.' >&2; exit 1; }
if (( $# == 0 )); then
  exec systemd-run --unit=counter-update --collect /usr/local/sbin/counter-update --run
fi
[[ $# == 1 && $1 == --run ]] || { echo 'Unsupported update request.' >&2; exit 2; }
work=$(mktemp -d /var/tmp/counter-update-XXXXXX)
# Retain the checkout for diagnostics instead of deleting files during update.
export GIT_TERMINAL_PROMPT=0
git clone --depth 1 --branch main https://github.com/AloeVeraZ/Counter.git "$work/source"
bash "$work/source/install.sh" --user '@INSTALL_USER@'
