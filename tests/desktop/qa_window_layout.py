"""Настоящий скрытый Tauri WebView2; управление исключительно через CDP API."""

import os, subprocess, time, json
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright, Error as PlaywrightError

root = Path(__file__).resolve().parents[2]
import ctypes
from ctypes import wintypes

ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
fixture = root / ".cache/qa-workspace"
fixture.mkdir(parents=True, exist_ok=True)
(fixture / "hello.py").write_text("print(27)\n", encoding="utf-8")
env = {
    **os.environ,
    "JARVIS_ROOT": str(root),
    "JARVIS_DESKTOP_TEST": "1",
    "WEBVIEW2_USER_DATA_FOLDER": str(root / ".cache/desktop-test/webview2"),
    "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS": "--remote-debugging-port=9337 --remote-debugging-address=127.0.0.1",
}
log = (root / ".cache/tauri-native-test.log").open("w", encoding="utf-8")
process = subprocess.Popen(
    [
        str(
            root
            / "desktop-bin"
            / json.loads(
                (root / "desktop-bin/manifest.json").read_text(encoding="utf-8-sig")
            ).get("file", "jarvis-desktop.exe")
        )
    ],
    cwd=root,
    env=env,
    stdout=log,
    stderr=log,
    creationflags=0x08000000,
)

user = ctypes.WinDLL("user32", use_last_error=True)
user.SetWindowPos.argtypes = [
    wintypes.HWND,
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_uint,
]
user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user.IsWindowVisible.argtypes = [wintypes.HWND]


class Monitor(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD),
        ("bounds", wintypes.RECT),
        ("work", wintypes.RECT),
        ("flags", wintypes.DWORD),
    ]


monitors = []


@ctypes.WINFUNCTYPE(
    wintypes.BOOL,
    wintypes.HMONITOR,
    wintypes.HDC,
    ctypes.POINTER(wintypes.RECT),
    wintypes.LPARAM,
)
def monitor_callback(handle, dc, rect, param):
    info = Monitor()
    info.size = ctypes.sizeof(info)
    user.GetMonitorInfoW(handle, ctypes.byref(info))
    monitors.append(
        [info.bounds.left, info.bounds.top, info.bounds.right, info.bounds.bottom]
    )
    return True


user.EnumDisplayMonitors(None, None, monitor_callback, 0)
handles = []


@ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
def window_callback(hwnd, param):
    pid = wintypes.DWORD()
    user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    title = ctypes.create_unicode_buffer(300)
    user.GetWindowTextW(hwnd, title, 300)
    if pid.value == process.pid and title.value == "Jarvis — голосовой помощник":
        handles.append(hwnd)
    return True


def check_layout(page):
    return page.evaluate(
        """() => {const frame=document.querySelector('iframe[src="workbench.html"]');const input=frame.contentDocument.querySelector('[data-shared-composer]').getBoundingClientRect();const offset=frame.getBoundingClientRect().top;const shell=document.querySelector('.shell').getBoundingClientRect();const footer=document.querySelector('.footer').getBoundingClientRect();return {width:innerWidth,height:innerHeight,shellWidth:shell.width,inputBottom:input.bottom+offset,footerBottom:footer.bottom,overflow:document.documentElement.scrollWidth>innerWidth}}"""
    )


