@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%" >nul

set "APP_NAME=Vidoon2026"
set "MAIN_FILE=main.py"
set "ICON_FILE=icon.ico"
set "DIST_DIR=dist"
set "DIST_APP_DIR=%DIST_DIR%\%APP_NAME%"
set "BUILD_DIR=build"
set "SPEC_FILE=%APP_NAME%.spec"
set "ZIP_NAME=%APP_NAME%_latest.zip"
set "VERSION_FILE=version.json"

echo ==============================================
echo Build %APP_NAME%
echo ==============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found in PATH.
    goto :fail
)

python -m PyInstaller --version >nul 2>nul
if errorlevel 1 (
    echo [ERROR] PyInstaller is not installed.
    echo Run: python -m pip install pyinstaller
    goto :fail
)

set "missing_files="
for %%F in ("%MAIN_FILE%" "%ICON_FILE%" "yt-dlp.exe" "ffmpeg.exe" "deno.exe") do (
    if not exist "%%~F" (
        set "missing_files=!missing_files! %%~F"
    )
)

if defined missing_files (
    echo [ERROR] Missing required files:
    echo !missing_files!
    goto :fail
)

echo [1/6] Python syntax check...
python -m py_compile "%MAIN_FILE%"
if errorlevel 1 (
    echo [ERROR] %MAIN_FILE% has syntax errors.
    goto :fail
)

for %%F in (app_config.py auto_update.py about.py shouquan.py setting.py piliang.py videodown.py rizhi.py core\__init__.py core\download_types.py core\download_utils.py core\download_router.py core\youtube_pot_provider.py platforms\__init__.py platforms\youtube_download.py platforms\instagram_download.py platforms\tiktok_download.py platforms\twitter_download.py) do (
    if exist "%%F" (
        python -m py_compile "%%F"
        if errorlevel 1 (
            echo [ERROR] %%F has syntax errors.
            goto :fail
        )
    )
)

echo Prepare YouTube PO Token Provider...
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\prepare_bgutil_provider.ps1" -ProjectRoot "%CD%"
if errorlevel 1 (
    echo [ERROR] Failed to prepare YouTube PO Token Provider.
    goto :fail
)

echo [2/6] Clean old build output...
if exist "%BUILD_DIR%" rmdir /s /q "%BUILD_DIR%"
if exist "%DIST_DIR%" rmdir /s /q "%DIST_DIR%"
if exist "%SPEC_FILE%" del /q "%SPEC_FILE%"
if exist "%APP_NAME%" rmdir /s /q "%APP_NAME%"

echo [3/6] Run PyInstaller...
python -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --noupx ^
  --noconsole ^
  --onedir ^
  --name "%APP_NAME%" ^
  --icon "%ICON_FILE%" ^
  --hidden-import=wmi ^
  --hidden-import=winreg ^
  --hidden-import=shouquan ^
  --hidden-import=rizhi ^
  --hidden-import=setting ^
  --hidden-import=about ^
  --hidden-import=auto_update ^
  --hidden-import=piliang ^
  --hidden-import=videodown ^
  --hidden-import=core.download_router ^
  --hidden-import=core.download_types ^
  --hidden-import=core.download_utils ^
  --hidden-import=core.youtube_pot_provider ^
  --hidden-import=platforms.youtube_download ^
  --hidden-import=platforms.instagram_download ^
  --hidden-import=platforms.tiktok_download ^
  --hidden-import=platforms.twitter_download ^
  "%MAIN_FILE%"

if errorlevel 1 (
    echo [ERROR] PyInstaller build failed.
    goto :fail
)

if not exist "%DIST_APP_DIR%\%APP_NAME%.exe" (
    echo [ERROR] Output exe not found: %DIST_APP_DIR%\%APP_NAME%.exe
    goto :fail
)

echo [4/6] Copy runtime files...
for %%F in ("yt-dlp.exe" "ffmpeg.exe" "deno.exe" "config.json" "app_settings.json" "version.json" "icon.ico" "logo.png") do (
    if exist "%%~F" (
        copy /Y "%%~F" "%DIST_APP_DIR%\" >nul
        if errorlevel 1 (
            echo [ERROR] Failed to copy %%~F
            goto :fail
        )
    )
)
echo [INFO] Cookie files will be imported by user separately.

if exist "yt-dlp-plugins" (
    xcopy /E /I /Y "yt-dlp-plugins" "%DIST_APP_DIR%\yt-dlp-plugins" >nul
    if errorlevel 1 (
        echo [ERROR] Failed to copy yt-dlp plugins.
        goto :fail
    )
)
if exist "vendor\bgutil-provider" (
    xcopy /E /I /Y "vendor\bgutil-provider" "%DIST_APP_DIR%\vendor\bgutil-provider" >nul
    if errorlevel 1 (
        echo [ERROR] Failed to copy BgUtils Provider runtime.
        goto :fail
    )
)
if exist "THIRD_PARTY_NOTICES.md" copy /Y "THIRD_PARTY_NOTICES.md" "%DIST_APP_DIR%\" >nul

if exist "%DIST_APP_DIR%\config.json" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$path = Join-Path (Get-Location) '%DIST_APP_DIR%\\config.json'; $data = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json; @('download_path','last_preview_file','last_youtube_preview_file','cookies_file','proxy_list') | ForEach-Object { $data.PSObject.Properties.Remove($_) }; $utf8 = New-Object System.Text.UTF8Encoding($false); [IO.File]::WriteAllText($path, ($data | ConvertTo-Json -Depth 10), $utf8)"
    if errorlevel 1 (
        echo [ERROR] Failed to sanitize packaged config.json.
        goto :fail
    )
)

