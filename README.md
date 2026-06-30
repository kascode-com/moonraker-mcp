# Moonraker MCP

![License: MIT](https://img.shields.io/badge/license-MIT-blue)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![MCP](https://img.shields.io/badge/MCP-server-purple)

An [MCP](https://modelcontextprotocol.io) server that lets an AI client (Claude
Desktop / Claude Code) query and control a Klipper 3D printer through the
[Moonraker API](https://moonraker.readthedocs.io/en/latest/external_api/introduction/).

It runs as a small HTTP daemon - designed to live on the Raspberry Pi next to
Moonraker - and exposes printer status, temperatures, files, print-job control,
and G-code/macro execution as MCP tools.

> ## ⚠️ Safety & security - read first
>
> This server lets an AI client **physically control a 3D printer** - heaters
> that reach 250 °C+, a moving toolhead, and unattended prints. Treat it
> accordingly:
>
> - **Run on a trusted LAN only. Never expose port 8790 to the internet.** It
>   binds `0.0.0.0` by default. Put it behind your firewall/VPN (e.g. Tailscale),
>   not a port-forward.
> - **Set `MCP_AUTH_TOKEN`.** Blank means the endpoint is **unauthenticated** -
>   anyone who can reach it gets full printer control.
> - **Supervise prints.** Confirmation gating (below) reduces accidental
>   destructive calls, but it is not a substitute for a human watching hot,
>   moving hardware. Keep an emergency stop within reach.

## Confirmation gating

Full control is available, but **dangerous operations refuse to run until called
again with `confirm=true`**. When confirmation is missing the tool does nothing
and returns a message describing what would happen, which the AI surfaces to you
as a yes/no. Gated operations:

- `emergency_stop`
- `cancel_print`
- `pause_print`
- `run_gcode` / `run_macro` - **only** when the command drives a heater
  (`M104/M109/M140/M190/M141/M191/SET_HEATER_TEMPERATURE/TEMPERATURE_WAIT`)
- `start_print`, `resume_print`
- `firmware_restart`, `restart_host`, `delete_file`

Non-heater `run_gcode`/`run_macro` are the only state-changing operations that
run without confirmation.

## Requirements

- **Python 3.10+** (hard requirement - the `mcp` SDK and several deps refuse to
  install on anything older)
- A reachable **Moonraker** instance (Klipper printer), local or remote
- For the deploy path: a **Linux host with systemd** (e.g. Raspberry Pi OS) -
  the install script sets up a systemd service. The server itself is pure
  Python and also runs on Windows/macOS for local dev.

> ### ⚠️ Check your Python version before installing on a printer host
>
> Run `python3 --version` **first**. Many Klipper/Moonraker hosts run older OS
> images that ship Python **3.9 or earlier** - notably **Debian Bullseye** and
> the legacy **Raspberry Pi OS** image, and older **Armbian** builds (some on
> 3.8). `bullseye-backports` does *not* ship a newer interpreter, so `apt` won't
> save you. If your `python3` is below 3.10, `bash deploy/install.sh` will fail
> at dependency resolution - **before** touching your printer config.
>
> Fixes, easiest first:
> - Use a **Bookworm**-based image (ships Python 3.11).
> - Run the server in **Docker** with a `python:3.11-slim` base - sidesteps the
>   host interpreter entirely.
> - Install 3.10+ via **pyenv** and point the service's venv at it (the systemd
>   unit already calls an explicit `venv/bin/python`).

## Configuration

Copy `.env.example` to `.env` (or set the env vars in the systemd unit):

| Variable | Default | Purpose |
|----------|---------|---------|
| `PRINTERS_FILE` | _(auto)_ | Path to a multi-printer TOML/JSON file (see below) |
| `MOONRAKER_URL` | `http://localhost:7125` | Single-printer URL (used only if no printers file) |
| `MOONRAKER_API_KEY` | _(blank)_ | `X-Api-Key`; blank if a trusted LAN client |
| `MCP_HOST` | `0.0.0.0` | Bind address |
| `MCP_PORT` | `8790` | Bind port |
| `MCP_AUTH_TOKEN` | _(blank)_ | Bearer token clients must send. **Blank = unauthenticated** |
| `REQUEST_TIMEOUT` | `10` | Per-request timeout to Moonraker (seconds) |

### One or many printers

A single MCP instance can front **multiple** printers (each a remote Moonraker).
Define them in a TOML or JSON file - copy `printers.example.toml` to
`printers.toml` (auto-detected in the working dir) or set `PRINTERS_FILE`.
`printers.toml`/`printers.json` are gitignored since they hold your URLs and API
keys; only the `.example` template is tracked.

```toml
[[printers]]
name = "voron"
url  = "http://voron.local:7125"
primary = true

[[printers]]
name = "ender"
url  = "http://ender.local:7125"
```

Every tool then takes an optional `printer` argument (the configured name);
omit it to target the **primary**. `list_printers` enumerates them. When no
printers file is found, the server falls back to the single-printer
`MOONRAKER_URL` (named `default`) - so existing single-printer setups keep
working unchanged.

## Run locally (dev)

```bash
python -m venv venv && ./venv/bin/pip install -e ".[dev]"   # Windows: venv\Scripts\pip
cp .env.example .env   # edit MOONRAKER_URL
python -m moonraker_mcp
```

The MCP endpoint is served at `http://<host>:<port>/mcp` (Streamable HTTP - the
current MCP HTTP transport; this is what Claude's "HTTP/SSE" connector expects).

### Interactive testing with the MCP Inspector

```bash
mcp dev src/moonraker_mcp/server.py   # or use the Inspector against the running URL
```

## Deploy on the Raspberry Pi

> **Before you start:** run `python3 --version` on the Pi and confirm it's 3.10+ (see the [Python version warning](#requirements) above) - the install will fail on Bullseye/older images otherwise.

First-time install, run on the Pi:

```bash
git clone https://github.com/<your-username>/moonraker-mcp.git ~/moonraker-mcp && cd ~/moonraker-mcp
bash deploy/install.sh         # creates venv, installs, sets up systemd
sudo nano /etc/moonraker-mcp.env   # set MOONRAKER_URL + MCP_AUTH_TOKEN
sudo systemctl restart moonraker-mcp
```

`install.sh` re-runs safely: it reinstalls the package and restarts the unit,
and never overwrites an existing `/etc/moonraker-mcp.env`. To update an existing
install, pull the latest and re-run it:

```bash
cd ~/moonraker-mcp && git pull && bash deploy/install.sh
```

## Connect from Claude

Add an HTTP MCP server pointing at `http://<pi-host>:8790/mcp`, with header
`Authorization: Bearer <MCP_AUTH_TOKEN>`. Example (`claude_desktop_config.json` /
`.mcp.json`):

```json
{
  "mcpServers": {
    "moonraker": {
      "url": "http://printer.local:8790/mcp",
      "headers": { "Authorization": "Bearer YOUR_TOKEN" }
    }
  }
}
```

## Tools

All tools accept an optional `printer` argument (a configured printer name;
omit for the primary).

Discovery: `list_printers`.

Read: `get_printer_status`, `get_print_status`, `get_temperatures`,
`list_printer_objects`, `get_printer_info`, `get_server_info`, `query_endstops`,
`get_temperature_history`, `get_recent_gcode_responses`, `get_gcode_help`.

Files: `list_gcode_files`, `list_files`, `get_file_metadata`, `get_file`,
`delete_file`*.

`get_file` reads raw file contents (G-code, config, etc.) with an optional byte
range. It's capped at `max_bytes` (default 32 KB) and flags `truncated` when the
cap trims the response - pass `start_byte` (from `get_file_metadata`'s
`gcode_start_byte`) to skip the slicer header and sample the print body.

Control: `start_print`*, `pause_print`*, `resume_print`*, `cancel_print`*,
`run_gcode`*, `run_macro`*, `emergency_stop`*, `firmware_restart`*, `restart_host`*.

Server config: `get_server_config`.

History: `get_history`, `get_history_totals`, `get_history_job`,
`reset_history_totals`*, `delete_history_job`*.

Webcams: `list_webcams`, `get_webcam`, `test_webcam`, `set_webcam`*, `delete_webcam`*.

Sensors: `list_sensors`, `get_sensor_info`, `get_sensor_measurements`.

Spoolman: `get_spoolman_status`, `get_active_spool`, `set_active_spool`,
`spoolman_proxy`* (gated for non-GET methods only).

(* = confirmation-gated. `run_gcode`/`run_macro` only when a heater command is
present; `spoolman_proxy` only for non-GET methods.)

## Tests

```bash
./venv/bin/pytest
```

## License

[MIT](LICENSE) © the Moonraker MCP contributors
