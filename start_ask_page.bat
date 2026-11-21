@echo off
rem Starts the Ask page on this computer and opens it in your browser.
rem Your Groq key is typed in this window only. It is never written to a file and ends when the window closes.
setlocal
set PYTHONUTF8=1
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install Python 3 from python.org, tick "Add python to PATH", then run this file again.
  pause
  exit /b 1
)
echo.
echo Ask the playbook
echo ----------------
echo Paste your Groq key and press Enter to get written answers.
echo Press Enter with nothing typed to see the matching playbook text instead (no AI call, no key).
echo The key is visible while you type, so close this window when you are done.
echo.
set "GROQ_API_KEY="
set /p GROQ_API_KEY=Groq key: 
echo.
if "%GROQ_API_KEY%"=="" (
  python rag_server.py --no-ai --open
) else (
  python rag_server.py --open
)
echo.
echo The Ask page has stopped.
pause