if exist "%DIST_APP_DIR%\version.json" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$path = Join-Path (Get-Location) '%DIST_APP_DIR%\\version.json'; $data = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json; $data.sha256 = ''; $utf8 = New-Object System.Text.UTF8Encoding($false); [IO.File]::WriteAllText($path, ($data | ConvertTo-Json -Depth 5), $utf8)"
    if errorlevel 1 (
        echo [ERROR] Failed to prepare packaged version.json.
        goto :fail
    )
)

echo [5/6] Verify output...
set "required_ok=true"
for %%F in ("%DIST_APP_DIR%\%APP_NAME%.exe" "%DIST_APP_DIR%\yt-dlp.exe" "%DIST_APP_DIR%\ffmpeg.exe" "%DIST_APP_DIR%\deno.exe" "%DIST_APP_DIR%\yt-dlp-plugins\bgutil-ytdlp-pot-provider.zip" "%DIST_APP_DIR%\vendor\bgutil-provider\node.exe" "%DIST_APP_DIR%\vendor\bgutil-provider\server\build\main.js") do (
    if not exist "%%~F" (
        echo [WARN] Missing file: %%~F
        set "required_ok=false"
    )
)

set "SELF_TEST_REPORT=%TEMP%\%APP_NAME%_package_self_test.json"
if exist "!SELF_TEST_REPORT!" del /q "!SELF_TEST_REPORT!"
echo Run packaged EXE self-test...
start "" /wait "%DIST_APP_DIR%\%APP_NAME%.exe" --package-self-test "!SELF_TEST_REPORT!"
if errorlevel 1 (
    echo [ERROR] Packaged EXE self-test failed.
    if exist "!SELF_TEST_REPORT!" type "!SELF_TEST_REPORT!"
    goto :fail
)
if not exist "!SELF_TEST_REPORT!" (
    echo [ERROR] Packaged EXE did not create a self-test report.
    goto :fail
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$report = Get-Content -LiteralPath $env:SELF_TEST_REPORT -Raw -Encoding UTF8 | ConvertFrom-Json; if (-not $report.ok) { $report | ConvertTo-Json -Depth 5; exit 1 }; Write-Host ('[OK] Package self-test: files, storage, certificate and API are ready.')"
if errorlevel 1 (
    echo [ERROR] Packaged EXE self-test report is not healthy.
    type "!SELF_TEST_REPORT!"
    goto :fail
)
del /q "!SELF_TEST_REPORT!" >nul 2>nul

echo.
echo app files:
echo ----------------------------------------------
dir /b "%DIST_APP_DIR%"
echo ----------------------------------------------

for /f %%I in ('dir /b "%DIST_APP_DIR%" ^| find /c /v ""') do set "total_files=%%I"
echo Total files: !total_files!
echo [6/6] Create ZIP package...
echo Clean old ZIP packages...
if exist "%ZIP_NAME%" del /q "%ZIP_NAME%"
call :make_zip
if errorlevel 1 goto :fail

echo.
if /i "!required_ok!"=="true" (
    echo ==============================================
    echo Build success
    echo EXE: %DIST_APP_DIR%\%APP_NAME%.exe
    echo ZIP: %ZIP_NAME%
    echo ==============================================
) else (
    echo ==============================================
    echo Build finished with warnings
    echo Check the missing file messages above.
    echo ==============================================
)

echo.
if exist "%BUILD_DIR%" rmdir /s /q "%BUILD_DIR%"
if exist "%SPEC_FILE%" del /q "%SPEC_FILE%"
echo Temp files cleaned automatically.

echo.
echo Done.
popd >nul
exit /b 0

:make_zip
echo Create ZIP...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$src = Join-Path (Get-Location) '%DIST_APP_DIR%'; $dst = Join-Path (Get-Location) '%ZIP_NAME%'; for ($i = 1; $i -le 5; $i++) { try { Compress-Archive -LiteralPath $src -DestinationPath $dst -Force -ErrorAction Stop; exit 0 } catch { if ($i -eq 5) { Write-Error $_; exit 1 }; Start-Sleep -Seconds 2 } }"
if errorlevel 1 (
    echo [ERROR] ZIP create failed.
    exit /b 1
)

if exist "%ZIP_NAME%" (
    echo ZIP created: %ZIP_NAME%
    if exist "%VERSION_FILE%" (
        powershell -NoProfile -ExecutionPolicy Bypass -Command "$jsonPath = Join-Path (Get-Location) '%VERSION_FILE%'; $zipPath = Join-Path (Get-Location) '%ZIP_NAME%'; $data = Get-Content -LiteralPath $jsonPath -Raw -Encoding UTF8 | ConvertFrom-Json; $data.sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $zipPath).Hash.ToLower(); $data | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $jsonPath -Encoding UTF8"
        if errorlevel 1 (
            echo [WARN] Failed to update %VERSION_FILE% sha256.
        ) else (
            echo %VERSION_FILE% sha256 updated.
        )
    ) else (
        echo [WARN] %VERSION_FILE% not found, sha256 was not updated.
    )
) else (
    echo [ERROR] ZIP create failed.
    exit /b 1
)
exit /b 0

:cleanup_auto
echo.
if exist "%BUILD_DIR%" rmdir /s /q "%BUILD_DIR%"
if exist "%SPEC_FILE%" del /q "%SPEC_FILE%"
echo Temp files cleaned automatically.

echo.
echo Done.
popd >nul
exit /b 0

:fail
echo.
echo Build aborted.
popd >nul
exit /b 1
