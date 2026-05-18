# Klipper EZ-Flash

## Synopsis
A tool to guide users through the process of building and flashing klipper
with a minimum of "close to the metal" interactions

__NOTE__: At this point, most of the following docs are developer facing. As the project matures,
          these will be moved to their own location, and this file will become user-facing.

## Aspects of a user
* The end user is able to;
  * SSH to a print host
  * Execute pre-provided commands
  * Interact with dialogs
  * Interact with simple TTY prompts (e.g. `Install the thing [y/n]`)
  * Identify the make, model, and variants of boards they are using, and know what commuication method they plan to use.
* The user is not expected to:
  * Be able to read and understand a backtrace
  * Troubleshoot python environment issues (missing deps)
  * Be comfortably aware of the modal aspects of their SSH session (working dir, envvars, &c)
* It is assumed that the user probably is:
  * On debian, armbian, or similar (or is able to compensate for environmental differences)
  * Running a supported version of their OS
    * Debian/Armbian Bookworm, Trixie
    * Ubuntu LTS: Jammy, Noble, Resolute
    * Ubuntu Interim: Questing (as of this writing)
  * Has installed their distribution's default python.
  * Has klipper checked out at `~/klipper`

## User-facing docs guidance
* When providing a command for a user to run, do not use relative paths (`~` and `$` expansions are fine)
  * We assume that the user is a novice when it comes to the CLI
    and may not be aware of their current working directory.
* If providing a multi-line thing to copy-paste, wrap it in `{}` to guard against partial copy-paste.
  * This makes such copies all or nothing, as they will "hang" looking for the closing brace before executing.
  * Maybe we should consider calling out Ctrl-C in case this happens, since that discards then entire block
* When providing a command for a user to run, also provide an example of output,
  or indicate that the command should not produce any output.

## Usage
(none yet)

## Developer tooling
### `check_kboards` (will be renamed soon)
Iterates through all defined boards, and runs the configurator against them for each communication type supported

This allows quick visibility into possible breakages, either from our changes, or klipper ones.

## Components
### Board DB (`board/`)
A JSON-formatted list of supported boards, containing sufficient information to generate a klipper config.

### UI
(TBD)

This is expected to interact with the user, to guide them through selecting from the board db.

Besides the user, creates and interacts with a Configurator instance to generate a .config

May also launch builds in the klipper source tree, or may farm that out to a future component.

### Configurator (`board2kconf/configurator.py`)
Given a board config and klipper instance, uses `kconfiglib` (the same as upstream klipper) to make selections
as a user would. Typically used to generate a `.config`

This should be the __only__ part of the codebase that interacts with the kconfig wrapper

### Kconfig Wrapper (`board2kconf/kconfig.py`)
Wraps kconfiglib primitives in a more friendly interface.

This should be the __only__ part of the codebase that interacts with `kconfiglib`
