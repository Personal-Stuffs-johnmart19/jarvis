"""YouTube через Playwright: выбор видео и проверка настоящего воспроизведения."""
from __future__ import annotations
from pathlib import Path
import re
import threading
import time
from urllib.parse import quote_plus
from typing import Any


class YouTubePlayer:
    """Личный профиль для ссылок, отдельный — для проверяемого управления.

    Sync Playwright принадлежит одному рабочему потоку. Браузер остаётся открытым
    до выхода из Jarvis; повтор команды использует ту же вкладку.
    """

    def __init__(self, profile: Path, channel: str = "chrome", stop: threading.Event | None = None) -> None:
        self.profile = profile
        self.channel = channel
        self.stop = stop or threading.Event()
        self.runtime: Any = None
        self.context: Any = None
        self.page: Any = None
        self.owner: int | None = None
        self.browser: Any = None

    def _check_cancelled(self) -> None:
        if self.stop.is_set():
            raise RuntimeError("Действие отменено пользователем.")

    def _ensure(self) -> None:
        self._check_cancelled()
        if self.owner is not None and self.owner != threading.get_ident():
            raise RuntimeError("Браузер должен управляться одним рабочим потоком.")
        if self.page is not None and not self.page.is_closed():
            return
        from playwright.sync_api import sync_playwright
        self.owner = threading.get_ident()
        self.profile.mkdir(parents=True, exist_ok=True)
        if self.runtime is None:
            self.runtime = sync_playwright().start()
        if self.context is None:
            from local_mode.browser_profiles import managed_endpoint
            self.browser = self.runtime.chromium.connect_over_cdp(managed_endpoint(self.channel))
            self.context = self.browser.contexts[0]
        self.page = self.context.new_page()
        self.page.set_default_timeout(15000)

    def _cookie_consent(self) -> None:
        # Никаких CAPTCHA/логина: обрабатываем только обычный выбор cookies.
        for label in (r"Reject all", r"Отклонить все", r"Відхилити всі"):
            button = self.page.get_by_role("button", name=re.compile(label, re.I)).first
            if button.count() and button.is_visible():
                button.click()
                self.page.wait_for_load_state("domcontentloaded")
                return

    def state(self) -> dict[str, object]:
        if self.page is None or self.page.is_closed():
            return {"ok": False, "reason": "Вкладка закрыта."}
        state: dict[str, object] = self.page.evaluate("""() => {
            const v = document.querySelector('video');
            const p = document.querySelector('#movie_player');
            return {url: location.href, title: document.title, video: !!v,
                current_time: v?.currentTime ?? 0, paused: v?.paused ?? true,
                muted: v?.muted ?? true, volume: v?.volume ?? 0,
                ready_state: v?.readyState ?? 0, ended: v?.ended ?? false,
                ad_showing: p?.classList.contains('ad-showing') ?? false,
                error: v?.error?.message ?? null};
        }""")
        return state

    def play(self, query: str) -> dict[str, object]:
        if not isinstance(query, str) or not query.strip() or len(query) > 200:
            raise ValueError("Нужен поисковый запрос YouTube длиной до 200 символов.")
        from local_mode.browser_profiles import read_preferences, open_url
        if read_preferences()["mode"] != "new":
            self._check_cancelled()
            url = "https://www.youtube.com/results?search_query=" + quote_plus(query)
            # Поиск первого обычного видео идёт без доступа к личным cookies.
            try:
                import requests
                response = requests.get(url, timeout=12, headers={"User-Agent": "Mozilla/5.0"})
                response.raise_for_status()
                match = re.search(r'"videoRenderer"\s*:\s*\{\s*"videoId"\s*:\s*"([A-Za-z0-9_-]{11})"', response.text)
                if match:
                    url = "https://www.youtube.com/watch?v=" + match.group(1) + "&autoplay=1"
            except requests.RequestException:
                pass
            self._check_cancelled()
            open_url(url, self.channel)
            return {"ok": True, "action": "youtube_play", "verified": False, "url": url,
                    "reply": "YouTube открыт в выбранном профиле. Автозапуск зависит от настроек браузера; воспроизведение не подтверждено."}
        self._ensure()
        self.page.goto("https://www.youtube.com/results?search_query=" + quote_plus(query),
                       wait_until="domcontentloaded", timeout=45000)
        self._check_cancelled()
        self._cookie_consent()
        # Выбираем обычное видео, а не рекламный блок/Shorts. Никаких координат наугад.
        videos = self.page.locator('ytd-video-renderer a#video-title, yt-lockup-view-model a[href^="/watch"]')
        videos.first.wait_for(state="visible", timeout=30000)
        self._check_cancelled()
        videos.first.click()
        self.page.wait_for_url(re.compile(r"https://www\.youtube\.com/watch\?"), timeout=30000)
        self.page.locator("video").first.wait_for(state="attached", timeout=30000)
        deadline = time.monotonic() + 65
        initial: float | None = None
        while time.monotonic() < deadline:
            self._check_cancelled()
            state = self.state()
            if state.get("error"):
                raise RuntimeError(f"YouTube сообщил ошибку плеера: {state['error']}")
            if state.get("ad_showing"):
                initial = None
                skip = self.page.locator('.ytp-skip-ad-button, .ytp-ad-skip-button-modern').first
                if skip.count() and skip.is_visible():
                    skip.click()
            elif state.get("video") and state.get("ready_state", 0) >= 2:
                if state["paused"]:
                    play = self.page.locator(".ytp-play-button").first
                    if play.count() and play.is_visible():
                        play.click()
                    initial = None
                elif state["muted"] or state["volume"] == 0:
                    # Пользователь прямо попросил включить музыку; задаём умеренную громкость.
                    self.page.locator("video").first.evaluate("v => { v.muted = false; v.volume = 0.25; }")
                    initial = None
                elif not state["ended"]:
                    current = float(state["current_time"])
                    if initial is None:
                        initial = current
                    elif current - initial >= 0.5:
                        return {"ok": True, "action": "youtube_play", "verified": True,
                                "query": query, "progress_seconds": round(current - initial, 2), **state}
            self.page.wait_for_timeout(500)
        raise RuntimeError("YouTube не подтвердил воспроизведение музыки. Возможны реклама, блокировка или запрос входа. Проверьте открытую вкладку.")

    def pause(self) -> dict[str, object]:
        from local_mode.browser_profiles import read_preferences
        if read_preferences()["mode"] != "new":
            raise ValueError("Для проверяемого управления YouTube выберите отдельный профиль Jarvis в меню «Браузер и профиль».")
        if self.page is None or self.page.is_closed():
            raise ValueError("В этом сеансе YouTube ещё не запущен.")
        self.page.locator("video").first.evaluate("v => v.pause()")
        state = self.state()
        return {"ok": bool(state.get("paused")), "action": "youtube_pause", "verified": bool(state.get("paused")), **state}

    def close(self) -> None:
        try:
            if self.page is not None and not self.page.is_closed():
                self.page.close()
        finally:
            self.context = self.page = self.browser = None
            if self.runtime is not None:
                self.runtime.stop()
            self.runtime = None
            self.owner = None
