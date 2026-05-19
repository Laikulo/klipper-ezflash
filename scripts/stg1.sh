#!/usr/bin/env bash

# Invoke remotely with bash <(curl -L URL)
# This preserves stdin/out for proper console interactions

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
  :
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
    become apt-get update |& tee -a "$_LOGFDF"
    become apt-get -y install dialog |& tee -a "$_LOGFDF"
  elif have_command dnf; then
    log installing with dnf
    become dnf --refresh install -y dialog |& tee -a "$_LOGFDF"
  else
    log could not install - unknown package manager
    exit 1
  fi
}

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
}

trap handle_exit EXIT
trap handle_err  ERR
main "$@"
exit $?