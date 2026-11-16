@echo off
chcp 65001 >nul
title SCHALE 전투 추적 시스템 (myoi)

:: 현재 배치 파일이 위치한 디렉터리로 이동
cd /d "%~dp0"
set "PYTHONPATH=%~dp0;%PYTHONPATH%"

echo ========================================================================
echo   SCHALE 전투 추적 및 YOLO 데이터 수집 시스템 (myoi)
echo ========================================================================
echo.

:: 1. Python 인터프리터 자동 탐색 (.venv -> venv -> python -> py)
set "PY_CMD="
if exist "%~dp0.venv\Scripts\python.exe" (
    set "PY_CMD=%~dp0.venv\Scripts\python.exe"
    goto :FOUND_PY
)
if exist "%~dp0venv\Scripts\python.exe" (
    set "PY_CMD=%~dp0venv\Scripts\python.exe"
    goto :FOUND_PY
)
where python >nul 2>&1
if not errorlevel 1 (
    set "PY_CMD=python"
    goto :FOUND_PY
)
where py >nul 2>&1
if not errorlevel 1 (
    set "PY_CMD=py"
    goto :FOUND_PY
)

:NO_PYTHON
echo ========================================================================
echo [오류] Python이 컴퓨터에 설치되어 있지 않거나 환경 변수(PATH)에 없습니다!
echo ========================================================================
echo  1. https://www.python.org/downloads/ 에서 Python 3.10 이상을 설치해주세요.
echo  2. 설치 마법사 창 맨 아래의 [Add python.exe to PATH] 체크박스를 꼭 체크해주세요.
echo ========================================================================
echo.
echo 아무 키나 누르면 창을 닫습니다...
pause >nul
exit /b 1

:FOUND_PY
:: 2. 서버 실행 및 브라우저 자동 오픈
"%PY_CMD%" scripts\launcher.py run
set "LAUNCH_ERR=%ERRORLEVEL%"

if not "%LAUNCH_ERR%"=="0" (
    echo.
    echo ========================================================================
    echo [오류 발생] 서버 실행이 중단되었습니다. (오류 코드: %LAUNCH_ERR%)
    echo 상세한 원인은 위 메시지 또는 logs\server.log 파일을 확인해주세요.
    echo ========================================================================
    echo.
    echo 아무 키나 누르면 창을 닫습니다...
    pause >nul
    exit /b %LAUNCH_ERR%
)

exit /b 0