try:
    for _ in range(100):
        if process.poll() is not None:
            raise AssertionError("Native app exited early")
        try:
            if (
                httpx.get("http://127.0.0.1:9337/json/version", timeout=0.4).status_code
                == 200
            ):
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9337")
        context = browser.contexts[0]
        page = next(page for page in context.pages if "tauri.localhost" in page.url)
        page.frame_locator('iframe[src="workbench.html"]').locator('[data-shared-composer] textarea').wait_for(timeout=30000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        user.EnumWindows(window_callback, 0)
        assert len(handles) == 1, handles
        hwnd = handles[0]
        assert not user.IsWindowVisible(hwnd), "QA window must stay hidden"
        results = []
        for left, top, right, bottom in monitors:
            # Только наше скрытое окно, без активации и изменения Z-order.
            assert user.SetWindowPos(hwnd, None, left + 40, top + 40, 1000, 700, 0x14)
            page.wait_for_timeout(200)
            page.get_by_role("button", name="Full Screen", exact=True).click()
            page.get_by_role(
                "button", name="Exit Full Screen", exact=True
            ).wait_for()
            page.wait_for_timeout(300)
            rect = wintypes.RECT()
            user.GetWindowRect(hwnd, ctypes.byref(rect))
            actual = [rect.left, rect.top, rect.right, rect.bottom]
            assert actual == [left, top, right, bottom], (
                actual,
                [left, top, right, bottom],
            )
            assert not user.IsWindowVisible(
                hwnd
            ), "Fullscreen must not show hidden QA window"
            layout = check_layout(page)
            assert not layout["overflow"], layout
            assert abs(layout["shellWidth"] - layout["width"]) <= 1, layout
            assert (
                layout["inputBottom"] <= layout["height"]
                and layout["footerBottom"] <= layout["height"]
            ), layout
            page.screenshot(
                path=str(root / ".cache" / ("fullscreen-" + str(right - left) + ".png"))
            )
            page.keyboard.press("Escape")
            page.get_by_role(
                "button", name="Full Screen", exact=True
            ).wait_for()
            page.wait_for_timeout(200)
            user.GetWindowRect(hwnd, ctypes.byref(rect))
            assert (
                abs(rect.left - (left + 40)) <= 1 and abs(rect.top - (top + 40)) <= 1
            ), (rect.left, rect.top)
            results.append(
                {
                    "monitor": [left, top, right, bottom],
                    "fullscreen": layout,
                    "restore": "PASS",
                }
            )
        for _ in range(3):
            page.get_by_role("button", name="Compact Window", exact=True).click()
            for attempt in range(100):
                orbs = [item for item in context.pages if "orb=1" in item.url]
                if orbs:
                    break
                page.wait_for_timeout(50)
            orb = orbs[0]
            orb.get_by_role("button", name="Close Compact Window").wait_for(
                timeout=15000
            )
            orb.get_by_text("JARVIS", exact=True).wait_for()
            orb.get_by_role("button", name="Close Compact Window").click()
            page.frame_locator('iframe[src="workbench.html"]').locator('[data-shared-composer] textarea').fill("проверка")
            assert process.poll() is None, "Closing orb killed main window"
        # Масштабы эмулируются в CDP; Windows DPI и пользовательские настройки не меняются.
        cdp = context.new_cdp_session(page)
        for width, height in [(2560, 1440), (1920, 1080)]:
            for scale in [1, 1.25, 1.5, 2]:
                cssw, cssh = round(width / scale), round(height / scale)
                cdp.send(
                    "Emulation.setDeviceMetricsOverride",
                    {
                        "width": cssw,
                        "height": cssh,
                        "deviceScaleFactor": scale,
                        "mobile": False,
                    },
                )
                page.wait_for_timeout(100)
                layout = check_layout(page)
                assert (
                    not layout["overflow"] and abs(layout["shellWidth"] - cssw) <= 1
                ), layout
                assert (
                    layout["inputBottom"] <= cssh and layout["footerBottom"] <= cssh
                ), layout
                results.append(
                    {
                        "resolution": [width, height],
                        "emulatedScale": scale,
                        "layout": layout,
                    }
                )
        cdp.send("Emulation.clearDeviceMetricsOverride")
        assert not errors, errors
        (root / ".cache/window-layout-result.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            "PASS: hidden native fullscreen + Escape restore on both physical monitors; 3 compact open/close cycles; 8 resolution/scale combinations; no page errors."
        )
        try:
            page.evaluate("window.__TAURI_INTERNALS__.invoke('plugin:window|close')")
        except PlaywrightError:
            pass
        browser.close()
    process.wait(timeout=20)
    assert process.returncode == 0, process.returncode
finally:
    if process.poll() is None:
        process.terminate()
        process.wait(timeout=15)
    log.close()
