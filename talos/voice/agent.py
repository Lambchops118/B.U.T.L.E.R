from __future__ import annotations

import io
import os
import time
import wave
import audioop
import tempfile
import boto3
import openai
import whisper
import pyaudio
import threading
import contextlib
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import speech_recognition as sr

from talos.config import env_bool, load_environment, require_env
from talos.text.service_client import send_message, stream_message
from talos.voice.benchmarking import VoiceBenchmarkSession
from talos.voice.streaming.speaker import StreamingSpeaker


load_environment()

r = sr.Recognizer()
WAKE_WORD = os.getenv("WAKE_WORD", "butler").lower()
WAKE_WORD_MODE = os.getenv("WAKE_WORD_MODE", "local").lower()
WAKE_WORD_MODEL = os.getenv("WAKE_WORD_MODEL", "base")
VOICE_SESSION_ID = os.getenv("TALOS_VOICE_SESSION", "voice-worker")
VOICE_AGENT_URL = os.getenv("TALOS_TEXT_AGENT_URL", "http://127.0.0.1:8420")
VOICE_AGENT_TOKEN = os.getenv("TALOS_TEXT_AGENT_TOKEN", os.getenv("TEXT_AGENT_API_TOKEN", ""))
VOICE_AGENT_TIMEOUT = float(os.getenv("TALOS_TEXT_AGENT_CLIENT_TIMEOUT", "30"))
VOICE_STREAMING = env_bool("TALOS_VOICE_STREAMING", True)
# Use one local STT pass to serve both wake-word and command (removes the remote
# whisper-1 round-trip and the separate local wake transcription). Falls back to
# the remote path automatically if the local backend is unavailable.
LOCAL_STT_ENABLED = env_bool("TALOS_LOCAL_STT", True)

audio_interface = pyaudio.PyAudio()
aws_access_key = os.getenv("AWS_ACCESS_KEY")
aws_secret_key = os.getenv("AWS_SECRET_KEY")
VOICE_AUDIO_OUTPUT_DEVICE_INDEX = os.getenv("TALOS_AUDIO_OUTPUT_DEVICE_INDEX")

openai.api_key = require_env("OPENAI_API_KEY")
client = openai.OpenAI(api_key=openai.api_key)
polly_client = boto3.client(
    "polly",
    aws_access_key_id=aws_access_key,
    aws_secret_access_key=aws_secret_key,
    region_name="us-west-2",
)

_wake_model = None
_wake_model_lock = threading.Lock()
_wake_infer_lock = threading.Lock()
_command_executor = ThreadPoolExecutor(max_workers=2)


def _resolve_output_device_index():
    if not VOICE_AUDIO_OUTPUT_DEVICE_INDEX:
        return None
    try:
        return int(VOICE_AUDIO_OUTPUT_DEVICE_INDEX)
    except ValueError as exc:
        raise RuntimeError(
            "TALOS_AUDIO_OUTPUT_DEVICE_INDEX must be an integer if set."
        ) from exc


def _describe_output_device(device_index):
    try:
        if device_index is None:
            info = audio_interface.get_default_output_device_info()
        else:
            info = audio_interface.get_device_info_by_index(device_index)
    except Exception as exc:
        return f"unavailable ({exc})"

    name = info.get("name", "unknown")
    host_api = info.get("hostApi")
    max_channels = info.get("maxOutputChannels")
    return f"{name} (index={info.get('index')}, hostApi={host_api}, maxOutputChannels={max_channels})"


def _get_wake_model():
    global _wake_model
    if _wake_model is None:
        with _wake_model_lock:
            if _wake_model is None:
                print(f"Loading local wake-word model: {WAKE_WORD_MODEL}")
                _wake_model = whisper.load_model(WAKE_WORD_MODEL)
    return _wake_model


def _local_wake_word_detect(audio_data):
    if WAKE_WORD_MODE != "local":
        return True

    try:
        model = _get_wake_model()
        raw_audio = audio_data.get_raw_data(convert_rate=16000, convert_width=2)
        if not raw_audio:
            return False

        audio = np.frombuffer(raw_audio, np.int16).astype(np.float32) / 32768.0
        if audio.size == 0:
            return False

        with _wake_infer_lock:
            result = model.transcribe(
                audio,
                language="en",
                task="transcribe",
                fp16=False,
                temperature=0,
                condition_on_previous_text=False,
                no_speech_threshold=0.6,
            )

        text = (result.get("text") or "").strip().lower()
        print(f"Local wake check: '{text}'")
        return WAKE_WORD in text
    except Exception as exc:
        print(f"Local wake-word detection error: {exc}")
        return True


