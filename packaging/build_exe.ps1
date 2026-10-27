# DubTool Windows build (AGU4 11, extended by AGENT_UPDATE_5.md section 8,
# reworked for AGENT_UPDATE_6.md 6.4).
# One command from the repo root:  powershell -File packaging/build_exe.ps1
#  - .venv-build is recreated whenever the pinned requirements change (the
#    SHA-256 of the pinned list is stored in .venv-build/.reqhash), so the
#    "clean build environment" claim is now real; otherwise it is reused.
#  - PyInstaller builds into a temporary dist folder; dist/DubTool is only
#    replaced after the build and checks succeed, so a failed build can
#    never leave a half-built dist/.
#  - build/ is deleted after success unless -KeepBuild is passed.
#  - dist/ keeps only the newest DubTool folder and the newest zip.

param([switch]$KeepBuild, [switch]$SkipTests)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root

$version = "1.0.0"
$distDir = Join-Path $root "dist"
$appDir = Join-Path $distDir "DubTool"
$venvDir = Join-Path $root ".venv-build"
$reqFileUnit = Join-Path $root ".venv-build-.req-units.tmp"

# The pinned dependency set for the packaged app. Changing anything here
# (including versions) invalidates .venv-build.
$Pinned = @(@"
faster-whisper>=1.0
ctranslate2>=4.0
onnxruntime>=1.16
# PyAV 19 removed metadata_errors, which faster-whisper 1.2.1's decode passes;
# the exe selftest guards this (av_decode_compat).
av>=11,<19
tokenizers>=0.15
huggingface_hub>=0.20
certifi
edge-tts>=6.1
google-genai>=2.33.0,<3
anthropic>=0.40
pydub>=0.25
audioop-lts
python-dotenv>=1.0
python-docx>=1.1
pydantic
pyyaml
PySide6>=6.6
keyring>=24
colorama
psutil
tqdm
pyinstaller>=6.10
pytest>=8
pytest-qt
pytest-timeout
"@.TrimEnd())

try {
    # 1. Build environment: fresh only when the pinned set changed
    Write-Host "== [1/6] build environment"
    $reqHash = ""
    $enc = [System.Text.Encoding]::UTF8
    $ms = New-Object System.IO.MemoryStream (, $enc.GetBytes($Pinned))
    $reqHash = [BitConverter]::ToString(
        [System.Security.Cryptography.SHA256]::Create().ComputeHash($ms)
    ).Replace("-", "").ToLower()
    $hashFile = Join-Path $venvDir ".reqhash"
    $reuse = $false
    if (Test-Path $venvDir) {
        if ((Test-Path $hashFile) -and
            ((Get-Content $hashFile -Raw).Trim() -eq $reqHash)) {
            $reuse = $true
            Write-Host "reusing .venv-build (pinned requirements unchanged)"
        } else {
            Write-Host "pinned requirements changed - recreating .venv-build"
            Remove-Item -Recurse -Force $venvDir
        }
    }
    if (-not (Test-Path $venvDir)) {
        py -m venv $venvDir
        if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }
        $py = Join-Path $root ".venv-build\Scripts\python.exe"
        & $py -m pip install --upgrade pip -q
        Write-Host "installing pinned runtime + build dependencies"
        $reqs = $Pinned -split "`r?`n" | Where-Object { $_ -and $_ -notmatch '^#' }   # comments are not pip requirements
        & $py -m pip install -q @reqs
        if ($LASTEXITCODE -ne 0) { throw "dependency install failed" }
        Set-Content -Path $hashFile -Value $reqHash -NoNewline -Encoding UTF8
    }
    $py = Join-Path $root ".venv-build\Scripts\python.exe"
    # The tests import dubtool itself; the editable install is idempotent and
    # cheap when already present, so it runs on every build (a fresh or
    # reused venv) instead of only at venv creation.
    & $py -m pip install -q -e . --no-deps
    if ($LASTEXITCODE -ne 0) { throw "editable project install failed" }

    # 2. Tests (offline)
    if (-not $SkipTests) {
        Write-Host "== [2/6] offline test suite"
        & $py -m pytest tests -q --timeout=120
        if ($LASTEXITCODE -ne 0) { throw "tests failed - not packaging a failing build" }
    } else {
        Write-Host "== [2/6] tests skipped (-SkipTests)"
    }

    # 3. PyInstaller into a temporary dist, promote only on success
    Write-Host "== [3/6] PyInstaller build (temp dist)"
    $tmpDist = Join-Path $root ".venv-build-tmpdist"
    if (Test-Path $tmpDist) { Remove-Item -Recurse -Force $tmpDist }
    & $py -m PyInstaller packaging/dubtool.spec --noconfirm --distpath $tmpDist
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }
    $tmpAppDir = Join-Path $tmpDist "DubTool"

    # 4. Docs into the temp output
    Write-Host "== [4/6] documentation"
    Copy-Item "packaging/THIRD_PARTY.md" -Destination $tmpAppDir -Force
    Copy-Item "README_BUILD.md" -Destination $tmpAppDir -Force
    Copy-Item "packaging/README_APP.md" -Destination $tmpAppDir -Force -ErrorAction SilentlyContinue
    # AGU7 task 5: the build identity next to the exe; --version and the
    # About dialog print the same build id line.
    $exePath = Join-Path $tmpAppDir "DubTool.exe"
    $sha = (Get-FileHash $exePath -Algorithm SHA256).Hash.ToLower()
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd HH:mm:ss")
    $git = "no-repo"
    $gitOut = & git -C $root rev-parse --short HEAD 2>$null
    if ($LASTEXITCODE -eq 0 -and $gitOut) { $git = ([string]$gitOut).Trim() }
    $buildLines = @(
        "update: 7",
        "git: $git",
        "built (UTC): $stamp",
        "sha256: $sha",
        "build id: Update 7 ($git)"
    )
    Set-Content -Path (Join-Path $tmpAppDir "BUILD_INFO.txt") -Value $buildLines -Encoding UTF8

    # 5. Verify the bundled ffmpeg/font landed
    Write-Host "== [5/6] structure check"
    foreach ($p in @("_internal\vendor\ffmpeg.exe", "_internal\vendor\ffprobe.exe", "DubTool.exe")) {
        if (-not (Test-Path (Join-Path $tmpAppDir $p))) { throw "missing $p in the build" }
    }

    # Promote: replace dist/DubTool only now.
    if (Test-Path $appDir) { Remove-Item -Recurse -Force $appDir }
    if (Test-Path (Join-Path $tmpDist "DubTool")) {
        Move-Item (Join-Path $tmpDist "DubTool") $distDir
    }
    Remove-Item -Recurse -Force $tmpDist

    # 6. Release zip + keep only the newest artifacts
    Write-Host "== [6/6] zip + dist housekeeping"
    $zip = Join-Path $distDir "DubTool-$version-win64.zip"
    if (Test-Path $zip) { Remove-Item $zip -Force }
    Compress-Archive -Path $appDir/* -DestinationPath $zip
    # Keep only the newest dist folder artifacts: remove other zips except
    # the one just written and any stray content that is not the app dir.
    Get-ChildItem $distDir |
      Where-Object { $_.FullName -ne $appDir -and $_.FullName -ne $zip } |
      Remove-Item -Recurse -Force

    if (-not $KeepBuild) {
        Write-Host "deleting build/ (-KeepBuild to keep it)"
        if (Test-Path (Join-Path $root "build")) { Remove-Item -Recurse -Force (Join-Path $root "build") }
    }
    Write-Host "built: $appDir"
    Write-Host "zip:   $zip"
}
finally {
    Pop-Location
}
