#!/usr/bin/env bash

die() {
  # (RC, message)
  rc="${1:-1}"
  message="${2:"No message given"}"
  echo >&2 "FATAL: $message"
  exit $rc
}

l() {
  echo >&2 "$@"
}

typeset -A PKGS_DEBIAN PKGLIST_TAB PKGS_FEDORA
PKGLIST_TAB=(
  [debian]=PKGS_DEBIAN
  [ubuntu]=PKGS_DEBIAN
  [fedora]=PKGS_FEDORA
)

PKGS_DEBIAN=(
  [dialog]=dialog
  [python3]=python3-minimal
  [git]=git
)

PKGS_FEDORA=(
  [dialog]=dialog
  [python3]=python3
  [git]=git
)

distro_id() {
  while IFS="=" read key val; do
    if [[ $key == "ID" ]]; then
      echo "$val"
      return 0
    fi
  done </etc/os-release
  return 1
}

get_pkgname() {
  local cmd qstr pkg
  cmd="${1:?Command required}"
  qstr="${PKGLIST_TAB[$(distro_id)]}[$cmd]"
  pkg="${!qstr}"
  if [[ $pkg ]]; then
    echo "$pkg"
    return 0
  fi
  return 1
}

install_command() {
  local distro cmd
  cmd="${1:?Command Required}"
  distro="$(distro_id)"

  pkg="$(get_pkgname "$cmd")"

  l "Preparing to install ${cmd} from package ${pkg}. Password may be required"

  if [[ $EUID -eq 0 ]]; then
    SUDO=""
  else
    SUDO="sudo"
  fi

  if [[ $distro == 'debian' || $distro == 'ubuntu' ]]; then
    $SUDO apt-get update
    $SUDO apt-get install -y "${pkg}"
  elif [[ $distro == 'fedora' ]]; then
    $SUDO dnf --refresh install -y "${pkg}"
  fi


}

ensure_command() {
  local cmd
  cmd="${1:?Command Required}"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    install_command "$cmd"
  else
    l "Using already installed ${cmd}"
  fi
}

build_venv() {
  # (dir, specs)
  dir="$1"
  shift 1
  python3 -m venv "$dir" --upgrade-deps
  "$dir/bin/pip" install wheel "$@"
}

main() {
  ensure_command dialog
  if ! dialog --defaultno --yesno \
         "EZFlash is prerelease software\n\nIt may eat your firmware and/or cat.\nWould you like to continue?" \
         0 0; then
    dialog --infobox "Exiting on user request..." 3 30
    exit 0
  fi
  ensure_command python3 2>&1 | dialog --progressbox "Dependency: python3" 30 80
  : "${HOME:?HOME is not set, not sure how that happened}"
  [[ -d $HOME/.ezf ]] || mkdir $HOME/.ezf
  build_venv "$HOME/.ezf/py" |& dialog --sleep 2 --progressbox "Python Environment" 30 80
  ensure_command git 2>&1 |& dialog --progressbox "Dependency: git" 30 80
  (
  if [[ -d "$HOME/.ezf/src" ]]; then
    GIT_WORK_TREE="$HOME/.ezf/src" GIT_DIR="$GIT_WORK_TREE/.git" git fetch
    GIT_WORK_TREE="$HOME/.ezf/src" GIT_DIR="$GIT_WORK_TREE/.git" git reset --hard origin/main
  else
    git clone "https://github.com/laikulo/klipper-ezflash.git" "$HOME/.ezf/src"
  fi
  ) |& dialog --sleep 2 --progressbox "Development checkout" 30 80
  "$HOME/.ezf/py/bin/pip" install "$HOME/.ezf/src" |& dialog --sleep 2 --progressbox "Development installation" 30 80
  dialog --clear --msgbox "Complete!" 0 0
}

main "$@"
exit $?
}