def _extract_transcription_text(transcription_result):
    if isinstance(transcription_result, str):
        return transcription_result
    text = getattr(transcription_result, "text", None)
    if text is not None:
        return text
    if isinstance(transcription_result, dict):
        return transcription_result.get("text", "")
    return ""


def _extract_transcription_words(transcription_result):
    words = getattr(transcription_result, "words", None)
    if words is not None:
        return words
    if isinstance(transcription_result, dict):
        return transcription_result.get("words")
    return None


def play_audio(filename, benchmark=None):
    try:
        chunk = 1024
        output_device_index = _resolve_output_device_index()
        if benchmark:
            benchmark.mark_stage("audio_open_start")
        with wave.open(filename, "rb") as wf:
            print(f"Opening playback stream for '{filename}' using output device: {_describe_output_device(output_device_index)}")
            stream = audio_interface.open(
                format=audio_interface.get_format_from_width(wf.getsampwidth()),
                channels=wf.getnchannels(),
                rate=wf.getframerate(),
                output=True,
                output_device_index=output_device_index,
            )
            if benchmark:
                benchmark.mark_stage("audio_stream_ready")
            data = wf.readframes(chunk)
            first_chunk = True
            while data:
                stream.write(data)
                if benchmark and first_chunk:
                    benchmark.mark_stage("first_audio")
                    benchmark.emit_summary_once("first_audio")
                    first_chunk = False
                data = wf.readframes(chunk)
            stream.stop_stream()
            stream.close()
            print(f"Finished playback for '{filename}'.")

        time.sleep(0.2)
        os.remove(filename)
        print(f"Removed temporary audio file '{filename}'.")
    except Exception as exc:
        if benchmark:
            benchmark.add_error(f"Audio playback error: {exc}")
            benchmark.emit_summary_once("audio_playback_error")
        print(f"Error in play_audio: {exc}")


_stt_backend = None
_stt_backend_lock = threading.Lock()
_stt_unavailable = False


def _get_stt_backend():
    global _stt_backend
    if _stt_backend is None:
        with _stt_backend_lock:
            if _stt_backend is None:
                from talos.voice.backends.factory import get_stt_backend
                _stt_backend = get_stt_backend()
    return _stt_backend


def _transcribe_local(audio_data, benchmark):
    """Single local STT pass; the transcript serves both wake and command."""
    from talos.voice.backends.base import AudioChunk

    raw = audio_data.get_raw_data(convert_rate=16000, convert_width=2)
    benchmark.mark_stage("stt_send")
    result = _get_stt_backend().transcribe(AudioChunk(pcm=raw, sample_rate=16000))
    benchmark.mark_stage("stt_done")
    return result.text


def _transcribe_remote_with_wake_gate(audio_data, benchmark):
    """Legacy path: cheap local wake gate, then remote whisper-1 transcription.

    Returns the transcript text, or ``None`` if the wake gate rejected the clip
    (already logged/emitted).
    """
    if WAKE_WORD_MODE == "local":
        benchmark.mark_stage("local_wake_send")
        wake_detected = _local_wake_word_detect(audio_data)
        benchmark.mark_stage("local_wake_done")
    else:
        wake_detected = True

    if not wake_detected:
        print("Wake word not detected locally; skipping Whisper API call.")
        benchmark.add_note("Local wake-word check rejected the clip before remote STT.")
        benchmark.emit_summary_once("wake_word_rejected")
        return None

    wav_bytes = audio_data.get_wav_data()
    audio_file = io.BytesIO(wav_bytes)
    audio_file.name = "speech.wav"

    benchmark.mark_stage("stt_send")
    whisper_result = client.audio.transcriptions.create(
        model="whisper-1",
        file=audio_file,
        response_format="verbose_json",
        language="en",
        temperature=0,
        timestamp_granularities=["word"],
    )
    benchmark.mark_stage("stt_done")

    text = _extract_transcription_text(whisper_result).strip().lower()
    if text:
        benchmark.note_wake_word_offsets(_extract_transcription_words(whisper_result))
    return text


