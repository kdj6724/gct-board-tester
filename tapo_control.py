"""
Tapo P115 스마트 플러그 전원 제어.
기존 auto_script.py 의 단일 이벤트 루프 방식을 그대로 유지한다.
"""
from __future__ import annotations
import asyncio
import threading
import time

try:
    from tapo import ApiClient
except Exception:  # pragma: no cover - tapo 미설치 환경에서도 import 는 되게
    ApiClient = None


class TapoController:
    def __init__(self):
        self._loop = asyncio.new_event_loop()
        self._device = None
        self._creds = None  # (ip, email, password) - 재연결용으로 기억해둠(set_power 재시도 때 씀)
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _run_async(self, coro, timeout=15):
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout=timeout)

    def connect(self, ip: str, email: str, password: str, _retries: int = 2):
        """맨 처음 붙을 때(앱 켤 때, 또는 "재연결" 버튼) 쓰는 함수 - 얘도 한 번
        실패했다고 바로 포기하지 않고 몇 번 더 시도해본다. "connect 자체가 안 되는
        경우가 많다"는 요청으로 추가함 - 원래는 딱 한 번만 시도하고 실패하면 앱을
        다시 켜기 전까진 재시도할 방법이 아예 없었다(수동 재연결 버튼도 없었음)."""
        if ApiClient is None:
            return False, "tapo 패키지가 설치되어 있지 않습니다"

        async def _do():
            client = ApiClient(email, password)
            self._device = await client.p115(ip)

        last_err = ""
        for attempt in range(_retries + 1):
            try:
                self._run_async(_do())
                self._creds = (ip, email, password)
                suffix = f" ({attempt}회 재시도 후 성공)" if attempt > 0 else ""
                return True, "Connected OK" + suffix
            except Exception as e:  # noqa: BLE001
                last_err = str(e)
                if attempt < _retries:
                    time.sleep(1.0)
        return False, f"{_retries + 1}번 시도 모두 실패: {last_err}"

    def _reconnect(self) -> bool:
        """Tapo 플러그와의 세션이 끊기거나(오래 켜두면 종종 있음) 네트워크가 잠깐
        흔들려서 on()/off() 가 실패하는 경우, set_power() 재시도 전에 이걸로 다시
        붙여본다. connect() 를 처음 부를 때 저장해둔 접속 정보를 그대로 재사용한다.
        여긴 딱 한 번만 시도한다(_retries=0) - set_power() 자신의 재시도 루프가
        이미 몇 번 돌면서 매번 이걸 부르므로, 여기서까지 또 여러 번 재시도하면
        전체 대기 시간이 너무 길어진다."""
        if not self._creds:
            return False
        ip, email, password = self._creds
        ok, _ = self.connect(ip, email, password, _retries=0)
        return ok

    def set_power(self, state: bool, _retries: int = 2):
        """가끔 Tapo 플러그 on()/off() 가 실패하는 문제(세션 끊김/네트워크 순단 등으로
        종종 발생한다는 요청으로 추가) - 한 번 실패했다고 바로 포기해서 테스트 전체를
        중단시키지 않고, 재연결 후 재시도를 몇 번 더 해본 다음에도 안 되면 그때
        실패로 보고한다. 재시도 끝에 성공하면 메시지에 몇 번째에 성공했는지 남긴다."""
        if not self._device:
            return False, "Not connected"

        async def _do():
            if state:
                await self._device.on()
            else:
                await self._device.off()

        last_err = ""
        for attempt in range(_retries + 1):
            try:
                self._run_async(_do())
                suffix = f" ({attempt}회 재시도 후 성공)" if attempt > 0 else ""
                return True, ("on" if state else "off") + suffix
            except Exception as e:  # noqa: BLE001
                last_err = str(e)
                if attempt < _retries:
                    time.sleep(1.0)
                    self._reconnect()  # 세션이 끊겼을 수 있으니 다시 붙여보고 재시도
        return False, f"{_retries + 1}번 시도 모두 실패: {last_err}"
