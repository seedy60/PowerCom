# PowerCom

PowerCom is a   distribution of Doug Lee's [TeamTalk Commander](https://dlee.org/ttcom/beta) based on the [DWCom](https://github.com/dragonwolfsp/dwcom) plugin. It includes event speech through Prism, sound playback through `sound_lib`, push/system notifications, and rotating event logs as built-in features.

The original DWCom plugin required unnecessary dependencies, used dead libraries, hasn't been updated in the best part of a year and causes issues when run as a compiled executable.

***

## Requirements

- Python 3.12 or newer
- `uv` for dependency management
- Optional: UPX to squeeze the binary down to a smaller size when running `compile.cmd`

***

## Setup

1. Press Windows + R, type powershell and press Enter.
2. Clone the repository.

```powershell
git clone https://github.com/seedy60/PowerCom
cd PowerCom
```

3. Install `uv` if needed.

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

4. Install Python and dependencies.

```powershell
uv python install
uv sync
```

5. If you already have a `ttcom.conf`, place it in the PowerCom folder. For a new setup, copy `ttcom.conf.sample` to `ttcom.conf` and edit the server entries.

6. Run PowerCom.

```powershell
uv run python powercom.py
```

7. To build a configuration file in the wxPython UI, run:

```powershell
uv run python powercom.py --config
```

***

## Compiling

Run from the repository root:

```cmd
compile.cmd
```

The script runs `uv sync`, builds `powercom.exe` with PyInstaller, collects Prism and `sound_lib` native binaries, then copies these runtime assets into `dist\powercom`:

- `sounds`
- `ttcom.conf.sample`
- `powercom_defaults.ini`

***
## Compiled release

