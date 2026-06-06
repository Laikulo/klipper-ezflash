#!/usr/bin/env bash

# Invoke remotely with bash <(curl -L URL)
# This preserves stdin/out for proper console interactions

# TODO: Write failure handler
# TODO: Check status of everything important

handle_exit() {
  trap - EXIT
  if [[ $_LOGFD ]]; then
    exec {_LOGFD}>&-
    unset _LOGFD
    unset _LOGFDF
  fi
  if [[ $_LOGFILE ]]; then
    echo "Log file written to $_LOGFILE"
    echo "===END===" >> "$_LOGFILE"
    unset _LOGFILE
  fi
}

handle_err(){
  log "FATAL: In error handler"
  echo "A fatal error has occurred. "
  # TODO: Write some details to the log
}

setup_logging() {
  local logdir logname
  if [[ -d "$HOME/printer_data/logs" ]]; then
    logdir="$HOME/printer_data/logs"
  else
    logdir="/tmp"
  fi

  logname="ezfinstall-$EPOCHSECONDS-$$.log"

  _LOGFILE="$logdir/$logname"
  exec {_LOGFD}>>"$_LOGFILE"
  _LOGFDF="/dev/fd/$_LOGFD"
  cat <<<===START=== >&$_LOGFD
  echo "Logging to '${_LOGFILE}'. please provide this file when requesting support." >&2
}

log() {
  echo $@ >&$_LOGFD
}

confirm_tty() {
  local response
  while true; do
    read -p "[y/n]? " -n1 -r response
    if [[ $response =~ [yY] ]]; then
      echo ""
      return 0
    elif [[ $response =~ [Nn] ]]; then
      echo ""
      return 1
    fi
  done
}

become() {
  if [[ $EUID -ne 0 ]]; then
    sudo "$@"
  else
    "$@"
  fi
  return $?
}

have_command() {
  command -v "$1" &>/dev/null
  return $?
}

distro_id() {
  while IFS="=" read key val; do
    if [[ $key == "ID" ]]; then
      echo "$val"
      return 0
    fi
  done </etc/os-release
  return 1
}

log_tee() {
  local prefix input_line
  if [[ $1 ]]; then
    prefix="$1: "
  else
    prefix=""
  fi

  while read -r input_line; do
    echo "$prefix$input_line" >&$_LOGFD
    echo "$input_line"
  done
}

ensure_dialog() {
  if have_command dialog; then
    log Dialog already installed
    return
  fi
  log Offering dialog install
  echo "Welcome to the EZFlash installer."
  echo "This tool requires the dialog command, which does not appear to be installed."
  echo -n "Shall I attempt to install it "
  if ! confirm_tty; then
    log User declined dialog install
    echo -n "Exiting on user request"
    exit 0
  fi
  log Beginning dialog install
  if have_command apt-get; then
    log installing with apt
    become apt-get update |& log_tee apt-update
    become apt-get -y install dialog |& log_tee apt-install
  elif have_command dnf; then
    log installing with dnf
    become dnf --refresh install -y dialog |& log_tee dnf
  else
    log could not install - unknown package manager
    exit 1
  fi
  _HAVE_DIALOG="yes"
}

_HAVE_DIALOG=""



main() {
  setup_logging
  ensure_dialog
  if ! dialog --defaultno --yesno \
         "EZFlash is prerelease software\n\nIt may eat your firmware and/or cat.\nWould you like to continue?" \
         0 0; then
    log Exiting on user request
    dialog --infobox "Exiting on user request..." 3 30
    exit 0
  fi
  local basedir pyenv distro repodir

  distro="$(distro_id)"

  if [[ $distro =~ debian|ubuntu ]]; then
    dialog --infobox "Checking dependencies..." 3 30
    typeset -a missing_pkgs
    missing_debs=()
    for pkgname in python3 python3-pip-whl python3-venv git; do
      log "checking $pkgname"
      dpkg-query --show "$pkgname" |& log_tee dpkg-query >/dev/null
      if [[ ${PIPESTATUS[0]} -eq 0 ]]; then
        log "found $pkgname"
      else
        log "added $pkgname to install list"
        missing_debs+=( "$pkgname" )
      fi
    done

    if [[ "${missing_debs}" ]] ; then
      dialog --msgbox "Ready to install dependencies\nyour password may be needed" 8 50
      become apt-get update | log_tee apt-update | dialog --progressbox "Package DB Update" 20 80
      become apt-get install -y "${missing_debs[@]}" | log_tee apt-install | dialog --progressbox "Package Install" 20 80
    else
      dialog --sleep 2 --infobox "All dependencies already present" 3 50
    fi
  else
    dialog --msgbox "Unknown distro $distro, dependencies will not be automatically installed"
  fi


  basedir="$HOME/.ezf"
  if [[ -d $basedir ]]; then
    log "Basedir already exists"
    if ! dialog --defaultno --yesno \
           "There may already be a copy of EZFlash installed. Proceed?" \
           0 0; then
      log Exiting on user request
      dialog --infobox "Exiting on user request..." 3 30
      exit 0
    fi
  else
    mkdir "$basedir"
  fi

  pyenv="$basedir/py"
  python3 -m venv --clear --prompt ezf "$pyenv" |& log_tee py-venv | dialog --progressbox "Python environment" 20 80
  (
    $pyenv/bin/pip install -U pip
    $pyenv/bin/pip install wheel
  ) |& log_tee pip | dialog --progressbox "Python packages" 20 80

  repodir="$basedir/src"
  if [[ -d $repodir ]]; then
    log "Using existing repo"
    pushd "$repodir"
    (
      git fetch -v origin feat/installer
      git reset --hard FETCH_HEAD
    ) |& log_tee git | dialog --progressbox "Git Fetch" 20 80
  else
    log "Cloning new repo"
    git clone -v https://github.com/laikulo/klipper-ezflash.git -b main "$repodir" |& log_tee git | dialog --progressbox "Git Clone" 20 80
  fi

  log "editable installation"
  "$pyenv/bin/pip" install -e "$repodir" |& log_tee pip | dialog --progressbox "Development Installation" 20 80

  dialog --infobox "Creating Symlinks..."
  [[ -d $HOME/.local/bin ]] || mkdir -p "$HOME/.local/bin"
  ln -s "$pyenv/bin/ezf" "$HOME/.local/bin"
  ln -s "$pyenv/bin/ezf" "$HOME/ezf"

  dialog --clear --msgbox "Installation complete!" 5 30
  clear
}

trap handle_exit EXIT
trap handle_err  ERR
main "$@"
exit $?
