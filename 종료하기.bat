@echo off
chcp 65001 >nul
title SCHALE 전투 추적 시스템 - 종료기

cd /d "%~dp0"
set "PYTHONPATH=%~dp0;%PYTHONPATH%"

echo ========================================================================
echo   SCHALE 전투 추적 시스템 종료
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
echo [오류] Python 인터프리터를 찾을 수 없습니다.
pause >nul
exit /b 1

:FOUND_PY
:: 2. 서버 안전 종료 (PID 기반 타겟 프로세스만 종료)
"%PY_CMD%" scripts\launcher.py stop
set "STOP_ERR=%ERRORLEVEL%"

if not "%STOP_ERR%"=="0" (
    echo.
    echo [오류 발생] 서버 종료 중 문제가 발생했습니다. 위 내용을 확인해주세요.
    echo.
    pause >nul
    exit /b %STOP_ERR%
)

echo.
echo 3초 후 이 창은 자동으로 닫힙니다...
timeout /t 3 >nul
exit /b 0
