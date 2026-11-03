"""gct-board-tester 공통 모듈 (docs/FEATURES.md "소스 구조")

  gbt.rig     보드 정보(rig.json) + 사용 중 관리(lock)   - 모든 도구가 이걸 통해서만 보드를 씀
  gbt.board   보드 한 대 제어: Tapo 전원, UART, fastboot 진입, 부팅 로그 읽기
  gbt.images  이미지 등록 표(config/images.json) + 빌드
"""
