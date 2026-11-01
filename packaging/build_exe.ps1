# DubTool Windows build (AGU4 11, extended by AGENT_UPDATE_5.md section 8,
# reworked for AGENT_UPDATE_6.md 6.4).
# One command from the repo root:  powershell -File packaging/build_exe.ps1
#  - the build root is OUTSIDE the project (DUBTOOL_BUILD_ROOT or
#    <project parent>\<project folder>_build): venv-build, build/, dist/,
#    pip-cache, screenshots/ and the shared model caches all live there.
#  - venv-build is recreated whenever the pinned requirements change (the
#    SHA-256 of the pinned list is stored in venv-build/.reqhash), so the
#    "clean build environment" claim is now real; otherwise it is reused.
#  - PyInstaller builds into a temporary dist folder; dist/DubTool is only
#    replaced after the build and checks succeed, so a failed build can
#    never leave a half-built dist/. -ZipOnly deletes the unpacked folder
#    after zipping.
#  - the PyInstaller work dir is deleted after success unless -KeepBuild.
#  - dist/ keeps only the newest DubTool folder and the newest zip.

param([switch]$KeepBuild, [switch]$SkipTests, [switch]$ZipOnly)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root

# AGU10 task 2: the build root lives OUTSIDE the project (env override,
# default <project parent>\<project folder>_build). Nothing generated is
# ever written into the project folder any more.
$buildRootEnv = $env:DUBTOOL_BUILD_ROOT
$projectDir = (Resolve-Path $root).Path
if ([string]::IsNullOrWhiteSpace($buildRootEnv)) {
  $buildRoot = Join-Path (Split-Path -Parent $projectDir) ((Split-Path -Leaf $projectDir) + "_build")
} else {
  $buildRoot = (Resolve-Path -LiteralPath $buildRootEnv).Path
}
if ($buildRoot -like ($projectDir + "\*") -or $buildRoot -eq $projectDir) {
  throw "the build root must be OUTSIDE the project folder ($buildRoot)"
}
New-Item -ItemType Directory -Force -Path $buildRoot | Out-Null
$logsDir = Join-Path $buildRoot "logs"
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null
Write-Host "build root: $buildRoot"

# AGU9 5.2: the update number comes from packaging/update_number.txt and the
# version is `1.0.<N>` — nobody has to remember it any more.
$updateLine = (Get-Content (Join-Path $root "packaging\update_number.txt") -Raw).Trim()
if (-not ($updateLine -match '^\d+$')) { throw "packaging/update_number.txt must contain just the update number, got '$updateLine'" }
$update = $updateLine
$version = "1.0.$update"
$buildId = "Update $update ($git)"
# AGU10 task 2.2: one version source + one build root. The venv, the pip
# cache, the PyInstaller work dir and dist/ all live in the build root;
# nothing generated is ever written into the project folder again.
$distDir = Join-Path $buildRoot "dist"
$appDir = Join-Path $distDir "DubTool"
$workDir = Join-Path $buildRoot "build"
$pyCache = Join-Path $buildRoot "pip-cache"
$venvDir = Join-Path $buildRoot "venv-build"
$reqFileUnit = Join-Path $buildRoot ".req-units.tmp"

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
# AGU10 task 2: PySide6-Essentials instead of the full PySide6 wheel — the
# app imports only QtCore/QtGui/QtWidgets/QtTest (verified by the import
# scan, tests/test_build_env.py), none of which is an Addons module.
PySide6-Essentials>=6.6
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
    # AGU10 task 3.1: the shared model cache for every sanitized run —
