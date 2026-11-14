"""Telegram/Bluetooth подключаются к той же очереди, инструментам и Vosk.

Сервисы стартуют только при явной настройке. Прослушивания TCP-порта нет.
"""
from __future__ import annotations
import asyncio
from concurrent.futures import Future
import os
import threading
from typing import Any, Callable
from local_mode.gemini import safe_error
from local_mode.results import user_message


class RemoteServices:
    def __init__(self, ui: Any, submit: Callable[..., Future], stop: threading.Event,
                 report: Callable[[str], None], bluetooth: bool = False) -> None:
        self.ui = ui
        self.submit_remote = submit
        self.stop_event = stop
        self.report = report
        self.bluetooth = bluetooth or os.environ.get("JARVIS_BLUETOOTH") == "1"
        self._loop: asyncio.AbstractEventLoop | None = None
        self._bridge = None
        self.threads: list[threading.Thread] = []

    def start(self) -> None:
        import telegram_bot
        cfg = telegram_bot.load_config()
        token = os.environ.get("JARVIS_TELEGRAM_TOKEN") or cfg.get("telegram_bot_token")
        chat = os.environ.get("JARVIS_TELEGRAM_CHAT_ID") or cfg.get("telegram_chat_id")
        if token and chat:
            thread = threading.Thread(target=self._telegram, args=(token, str(chat)), name="Jarvis-Telegram", daemon=True)
            thread.start()
            self.threads.append(thread)
        if self.bluetooth:
            thread = threading.Thread(target=lambda: asyncio.run(self._bluetooth()), name="Jarvis-Bluetooth", daemon=True)
            thread.start()
            self.threads.append(thread)

    def allowed(self, message: dict, owner: str) -> bool:
        # Fail closed: только личный чат владельца. Группа не является владельцем.
        return (str(message.get("chat", {}).get("id")) == owner
                and str(message.get("from", {}).get("id")) == owner)

    def _telegram(self, token: str, owner: str) -> None:
        import telegram_bot as tg
        offset = 0
        # Не исполняем старые команды из backlog после перезапуска.
        backlog = tg.get_updates(token, -1, timeout=0)
        if backlog:
            offset = max(int(u["update_id"]) for u in backlog) + 1
        while not self.stop_event.is_set():
            try:
                for update in tg.get_updates(token, offset, timeout=2):
                    offset = max(offset, int(update["update_id"]) + 1)
                    message = update.get("message", {})
                    if not self.allowed(message, owner):
                        continue
                    text = str(message.get("text", "")).strip()
                    voice = message.get("voice")
                    if voice:
                        if voice.get("duration", 0) > 180 or voice.get("file_size", 0) > 20 * 1024 * 1024:
                            tg.send_message(token, owner, "Голос: максимум 3 минуты / 20 МиБ.", parse_mode="")
                            continue
                        audio = tg.download_file(token, voice["file_id"])
                        text = tg.transcribe_voice("", audio) if audio else ""
                    if not text:
                        continue
                    future = self.submit_remote(text, source="telegram:" + owner)
                    while not future.done() and not self.stop_event.wait(0.2):
                        pass
                    if self.stop_event.is_set():
                        return
                    result = future.result()
                    reply = user_message(result)
                    tg.send_message(token, owner, reply[:12000], parse_mode="")
            except Exception as error:
                self.report("Telegram: " + safe_error(error))
                self.stop_event.wait(5)

    async def _bluetooth(self) -> None:
        from remote.bluetooth_transport import BluetoothBridge
        self._loop = asyncio.get_running_loop()
        self._bridge = BluetoothBridge(self)
        try:
            if not await self._bridge.start():
                self.report("Bluetooth недоступен: проверьте поддержку BLE peripheral в адаптере Windows.")
                return
            while not self.stop_event.is_set():
                await asyncio.sleep(0.2)
        except Exception as error:
            self.report("Bluetooth: " + safe_error(error))
        finally:
            await self._bridge.stop()

    def close(self) -> None:
        for thread in self.threads:
            thread.join(timeout=3)
