from __future__ import annotations

import time

from talos.voice import agent as voice_agent


def main() -> int:
    stop_listening = None
    speak_server = None
    try:
        # Inbound proactive-speech endpoint (reminders, awareness alerts,
        # scheduled reports). The voice worker is the only process with TTS.
        speak_server = voice_agent.run_speak_server()
        stop_listening = voice_agent.run_voice_recognition()
        print("Voice worker running. Press Ctrl+C to stop.")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping voice worker.")
    finally:
        if speak_server is not None:
            speak_server.shutdown()
        if stop_listening:
            stop_listening(wait_for_stop=False)
        voice_agent.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
