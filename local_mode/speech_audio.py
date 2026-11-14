"""Общий WAV-плеер с паузами и отменой; без изменения системных настроек звука."""
from __future__ import annotations
import io
from threading import Event
import wave
from typing import Callable
from local_mode.messages import OwnedRuntimeError, OwnedValueError


class PlaybackStop(Event):
    """Совместимый Event отмены с общим состоянием паузы озвучки."""
    def __init__(self, paused: Event, on_state: Callable[[str], None] = lambda state: None,
                 on_level: Callable[[float], None] = lambda level: None):
        super().__init__()
        self.paused = paused
        self.on_state = on_state
        self.on_level = on_level


def _output_formats(sd):
    """Сначала штатный формат, затем системные выходы других Windows API."""
    try:
        device = sd.query_devices(kind="output")
        yield {"samplerate": int(device["default_samplerate"]),
               "channels": min(2, int(device["max_output_channels"]))}
    except sd.PortAudioError:
        pass
    try:
        hosts = sd.query_hostapis()
    except sd.PortAudioError:
        return
    for name in ("Windows WASAPI", "Windows DirectSound"):
        for host in hosts:
            index = host["default_output_device"]
            if host["name"] != name or index < 0:
                continue
            try:
                device = sd.query_devices(index, "output")
                yield {"device": index, "samplerate": int(device["default_samplerate"]),
                       "channels": min(2, int(device["max_output_channels"]))}
            except sd.PortAudioError:
                continue


def _open_output(sd, rate: int):
    """Ошибка бывает и в start(), а не только при создании потока MME."""
    from itertools import chain
    tried = set()
    last_error = None
    for options in chain([{"samplerate": rate, "channels": 1}], _output_formats(sd)):
        signature = (options.get("device"), options["samplerate"], options["channels"])
        if signature in tried or options["channels"] < 1:
            continue
        tried.add(signature)
        stream = None
        try:
            stream = sd.OutputStream(dtype="int16", **options)
            stream.start()
            return stream, options["samplerate"], options["channels"]
        except sd.PortAudioError as error:
            last_error = error
            if stream is not None:
                stream.close()
    raise OwnedRuntimeError("Could not open the Windows audio output. Check the output device and driver. Details: {0}",
                            str(last_error)) from last_error


def trim_wav_edges(data: bytes) -> bytes:
    """Убираем только внешнюю тишину, оставляя запас для тихих согласных."""
    import numpy as np
    with wave.open(io.BytesIO(data), "rb") as source:
        rate = source.getframerate()
        if source.getnchannels() != 1 or source.getsampwidth() != 2:
            raise OwnedValueError("Speech playback requires mono PCM16 WAV audio.")
        audio = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2")
    if not len(audio):
        raise OwnedValueError("The speech engine returned empty WAV audio.")
    magnitude = np.abs(audio.astype(np.int32))
    active = np.flatnonzero(magnitude > max(8, int(magnitude.max()) * 0.002))
    if not len(active):
        return data
    start = max(0, int(active[0]) - rate * 80 // 1000)
    end = min(len(audio), int(active[-1]) + 1 + rate * 120 // 1000)
    result = io.BytesIO()
    with wave.open(result, "wb") as target:
        target.setnchannels(1); target.setsampwidth(2); target.setframerate(rate)
        target.writeframes(audio[start:end].tobytes())
    return result.getvalue()


def play_wav(data: bytes, stop: Event, pause_ms: int = 0) -> None:
    import numpy as np
    import sounddevice as sd
    with wave.open(io.BytesIO(data), "rb") as source:
        rate = source.getframerate()
        if source.getsampwidth() != 2 or source.getnchannels() != 1:
            raise OwnedValueError("Speech playback requires mono PCM16 WAV audio.")
        frames = source.getnframes()
        raw = source.readframes(frames)
        if not frames or len(raw) != frames * 2:
            raise OwnedValueError("The speech engine returned empty or truncated WAV audio.")
        audio = np.frombuffer(raw, dtype="<i2")
    if stop.is_set(): return
    if pause_ms:
        audio = np.concatenate((audio, np.zeros(rate * pause_ms // 1000, dtype=np.int16)))
    notify = getattr(stop, "on_state", lambda state: None)
    level = getattr(stop, "on_level", lambda value: None)
    level(0.0)
    stream, target_rate, channels = _open_output(sd, rate)
    try:
        if target_rate != rate:
            import numpy as np
            positions = np.arange(round(len(audio) * target_rate / rate)) * rate / target_rate
            audio = np.interp(positions, np.arange(len(audio)), audio).round().astype(np.int16)
            rate = target_rate
        if channels == 2:
            audio = np.column_stack((audio, audio))
        output = stream
        for start in range(0, len(audio), max(1, rate // 10)):
            paused = getattr(stop,"paused",None)
            if paused is not None and paused.is_set() and not stop.is_set():
                output.stop()
                level(0.0)
                notify("PAUSED")
                while paused.is_set() and not stop.is_set():
                    stop.wait(0.05)
                if not stop.is_set():
                    output.start()
            if stop.is_set():
                output.abort()
                return
            notify("SPEAKING")
            chunk=audio[start:start + max(1, rate // 10)]
            # RMS только воспроизводимого PCM, без микрофона и loopback-записи.
            normalized=chunk.astype(np.float32)/32768.0
            energy=float(np.sqrt(np.mean(normalized*normalized)))
            level(min(1.0,energy*6.0))
            output.write(chunk)
        output.stop()  # Дождаться последних сэмплов, не обрезая конец ответа.
    except sd.PortAudioError as error:
        raise OwnedRuntimeError("The Windows audio device disconnected during speech. Check the output and driver. Details: {0}",
                                str(error)) from error
    finally:
        level(0.0)
        try:
            stream.close()
        finally:
            notify("SYNTHESIZING")
