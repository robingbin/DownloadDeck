# Builds the shareable Windows package: release\DownloadDeck-<version>-win64.zip
# Usage (from this folder):  powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$py = ".venv\Scripts\python.exe"

if (-not (Test-Path $py)) {
    python -m venv .venv
}
& $py -m pip install -q -r requirements.txt pyinstaller
& $py -m pip install -q -U "yt-dlp[default]"          # newest YouTube support in every build

& ".venv\Scripts\pyinstaller.exe" DownloadDeck.spec --noconfirm --distpath build_out\dist --workpath build_out\work
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$dist = "build_out\dist\DownloadDeck"
Copy-Item (& $py -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())") "$dist\ffmpeg.exe" -Force
Copy-Item ".venv\Scripts\deno.exe" "$dist\deno.exe" -Force
Remove-Item "$dist\chrome-extension" -Recurse -Force -ErrorAction SilentlyContinue
Copy-Item "chrome-extension" "$dist\chrome-extension" -Recurse
Copy-Item "THIRD-PARTY-NOTICES.md" "$dist\" -Force
Copy-Item "docs\INSTALL.txt" "$dist\READ ME FIRST.txt" -Force

$version = (Select-String -Path app.py -Pattern '^VERSION = "(.+)"').Matches[0].Groups[1].Value
New-Item -ItemType Directory -Force release | Out-Null
$zip = "release\DownloadDeck-$version-win64.zip"
Remove-Item $zip -ErrorAction SilentlyContinue
Compress-Archive -Path $dist -DestinationPath $zip -CompressionLevel Optimal
Write-Host "Built $zip ($([math]::Round((Get-Item $zip).Length / 1MB)) MB)"
