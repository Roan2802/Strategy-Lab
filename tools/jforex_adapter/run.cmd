@echo off
rem Usage: run.cmd SYMBOL YYYY-MM-DD STAGING_DIR   |   run.cmd --self-test
setlocal
set "HERE=%~dp0"
set "ROOT=%HERE%..\.."
set "LIBS=C:\Users\Roanv\AppData\Local\Programs\JForex4\libs\demo\4.9.6"
set "JAVA=C:\Users\Roanv\AppData\Local\Programs\JForex4\jre\bin\java.exe"
if not exist "%HERE%classes\DukascopyDayExport.class" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%build.ps1" 1>&2 || exit /b 3
)
"%JAVA%" -Xss1m -Xms128m -Djava.net.preferIPv4Stack=true -Djava.awt.headless=true ^
  --add-exports java.base/java.lang=ALL-UNNAMED ^
  --add-exports java.desktop/sun.awt=ALL-UNNAMED --add-opens java.desktop/sun.awt=ALL-UNNAMED ^
  --add-exports java.desktop/sun.java2d=ALL-UNNAMED ^
  "-Djforex.root=%ROOT%" "-Djforex.cache=%HERE%cache" ^
  -cp "%HERE%classes;%LIBS%\*" DukascopyDayExport %*
exit /b %ERRORLEVEL%
