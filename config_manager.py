"""
설정 & 프로파일 저장/로드.

- settings.json : COM 포트, Baud, Tapo IP 등 연결 설정 + 마지막으로 사용한 프로파일 이름
- profiles/*.json : 블록 시퀀스(프로파일) 각각. 보드/프로젝트별로 여러 개 저장해두고
  드롭다운으로 전환한다.
- .env : TAPO_EMAIL / TAPO_PASSWORD / TAPO_IP (기존과 동일)
- 기존 config.json(구버전, commands/para1/script 방식)이 있으면 최초 실행 시
  자동으로 블록 프로파일로 변환해준다.
"""
from __future__ import annotations
import json
import os

import step_types as st

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(BASE_DIR, "settings.json")
PROFILES_DIR = os.path.join(BASE_DIR, "profiles")
LEGACY_CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
ENV_PATH = os.path.join(BASE_DIR, ".env")

os.makedirs(PROFILES_DIR, exist_ok=True)

DEFAULT_SETTINGS = {
    "com_port": "COM4",
    "baud_rate": "921600",
    "tapo_ip": "192.168.10.45",
    "last_profile": "default",
}


def load_env(path: str = ENV_PATH) -> dict:
    env = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    return env


def load_settings() -> dict:
    data = dict(DEFAULT_SETTINGS)
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, encoding="utf-8") as f:
                data.update(json.load(f))
        except Exception:  # noqa: BLE001
            pass
    else:
        env = load_env()
        if "TAPO_IP" in env:
            data["tapo_ip"] = env["TAPO_IP"]
    return data


def save_settings(settings: dict):
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


def _profile_path(name: str) -> str:
    safe = "".join(c for c in name if c.isalnum() or c in " _-()가-힣").strip() or "profile"
    return os.path.join(PROFILES_DIR, safe + ".json")


def list_profiles() -> list[str]:
    names = []
    for fn in sorted(os.listdir(PROFILES_DIR)):
        if fn.endswith(".json"):
            names.append(fn[:-5])
    return names


def load_profile(name: str) -> list[dict]:
    path = _profile_path(name)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("blocks", [])


def save_profile(name: str, blocks: list[dict]):
    path = _profile_path(name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"name": name, "blocks": blocks}, f, ensure_ascii=False, indent=2)


def delete_profile(name: str):
    path = _profile_path(name)
    if os.path.exists(path):
        os.remove(path)


def rename_profile(old: str, new: str):
    blocks = load_profile(old)
    save_profile(new, blocks)
    delete_profile(old)


def default_blocks() -> list[dict]:
    """새 사용자를 위한 기본 예시 시퀀스: power on -> 부팅 문자열 대기 ->
    엔터 입력(+응답 확인) -> power off, 이 전체를 무한 반복하는 Loop 로 감싼다."""
    loop_start, loop_end = st.make_loop_pair({
        "label": "Main Loop", "var_name": "i", "start": "0", "op": "<", "end": "100",
        "step": "1", "infinite": True,
    })
    return [
        st.make_block(st.POWER, {"state": "off", "delay_after": 3.0}),
        loop_start,
        st.make_block(st.POWER, {"state": "on", "delay_after": 2.0}),
        st.make_block(st.WAIT_STRING, {
            "pattern": "bcfg: cmn nv load complete", "regex": False,
            "timeout": 40.0, "on_timeout": "stop"}),
        st.make_block(st.SEND, {"text": "", "append_enter": True, "delay_after": 0.5,
                                 "check": {"pattern": "TEST_DONE", "regex": False,
                                           "timeout": 300.0, "on_timeout": "continue"}}),
        st.make_block(st.POWER, {"state": "off", "delay_after": 1.0}),
        loop_end,
    ]


def migrate_legacy_if_needed():
    """구버전 config.json(commands/para1/script) 을 찾으면 'legacy' 프로파일로 변환한다."""
    if "legacy" in list_profiles():
        return
    if not os.path.exists(LEGACY_CONFIG_PATH):
        return
    try:
        with open(LEGACY_CONFIG_PATH, encoding="utf-8") as f:
            old = json.load(f)
    except Exception:  # noqa: BLE001
        return

    blocks = [st.make_block(st.POWER, {"state": "off", "delay_after": 3.0})]
    para1 = old.get("para1", {})
    use_sweep = bool(para1.get("enabled")) and any(
        "{para1}" in c for c in old.get("commands", []))

    inner: list[dict] = []
    inner.append(st.make_block(st.POWER, {"state": "on", "delay_after": 2.0}))
    inner.append(st.make_block(st.WAIT_STRING, {
        "pattern": "bcfg: cmn nv load complete", "regex": False,
        "timeout": 40.0, "on_timeout": "stop"}))
    inner.append(st.make_block(st.SEND, {"text": "", "append_enter": True, "delay_after": 0.3, "check": None}))
    script = old.get("script", "")
    if script.strip():
        inner.append(st.make_block(st.UPLOAD_SCRIPT, {"script": script, "target_path": "/tmp/runtest.sh",
                                                        "source_path": ""}))
    for cmd in old.get("commands", []):
        inner.append(st.make_block(st.SEND, {
            "text": cmd, "append_enter": True, "delay_after": 0.0,
            "check": {"pattern": "#|TEST_DONE", "regex": True, "timeout": 300.0, "on_timeout": "continue"}}))
    inner.append(st.make_block(st.POWER, {"state": "off", "delay_after": 1.0}))

    if use_sweep:
        step_sign = st.parse_num(para1.get("step", "-0x10") or "-1")
        loop_params = {"label": "Legacy Loop", "var_name": "para1",
                       "start": para1.get("start", "0x1F0"),
                       "op": ">=" if step_sign < 0 else "<=",
                       "end": para1.get("end", "0x100"),
                       "step": para1.get("step", "-0x10"), "infinite": False}
        loop_start, loop_end = st.make_loop_pair(loop_params)
        blocks += [loop_start] + inner + [loop_end]
    else:
        blocks += inner
    blocks.append(st.make_block(st.POWER, {"state": "off", "delay_after": 1.0}))
    blocks.append(loop_end)
    save_profile("legacy", blocks)
