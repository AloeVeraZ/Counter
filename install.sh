#!/usr/bin/env bash
# Run from a checkout or stream this launcher to Bash on the Pi.
set -Eeuo pipefail
repository=https://github.com/AloeVeraZ/Counter.git
branch=''
install_user=${SUDO_USER:-}
while (( $# )); do
  case "$1" in
    --branch) branch=${2:?Missing branch}; shift 2 ;;
    main|testing) branch=$1; shift ;;
    --user) install_user=${2:?Missing user}; shift 2 ;;
    --help|-h)
      echo 'Usage: bash install.sh [--branch main|testing] [--user PI_USERNAME]'
      echo 'Run on Raspberry Pi OS. The first successful install reboots the Pi.'
      exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
[[ -z $branch || $branch == main || $branch == testing ]] || { echo 'Choose main or testing.' >&2; exit 2; }
if [[ -z $install_user ]]; then install_user=$(id -un); fi
[[ $install_user != root ]] || { echo 'Run as your Pi user, or pass --user PI_USERNAME.' >&2; exit 2; }
source_dir=''
if [[ -n ${BASH_SOURCE[0]:-} ]]; then
  source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
fi
checkout_branch=''
if [[ -n $source_dir && -f $source_dir/installer/install.sh && -f $source_dir/pyproject.toml ]]; then
  checkout_branch=$(git -c safe.directory="$source_dir" -C "$source_dir" symbolic-ref --short HEAD 2>/dev/null || true)
  if [[ -z $branch && ( $checkout_branch == main || $checkout_branch == testing ) ]]; then branch=$checkout_branch; fi
  branch=${branch:-main}
  if [[ $branch == "$checkout_branch" ]]; then
    exec bash "$source_dir/installer/install.sh" --source "$source_dir" --branch "$branch" --user "$install_user"
  fi
fi
branch=${branch:-main}
[[ -f /proc/device-tree/model ]] && grep -q 'Raspberry Pi' /proc/device-tree/model || { echo 'Run this installer on Raspberry Pi OS. See the PC simulation instructions for other systems.' >&2; exit 2; }
if ! command -v git >/dev/null 2>&1; then
  sudo apt-get -o DPkg::Lock::Timeout=60 update
  sudo apt-get -o DPkg::Lock::Timeout=60 install -y git ca-certificates
fi
# Keep the download for diagnostics; never modify the caller's checkout.
work=$(mktemp -d /var/tmp/counter-download-XXXXXX)
echo "Downloading Counter / $branch..."
git clone --depth 1 --branch "$branch" "$repository" "$work/source"
[[ -f $work/source/installer/install.sh ]] || { echo "The $branch branch does not contain the new installer yet. Use testing until it is merged into main." >&2; exit 1; }
bash "$work/source/installer/install.sh" --source "$work/source" --branch "$branch" --user "$install_user"
