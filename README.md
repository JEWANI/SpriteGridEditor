# 🟦 SpriteGrid Editor

> 도트 스프라이트 시트를 **셀 단위로 복사·반전·붙여넣기·지우기** 하고, (2단계부터) 컬러 옷 시트를
> **회색 원본 + 인덱스 마스크(`_M`)** 로 변환해 Unity 에 바로 넣을 PNG 로 내보내는 독립 실행형 데스크톱 툴.
> 모든 편집·확대·저장은 최근접 이웃(Nearest)만 사용해 도트가 뭉개지지 않는다.

기획서: 볼트 `에디터 툴 제작\EDT-ETC.6 스프라이트 시트 편집기 SpriteGrid Editor 기획서 v1.1.md`
스택: Python 3 + PySide6(Qt) + Pillow + NumPy

![SpriteGrid Editor 메인 화면 (H1 걷기 시트, Base Body 참조)](docs/images/main_ko.png)

## 📌 주요 기능 (V0.1.0 · 1단계)

### 🖼 캔버스
- 줌 100~800% (정수 배율, **보간 없음**), 휠 클릭 드래그 / Space+드래그 이동, 그리드 오버레이(G)
- 셀 선택: 클릭 = Source, Shift/Ctrl+클릭 = Target, 드래그 = 다중 셀 범위
- 배경 레이어: 투명 체크무늬 / **Base Body 오버레이** / 흰색 / 검은색 / 도트 격자 (B 순환)

### ✂ 셀 편집
- 복사 / 잘라내기 / 붙여넣기 / **미러 붙여넣기** / 상하 반전 / **미러 & 적용** / 셀 지우기
- 임의 셀 ↔ 임의 셀, 한 행 전체 같은 다중 셀 한 번에 처리 (미러는 셀마다 반전)
- Undo / Redo 100단계

### 📐 시트 규격 · 체형
- H1 걷기 3열×4행(셀 100×135), H2 전투 6열×5행(셀 256×256), 임의 설정, 사용자 프리셋, 이미지 크기로 자동 선택
- 체형 여(F)/남(M)/공용 — Base Body 자동 매칭, 파일명 접미사 검증, 다른 체형 붙여넣기 경고, 파일명 도우미

### 💾 저장
- PNG(RGBA) 전용, 알파 임계값(기본 128), 투명 픽셀 RGB (0,0,0), 덮어쓰기 시 `_backup.png` 보관
- 저장 폴더 지정 + 최근 폴더 10개, 기본은 프로그램 폴더 아래 `output/`
- 자동 저장(10분 · 2개) + 비정상 종료 후 복구

### 🌐 앱 공통
- 제목줄 `SpriteGrid Editor v0.1.0 - 파일명 *`, 도움말(사용법 F1 / 변경 내역 / 정보), 한국어·English, 다크/라이트

---

## 🚀 실행 방법

### 소스로 실행
```bash
pip install -r requirements.txt
python main.py
```

### 실행파일로 빌드 (PyInstaller)
```bash
pip install pyinstaller
```
`실행파일 만들기.bat` 더블클릭 → `dist/SpriteGridEditor v0.1.0.exe` 생성 (버전은 `app_info.py` 에서 읽는다).
`locales/`, `HELP_*.md`, `CHANGELOG.md`, `icon.ico` 는 exe 안에 포함되고,
`config.json` · `autosave/` · `output/` 은 **exe 가 있는 폴더**에 만들어진다.

### 테스트
```bash
python -m unittest discover tests -v
```
(cell_ops · history · export_utils · 자동 저장 · i18n 단위 테스트 + 오프스크린 UI 스모크 테스트)

---

## 📁 파일 구성

```
SpriteGridEditor/
├── main.py                # 진입점 (QApplication, MainWindow)
├── main_window.py         # 메뉴/툴바/좌우 패널/상태바, 열기·저장·편집·자동 저장 흐름
├── app_info.py            # 이름·버전·코드네임·만든이·링크 상수 (단일 소스)
├── config_manager.py      # config.json (언어·테마·저장 폴더·최근 목록·프리셋·자동 저장) + base_dir/resource_path
├── i18n.py                # tr(), 언어 로드/전환, 폴백(en → ko → 키)
├── locales/ko.json, en.json
├── theme.py               # 다크/라이트 QSS + 캔버스 색
├── canvas_widget.py       # 줌/팬/그리드/셀 선택/배경 레이어 (Nearest 렌더)
├── sheet_model.py         # 시트 RGBA 배열(NumPy), 그리드/셀 영역, 시트 규격 프리셋, 체형 인식
├── cell_ops.py            # 복사·붙여넣기·미러·클리어 (순수 함수)
├── history.py             # Undo/Redo (영역 스냅샷)
├── export_utils.py        # 알파 임계값, PNG 저장, 백업, 저장 폴더 결정, 파일명·시트 검증
├── autosave.py            # 자동 저장·크래시 복구 (autosave/)
├── reference_layers.py    # Base Body 탐색
├── preset_dialog.py       # 시트 규격 임의 설정 / 프리셋 관리
├── save_dialog.py         # 저장 폴더·파일명 도우미
├── settings_dialog.py     # 환경설정 (일반/저장/시트/참조/자동 저장)
├── about_dialog.py, help_dialog.py, ui_utils.py
├── HELP_ko.md, HELP_en.md, CHANGELOG.md, README.md
├── icon.ico, requirements.txt, .gitignore, 실행파일 만들기.bat
├── docs/images/           # README 스크린샷
├── tests/                 # test_core.py, test_i18n.py, test_ui_smoke.py
├── config.json            # (자동 생성, git 제외)
├── output/                # (자동 생성) 저장 폴더 미지정 시 기본 저장 위치 — git 제외
└── autosave/              # (자동 생성) 자동 저장본 — git 제외
```

---

## 🗺 로드맵

| 단계 | 버전 | 범위 |
| :--- | :--- | :--- |
| **1단계** | **V0.1.0** ✅ | 시트 열기/저장, 규격 프리셋, 그리드·줌, 셀 편집·다중 셀, Undo/Redo, 배경 레이어, 알파 임계값, 자동 저장, 공통 구성 |
| 2단계 | V0.2.0 | 컬러 → 회색 + 마스크 자동 변환, 밝기 정규화, Nudge, 프리셋 색 미리보기·4색 비교, 마스크 통계 |
| 3단계 | V0.3.0 | 마스크 경계 보정 브러시, F/M 쌍 작업 모드, 다중 참조 레이어 |
| 4단계 | V0.4.0~ | 파일명 검증 강화, 배치 변환, 미러 제외 영역, `.meta` 복제 검토 |

*전체 변경 이력은 [CHANGELOG.md](./CHANGELOG.md) 참고.*

---

## 👤 만든이
재와니
