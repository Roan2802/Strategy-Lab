$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$libs = 'C:\Users\Roanv\AppData\Local\Programs\JForex4\libs\demo\4.9.6'
$javac = 'C:\Program Files\Microsoft\jdk-21.0.9.10-hotspot\bin\javac.exe'
$out = Join-Path $here 'classes'
New-Item -ItemType Directory -Force -Path $out | Out-Null
& $javac -encoding UTF-8 --release 21 -proc:none -d $out -cp "$libs\*" (Join-Path $here 'src\DukascopyDayExport.java')
exit $LASTEXITCODE
