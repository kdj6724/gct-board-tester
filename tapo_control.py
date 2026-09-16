"""
Tapo P115 스마트 플러그 전원 제어.
기존 auto_script.py 의 단일 이벤트 루프 방식을 그대로 유지한다.
"""
from __future__ import annotations
import asyncio
import threading

try:
    from tapo import ApiClient
except Exception:  # pragma: no cover - tapo 미설치 환경에서도 import 는 되게
    ApiClient = None


class TapoController:
    def __init__(self):
        self._loop = asyncio.new_event_loop()
        self._device = None
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _run_async(self, coro, timeout=15):
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout=timeout)

    def connect(self, ip: str, email: str, password: str):
        if ApiClient is None:
            return False, "tapo 패키지가 설치되어 있지 않습니다"

        async def _do():
            client = ApiClient(email, password)
            self._device = await client.p115(ip)

        try:
            self._run_async(_do())
            return True, "Connected OK"
        except Exception as e:  # noqa: BLE001
            return False, str(e)

    def set_power(self, state: bool):
        if not self._device:
            return False, "Not connected"

        async def _do():
            if state:
                await self._device.on()
            else:
                await self._device.off()

        try:
            self._run_async(_do())
            return True, "on" if state else "off"
        except Exception as e:  # noqa: BLE001
            return False, str(e)
