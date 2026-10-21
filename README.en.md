# Install FL2 / FL3 on the A7C II using a Mac

[简体中文](README.md) | [English](README.en.md)

Add the FL2 / FL3 Creative Looks to a **Sony A7C II (ILCE-7CM2) running firmware 2.01** using a Mac, with an option to skip the NTSC startup notice. Current tool version: **0.2.3**.

**The FL2 / FL3 tone curves, chroma data, and color matrices come from Sony α1 II (Alpha 1 II, ILCE-1M2) firmware 4.00.** The upstream project ported and adapted them to the A7C II 2.01; this project adds macOS support and related fixes. Color matching against a physical α1 II has not been validated. See [Acknowledgements](#acknowledgements) and [ACKNOWLEDGEMENTS.md](ACKNOWLEDGEMENTS.md) for sources and attribution.

## Features

| Feature | Description |
| --- | --- |
| Add FL2 / FL3 | Select the two Creative Looks in the camera for still photography |
| Optionally skip the startup notice | Choose a version that skips the NTSC notice at startup |
| Back up, install, and upgrade | Keep the camera's own original backup; upgrade an existing older FL2 / FL3 installation |
| Verify after installation | Check the installation and whether the changes are active after restarting |
| Restore stock files | A restore function is provided, but actual restoration has not been tested and is not a guaranteed recovery method |

**M1–M4 are stored on the memory card. The camera's internal 1 / 2 / 3 memories are a separate set. Both versions include the card-preset fix.**

## Choose a version

| What you need | Choose |
| --- | --- |
| FL2 / FL3, keeping the original startup notice | **FL2 / FL3 (default)** |
| FL2 / FL3, also skipping the NTSC startup notice | **FL2 / FL3 + skip NTSC notice** |

This option skips the notice; you still select photo and video settings in the camera. If this camera-side version is already installed and working, **updating the computer-side tool does not require flashing the camera again**.

## Before you start

- **Only A7C II firmware 2.01 is supported.** A7CR, other models, and other firmware versions are not supported.
- You need a Mac and a USB data cable. Hardware testing used a MacBook Air M4; other Macs have not been tested.
- Before writing, check that the camera's actual battery level is at least **50%** and connect the Mac to power. Do not estimate the battery level.
- Keep backups on your own computer. Do not delete them or upload them publicly.
- **This is an experimental modification and can leave the camera unable to boot. A backup does not guarantee recovery.** A known installation interruption still affects previously unmodified cameras; users of these cameras are advised to wait.

One camera completed an upgrade from the older FL2 / FL3 installation to the version that skips the notice. Its owner confirmed photo capture/playback, M1–M4 save/recall, and the expected startup-notice behavior. **The automatic upgrade completion flow added in 0.2.1 has only been tested offline and has not been retested end to end on a camera.** See [Validation](docs/VALIDATION.md) for the exact scope.

## Use a local AI assistant

If you use Codex, Claude Code, or another assistant that can access files and USB on your Mac, copy the following prompt and specify your chosen version:

```text
Help me use https://github.com/MDHan/a7c2-fl23-macos with my A7C II.
First read README.en.md, AGENTS.md, docs/AI-INSTALL.md, and docs/VALIDATION.md.
My chosen version is: FL2/FL3, or FL2/FL3 + skip the NTSC notice.
First confirm the model, firmware, any existing installation, and the original backup folder.
I understand the experimental risks and authorize the selected installation or upgrade and verification.
Do not reinstall an already installed camera. For an upgrade, preserve the old folder and create a separate upgrade folder.
Keep one original backup by default. Preserve existing files, keep all required checks, and do not retry writes blindly.
Complete the steps you can perform automatically. Ask for my help when I need to connect the camera,
read its actual battery level, enter my administrator password locally, remove the battery, or take test photos.
Do not ask for my password in chat, guess the battery level, or assume that I have removed the battery and restarted.
When finished, report which checks actually passed and where the backup is stored.
```

A browser-only chat cannot directly operate your Mac's USB camera. An assistant cannot physically remove the battery, inspect the camera's screen, or take test photos for you.

The manual steps follow. If you are unfamiliar with Terminal, consider using a local assistant as described above. Tool messages and the linked detailed guides are currently primarily in Chinese; this English README translates the main workflow. Exact status tokens and command options are unchanged.

## Download and set up

Open Terminal on your Mac and check Python:

```sh
python3 --version
```

Python 3.10 or newer is required. If needed, ask your local assistant to help set it up.

Then run:

```sh
git clone https://github.com/MDHan/a7c2-fl23-macos.git
cd a7c2-fl23-macos
./setup.sh
```

No camera connection or battery-level input is needed for setup. If you already have a checkout, preserve local files and update the tools without deleting old backups. Run the remaining commands from the `a7c2-fl23-macos` directory.

## Updating the tools

**This updates the Mac-side tools; it does not flash the camera or create another camera backup.** Wait until no installation or other tool operation is running. Preserve the entire original workspace, such as `Pictures/A7C2-work`. Do not use `sudo` or enter a camera battery level for these update commands.

### If you cloned with Git

1. Return to the Terminal window where you ran `./a7c2-mac`. If the prompt already shows `a7c2-fl23-macos %`, you are in the right folder and **do not need another `cd`**. In a new Terminal window, enter the original tool folder. If it was downloaded into your home directory:

```sh
cd "$HOME/a7c2-fl23-macos"
```

If this folder cannot be found, locate the original tool folder in Finder, type `cd` followed by a space in Terminal, drag the folder into Terminal, and press Return. The backup workspace `A7C2-work` is a different folder.

2. From the tool folder, copy and run the complete block:

```sh
git pull --ff-only &&
./setup.sh &&
./a7c2-mac --version
```

This updates the code, installs its environment, then displays the version. A failed step prevents later steps from running. **The `0336` retry feature requires 0.2.3 or newer.** Preserve any conflict or “not a git repository” error. If Git says it is up to date but the version is older, stop rather than deleting files or forcing an overwrite.

### If you downloaded a ZIP or cannot update the old folder

Preserve the old tools and workspace. Download fresh tools into a separate folder in your home directory:

```sh
(
  cd "$HOME" &&
  git clone https://github.com/MDHan/a7c2-fl23-macos.git a7c2-fl23-macos-latest &&
  cd a7c2-fl23-macos-latest &&
  ./setup.sh &&
  ./a7c2-mac --version
)
```

If `a7c2-fl23-macos-latest` already exists, this stops without overwriting it. After a successful download, run `cd "$HOME/a7c2-fl23-macos-latest"` in Terminal and use the tools there. Keep `--workspace` pointing to the **original workspace**, without preparing a new empty one.

Updating the tools does not complete an installation. For a `0336` failure, continue with the [retry steps below](#installation-stops-while-waiting-for-0336).

## Connection and cold start

**Connect the camera:** use a USB data cable directly to the Mac. Select **option 2, “Image Transfer (MSC)”**, on the camera. Do not select streaming or PC Remote. Select MSC again if prompted later.

**When a cold start is required:** disconnect USB, turn the camera off normally, wait for the access lamp to go out, remove the battery for about **10 seconds**, then reinsert it, turn the camera on, and reconnect. Turning it off and on without removing the battery does not complete this step.

**Only shut down and remove the battery when the tool explicitly reports completion and the camera has no error. If a write is in progress, an error occurs, or the result is unknown, stop and review the instructions before disconnecting or flashing again.**

## First installation

This applies only to a **previously unmodified camera still running stock firmware 2.01**. The known first-installation interruption has not been resolved; the following steps are provided for users who understand the risk and choose to proceed. If FL2 / FL3 is already installed, use [Upgrade an older installation](#upgrade-an-older-installation).

### 1. Connect and check

Connect as described above, then run:

```sh
./a7c2-mac usb-status
```

If the camera's disk is not detected, check the cable and connection mode before proceeding.

### 2. Choose a version and back up

Run **only one** of the following commands. The backup goes into `A7C2-work` in your Pictures folder. This must be a new folder; if it already exists, preserve and inspect its records first.

```sh
# FL2 / FL3: keep the startup notice
sudo ./a7c2-mac prepare --workspace "$HOME/Pictures/A7C2-work"
```

```sh
# Or: FL2 / FL3 + skip the NTSC startup notice
sudo ./a7c2-mac prepare --workspace "$HOME/Pictures/A7C2-work" --disable-ntsc-warning
```

When `Password:` appears, enter your Mac administrator password only in Terminal. No characters appear as you type; this is normal. Do not send the password in chat.

**Wait for the command to finish and report `RESULT: prepared` before continuing.** The backup and pre-installation checks are then complete; the installation has not written firmware or saved shooting settings. Preparation does run a temporary diagnostic module inside the camera.

### 3. Install

Copy the whole block and enter the current battery percentage shown on the camera, using digits only:

**Paste the complete block and press Return first. Enter the battery percentage only when prompted; do not append it to the code or parentheses. `$HOME` automatically expands to your own user directory and does not need replacing. Change the path only if you used a different workspace.**

```sh
(
  printf 'Actual camera battery level (50–100, digits only): '
  read -r A7C2_BATTERY
  sudo ./a7c2-mac install --workspace "$HOME/Pictures/A7C2-work" --battery-percent "$A7C2_BATTERY"
)
```

Keep the camera connected and powered on. Do not run another camera tool simultaneously. `RESULT: installed_pending_cold_start` means this installation attempt completed; continue with [Cold start and verification](#cold-start-and-verification).

If this completion result does not appear, or you see `STOPPED`, do not rerun installation. See [Status and errors](#status-and-errors).

### Installation stops while waiting for `0336`

```text
STOPPED: Expected USB identity did not appear: 0336
```

This means the computer did not observe the camera's service-mode identity before the deadline. **If it happened before the installation body began, this attempt has not started installation writes.** The error number or a mass-storage screen alone does not prove retry eligibility; the tool checks the original traceback and complete records.

Version 0.2.3 adds an explicit retry for this case instead of blocking every `needs_review` state:

1. **Keep the entire original `A7C2-work` folder. Do not prepare again, delete backups, or edit state/error files.** Follow the complete [tool update steps](#updating-the-tools) first and confirm **0.2.3 or newer** before continuing.
2. The camera should show mass storage / image transfer (MSC), with no error. With no other camera operation running, inspect the saved state and the Mac's camera-disk detection:

```sh
./a7c2-mac status --workspace "$HOME/Pictures/A7C2-work"
./a7c2-mac usb-status
```

The eligible state has `phase=needs_review`, `failed_action=install`, `failed_during=prepared` (or `prepared_usb_unconfirmed`), and exactly `UsbWaitTimeout: Expected USB identity did not appear: 0336` as `last_error`. If no camera disk is detected, inspect the connection first. An abnormal screen, an unresolved service session, or other failures are not reasons to retry directly.

3. Confirm stock firmware 2.01 and an actual battery level of at least 50%. If you choose to retry, copy the full block and enter the current battery percentage when prompted:

```sh
(
  printf 'Actual camera battery level (50–100, digits only): '
  read -r A7C2_BATTERY
  sudo ./a7c2-mac install --workspace "$HOME/Pictures/A7C2-work" \
    --battery-percent "$A7C2_BATTERY" --retry-service-entry
)
```

Use the **original workspace**. The tool reviews the records, archives the previous state, and attempts entry once. It checks the same camera, complete stock programs and original target settings before writing. Failures inside the installation body, unknown write outcomes, restore/upgrade failures and incomplete records remain blocked.

4. After `RESULT: installed_pending_cold_start`, continue with [Cold start and verification](#cold-start-and-verification). A result ending in `_usb_unconfirmed` requires the [state guide](docs/STATE-MACHINE.md), without rerunning installation. If another error occurs, preserve the new records and inspect it before another attempt.

**This retry path has only been tested offline, and does not guarantee that connection timeouts will be resolved.** See the [detailed retry guide](docs/INSTALL-RETRY.md) for all conditions.

## Upgrade an older installation

This is for cameras **already running the older FL2 / FL3 version, with the complete original backup and installation folder preserved**. The original installation must have completed and passed verification. This flow does not currently accept the older notice-skipping version, other modifications, missing backups, or an unfinished previous operation.

### 1. Prepare a separate upgrade folder

`A7C2-old-work` below stands for your original installation folder. **Replace it with the actual location.** If you do not know where it is, ask your local assistant to help locate it rather than guessing.

The original folder is preserved; the new files go into `A7C2-upgrade` in Pictures. The new folder must not already exist. Again, run **only one** of these commands:

```sh
# Upgrade to FL2 / FL3, keeping the startup notice
./a7c2-mac prepare-upgrade \
  --source-workspace "$HOME/Pictures/A7C2-old-work" \
  --workspace "$HOME/Pictures/A7C2-upgrade"
```

```sh
# Or: upgrade to FL2 / FL3 + skip the NTSC startup notice
./a7c2-mac prepare-upgrade \
  --source-workspace "$HOME/Pictures/A7C2-old-work" \
  --workspace "$HOME/Pictures/A7C2-upgrade" --disable-ntsc-warning
```

This step is offline: do not connect the camera or use `sudo`. Continue only after the tool reports that the offline upgrade folder is ready (`离线升级目录已准备`).

### 2. Connect and upgrade

First perform a [battery-removal cold start](#connection-and-cold-start), then reconnect and choose MSC (option 2). Copy the whole block and enter the actual current battery percentage:

```sh
(
  printf 'Actual camera battery level (50–100, digits only): '
  read -r A7C2_BATTERY
  sudo ./a7c2-mac upgrade --workspace "$HOME/Pictures/A7C2-upgrade" \
    --battery-percent "$A7C2_BATTERY" --cold-start-confirmed \
    --accept-experimental-upgrade
)
```

Version 0.2.1 addresses the observed intermediate settings-check stop. **When the checks pass, this command completes the upgrade without a separate finishing command.** Other unexpected conditions still stop the operation.

After `RESULT: installed_pending_cold_start`, follow the next section. Use the **new upgrade folder** for subsequent verification and restoration. Keep the old folder and do not mix them up.

## Cold start and verification

Once installation or upgrade has explicitly completed:

1. Disconnect USB, shut down normally, wait for the access lamp to go out, remove the battery for about 10 seconds, then reinsert it and turn the camera on.
2. Capture and play back new photos using ST, FL2, and FL3.
3. Save FL2 / FL3 to unused M1–M4 positions, then recall them and check for “File Error.” Do not overwrite presets you need to keep.
4. If you installed the notice-skipping version, check that the NTSC startup notice no longer appears where it previously did.
5. Reconnect using MSC (option 2) and run the appropriate verification command below.

**After a first installation:**

```sh
sudo ./a7c2-mac verify --workspace "$HOME/Pictures/A7C2-work" --cold-start-confirmed
```

**After an upgrade:**

```sh
sudo ./a7c2-mac verify --workspace "$HOME/Pictures/A7C2-upgrade" --cold-start-confirmed
```

`RESULT: verified_after_cold_start` means the tool's checks passed. Combine this with the photo and preset-recall checks above to confirm normal use. Keep the entire installation or upgrade folder. Routine use does not require repeated flashing.

## Verification still fails after battery removal

**Do not restore or reinstall first.** Version 0.2.2 adds verification retry support for the older FL2 / FL3 installation. A previous verification failure alone no longer blocks another check if the evidence meets the required conditions.

1. Update the computer-side tools, preserving the original installation and backup folders. Do not flash the camera again.
2. Have a local assistant review the existing failure records using the [verification retry guide](docs/VERIFY-RETRY.md). Continue only if installation completed and the previous verification failed because old runtime contents were still observed. That observation alone is not proof that you did not remove the battery.
3. After review, confirm that you actually completed the required battery-removal restart and reconnected in MSC, then verify using the **original installation folder**. If the restart has not been done, do it once; do not repeatedly remove the battery unnecessarily. Do not switch to a new empty folder.

Only after verification passes should you use the normal upgrade flow, if needed. An incomplete installation, an unknown connection outcome, or a different error still causes a stop. Do not edit records to force the checks to pass.

## Restore stock files

A restore function exists, but **actual restoration has not been tested**. It requires this camera's own original backup and the matching installation folder, and should not be attempted directly after an interrupted upgrade. If restoration is needed, first read [Recovery and troubleshooting](docs/ADVANCED.md) or have a local assistant inspect the situation. Do not simply reinstall.

## Status and errors

| What you see | What to do |
| --- | --- |
| `RESULT: prepared` | Backup and pre-installation checks completed; the first installation can proceed |
| `RESULT: installed_pending_cold_start` | The installation or upgrade attempt completed; remove the battery as instructed, restart, and verify |
| `RESULT: verified_after_cold_start` | Post-restart tool checks passed; also confirm actual photography and preset use |
| Verification keeps failing after a missed battery-removal step | Update to 0.2.2 and review the existing records using the [retry guide](docs/VERIFY-RETRY.md); do not restore or reinstall |
| Waiting for `0336` times out at installation entry | Update to 0.2.3 and follow the [steps on this page](#installation-stops-while-waiting-for-0336), using the original folder |
| `STOPPED`, another error, or a result ending in `_usb_unconfirmed` | Preserve the current connection, folder, and records; follow the [state guide](docs/STATE-MACHINE.md) before retrying or removing the battery |
| The folder already exists | Preserve it and inspect its records; do not delete it to start over |
| No characters appear while entering the password | Normal; enter your Mac administrator password only in Terminal |
| Unsupported firmware or mismatched program files | Stop; do not force installation or edit files to bypass checks |

When no other operation is running, you can inspect the records. Replace the path with your current installation or upgrade folder:

```sh
./a7c2-mac status --workspace "$HOME/Pictures/A7C2-work"
```

Report problems in [Issues](https://github.com/MDHan/a7c2-fl23-macos/issues). Include the model, firmware version, the step that failed, and error text with personal information removed. Do not upload complete backups, original camera files, or private logs.

## Acknowledgements

This project adapts **[QAQLeonard/a7c2-fl23-research](https://github.com/QAQLeonard/a7c2-fl23-research)** for macOS. Credit for the FL2 / FL3 port and original installation tools belongs to the upstream author and the researchers cited there.

| Source | Main contribution |
| --- | --- |
| [QAQLeonard/a7c2-fl23-research](https://github.com/QAQLeonard/a7c2-fl23-research) | A7C II 2.01 FL2 / FL3 port, original installation, restoration, and verification tools |
| [lzh2016zzz/a7cr-fl23-research](https://github.com/lzh2016zzz/a7cr-fl23-research) | A7CR FL2 / FL3 research cited upstream; its patches cannot be applied directly to this model |
| [YuuLuo/a7c2-service-preload](https://github.com/YuuLuo/a7c2-service-preload) | A7C II communication and execution research cited upstream |
| [ma1co/Sony-PMCA-RE](https://github.com/ma1co/Sony-PMCA-RE) | Camera communication, backup operations, and backup parsing |
| [ma1co/fwtool.py PR #52](https://github.com/ma1co/fwtool.py/pull/52) | Stock camera software analysis tooling cited upstream; not needed for routine installation |

See [ACKNOWLEDGEMENTS.md](ACKNOWLEDGEMENTS.md) for complete attribution and licensing details.

## Changes made in this project

- **macOS support.** Connection, backup, installation, verification, error handling, and usage instructions, preserving upstream attribution and licenses.
- **Card-preset recall fix.** The older version could save FL2 / FL3 to internal memories 1 / 2 / 3 but could produce “File Error” when recalling M1–M4 from a card. This version completes the relevant checks; the owner of one camera confirmed that all four M1–M4 positions recall correctly.
- **Optional notice skipping.** Skip the NTSC startup notice while keeping photo and video settings under the user's control. The expected notice behavior was confirmed on one camera; video has not been comprehensively tested.
- **Two versions.** Choose FL2 / FL3 or FL2 / FL3 + skip NTSC notice. Both include the card-preset fix.
- **0.2.1: address the observed intermediate upgrade stop.** A value inside the camera that increased with elapsed time previously caused the tool to stop at its settings-preservation check. The upgrade now continues only if this value follows the observed pattern, saved settings remain exactly unchanged, and the other checks pass. Other anomalies still stop the operation. Previously interrupted folders do not automatically resume writes.
- **0.2.2: support reviewed verification retries for older installations.** After a verification failure involving old runtime contents, review the original records first. If eligible, rerun verification without restoring or reinstalling. Another failure preserves the original failed phase and records the new error; failure markers are cleared only after successful verification.
- **0.2.3: allow an explicit installation retry after service-entry timeout.** If the original traceback and journal prove that installation stopped while waiting for `0336`, preserve the backup and archive the state before attempting the connection once. Recheck the same camera, complete stock programs and target settings before writing. Failures after entering the installation body remain blocked; there is no automatic retry loop.

The current release has passed **112 host-side tests and 8 upstream tests**, with previous failures also checked against saved records. These computer-side updates do not change the camera-side functionality; a working installation does not need reflashing. **This installation retry path has only been tested offline. The new automatic upgrade flow and the complete older-version verification retry flow have not been retested on hardware. First installation and restoration still have unverified paths.**

## Further documentation

The detailed documents below are currently in Chinese.

| Document | Contents |
| --- | --- |
| [Validation](docs/VALIDATION.md) | What has actually been tested and what remains untested |
| [Upgrade guide](docs/UPGRADE.md) | Upgrade requirements and procedure |
| [Verification retry](docs/VERIFY-RETRY.md) | Reviewing old or new installation records after a verification failure |
| [Installation entry retry](docs/INSTALL-RETRY.md) | Explicitly retrying only a proven service-entry timeout before installation begins |
| [Interrupted upgrade handling](docs/UPGRADE-FINISH.md) | Reviewing an already interrupted upgrade while preserving its records |
| [Local AI guide](docs/AI-INSTALL.md) | Instructions for a local assistant |
| [Recovery and troubleshooting](docs/ADVANCED.md) | Connection, restoration, and common issues |
| [Implementation and research](research/two-profiles/README.md) | Technical implementation and verification results |