# a fresh temporary HOME must never re-download the 483 MB Whisper model.
$hfCache = Join-Path $buildRoot "hf-cache"
New-Item -ItemType Directory -Force -Path $hfCache | Out-Null
$env:HF_HOME = $hfCache
$env:HF_HUB_CACHE = (Join-Path $hfCache "hub")
$modelsCache = Join-Path $buildRoot "models"
New-Item -ItemType Directory -Force -Path $modelsCache | Out-Null
$env:DUBTOOL_MODELS_DIR = $modelsCache
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
        $py = Join-Path $venvDir "Scripts\python.exe"
        & $py -m pip install --upgrade pip -q --cache-dir $pyCache
        Write-Host "installing pinned runtime + build dependencies"
        $reqs = $Pinned -split "`r?`n" | Where-Object { $_ -and $_ -notmatch '^#' }   # comments are not pip requirements
        & $py -m pip install -q @reqs --cache-dir $pyCache
        if ($LASTEXITCODE -ne 0) { throw "dependency install failed" }
        Set-Content -Path $hashFile -Value $reqHash -NoNewline -Encoding UTF8
    }
    $py = Join-Path $venvDir "Scripts\python.exe"
    # The tests import dubtool itself; the editable install is idempotent and
    # cheap when already present, so it runs on every build (a fresh or
    # reused venv) instead of only at venv creation.
    & $py -m pip install -q -e . --no-deps --cache-dir $pyCache
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
    $tmpDist = Join-Path $buildRoot "tmp-dist"
    if (Test-Path $tmpDist) { Remove-Item -Recurse -Force $tmpDist }
    & $py -m PyInstaller packaging/dubtool.spec --noconfirm --distpath $tmpDist --workpath $workDir
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
    $buildId = "Update $update ($git)"
    $buildLines = @(
        "update: $update",
        "version: $version",
        "git: $git",
        "built (UTC): $stamp",
        "sha256: $sha",
        "build id: $buildId"
    )
    Set-Content -Path (Join-Path $tmpAppDir "BUILD_INFO.txt") -Value $buildLines -Encoding UTF8

    # 5. Verify the bundled ffmpeg/font landed. ffprobe.exe is deliberately
    #    NOT shipped since AGU9 task 8 (probing runs in-process via PyAV).
    Write-Host "== [5/6] structure check"
    foreach ($p in @("_internal\vendor\ffmpeg.exe", "DubTool.exe")) {
        if (-not (Test-Path (Join-Path $tmpAppDir $p))) { throw "missing $p in the build" }
    }
    if (Test-Path (Join-Path $tmpAppDir "_internal\vendor\ffprobe.exe")) {
        throw "ffprobe.exe must not be in the build (AGU9 task 8)"
    }

    # Promote: replace dist/DubTool only now. $distDir must EXIST first:
    # Move-Item with a missing destination would RENAME tmp-dist\DubTool
    # to dist\ and stray the whole app one level up (found in build 1.0.10).
    if (Test-Path $appDir) { Remove-Item -Recurse -Force $appDir }
    New-Item -ItemType Directory -Force -Path $distDir | Out-Null
    if (Test-Path (Join-Path $tmpDist "DubTool")) {
        Move-Item (Join-Path $tmpDist "DubTool") $distDir
    }
    Remove-Item -Recurse -Force $tmpDist

    # 6. Release zip + keep only the newest artifacts
    Write-Host "== [6/6] zip + dist housekeeping"
    $zip = Join-Path $distDir "DubTool-$version-win64.zip"
    if (Test-Path $zip) { Remove-Item $zip -Force }
    Compress-Archive -Path $appDir/* -DestinationPath $zip
    # AGU10 task 5.6: -ZipOnly deletes the unpacked folder after zipping.
    if ($ZipOnly) {
        Remove-Item -Recurse -Force $appDir
        Write-Host "-ZipOnly: unpacked DubTool/ deleted, keeping $zip"
    }
    # Keep only the newest dist folder artifacts: remove other zips except
    # the one just written and any stray content that is not the app dir.
    Get-ChildItem $distDir |
      Where-Object { $_.Name -ne "DubTool" -and $_.Name -ne (Split-Path -Leaf $zip) } |
      Remove-Item -Recurse -Force

    if (-not $KeepBuild) {
        Write-Host "deleting the PyInstaller work dir (-KeepBuild to keep it)"
        if (Test-Path $workDir) { Remove-Item -Recurse -Force $workDir }
    }
    Write-Host "built: $((Resolve-Path $appDir).Path)"
    Write-Host "zip:   $((Resolve-Path $zip).Path)"
    Write-Host "run this exe: $((Join-Path $appDir 'DubTool.exe'))"

    # 7. Screenshots from the *fresh* exe (AGU7 task 5): a new folder named
    # after the build id, older folders deleted, then the screenshots test
    # asserts the set against this exe's BUILD_INFO.txt. Done after the
    # tests gate on purpose: the PNGs must prove the exe that was just
    # promoted, not a previous one.
    Write-Host "== [7/6] screenshots from the fresh exe"
    $buildIdFull = "Update $update ($git)"
    $slug = ($buildIdFull.ToLower() -replace " ", "_") -replace "[()]", ""
    $shotsRoot = Join-Path $buildRoot "screenshots"
    New-Item -ItemType Directory -Force -Path $shotsRoot | Out-Null
    $shotDir = Join-Path $shotsRoot $slug
    if (Test-Path $shotDir) { Remove-Item -Recurse -Force $shotDir }
    # A GUI-subsystem exe does not block PS `&` — wait explicitly.
    $ssProc = Start-Process -FilePath (Join-Path $appDir "DubTool.exe") `
      -ArgumentList "--screenshots", $shotDir -Wait -NoNewWindow -PassThru
    if ($ssProc.ExitCode -ne 0) { throw "screenshot generation failed ($($ssProc.ExitCode))" }
    Get-ChildItem $shotsRoot -Directory |
      Where-Object { $_.FullName -ne $shotDir } |
      Remove-Item -Recurse -Force
    Write-Host "shots:  $shotDir"
    & $py -m pytest tests/test_screenshots.py -q
    if ($LASTEXITCODE -ne 0) { throw "screenshot set check failed" }

    # 8. README images (B update 9 task 7): copy the chosen states from the
    # fresh screenshot folder into the tracked docs/images with stable
    # names, then verify every README link resolves to a tracked file.
    Write-Host "== [7/6] README images from the fresh set"
    & $py (Join-Path $root "scripts\update_readme_images.py") $shotDir
    if ($LASTEXITCODE -ne 0) { throw "README image copy failed" }
    & $py (Join-Path $root "scripts\check_readme_links.py")
    if ($LASTEXITCODE -ne 0) { throw "README link check failed" }
}
finally {
    Pop-Location
}
