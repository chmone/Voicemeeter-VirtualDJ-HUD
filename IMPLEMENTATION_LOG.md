# Implementation log

## 2026-11-21 | Audit and implementation order

This is the existing v6 source directory, with no Git metadata. `overlay.py` owns Tk, tray state, live Voicemeeter polling, shortcut dispatch and VBAN transmission. `elka_import.py` previously searched guessed property names. `dj_setup.py` writes a dedicated keyboard mapper and attempts activation. The batch launchers use a project virtual environment. HUD preferences live in `%APPDATA%\VoicemeeterCornerHUD\settings.json`.

1. Add a version-checked layout editor and offline tests. Use ELKA startup restoration and keep live FX switching on VBAN.
2. Match physical numpad scan codes and keep each key's down/up decision paired. Handle MacroButtons explicitly in DJ mode.
3. Use one VirtualDJ command path at a time. Verify the installed data-directory and mapper shape, and provide scoped rollback.
4. Check the existing HUD views and preference migration, then verify with isolated fixtures and installed components.

### ELKA 0.8.3.0

The active settings file is `%APPDATA%\ElkaSoft\VoiceMeeterFxHost\wpf-main-window.json`. The legacy fallback is `%LOCALAPPDATA%\ElkaVoiceMeeterFxHost\wpf-main-window.json`. `%LOCALAPPDATA%\ElkaSoft\VoiceMeeterFxHost` contains logs and the extracted plugin worker. The roaming directory also contains the JUCE XML plugin cache. The user's separate Save As export shares the active JSON shape.

The active edition is at the root. Other edition graphs may appear in `EditionWorkspaces`. `PluginNodes` contains the plugin path (`PluginIdentifier`), format, `InstanceId` for VBAN, `Slot` for routing, pin layouts, bypass/power and opaque JUCE state strings. Indexed VBAN parameters are zero-based. The parameter snapshot stores normalized values by index and uses JUCE's MemoryBlock encoding. The installed Banana filter is ID 0 with callback Input L/R on 14 and 15. Potato AUX would use 18 and 19. The native graph has a 16-node limit at this version.

ELKA loads the roaming file during startup, matches plugin paths to scanner choices, remaps saved slots to live slots, and restores the cables. VBAN can address controls or reload an existing instance. The native graph APIs act on the calling process's engine. The worker IPC loads isolated plugins and exposes their metadata. The main application single-instance event activates its window and accepts no layout path. The checked upstream v0.8.3.32 command documentation still describes the same graph-editing limit.

