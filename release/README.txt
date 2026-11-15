==============================================================
 SpriteGrid Editor v0.4.0 — 셀 편집 · 일괄 변환 (Cell Editor · Batch Convert)
 2026-11-15 · 만든이 재와니
 https://github.com/JEWANI/SpriteGridEditor
==============================================================

[한국어]

도트 스프라이트 시트를 셀 단위로 복사·반전·붙여넣기·지우기 하고,
컬러 옷 시트를 "회색 원본 + 인덱스 마스크(_M)"로 바꿔 Unity에 바로 넣을 PNG로 내보내는 툴입니다.
모든 편집·확대·저장은 최근접 이웃(Nearest)만 써서 도트가 뭉개지지 않습니다.

■ 실행
  1. 압축을 원하는 폴더에 풉니다 (설치 불필요, Windows 10/11 64bit).
  2. "SpriteGridEditor v0.4.0.exe"를 실행합니다.
     - 서명되지 않은 실행파일이라 Windows SmartScreen 경고가 뜨면 [추가 정보] → [실행].
  3. 처음 실행하면 exe가 있는 폴더에 config.json(설정) · output\(기본 저장 폴더) · autosave\(자동 저장)가 생깁니다.
     → 쓰기 권한이 있는 폴더(예: 바탕화면, 문서)에 두세요. Program Files 안은 권장하지 않습니다.

■ 빠른 시작
  1. Ctrl+O로 PNG를 열거나 창에 끌어다 놓기 → 시트 규격(H1 걷기 3×4 / H2 전투 6×5)이 자동 선택
  2. 클릭 = Source 셀, Shift/Ctrl+클릭 = Target 셀
  3. Ctrl+M(미러 & 적용), Ctrl+C → Ctrl+V 등으로 편집, Ctrl+Z 실행 취소
  4. Ctrl+S 저장 (마스크가 있으면 <이름>_M.png도 함께 저장)
  자세한 사용법: 프로그램 안 [도움말 → 사용법](F1) 또는 동봉된 HELP_ko.md

■ 동봉 파일
  SpriteGridEditor v0.4.0.exe  실행파일
  README.txt                    이 파일 (빠른 시작)
  CHANGELOG.md                  버전별 업데이트 내역
  HELP_ko.md / HELP_en.md       사용법 (한국어 / English)
  LICENSE                       MIT 라이선스 (PySide6 LGPL-3.0 · Pillow · NumPy 포함)

■ 업데이트
  이전 버전 폴더의 config.json을 새 버전 exe 옆에 복사하면 설정(저장 폴더·최근 파일·프리셋 등)이 이어집니다.
  .md 파일은 메모장으로도 열리지만 VS Code·Obsidian·GitHub에서 보면 표가 보기 좋게 나옵니다.

■ v0.4.0 주요 변경
  - 셀 편집 창(Ctrl+E / 셀 더블클릭): 도트 칠하기 B · 지우기 E · 스포이트 I, Alt 누르는 동안 임시 스포이트, 적용 = 실행 취소 한 단계
  - 영역 이동(S): 셀 경계와 상관없이 사각형을 선택해 끌어 옮기기 (방향키 1px / Shift 5px)
  - 폴더 일괄 변환(Ctrl+Shift+B): 폴더의 컬러 시트를 한 번에 회색 + 마스크로, 폴더의 다음/이전 파일(Ctrl+PgDn/PgUp)
  - 파일명 검증 강화, Unity .meta 복제(같은 폴더 시트의 임포트 설정 유지), 단축키 목록(Ctrl+/)
  전체 내역은 CHANGELOG.md 참고.


[English]

A tool for copying, mirroring, pasting and clearing pixel-art sprite sheets cell by cell,
and for converting colored outfit sheets into "grayscale + index mask (_M)" PNGs ready for Unity.
All editing, zooming and saving use nearest-neighbor only, so pixels never blur.

■ Run
  1. Extract the zip anywhere (no install, Windows 10/11 64-bit).
  2. Run "SpriteGridEditor v0.4.0.exe".
     - The exe is not code-signed; if SmartScreen warns, click [More info] → [Run anyway].
  3. config.json, output\ and autosave\ are created next to the exe — keep it in a writable folder.

■ Quick start
  Ctrl+O (or drag & drop) to open → click = Source cell, Shift/Ctrl+click = Target cell
  → Ctrl+M / Ctrl+C → Ctrl+V to edit → Ctrl+S to save.
  Full guide: Help → Usage (F1) in the app, or HELP_en.md. Language: Settings → General → English.

■ Files
  SpriteGridEditor v0.4.0.exe / README.txt / CHANGELOG.md (release notes) / HELP_ko.md, HELP_en.md (user guide) / LICENSE (MIT)
