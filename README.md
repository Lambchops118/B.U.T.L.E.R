<div align="center">

<h1>Monkey Butler</h1>

<p><strong>Better Understanding Text-to-Speech Large Language Encoder Routine</strong></p>

<p>
  <img src="docs/assets/butler-portrait.gif" alt="Animated Butler portrait" width="420">
</p>

<p>
  <a href="https://github.com/Lambchops118/Talos/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/Lambchops118/Talos/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://www.python.org/downloads/release/python-3120/"><img alt="Python 3.12" src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&amp;logoColor=white"></a>
  <a href="#requirements"><img alt="Platform: Windows, Linux, and macOS" src="https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey"></a>
  <a href="#project-status"><img alt="Status: active development" src="https://img.shields.io/badge/status-active%20development-orange"></a>
</p>

<h2>Peripheral Systems</h2>

<h3><a href="https://github.com/Lambchops118/Kitchen-Recipe-Screen">Kitchen Screen</a></h3>
<h3><a href="https://github.com/Lambchops118/Pump-Power-Controller">Pump Controller</a></h3>
<h3><a href="https://github.com/Lambchops118/Discord-Voice-Frontend">Discord Voice</a></h3>

</div>

B.U.T.L.E.R. is a local-first, voice-first AI assistant for conversation, home
automation, and engineering work. It combines local language-model inference,
speech recognition and synthesis, durable memory, environmental awareness, and
an extensible tool system. The launcher supervises the cooperating services
that make up a full deployment.


## What it can do

- Hold voice or text conversations through an OpenAI-compatible local model
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

The current implementation uses several independently deployable processes:

| Process | Responsibility |
| --- | --- |
| Main service (`python -m butler`) | Router, scheduler, agent runtime, memory, MCP client, text/SMS servers, and display-state projection |
| Room voice worker (`python -m butler.voice.worker`) | Wake word, microphone capture, local STT, TTS playback, and proactive-speech endpoint |
| Awareness API (`python -m butler.awareness serve`) | Presence, state, history, alerts, actions, briefings, and evidence-backed memory |
| Awareness database | TimescaleDB/PostgreSQL with pgvector, managed through Docker Compose |
| InfoPanel (`python -m butler.display.worker`) | Independently deployable pygame display client |
| Ollama | Local model server used by the main service |

The main service and room voice worker share `.venv-runtime`, but they are still
separate operating-system processes. The voice worker sends recognized commands
to the main service's HTTP API; the InfoPanel polls that API for display state.
The launcher starts and supervises both processes independently. A separate
public phone bridge is used only when phone calling is configured, and the
optional Discord voice frontend lives in its own repository.

The default inference path is local: Ollama provides an OpenAI-compatible LLM
endpoint and `faster-whisper` handles speech-to-text. The tracked default model
name is `mb-core-v1:latest`; this repository also includes a Ministral 3 14B
profile. Hosted LLM fallback requires an OpenAI API key, and hosted STT fallback
can be disabled when audio must remain local.

For a deeper technical assessment, see
[Architecture Review](docs/ARCHITECTURE_REVIEW.md). Awareness design and status
live in [Awareness and Memory](docs/awareness-memory/README.md).

## Requirements

For the core text agent:

- Python 3.12
- [Ollama](https://ollama.com/) with a configured chat model
- The packages in `requirements/runtime-py312.txt`

Additional features require additional infrastructure:

- Room voice: PortAudio-compatible audio hardware and libraries
- Awareness: Docker with Compose and `requirements/awareness-py312.txt`
- InfoPanel: `requirements/display-py312.txt`
- Home automation and awareness ingestion: a reachable MQTT broker
- Validated room-audio and GPU-pinning deployment: Windows with NVIDIA GPUs

Linux and macOS can run individual services, but the complete deployed stack is
currently optimized for Windows. Optional integrations may also require Node.js,
Tailscale, Twilio, ElevenLabs, AWS, or OpenWeather.

## Quick start

### 1. Create the environments

The main service and room voice worker share the runtime environment:

```bash
python3.12 -m venv .venv-runtime
source .venv-runtime/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements/runtime-py312.txt
```

The separately deployable display and awareness API use focused environments:

```bash
python3.12 -m venv .venv-display
.venv-display/bin/python -m pip install -r requirements/display-py312.txt

python3.12 -m venv .venv-awareness
.venv-awareness/bin/python -m pip install -r requirements/awareness-py312.txt
```

On Windows, use the interpreters under `Scripts` instead of `bin`; for example:

```powershell
py -3.12 -m venv .venv-runtime
.venv-runtime\Scripts\python.exe -m pip install -r requirements/runtime-py312.txt
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

Local LLM and STT operation do not require an OpenAI key. The committed
`models/mb-core-v1.Modelfile` references an owner-machine Ollama blob, so it is
not a portable recipe from a public base model.
`models/ministral-butler.Modelfile` is the reproducible profile included in the
repository.

### 3. Start the stack

Launch the supervised Windows control panel:

```powershell
.\Start-Butler.ps1
```

The launcher can manage Ollama, the main service, room voice worker, display,
awareness database/API, migrations, component selection, GPU assignment, and
logs. For a console-only launch with the last saved component selection:

```powershell
.\Start-Butler.ps1 --no-gui
```

## Running components separately

```bash
# Main service: agent runtime, scheduler, text/SMS APIs, and display state
.venv-runtime/bin/python -m butler

# Room voice worker; connects to the main service over HTTP
.venv-runtime/bin/python -m butler.voice.worker

# Display client (after installing display requirements)
.venv-display/bin/python -m butler.display.worker

# Debug dashboard
.venv-runtime/bin/python -m butler.debug_dashboard
```

Start the awareness service and database with:

```bash
docker compose -f docker-compose.awareness.yml up -d --wait
.venv-awareness/bin/python -m butler.awareness migrate
.venv-awareness/bin/python -m butler.awareness serve
```

See [the awareness service guide](butler/awareness/README.md) for configuration,
migrations, health checks, and operations.

## Interfaces

### Text chat

The main service exposes a small HTTP API and browser UI, intended for localhost
or a private Tailscale network. Set `TEXT_AGENT_API_TOKEN` in `.env` before
exposing it beyond loopback.

```bash
python -m butler.text.client \
  --url "http://<tailscale-hostname-or-ip>:8420" \
  --token "<your-token>" \
  --session-id "main-pc"
```

Windows users can run the same client through
`scripts\clients\butler-chat.cmd`.

### Debug dashboard

Run `python -m butler.debug_dashboard`, then open
`http://127.0.0.1:8787`. The dashboard shows interaction flow, component health,
pipeline latency, prompt composition, model placement, memory usage, jobs, tool
reliability, and available system telemetry. It is read-only and binds to
loopback by default because it can expose private data.

### Phone and SMS

Phone calls use ElevenLabs for the conversational voice agent and Twilio for
PSTN transport. SMS commands enter the same agent path as local text chat. The
tracked settings enable both integrations, but they become operational only
when their required credentials, identifiers, URLs, and allowlists are present.
The public phone bridge and SMS webhook should be the only internet-facing
components; keep the main Butler runtime private.

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
docker/                     Container and test-broker configuration
docs/                       Architecture, operations, and subsystem guides
models/                     Ollama model definitions
prototypes/                 Legacy and experimental UI/visualization work
requirements/               Dependency manifests for each runtime
scripts/                    Secondary command-line wrappers
tests/                      Unit and integration tests
tools/                      Maintenance, diagnostics, and asset exporters
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
