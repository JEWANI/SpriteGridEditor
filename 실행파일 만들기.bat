@echo off
REM 윈도우 시스템 명령 경로를 일시적으로 추가
set PATH=%SystemRoot%\system32;%SystemRoot%;%SystemRoot%\System32\Wbem;%PATH%

REM 이제 chcp 명령어가 정상 작동합니다.
chcp 65001 >nul

REM 버전은 app_info.py 한 곳에서만 관리한다 (exe 이름도 여기서 읽어 온다)
for /f "delims=" %%v in ('python -c "from app_info import APP_VERSION; print(APP_VERSION)"') do set APP_VER=%%v
set EXE_NAME=SpriteGridEditor v%APP_VER%

echo %EXE_NAME% 빌드를 시작합니다...

pyinstaller --noconsole --onefile --icon=icon.ico --add-data "icon.ico;." --add-data "locales;locales" --add-data "HELP_ko.md;." --add-data "HELP_en.md;." --add-data "CHANGELOG.md;." main.py --name "%EXE_NAME%"

echo.
echo 작업이 완료되었습니다. dist 폴더를 확인하세요. (dist\%EXE_NAME%.exe)
pause
