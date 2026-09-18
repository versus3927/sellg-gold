@echo off
REM Собирает debug APK портативным тулчейном.
setlocal
set "JAVA_HOME=C:\Users\pc\android-build-tools\jdk-17.0.20.1+1"
set "ANDROID_SDK_ROOT=C:\Users\pc\android-build-tools\sdk"
set "GRADLE=C:\Users\pc\android-build-tools\gradle-8.7\bin\gradle.bat"
set "PATH=%JAVA_HOME%\bin;%PATH%"
cd /d "%~dp0"
call "%GRADLE%" assembleDebug --no-daemon
echo.
echo APK: %~dp0app\build\outputs\apk\debug\app-debug.apk
endlocal