def _acquire_transcript(audio_data, benchmark):
    """Return the transcript text (or ``None`` if the clip was already handled).

    Prefers the single local STT pass; on any local failure it disables the local
    path and falls back to the remote wake-gate + whisper-1 flow.
    """
    global _stt_unavailable
    if LOCAL_STT_ENABLED and not _stt_unavailable:
        try:
            return _transcribe_local(audio_data, benchmark)
        except Exception as exc:
            print(f"Local STT failed ({exc}); falling back to remote STT.")
            benchmark.add_error(f"Local STT error: {exc}")
            _stt_unavailable = True
    return _transcribe_remote_with_wake_gate(audio_data, benchmark)


def recognition_callback(recognizer, audio_data):
    print("Recognition callback triggered.")
    benchmark = VoiceBenchmarkSession(wake_word=WAKE_WORD, wake_word_mode=WAKE_WORD_MODE)
    try:
        print("Trying recognition with Whisper...")
        raw_audio = audio_data.get_raw_data()
        sample_width = audio_data.sample_width or 2
        sample_rate = audio_data.sample_rate or 16000

        rms = audioop.rms(raw_audio, sample_width)
        duration = len(raw_audio) / float(sample_rate * sample_width) if sample_rate and sample_width else 0
        benchmark.note_recording_ready(duration)
        benchmark.set_metric("input_rms", rms)
        if rms < 300 or duration < 0.35:
            print(f"Skipping low-energy audio (rms={rms}, dur={duration:.2f}s)")
            benchmark.add_note("Skipped low-energy or too-short audio clip.")
            benchmark.emit_summary_once("discarded_audio")
            return

        transcript = _acquire_transcript(audio_data, benchmark)
        if transcript is None:
            return

        text_spoken = transcript.strip().lower()
        if not text_spoken:
            print("No transcription returned.")
            benchmark.add_note("STT returned an empty transcript.")
            benchmark.emit_summary_once("empty_transcript")
            return

        benchmark.set_transcript(text_spoken)
        print(f"User said: {text_spoken}")

        if text_spoken.startswith(WAKE_WORD):
            command = text_spoken[len(WAKE_WORD):].lstrip(" ,.:;!?-").strip()
            print(f"Command received: {command}")
            if command:
                benchmark.set_command(command)
                _command_executor.submit(handle_command, command, benchmark)
                return

        benchmark.add_note("Transcript did not begin with the configured wake word.")
        benchmark.emit_summary_once("wake_word_missing_in_transcript")
    except sr.UnknownValueError:
        print("Could not understand the audio.")
        benchmark.add_error("Speech recognition callback could not understand the audio.")
        benchmark.emit_summary_once("speech_recognition_unknown_value")
    except sr.RequestError as exc:
        print(f"Speech Recognition API error: {exc}")
        benchmark.add_error(f"Speech recognition request error: {exc}")
        benchmark.emit_summary_once("speech_recognition_request_error")
    except Exception as exc:
        print(f"Unexpected Error: {exc}")
        benchmark.add_error(f"Recognition callback error: {exc}")
        benchmark.emit_summary_once("recognition_callback_error")


def run_voice_recognition():
    mic = sr.Microphone()
    print("Microphone initialized.")
    with mic as source:
        r.adjust_for_ambient_noise(source, duration=1.0)
        r.dynamic_energy_threshold = False
        r.energy_threshold = 500
        r.pause_threshold = 0.6
        r.non_speaking_duration = 0.4
        print("Adjusted for ambient noise.")

    stop_listening = r.listen_in_background(mic, recognition_callback)
    print("Background listening started.")
    return stop_listening


def handle_command(command, benchmark=None):
    print(f"Handling voice command: {command}")
    if VOICE_STREAMING:
        progress = {"spoke": False}
        try:
            _handle_command_streaming(command, benchmark, progress)
            return
        except Exception as exc:
            print(f"Streaming voice path error: {exc}")
            if benchmark:
                benchmark.add_error(f"Streaming path error: {exc}")
            if progress["spoke"]:
                # Audio already started; do not replay via the fallback path.
                if benchmark:
                    benchmark.emit_summary_once("voice_worker_error")
                return
            print("Falling back to non-streaming voice path.")
    _handle_command_legacy(command, benchmark)


