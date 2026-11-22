# Voicemeeter Corner HUD v6

A Windows corner mixer for Voicemeeter Banana and a foreground two-deck controller for VirtualDJ. Music Mode uses Voicemeeter for EQ and levels and ELKA for Spotify AUX effects. Both modes have compact and large views, a system tray icon, and the diagonal resize button.

## Run the HUD

Run `setup_and_run.bat` once and use `start_hud.bat` after that. Use `enable_startup.bat` or `disable_startup.bat` to change per-user startup. Keep all project modules together when updating. HUD preferences live in `%APPDATA%\VoicemeeterCornerHUD\settings.json`.

## HUD settings

Right-click the HUD, or choose **Settings…** from its system tray icon.

- **Mode** switches between Music / Spotify and VirtualDJ.
- **Music** contains the shortcut assignments, filter, effects, and ELKA connection controls.
- **VirtualDJ** contains the keybind guide, shortcut step sizes, mapper installation and rollback, and DJ-mode options.
- **Appearance** contains the view size, monitor, corner, and opacity controls.

## DJ Mode

1. Launch VirtualDJ and right-click the HUD. Choose **Mode → DJ / VirtualDJ (2 decks)**.
2. When the mapper first needs installation or an update, the HUD can restart VirtualDJ to apply it. It waits for a clean exit, backs up affected files, installs `KEYBOARD - Corner HUD DJ.xml`, and selects the mapper for the next launch.
3. Keep VirtualDJ focused and Num Lock on for DJ commands. DJ Mode reserves the entire numpad, even if focus moves to another app. Closing VirtualDJ or choosing **Music / Spotify** restores the Music profile. By default, the HUD backs up the active MacroButtons map, closes MacroButtons when entering DJ Mode, and starts it again when leaving. If its active map is blank or missing, the HUD restores that session’s backup before reopening it. The **Pause MacroButtons in DJ Mode** menu setting controls that behavior for the next entry.
4. **Enter DJ Mode when VirtualDJ has focus** is optional. It uses an already installed mapper. Selecting Music manually overrides automatic entry until the VirtualDJ session ends.

The keybind guide opens next to the corner HUD when you enter DJ Mode with VirtualDJ focused. Use **×** to dismiss it for the session. To bring it back, choose **VirtualDJ → Show keybinds…** and return focus to VirtualDJ.

The installer detects the current VirtualDJ data directory, including the registered `HomeFolder`. Existing custom keyboard bindings are copied into the dedicated mapper where they do not conflict with the HUD keys. Factory mapping remains available in VirtualDJ. Other configuration bytes and separate mapper files are preserved. Use **VirtualDJ → Restore previous keyboard mapper…** to undo selection and file changes. Backups are stored in `Corner HUD Mapper Backups` in the VirtualDJ data directory.

| Numpad key | DJ action |
| --- | --- |
| `/` / `*` | Select Deck 1 / Deck 2 |
| `8` / `9` | Selected deck high EQ down / up |
| `5` / `6` | Selected deck mid EQ down / up |
| `2` / `3` | Selected deck low EQ down / up |
| `0` / `.` | Selected deck filter left / right |
| `1` / `4` | Selected deck volume down / up by 15% by default |
| `7` | Reset selected deck EQ |
| `-` | Center selected deck filter |
| `+` | Set selected deck volume to zero, or to full if it is already zero |
| `Enter` (bare) | Toggle the selected deck’s filter by default, configurable |

Use the keys bare or with Alt. The filter toggle uses bare Numpad Enter. Bare EQ steps cover 15% of the knob’s travel by default. Hold Alt for an 8% step. **VirtualDJ → Shortcuts and step sizes…** lets you change both EQ steps, filter and volume steps, and the Numpad Enter assignment. Main Enter remains available to VirtualDJ.

Both deck filters start bypassed. Numpad `0` and `.` adjust the selected deck’s filter in either state. Bipolar steps land on 50% when they reach or cross neutral. Numpad Minus centers the position. The HUD shows the adjustable position in gray while bypassed. Numpad Enter applies the saved position when enabling the filter. In VirtualDJ’s Starter layout the visible Filter dial stays centered during bypass.

