import io, json, threading, time, wave
from pathlib import Path
from unittest.mock import Mock, patch
from concurrent.futures import CancelledError
from local_mode.config import Settings
from local_mode.speech import RussianRecognizer, voice_command
from local_mode.silero_voice import RussianVoice as Silero
from local_mode.voice import LocalSpeaker
from local_mode.voice_cycle import VoiceCycle
from local_mode.assistant import LocalAgent
from local_mode.results import spoken_message
from local_mode.messages import state_label
from local_mode import gemini

states = []
requests = []
outputs = []
errors = []
speaker = LocalSpeaker()
speaker.on_error = errors.append
cycle = VoiceCycle(speaker)


def observe():
    state = cycle.display_state()
    label = state_label(state)
    if not states or states[-1] != label:
        states.append(label)
    return state


observe()
recognizer = RussianRecognizer(Settings().vosk_model)
source = Silero().wav("Джарвис, сколько будет двенадцать плюс пятнадцать?")
with wave.open(io.BytesIO(source), "rb") as wav:
    rate = wav.getframerate()
    pcm = wav.readframes(wav.getnframes()) + bytes(rate * 4)


def input_stream(**options):
    def enter():
        step = max(800, rate // 5) * 2
        for start in range(0, len(pcm), step):
            options["callback"](pcm[start : start + step], step // 2, None, None)

    context = Mock()
    context.__enter__ = Mock(side_effect=enter)
    context.__exit__ = Mock(return_value=False)
    return context


def phase(value):
    cycle.set_phase(value)
    observe()


token = cycle.capture_token()
with patch(
    "sounddevice.query_devices", return_value={"default_samplerate": rate}
), patch("sounddevice.RawInputStream", side_effect=input_stream):
    heard = voice_command(
        recognizer.listen(
            timeout=3,
            interrupted=lambda: cycle.capture_interrupted(token),
            on_state=phase,
        ),
        True,
    )
assert heard and not cycle.capture_interrupted(token)
cycle.set_phase("PROCESSING")
real_generate = gemini.generate_stream


def generate(client, model, contents, config, **kwargs):
    assert observe() == "THINKING"
    requests.append({"model": model, "prompt": contents[-1].parts[0].text})
    return real_generate(client, model, contents, config, **kwargs)


with patch("local_mode.assistant.WindowController"), patch(
    "local_mode.assistant.YouTubePlayer"
):
    bot = LocalAgent(Settings.from_env())
    bot.on_state = phase
    bot.on_text = lambda text: None
    try:
        with patch.object(
            gemini, "generate_stream", side_effect=generate
        ), patch.object(bot.tools, "execute") as execute:
            result = bot.run(heard)
        execute.assert_not_called()
        assert result.get("ok"), result
        assert (
            "27" in result["reply"] or "двадцать семь" in result["reply"].casefold()
        ), result
        assert requests[0]["prompt"] == heard
    finally:
        if bot.client:
            bot.client.close()
text = spoken_message(result)
cycle.set_phase("ONLINE")


class SilentOutput:
    def __init__(self, **options):
        self.options = options
        self.started = threading.Event()
        self.paused = threading.Event()
        self.chunks = []
        self.aborted = False
        self.closed = False
        outputs.append(self)

    def start(self):
        pass

    def stop(self):
        self.paused.set()

    def close(self):
        self.closed = True

    def abort(self):
        self.aborted = True

    def write(self, data):
        assert not cycle.can_listen()
        self.chunks.append(data.copy())
        self.started.set()
        time.sleep(0.01)


def wait_for_output(index):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        observe()
        if len(outputs) > index and outputs[index].started.is_set():
            return outputs[index]
        time.sleep(0.01)
    raise AssertionError("No speech output: " + str(errors))


def assert_no_self_capture():
    with patch("sounddevice.RawInputStream") as mic, patch(
        "sounddevice.query_devices"
    ) as devices:
        assert recognizer.listen(interrupted=lambda: not cycle.can_listen()) == ""
        mic.assert_not_called()
        devices.assert_not_called()


try:
    with patch("sounddevice.OutputStream", side_effect=SilentOutput):
        job = speaker.say(text)
        first = wait_for_output(0)
        assert observe() == "SPEAKING"
        assert_no_self_capture()
        assert speaker.pause()
        assert observe() == "PAUSED"
        assert first.paused.wait(2)
        count = len(first.chunks)
        time.sleep(0.1)
        assert len(first.chunks) == count
        assert_no_self_capture()
        speaker.resume()
        deadline = time.monotonic() + 2
        while len(first.chunks) == count and time.monotonic() < deadline:
            time.sleep(0.005)
        assert observe() == "SPEAKING"
        job.result(timeout=30)
        speaker.wait()
        assert observe() == "ONLINE"
        assert_no_self_capture()  # Защитный интервал после закрытия выхода.
        second_job = speaker.say(
            text + "\n\nПосле остановки этот второй абзац не должен воспроизводиться."
        )
        second = wait_for_output(1)
        assert observe() == "SPEAKING"
        speaker.cancel()
        observe()
        try:
            second_job.result(timeout=5)
            raise AssertionError("Stop did not cancel speech")
        except CancelledError:
            pass
        speaker.wait()
        assert second.aborted and second.closed
        cancelled_gpu = bool(getattr(speaker._vulkan_engine, "cancelled", False))
        assert_no_self_capture()
        assert observe() == "ONLINE"
        third_job = speaker.say("Проверка завершена.")
        third = wait_for_output(2)
        third_job.result(timeout=30)
        speaker.wait()
        assert not errors and not speaker.error
        time.sleep(0.45)
        assert cycle.can_listen() and cycle.capture_interrupted(token)
        assert observe() == "ONLINE"
    report = {
        "recognized": heard,
        "gemini": requests,
        "reply": result["reply"],
        "tts_voice": speaker.voice,
        "backend": speaker.backend_status,
        "states": states,
        "pause_resume_stop": "PASS",
        "self_capture_blocked": "PASS",
        "next_reply_after_stop": "PASS",
        "cancelled_gpu_inference": cancelled_gpu,
        "audio_written_to_device": False,
        "errors": errors,
    }
    Path(".cache/tauri-voice-cycle-result.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False), flush=True)
finally:
    speaker.close()