def _handle_command_streaming(command, benchmark, progress):
    """Stream the agent response and speak it sentence-by-sentence as it arrives."""
    output_device_index = _resolve_output_device_index()
    print(f"Opening streaming playback on output device: {_describe_output_device(output_device_index)}")
    stream = audio_interface.open(
        format=audio_interface.get_format_from_width(2),
        channels=1,
        rate=16000,
        output=True,
        output_device_index=output_device_index,
    )
    marks = {"polly": False}

    def synth(text):
        if benchmark and not marks["polly"]:
            marks["polly"] = True
            benchmark.mark_stage("polly_send")
        with contextlib.closing(
            polly_client.synthesize_speech(
                VoiceId="Brian",
                OutputFormat="pcm",
                SampleRate="16000",
                Text=text,
                Engine="neural",
            ).get("AudioStream")
        ) as audio_stream:
            return [audio_stream.read()]

    def sink(pcm):
        stream.write(pcm)

    def on_first_audio():
        progress["spoke"] = True
        if benchmark:
            benchmark.mark_stage("first_audio")
            benchmark.emit_summary_once("first_audio")

    def tracked_deltas():
        if benchmark:
            benchmark.mark_stage("llm_send")
        seen_first = False
        for delta in stream_message(
            command,
            session_id=VOICE_SESSION_ID,
            source="voice",
            base_url=VOICE_AGENT_URL,
            token=VOICE_AGENT_TOKEN,
            timeout=None,
        ):
            if not seen_first:
                seen_first = True
                if benchmark:
                    benchmark.mark_stage("llm_first_done")
            yield delta

    speaker = StreamingSpeaker(synth, sink, on_first_audio=on_first_audio)
    try:
        response_text = speaker.speak_stream(tracked_deltas())
    finally:
        try:
            stream.stop_stream()
            stream.close()
        except Exception:
            pass

    if benchmark:
        benchmark.mark_stage("llm_done")
        benchmark.set_response_text(response_text)
    print(f"Bot response: {response_text}")


def _handle_command_legacy(command, benchmark=None):
    print(f"Handling voice command (legacy path): {command}")
    try:
        if benchmark:
            benchmark.mark_stage("llm_send")
        response_text = send_message(
            command,
            session_id=VOICE_SESSION_ID,
            source="voice",
            base_url=VOICE_AGENT_URL,
            token=VOICE_AGENT_TOKEN,
            timeout=VOICE_AGENT_TIMEOUT,
        )
        if benchmark:
            benchmark.mark_stage("llm_done")
            benchmark.set_response_text(response_text)
        print(f"Bot response: {response_text}")

        if benchmark:
            benchmark.mark_stage("polly_send")
        with contextlib.closing(
            polly_client.synthesize_speech(
                VoiceId="Brian",
                OutputFormat="pcm",
                SampleRate="16000",
                Text=response_text,
                Engine="neural",
            ).get("AudioStream")
        ) as stream:
            pcm_data = stream.read()
            if benchmark:
                benchmark.mark_stage("polly_done")
            print("Speech synthesized successfully.")

        with tempfile.NamedTemporaryFile(prefix="talos_speech_", suffix=".wav", delete=False) as tmp_file:
            filename = tmp_file.name

        with wave.open(filename, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframesraw(pcm_data)

        print(f"Wrote synthesized speech to temporary file '{filename}'.")
        audio_thread = threading.Thread(target=play_audio, args=(filename, benchmark))
        print("Starting audio playback thread.")
        audio_thread.start()
    except openai.OpenAIError as exc:
        if benchmark:
            benchmark.add_error(f"OpenAI API error: {exc}")
            benchmark.emit_summary_once("openai_error")
        print(f"OpenAI API Error: {exc}")
    except boto3.exceptions.Boto3Error as exc:
        if benchmark:
            benchmark.add_error(f"AWS Polly error: {exc}")
            benchmark.emit_summary_once("polly_error")
        print(f"AWS Polly Error: {exc}")
    except Exception as exc:
        if benchmark:
            benchmark.add_error(f"Voice worker command error: {exc}")
            benchmark.emit_summary_once("voice_worker_error")
        print(f"Unexpected Error: {exc}")


def shutdown() -> None:
    _command_executor.shutdown(wait=False, cancel_futures=True)
    audio_interface.terminate()
