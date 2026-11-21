# Voicemeeter Corner HUD v6

A Windows corner mixer for Voicemeeter Banana and a foreground two-deck controller for VirtualDJ. Music Mode uses Voicemeeter for EQ and levels and ELKA for Spotify AUX effects. Both modes have compact and large views, a system tray icon, and the diagonal resize button.

## Run the HUD

Run `setup_and_run.bat` once and use `start_hud.bat` after that. Use `enable_startup.bat` or `disable_startup.bat` to change per-user startup. Keep all project modules together when updating. HUD preferences live in `%APPDATA%\VoicemeeterCornerHUD\settings.json`.

## DJ Mode

1. Launch VirtualDJ and right-click the HUD. Choose **Music / DJ Mode → DJ / VirtualDJ (2 decks)**.
2. When the mapper first needs installation or an update, the HUD can restart VirtualDJ to apply it. It waits for a clean exit, backs up affected files, installs `KEYBOARD - Corner HUD DJ.xml`, and selects the mapper for the next launch.
3. Keep VirtualDJ focused for DJ commands. Closing VirtualDJ or choosing **Music / Spotify** restores the Music profile. By default, the HUD closes a running MacroButtons instance when entering DJ Mode and starts it again when leaving. The **Pause MacroButtons in DJ Mode** menu setting controls that behavior for the next entry.
4. **Enter DJ Mode when VirtualDJ has focus** is optional. It uses an already installed mapper. Selecting Music manually overrides automatic entry until the VirtualDJ session ends.

The installer detects the current VirtualDJ data directory, including the registered `HomeFolder`. Existing custom keyboard bindings are copied into the dedicated mapper where they do not conflict with the HUD keys. Factory mapping remains available in VirtualDJ. Other configuration bytes and separate mapper files are preserved. Use **Restore previous VirtualDJ mapper** to undo selection and file changes. Backups are stored in `Corner HUD Mapper Backups` in the VirtualDJ data directory.

| Ctrl + Numpad | DJ action |
| --- | --- |
| `/` / `*` | Select Deck 1 / Deck 2 |
| `8` / `9` | Selected deck high EQ down / up |
| `5` / `6` | Selected deck mid EQ down / up |
| `2` / `3` | Selected deck low EQ down / up |
| `0` / `.` | Selected deck filter left / right |
| `1` / `4` | Selected deck volume down / up |
| `7` | Reset selected deck EQ |
| `-` | Center selected deck filter |
| `+` | Sync selected deck |
| `Enter` | Sync by default, configurable |

**Configure DJ keys and increments** changes the EQ, filter, and volume percentages, the Enter action, and F13–F18. For iCUE, existing Ctrl+numpad assignments work as the corresponding DJ keys. An iCUE software profile can instead try sending F13–F18 for G1–G6. Assign those keys independently in Music and DJ settings. The installed VirtualDJ build reads main and keypad Ctrl+Enter as the same `CTRL+RETURN` binding. Use Num Lock for the native numeric-key mappings.

The filter uses VirtualDJ's bipolar filter. Left is low-pass, right is high-pass, and a step that reaches or crosses neutral lands at exactly 50%. The next step crosses to the other side. The dash shortcut centers immediately.

### Deck values

Keyboard control uses VirtualDJ's native mapper and works without Network Control. The HUD labels command-tracked values **Keyboard Estimates**. Select a deck with `/` or `*` so the HUD can track it. F19 resets both decks’ EQ and filters, returns their volume faders to 100%, and selects Deck 1. That action gives the HUD a known starting point. The selected deck's EQ reset and filter-center commands also establish estimates for those controls independently. Mouse movements, other controllers, and automatic changes within VirtualDJ can make estimates drift. **Sync Sent** reports a keyboard command.

Existing licensed Network Control installations can opt into readback with `dj_http_readback` and the loopback `dj_http_base` in HUD settings. The [VirtualDJ Network Control documentation](https://www.virtualdj.com/wiki/NetworkControlPlugin.html) requires Pro for that extension.

## Music Mode

Exclusive Ctrl+numpad capture is enabled by default. The HUD pairs key-down and key-up by keypad identity, including key codes emitted with Num Lock off. Configure the map at **Exclusive Ctrl+numpad shortcuts**. Legacy iCUE profiles can also assign a specific Music action to Ctrl+Home. The same chord on a standard keyboard has the same action. Unassigned Ctrl+numpad keys are reserved while exclusive capture is on. Plain keypad keys pass through, with the optional bare keypad Enter filter toggle. Ctrl itself remains an ordinary modifier. Use modifier-free iCUE assignments if Ctrl alone triggers a game command.

| Ctrl + Numpad | Default Music action |
| --- | --- |
| `8` / `9` | HIGH EQ down / up 1 dB |
| `5` / `6` | MID EQ down / up 1 dB |
| `2` / `3` | LOW EQ down / up 1 dB |
| `0` / `.` | Filter darker / brighter |
| `1` / `4` | Spotify volume down / up 2 dB |
| `7` | Spotify mute |
| `+` | Clutch volume toggle |
| `-` | Bass reset |

### ELKA and Kilohearts

`Installed Kilohearts` lists local VST3 bundles. `Auto-register from ELKA saves` reads stable IDs from the active ELKA layout. Only register effects on the Spotify AUX chain. `Import IDs from saved ELKA layout` also accepts an explicit export. Once kHs Filter is paired, the HUD reads its cutoff and Type choices using ELKA’s isolated plugin loader, including the high-pass setting. Existing manually configured values are retained. The bipolar Music dial has a dry center. Bypass dims the current cutoff without losing its position.

ELKA 0.8.3.0 keeps the active graph under `%APPDATA%\ElkaSoft\VoiceMeeterFxHost\wpf-main-window.json`. ELKA restores nodes and cables from that file at startup. The HUD’s current setup reads that graph and uses VBAN to control loaded effects. New nodes still need to be installed in the graph. The [implementation log](IMPLEMENTATION_LOG.md) records the source-verified persistence mechanism and the remaining graph-automation work.

## Verification

After setup, run `.venv\Scripts\python.exe -B -m unittest discover -s tests -v`. The installed VirtualDJ build used for integration is 2026 b9746. See the [implementation log](IMPLEMENTATION_LOG.md) for coverage and outstanding hardware checks.