| Selected-deck action | Shortcut |
| --- | --- |
| Play / pause | Alt+1 |
| Cue | Alt+2 |
| Stop | Alt+3 |
| Toggle loop at the current size | Alt+4 |
| Halve loop size | Alt+5 |
| Double loop size | Alt+6 |
| Sync | Alt+Q |
| Set HIGH, MID, and LOW to minimum | Alt+W |

Select the transport deck with Numpad `/` or `*`. A conflicting binding in the previous custom mapper takes precedence for transport. **Alt+7** cuts the deck with the higher LOW EQ to 0% and boosts the other deck to 120% on the HUD’s EQ scale. If their LOW EQ is tied, the selected deck gets the boost. **Alt+8** resets both decks. F13–F18 are optional virtual keys for profiles that explicitly emit them.

### Deck values

Keyboard control uses VirtualDJ's native mapper and works without Network Control. The HUD labels command-tracked values **Keyboard Estimates** and starts from neutral EQ, centered bypassed filters, full volume faders, and Deck 1. EQ reads 100% at neutral, with cuts below 100% and boosts above it. Alt+8 resets both decks’ EQ, centers and bypasses the filters, returns their volume faders to 100%, and selects Deck 1. The selected deck's EQ reset and filter-center commands also establish estimates for those controls independently. Mouse movements, other controllers, and automatic changes within VirtualDJ can make estimates drift.

Existing licensed Network Control installations can opt into readback with `dj_http_readback` and the loopback `dj_http_base` in HUD settings. The [VirtualDJ Network Control documentation](https://www.virtualdj.com/wiki/NetworkControlPlugin.html) requires Pro for that extension.

## Music Mode

Exclusive Ctrl+numpad capture is enabled by default. The HUD pairs key-down and key-up by keypad identity, including key codes emitted with Num Lock off. Configure the map at **Music → Shortcuts…**. Legacy iCUE profiles can also assign a specific Music action to Ctrl+Home. The same chord on a standard keyboard has the same action. Unassigned Ctrl+numpad keys are reserved while exclusive capture is on. Plain keypad keys pass through. Ctrl itself remains an ordinary modifier. Use modifier-free iCUE assignments if Ctrl alone triggers a game command.

| Ctrl + Numpad | Default Music action |
| --- | --- |
| `8` / `9` | HIGH EQ down / up 1.92 dB |
| `5` / `6` | MID EQ down / up 1.92 dB |
| `2` / `3` | LOW EQ down / up 1.92 dB |
| `0` / `.` | Filter darker / brighter |
| `Enter` | Toggle music filter bypass |
| `1` / `4` | Spotify volume down / up 2 dB |
| `7` | Spotify mute |
| `+` | Clutch volume toggle |
| `-` | Bass reset |

Each Music EQ step is 8% of the full −12 to +12 dB range.

### ELKA and Kilohearts

`Installed Kilohearts` lists local VST3 bundles. **Music → ELKA connection → Auto-register from ELKA saves…** reads stable IDs from the active ELKA layout. Only register effects on the Spotify AUX chain. `Import IDs from saved ELKA layout` also accepts an explicit export. Once kHs Filter is paired, the HUD reads its cutoff and Type choices using ELKA’s isolated plugin loader, including the high-pass setting. Existing manually configured values are retained. Ctrl+Numpad Enter toggles Music filter bypass. At the center position the dial passes dry audio. Bypass dims the current cutoff without losing its position.

ELKA 0.8.3.0 keeps the active graph under `%APPDATA%\ElkaSoft\VoiceMeeterFxHost\wpf-main-window.json`. ELKA restores nodes and cables from that file at startup. The HUD’s current setup reads that graph and uses VBAN to control loaded effects. New nodes still need to be installed in the graph. The [implementation log](IMPLEMENTATION_LOG.md) records the source-verified persistence mechanism and the remaining graph-automation work.

## Verification

After setup, run `.venv\Scripts\python.exe -B -m unittest discover -s tests -v`. The installed VirtualDJ build used for integration is 2026 b9746. See the [implementation log](IMPLEMENTATION_LOG.md) for coverage and outstanding hardware checks.
