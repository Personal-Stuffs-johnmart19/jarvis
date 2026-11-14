"""Явный live-тест: запускайте только когда готовы к изменению окна и музыке."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import time
from local_mode.assistant import LocalAgent
from local_mode.config import ROOT, Settings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--window-title', required=True)
    parser.add_argument('--allow-actions', action='store_true')
    args = parser.parse_args()
    if not args.allow_actions:
        print('Действий нет. Для проверки добавьте --allow-actions. Сначала восстановите тестовое окно из развёрнутого состояния.')
        return 2
    bot = LocalAgent(Settings(window_title=args.window_title))
    report: dict[str, object] = {}
    try:
        report['maximize'] = bot.run('разверни окно')
        report['youtube'] = bot.run('включи музыку на YouTube')
        time.sleep(2)
        report['youtube_after_2s'] = bot.youtube.state()
        report['pause'] = bot.youtube.pause()
        ok = bool(report['maximize'].get('verified') and report['maximize'].get('changed') and
                  report['youtube'].get('verified') and report['youtube_after_2s'].get('current_time', 0) >
                  report['youtube'].get('current_time', 0))
        report['passed'] = ok
    except Exception as error:
        report['passed'] = False
        report['error'] = str(error)
    finally:
        bot.close()
    target = ROOT / 'data' / 'qa' / 'live-report.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get('passed') else 1


if __name__ == '__main__':
    raise SystemExit(main())
