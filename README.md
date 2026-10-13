# B.U.T.L.E.R.

### Better Understanding Text-to-Speech Large Language Encoder Routine

[![CI](https://github.com/Lambchops118/Talos/actions/workflows/ci.yml/badge.svg)](https://github.com/Lambchops118/Talos/actions/workflows/ci.yml)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/release/python-3120/)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)](#requirements)
[![Status](https://img.shields.io/badge/status-active%20development-orange)](#project-status)

B.U.T.L.E.R. is a local-first, voice-first AI assistant for conversation, home
automation, and engineering work. It combines a locally hosted language model,
speech recognition and synthesis, durable memory, environmental awareness, and
an extensible tool system in one multi-process runtime.

> [!IMPORTANT]
> This is an actively developed personal system, not a turnkey consumer
> product. Several integrations depend on the owner's hardware, network, and
> credentials. Physical actions remain bounded by explicit authorization and
> safety checks.

## What it can do

- Hold voice or text conversations backed by a local Ollama model
- Remember conversations and retrieve evidence-backed long-term context
- Track household presence, state, alerts, and proactive briefings
- Control registered MQTT devices and other home-automation integrations
- Use MCP tools for local services, files, KiCad, and Minecraft diagnostics
- Run durable background jobs without blocking the foreground conversation
- Accept text commands over a private Tailscale connection
- Place allowlisted phone calls and receive allowlisted SMS commands
- Drive a remote pygame information display
- Expose a read-only debug dashboard for health, latency, and tool telemetry

## Architecture

Butler is split into independently deployable processes:

| Process | Responsibility |
| --- | --- |
| Main agent | Routing, scheduling, memory, tools, text API, and display state |
| Voice worker | Wake word, microphone capture, local STT, and speech playback |
| Awareness service | PostgreSQL-backed presence, state, history, alerts, and memory |
| InfoPanel | Remote pygame display client |
| Phone bridge | Optional public webhook boundary for phone integrations |

The default inference path is local: Ollama provides an OpenAI-compatible LLM
endpoint and `faster-whisper` handles speech-to-text. Hosted services are
optional and require explicit configuration.

For a deeper technical assessment, see
[Architecture Review](docs/ARCHITECTURE_REVIEW.md). Awareness design and status
live in [Awareness and Memory](docs/awareness-memory/README.md).

## Requirements

- Python 3.12
- [Ollama](https://ollama.com/) with a configured chat model
- PortAudio-compatible audio devices and libraries for voice features
- An MQTT broker for device integrations
- Docker with Compose for the awareness database
- Windows for the full launcher and validated room-audio path

Linux and macOS can run many individual services, but the complete deployment
is currently optimized for Windows with NVIDIA GPUs. Optional integrations may
also require Node.js, Tailscale, Twilio, ElevenLabs, AWS, or OpenWeather.

## Quick start

### 1. Create the runtime environment

```bash
python3.12 -m venv .venv-runtime
source .venv-runtime/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-runtime-py312.txt
```

On Windows PowerShell, activate it with:

```powershell
.venv-runtime\Scripts\Activate.ps1
```

### 2. Configure secrets

```bash
cp .env.example .env
```

`settings.env` contains committed, non-secret defaults. Put API keys, tokens,
and passwords only in `.env`. Real environment variables override both files.

The default local LLM settings point to Ollama:

```env
BUTLER_LLM_BACKEND=ollama
BUTLER_LLM_BASE_URL=http://127.0.0.1:11434/v1
BUTLER_LLM_MODEL=mb-core-v1:latest
BUTLER_LOCAL_STT=1
BUTLER_VOICE_STREAMING=1
```

Local LLM and STT operation do not require an OpenAI key. Copy and customize
`mb-core-v1.Modelfile` if you want to build the repository's model profile.

### 3. Start Butler

Run the main agent directly:

```bash
.venv-runtime/bin/python -m butler
```

Or launch the supervised Windows control panel:

```powershell
.\Start-Butler.ps1
```

The launcher can manage Ollama, the main agent, voice worker, display,
awareness database, component selection, GPU assignment, and logs. For a
console-only launch:

```powershell
.\Start-Butler.ps1 --no-gui
```

## Running services separately

```bash
# Main agent and local text API
.venv-runtime/bin/python -m butler

# Voice worker
.venv-runtime/bin/python -m butler.voice.worker

# Display client (after installing display requirements)
.venv-display/bin/python -m butler.display.worker

# Debug dashboard
.venv-runtime/bin/python -m butler.debug_dashboard
```

The display uses its own environment:

```bash
python3.12 -m venv .venv-display
source .venv-display/bin/activate
python -m pip install -r requirements-display-py312.txt
```

Start the awareness service and database with:

```bash
docker compose -f docker-compose.awareness.yml up -d --wait
python3.12 -m venv .venv-awareness
source .venv-awareness/bin/activate
python -m pip install -r requirements-awareness-py312.txt
.venv-awareness/bin/python -m butler.awareness migrate
.venv-awareness/bin/python -m butler.awareness serve
```

See [the awareness service guide](butler/awareness/README.md) for configuration,
migrations, health checks, and operations.

## Interfaces

### Text chat

The main agent exposes a small HTTP API and browser UI, intended for localhost
or a private Tailscale network. Set `TEXT_AGENT_API_TOKEN` in `.env` before
exposing it beyond loopback.

```bash
python -m butler.text.client \
  --url "http://<tailscale-hostname-or-ip>:8420" \
  --token "<your-token>" \
  --session-id "main-pc"
```

Windows users can run the same client through `butler-chat.cmd`.

### Debug dashboard

Run `python -m butler.debug_dashboard`, then open
`http://127.0.0.1:8787`. The dashboard shows interaction flow, component health,
pipeline latency, prompt composition, model placement, memory usage, jobs, tool
reliability, and available system telemetry. It is read-only and binds to
loopback by default because it can expose private data.

### Phone and SMS

Phone calls use ElevenLabs for the conversational voice agent and Twilio for
PSTN transport. SMS commands enter the same agent path as local text chat. Both
features are optional, disabled until configured, and constrained by explicit
contact or sender allowlists. The public phone bridge and SMS webhook should be
the only internet-facing components; keep the main Butler runtime private.

Relevant variables are documented in `.env.example` and `settings.env`.

## Tools and integrations

Butler merges tools from one or more Model Context Protocol servers into a
single capability surface. The built-in server provides home automation,
awareness, and other local services. Optional servers can add:

- General filesystem access with read-only defaults
- KiCad assistance
- Minecraft Forge/modpack diagnostics
- Remote MCP services over streamable HTTP

Set `BUTLER_MCP_SERVERS` to configure the full server list. Tool names must be
unique; use `tool_prefix` when servers could collide. Detailed guides:

- [Tool Implementation Guide](docs/TOOL_IMPLEMENTATION_GUIDE.md)
- [Filesystem MCP](docs/filesystem-mcp.md)
- [Minecraft Diagnostics](docs/minecraft-forge-diagnostics.md)

## Hardware

Peripheral firmware is written for MicroPython on Raspberry Pi Pico W boards
and deployed manually. Current entry points include:

- `Peripherals/fan/main.py`
- `Peripherals/Pump-Power-Controller/Firmware/main.py`

Update device-specific Wi-Fi, MQTT, pin, and topic settings before copying a
script to a board as `main.py`. The pump controller is maintained as a Git
submodule.

## Repository layout

```text
butler/                     Main application and service packages
butler/awareness/           Awareness and long-term memory backend
InfoPanel/                  Pygame display modules and visual assets
Peripherals/                MicroPython firmware and MQTT helpers
docs/                       Architecture, operations, and subsystem guides
experiments/                Non-production visual and hardware experiments
tests/                      Unit, integration, and legacy prototype tests
```

Some persistent identifiers still use `talos` because they are external or
on-disk compatibility contracts, including database names, MQTT topics, state
files, and the GitHub repository URL. New source code, configuration, and
display names use Butler.

## Development

The CI workflow currently verifies that Python files compile under Python 3.12:

```bash
python -m compileall butler InfoPanel Peripherals tests
```

Run focused tests for the area you change, for example:

```bash
.venv-runtime/bin/python -m unittest tests/test_request_classifier.py
```

The project includes hardware, audio, database, and network integrations, so
the full test suite may require services or devices not available on every
development machine.

## Project status

Butler is under active development. Releases are created manually; the
repository does not currently publish installers or binary artifacts. See the
[implementation status](docs/awareness-memory/IMPLEMENTATION_STATUS.md) for the
awareness subsystem's completed and planned phases.

This repository does not currently include an open-source license. Unless a
license is added, the source is visible but no reuse rights are granted.