If you just want a precompiled binary that works right away, simply [download the latest release](https://github.com/seedy60/PowerCom/releases/latest/download/powercom.zip), extract the zip file, copy your ttcom.conf or ttcom.conf.sample to the extracted folder and run the powercom executable.

***

## Configuration

PowerCom reads its server and feature options from `ttcom.conf`.

You can place any PowerCom option under a specific `[server <name>]` section to apply it only to that server, or under `[server defaults]` to apply it to all servers unless a server overrides it.

PowerCom watches `ttcom.conf` and reloads its own feature options when the file changes. Connection-level changes may still require the `refresh` command.

You can build or edit `ttcom.conf` with the wxPython configuration UI:

```powershell
uv run python powercom.py --config
```

Boolean options accept:

- **Truthy:** `true`, `1`, `y`, `yes`
- **Falsy:** `false`, `0`, `n`, `no`

Keys are not case-sensitive. For example, `speechdModule` is the same as `speechdmodule`.

### Configuring Speech

By default:

- Speech is enabled for all servers.
- The speech backend (`speechEngine`) is `auto`.
- Speech interruption is enabled.

| Option | Type | Description |
|--------|------|-------------|
| `speech` | bool | Enables or disables speech. Default: `true`. |
| `speechEngine` | string | Prism backend to use. `auto` chooses the best available backend. Use `powercom backends` to list available backends on the current system. |
| `speechInterrupt` | bool | If `true`, new speech interrupts current speech. Default: `true`. |
| `speechdModule` | string | Output module for Speech Dispatcher. Ignored if Speech Dispatcher is not used. |
| `speechVoice` | string or number | Voice for the selected backend. Use a voice index, an exact voice name, or `-1` for the backend default. |
| `speechRate` | number | Speech rate for the selected backend. |
| `speechVolume` | number | Output volume for the selected backend. Values above `1` are treated as percentages. |
| `speechPitch` | number | Pitch for the selected backend. |
| `noSpeak` | string | Prevents certain events from being spoken. |

Common `speechEngine` aliases include `sapi`, `onecore`, `nvda`, `jaws`, `speechd`, `voiceover`, `nsspeech`, `orca`, `uia`, `windoweyes`, `systemaccess`, `zdsr`, and `zoomtext`. Availability depends on the operating system and installed assistive technologies.

### Changing Prism Backend and Voice

Use the `powercom` command inside PowerCom for the current server:

```text
powercom
powercom backends
powercom backend
powercom backend auto
powercom backend SAPI
powercom voices
powercom voices SAPI
powercom voice
powercom voice 0
powercom voice "Microsoft David Desktop - English (United States)"
powercom voice auto
powercom test
powercom test Hello from PowerCom
```

`powercom backend` shows or updates `speechEngine` in `ttcom.conf`.
`powercom voice` shows or updates `speechVoice` in `ttcom.conf`.
`powercom voice auto` stores the automatic/default voice setting.

Use `powercom backends` and `powercom voices <backend>` to see the Prism backend IDs and voice indexes available on the current system.

### `noSpeak` Usage

`noSpeak` accepts a list of event names joined with `+`.

```ini
noSpeak = updateuser+updatechannel+serverupdate
```

Common event names:

- `updateuser` - User status changes
- `adduser` - User joins a channel
- `removeuser` - User leaves a channel
- `loggedin` - User logs in
- `loggedout` - User logs out
- `messagedeliver` - A message is received
- `serverupdate` - The server is updated

### Randomized Login/Logout Messages

PowerCom supports custom randomized speech messages for login and logout events.

Create the following text files in the `text` directory:

- `logins.txt`
- `logouts.txt`

Each file should contain one possible spoken message per line. If the files are missing or empty, PowerCom falls back to `"logged in"` and `"logged out"`.

***

## Configuring Sounds

By default:

- Sounds are enabled.
- The sound pack is `default`.
- Playback type is `overlapping`.
- Volume is `100`.

| Option | Type | Description |
|--------|------|-------------|
| `sounds` | bool | Enables or disables sounds. Default: `true`. |
| `soundPack` | string | Name of the sound pack. Default: `default`. |
| `soundVolume` | number (0-100) | Playback volume. Default: `100`. |
| `playbackType` | string | How sounds are played. Options: `overlapping`, `interrupting`, `oneByOne`. Default: `overlapping`. |
| `noSound` | string | Prevents certain events from playing sounds. Usage is the same as `noSpeak`. |

Sound files are loaded from `sounds\<soundPack>`. Matching is case-insensitive and based on the sound file stem, so `join.wav` and `join.ogg` both match the `join` event sound.

***

## Configuring Notifications

By default, notification providers are disabled. When a provider is enabled, login/logout events and messages trigger notifications unless filtered.

| Option | Type | Description |
|--------|------|-------------|
| `notifyLogInOut` | bool | Notify when users log in or out. Default: `true`. |
| `notifyMessage` | bool | Notify when a message is received. Default: `true`. |

### Ntfy Notifications

| Option | Type | Description |
|--------|------|-------------|
| `ntfy` | bool | Enable or disable Ntfy notifications. Default: `false`. |
| `ntfyTopic` | string | Ntfy topic to publish to. |
| `ntfyUrl` | string | URL of the Ntfy instance. Default: `https://ntfy.sh`. |
| `ntfyUser` | string | Optional Ntfy username. |
| `ntfyPassword` | string | Optional Ntfy password. |

### Prowl Notifications

| Option | Type | Description |
|--------|------|-------------|
| `prowl` | bool | Enable or disable Prowl notifications. Default: `false`. |
| `prowlKey` | string | Prowl API key. |

### MG Notify

| Option | Type | Description |
|--------|------|-------------|
| `mgNotify` | bool | Enable or disable MG Notify notifications. Default: `false`. |
| `mgNotifyKey` | string | MG Notify API key. |

### Pushover Notifications

| Option | Type | Description |
|--------|------|-------------|
| `pushover` | bool | Enable or disable Pushover notifications. Default: `false`. |
| `pushoverUser` | string | Pushover user key. |
| `pushoverToken` | string | Pushover application API token. |
| `pushoverDevice` | string | Optional Pushover device name. |
| `pushoverSound` | string | Optional Pushover sound name. |
| `pushoverPriority` | number | Optional Pushover priority: `-2`, `-1`, `0`, `1`, or `2`. |
| `pushoverRetry` | number | Required when `pushoverPriority=2`; retry interval in seconds, minimum `30`. |
| `pushoverExpire` | number | Required when `pushoverPriority=2`; retry window in seconds, maximum `10800`. |

### System Notifications

| Option | Type | Description |
|--------|------|-------------|
| `systemNotify` | bool | Enable or disable system notifications. Default: `false`. |

***

## Configuring Logging

By default:

- Logging is enabled.
- Max log size is `4 MB` per file.
- Max log files is `5`.
- Total log storage is about `20 MB` before rotating.

| Option | Type | Description |
|--------|------|-------------|
| `log` | bool | Enable or disable logging. Default: `true`. |
| `maxLogSize` | number | Max size in MB before log rotation. Default: `4`. |
| `maxLogFiles` | number | Max number of log files before overwriting oldest. Default: `5`. |

***

## Known Issues

- **Windows COM errors:** Some Windows speech backends can raise COM errors. Use `powercom backends` to list available Prism backends and `powercom backend <backend>` to switch to another backend when one is unstable.
