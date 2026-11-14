"""Launch Jarvis with local commands and optional text or voice input."""
from __future__ import annotations
import argparse
import getpass
import json
from importlib.metadata import version
from pathlib import Path
import sys
import os
import time
from local_mode.assistant import LocalAgent
from local_mode.config import Settings, save_model_preferences
from local_mode.models import MODEL_ORDER
from local_mode.routing import ModelRouter
from local_mode.gemini import create_client, has_key, safe_error
from local_mode.speech import RussianRecognizer
from local_mode.results import user_message, spoken_message, present_result
from local_mode.messages import message
from google.genai import types


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--capabilities", action="store_true", help="List capabilities and validate tool imports")
    mode.add_argument("--voices", action="store_true", help="List installed local voices")
    mode.add_argument("--set-key", action="store_true", help="Save the key using Windows DPAPI (hidden input)")
    mode.add_argument("--text", action="store_true", help="Start a text chat")
    mode.add_argument("--voice", action="store_true", help="Use local speech; press Enter to record")
    mode.add_argument("--command", help="Run one command")
    mode.add_argument("--wav-command", type=Path, help="Transcribe a WAV locally and execute the command")
    mode.add_argument("--transcribe", type=Path, help="Transcribe a WAV without executing actions")
    mode.add_argument("--doctor", action="store_true", help="Check the environment without model requests")
    mode.add_argument("--check-gemini", action="store_true", help="Make one live Gemini text request")
    mode.add_argument("--devices", action="store_true", help="List microphone devices")
    parser.add_argument("--window-title", default=None, help="Explicit target for the maximize-window command")
    parser.add_argument("--browser", choices=["chrome", "msedge", "chromium"], default=None)
    parser.add_argument("--microphone", type=int, default=None)
    parser.add_argument("--hold", action="store_true", help="Keep the process and media session alive until Ctrl+C after one command")
    parser.add_argument("--remote", action="store_true", help="Enable the optional Bluetooth client transport")
    parser.add_argument("--language", choices=["en", "ru"], default="en", help="Language for app-owned reports")
    parser.add_argument("--model", choices=MODEL_ORDER, default=None)
    parser.add_argument("--auto-fallback", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--save-model", action="store_true", help="Save the selected model and fallback mode")
    args = parser.parse_args(argv)
    if args.remote:
        os.environ["JARVIS_BLUETOOTH"] = "1"
    try:
        settings = Settings.from_env(window_title=args.window_title, browser_channel=args.browser, microphone=args.microphone,
                                     model=args.model, auto_fallback=args.auto_fallback)
        # Вложенные исходные инструменты перечитывают Settings: передаём им тот же выбор.
        os.environ["JARVIS_MODEL"] = settings.model
        os.environ["JARVIS_AUTO_FALLBACK"] = str(settings.auto_fallback).lower()
        if args.save_model:
            save_model_preferences(settings)
            print(message("Model saved: {0}; automatic fallback: {1}", settings.model, settings.auto_fallback, locale=args.language))
            return 0
        if args.capabilities:
            from local_mode.capabilities import report
            result = report()
            print(json.dumps(present_result(result, locale=args.language), ensure_ascii=False, indent=2))
            return 0 if result["ok"] else 1
        if args.voices:
            from local_mode.voice import LocalSpeaker
            print(json.dumps(LocalSpeaker.voices(), ensure_ascii=False, indent=2))
            return 0
        if args.set_key:
            from local_mode.credentials import save_key
            save_key(getpass.getpass(message("Gemini API key (hidden input): ", locale=args.language)))
            print(message("Key saved using Windows DPAPI. It is not committed to Git.", locale=args.language))
            return 0
        if args.devices:
            import sounddevice as sd
            print(sd.query_devices())
            return 0
        if args.doctor:
            import sounddevice as sd
            from local_mode.whisper_voice import MODEL as WHISPER_MODEL
            from local_mode.network.network_preferences import read_network_preferences
            from local_mode.network.proxy_cache import read_cached_proxies
            print(json.dumps({"python": sys.version.split()[0], "google_genai": version("google-genai"),
                              "vosk": version("vosk"), "playwright": version("playwright"),
                              "asr_backend": os.environ.get("JARVIS_ASR", "whisper"),
                              "whisper_model_exists": (WHISPER_MODEL / "model.bin").is_file(),
                              "model_exists": (settings.vosk_model / "am" / "final.mdl").is_file(),
                              "gemini_key_configured": has_key(), "gemini_model": settings.model,
                              "network_mode": read_network_preferences()["mode"], "cached_proxy_count": len(read_cached_proxies()),
                              "auto_fallback": settings.auto_fallback, "model_chain": ModelRouter(settings.model, settings.auto_fallback).models,
                              "input_devices": sum(1 for device in sd.query_devices() if device["max_input_channels"] > 0)},
                             ensure_ascii=False, indent=2))
            valid = (settings.vosk_model / "am" / "final.mdl").is_file()
            if os.environ.get("JARVIS_ASR", "whisper") == "whisper":
                from local_mode.whisper_voice import MODEL
                valid = valid and (MODEL / "model.bin").is_file()
            return 0 if valid else 1
        if args.check_gemini:
            with create_client() as client:
                router = ModelRouter(settings.model, settings.auto_fallback)
                response = router.generate(client, contents=[types.Content(role="user", parts=[types.Part(text="Reply OK")])], config=
                                    types.GenerateContentConfig(http_options=types.HttpOptions(
                                        timeout=settings.timeout_ms, retry_options=types.HttpRetryOptions(attempts=1))))
                if not response.text:
                    raise RuntimeError("Gemini returned no text.")
                print(f"Model: {router.active_model}\n{response.text}")
            return 0
        if args.transcribe:
            print(RussianRecognizer(settings.vosk_model, settings.microphone).transcribe_wav(args.transcribe))
            return 0
        if not any((args.text, args.voice, args.command is not None, args.wav_command)):
            from local_mode.desktop_launch import launch
            return launch(Path(__file__).resolve().parent.parent)
        from local_mode.voice import LocalSpeaker
        speaker = LocalSpeaker(enabled=args.voice)
        speaker.prepare()
        def announce(text: str) -> None:
            text = user_message({"ok": True, "reply": text}, locale=args.language)
            print(text, flush=True)
            speaker.say(text)
        bot = LocalAgent(settings, speak=announce)
        bot.tools.special["set_voice"] = lambda args: speaker.select(args["voice"])
        if args.text or args.voice or args.hold:
            bot.background.report = announce
            bot.background.start()
        try:
            if args.command is not None or args.wav_command:
                command = args.command
                if args.wav_command:
                    command = RussianRecognizer(settings.vosk_model, settings.microphone).transcribe_wav(args.wav_command)
                    print(json.dumps({"recognized": command}, ensure_ascii=False), flush=True)
                result = bot.run(command)
                print(json.dumps(present_result(result, locale=args.language), ensure_ascii=False, indent=2), flush=True)
                if args.hold and result.get("ok"):
                    print(message("The process is active; Ctrl+C stops Jarvis and closes its owned browser session.", locale=args.language), flush=True)
                    while True:
                        time.sleep(0.5)
                return 0 if result.get("ok") else 1
            recognizer = RussianRecognizer(settings.vosk_model, settings.microphone) if args.voice else None
            while True:
                if recognizer:
                    if input(message("Enter to listen, /exit to quit: ", locale=args.language)).strip() == "/exit":
                        return 0
                    speaker.wait()
                    print(message("Listening…", locale=args.language), flush=True)
                    command = recognizer.listen()
                    print(message("Recognized:", locale=args.language), command)
                else:
                    command = input(message("You: ", locale=args.language))
                if command.strip().casefold() in {"exit", "/exit", "выход"}:
                    return 0
                if not command.strip():
                    continue
                try:
                    result = bot.run(command)
                    print(json.dumps(present_result(result, locale=args.language), ensure_ascii=False, indent=2))
                    if args.voice:
                        speaker.say(spoken_message(result, locale=args.language))
                except Exception as error:
                    print(message("Error:", locale=args.language), user_message({"ok": False, "error": safe_error(error)}, locale=args.language))
        finally:
            bot.stop.set()
            bot.close()
            speaker.close()
    except (KeyboardInterrupt, EOFError):
        return 0
    except Exception as error:
        print(json.dumps(present_result({"ok": False, "error": safe_error(error)}, locale=args.language), ensure_ascii=False), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