[ELKA 0.8.3.0 source](https://github.com/torment78/Elka.VoiceMeeterFxHost/tree/v0.8.3.0/src) · [VFX commands](https://github.com/torment78/Elka.VoiceMeeterFxHost/blob/v0.8.3.0/VFX_COMMANDS.md)

### VirtualDJ foreground controller

The installed executable reports file version 8.5.9746.0 and the app identifies itself as VirtualDJ 2026 b9746. The current Windows installation uses `HKCU\Software\VirtualDJ\HomeFolder` pointed at `%LOCALAPPDATA%\VirtualDJ`. The advanced editor identified `CTRL+NUM/` and `CTRL+RETURN`. The latter is shared by main and keypad Enter. VirtualDJ saved selection as `settings/controllers/controllersCustomization/controller[@name="KEYBOARD"]/@mapper` using the full filename stem.

The installer uses the native XML format, preserves settings outside the keyboard selection, carries forward unrelated mappings from a selected custom keyboard file, and stores original and before-reinstall backups. Rollback restores the original keyboard selection and mapper without restoring other settings changed since install. Writes are limited to closed VirtualDJ sessions. The native mapper runs one command per press on the selected deck. Numeric EQ verbs target frequency EQ regardless of the skin's STEMS mode. Filter scripts select VirtualDJ's Filter Color FX and snap to 50% when a step lands on or crosses center.

On the installed app, automatic activation persisted across restarts. With the foreground mixer visible, injected Windows keypad events selected Deck 2, changed its volume, and moved its filter to low-pass, back to exact neutral (the mixer displayed OFF), and onward to high-pass. Deck 1's filter stayed centered. The frequency-EQ controls and the F19 two-deck reset were exercised, and the HUD observed the selected-deck Sync command. The decks were unloaded, so beat alignment was outside this check. The app was returned to its Starter layout and prior EQ mode.

Keyboard ownership is paired from down to up. Music recognizes keypad scans with Num Lock off and distinguishes extended navigation/main Enter. The DLL injection flag represents event provenance rather than the original G-key identity. Dedicated F13–F18 actions are available in both profiles. The physical K100/iCUE G-key assignments and game input are still due a hands-on check.

MacroButtons was paused using a close message sent to its verified process window. Live tests observed its exit on DJ Mode entry, Music Mode restoration on VirtualDJ exit, and MacroButtons restarting while the original ELKA and Voicemeeter process IDs stayed stable. The HUD stores a recovery path while MacroButtons is paused and clears it after restoration.

The documented Network Control extension requires Pro. Skin queries are local to VirtualDJ, and the SDK's general-plugin surface would require an extension plus separate license/version validation. The default uses explicitly labeled keyboard estimates from reset/center baselines. The optional licensed read-only polling interface remains opt-in.

[VirtualDJ mapper format](https://virtualdj.com/wiki/ControllerMappingFile_v8.html) · [VDJScript verbs](https://virtualdj.com/manuals/virtualdj/appendix/vdjscriptverbs.html) · [Network Control](https://www.virtualdj.com/wiki/NetworkControlPlugin.html) · [Windows keyboard event structure](https://learn.microsoft.com/en-us/windows/desktop/api/winuser/ns-winuser-kbdllhookstruct)

### Kilohearts filter correction

The verified active layout contains `InstanceId` 0 for the plugin, and the HUD can now import that ID from the roaming save. ELKA's isolated loader reported Type at index 1, High Pass as choice 3, and Cutoff at index 2. The host compares displayed numeric values without converting between displayed units. On this filter, commands above 1000 Hz therefore use kHz while smaller commands use Hz. A live VBAN test of the HUD sender confirmed High Pass at 1.70 kHz and restored the original low-pass cutoff and bypass. The automatic graph-writing work remains pending.

### Existing iCUE profiles

The HUD supports a separately configurable Ctrl+Home action alongside Ctrl+numpad and F13–F18. Music actions include timed relative Voicemeeter fades, mute, clutch fade, bass drop/boost, and normalization. The active user's approved assignments were applied to local HUD settings with a backup. iCUE profiles and MacroButtons maps were read for verification and remain managed by their respective applications.

A live Windows test of the saved G1/G2 chords moved the Spotify strip from 0 to +2 dB and back to 0 dB. A separate test sent the Music Mode filter shortcut across center and ELKA reported its first high-pass setting at 35 Hz. Both tests used synthesized Windows keyboard input.

### Dedicated DJ numpad and settings

The right-click and tray menus expose Mode, Music, VirtualDJ, and Appearance. The shortcut editors use readable action names and hide optional virtual F-keys until expanded. In Music Mode the keyboard profile uses Ctrl-based capture by default. DJ Mode reserves the keypad when VirtualDJ is unfocused, and gives focused VirtualDJ separate bare and Ctrl bindings. Bare EQ uses the fast step. Ctrl uses the fine step. VirtualDJ uses one native identifier for both Enter keys, so the HUD suppresses the main Ctrl+Enter alias while the Corner HUD mapper is selected and VirtualDJ is focused. Only Ctrl+Numpad Enter reaches the filter toggle.

The second mapper revision adds real keyboard transport shortcuts and a physical Ctrl+F12 reset. Setup keeps existing nonconflicting custom bindings and detects that an older installed mapper must be updated after a clean VirtualDJ exit. Existing default Enter assignments migrate to the Ctrl+Numpad Enter filter toggle. A held keypad key cannot continue repeating into another app when DJ Mode loses focus. The new mapper and shortcut dispatch were verified with automated fixtures, including an upgrade on a copy of the installed VirtualDJ settings.

The third mapper revision defaults to 8% Ctrl EQ steps and 15% bare EQ steps. Ctrl+F11 moves the selected deck’s frequency LOW EQ to 50% and the other deck’s to 0%. The keyboard estimate records both positions once the selected deck is known. The DJ shortcut guide follows VirtualDJ foreground focus and uses a nonactivating Windows window.

On VirtualDJ 2026 b9746, the updated mapper stayed selected after restart. Live keyboard tests moved bass to each deck and exercised both EQ step bindings. VirtualDJ retained foreground focus with the guide visible. Both decks were empty during the mixer tests.

The Starter skin binds its visible Filter dial to custom Color FX slot 1. The native mapper now routes the filter commands to that slot in Starter and to the mixer filter in other layouts. Starter bypass stores the dial position and centers the slot. The HUD estimates start with both filters centered and faders at full volume. The selected deck shares one set of Alt transport keys. Numpad 1 and 4 default to 15% volume steps, and Numpad Plus reads the selected deck's actual fader before toggling zero or full. On the installed Starter layout, both filter knobs responded to their respective deck bindings, the volume fader moved in 15% steps, and the Plus shortcut returned the fader from zero to full.

Bare physical Numpad Enter uses the extended Enter scan flag in Windows. The HUD forwards just that press through an internal F20 mapper binding while VirtualDJ is focused in DJ Mode, so the main Enter key retains its normal action. VirtualDJ owns the filter command. The HUD keeps the remembered position visible in gray while bypassed.

Live testing of the native mapper binding centered a turned Starter filter dial, then restored its previous angle when re-enabled. Automated tests exercise the extended Enter down/up routing and the gray position display in both HUD sizes.

Mapper revision 7 initializes both filters centered and bypassed. While bypassed, filter adjustments change a per-deck saved position using the same endpoint limits and neutral snap as the active filter. Enabling applies that position. Resetting both decks also restores centered, bypassed filters.

Mapper revision 8 uses VirtualDJ's auto-loop toggle on Alt+4 so enabling the loop uses the selected length. Alt+5 halves that length and Alt+6 doubles it. The mapper routes all three to the selected deck and ignores key repeats. [VDJScript loop commands](https://virtualdj.com/manuals/virtualdj/appendix/vdjscriptverbs.html)

Mapper revision 9 uses Alt for modified DJ keypad and function-key commands. The hook recognizes the Windows Alt modifier separately so the mapper and HUD agree on the fine EQ step. Music Mode continues to use its saved Ctrl shortcuts.

Mapper revision 10 moves Bass Swap and Reset Both Decks to Alt+7 and Alt+8. Bass Swap compares the two frequency LOW EQ values inside VirtualDJ, cuts the higher value, and boosts the other deck to 60%. The selected deck receives the boost when the values are tied.

MacroButtons 1.1.4.1 reads its startup map from the Windows Documents folder. The HUD snapshots a configured map before requesting a close. On resume it can replace a blank or missing map from that session’s snapshot while the process is stopped. Populated and unfamiliar maps stay in place.

Music Mode’s incremental EQ commands move 1.92 dB per press, 8% of the existing −12 to +12 dB range.
