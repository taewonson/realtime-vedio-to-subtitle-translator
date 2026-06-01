$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$plainCredentials = @(
    'credentials/stt-and-trans-f23682fda0c1.json',
    'credentials/vocalogin-737579975a45.json'
)

$ffmpegBinDir = Join-Path $root 'third_party\ffmpeg\bin'
$ffmpegExe = $null
$ffprobeExe = $null

if (Test-Path $ffmpegBinDir) {
    $ffmpegExe = Join-Path $ffmpegBinDir 'ffmpeg.exe'
    $ffprobeExe = Join-Path $ffmpegBinDir 'ffprobe.exe'
} else {
    $ffmpegExe = (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source
    $ffprobeExe = (Get-Command ffprobe -ErrorAction SilentlyContinue).Source
    if ($ffmpegExe -and $ffprobeExe) {
        $ffmpegBinDir = Split-Path -Parent $ffmpegExe
    }
}

$stagingDir = Join-Path $root '.build_staging'
if (Test-Path $stagingDir) {
    Remove-Item $stagingDir -Recurse -Force
}
New-Item -ItemType Directory -Path $stagingDir | Out-Null

$useEncryptedCredentials = [bool]($env:GCP_CREDENTIALS_PASSPHRASE) -and [bool]($env:GCP_VOCAB_PASSPHRASE)

foreach ($path in $plainCredentials) {
    if (-not (Test-Path $path)) {
        throw "Missing source credential file: $path"
    }
}

try {
    python -m PyInstaller --version | Out-Null
} catch {
    throw "PyInstaller is not installed in the current Python environment. Run: python -m pip install pyinstaller"
}

if (-not $ffmpegExe -or -not $ffprobeExe) {
    throw "Missing ffmpeg tools. Install ffmpeg/ffprobe or place a portable build in $ffmpegBinDir."
}

if (-not (Test-Path $ffmpegExe) -or -not (Test-Path $ffprobeExe)) {
    throw "ffmpeg.exe and ffprobe.exe must exist in $ffmpegBinDir"
}

if ($useEncryptedCredentials) {
    python google_cloud_auth.py encrypt credentials/stt-and-trans-f23682fda0c1.json credentials/stt-and-trans-f23682fda0c1.json.enc --passphrase $env:GCP_CREDENTIALS_PASSPHRASE
    python google_cloud_auth.py encrypt credentials/vocalogin-737579975a45.json credentials/vocalogin-737579975a45.json.enc --passphrase $env:GCP_VOCAB_PASSPHRASE
}

$releaseEnv = Join-Path $stagingDir '.env'
Get-Content '.env' |
    ForEach-Object {
        if ($useEncryptedCredentials -and $_ -match '^GCP_CREDENTIALS_FILE=') {
            'GCP_CREDENTIALS_FILE=credentials/stt-and-trans-f23682fda0c1.json.enc'
        } elseif ($useEncryptedCredentials -and $_ -match '^GCP_VOCAB_CREDENTIALS_FILE=') {
            'GCP_VOCAB_CREDENTIALS_FILE=credentials/vocalogin-737579975a45.json.enc'
        } else {
            $_
        }
    } | Set-Content -Encoding UTF8 $releaseEnv

$envArgs = "$releaseEnv;."
$sttSource = if ($useEncryptedCredentials) { 'credentials/stt-and-trans-f23682fda0c1.json.enc' } else { 'credentials/stt-and-trans-f23682fda0c1.json' }
$vocabSource = if ($useEncryptedCredentials) { 'credentials/vocalogin-737579975a45.json.enc' } else { 'credentials/vocalogin-737579975a45.json' }
$sttArgs = "$sttSource;credentials"
$vocabArgs = "$vocabSource;credentials"
$extArgs = "extention;extention"
$ffmpegArgs = "$ffmpegBinDir;ffmpeg/bin"

python -m PyInstaller --noconfirm --clean --onefile --noconsole --name VocalogSubtitleHub `
    --add-data $envArgs `
    --add-data $sttArgs `
    --add-data $vocabArgs `
    --add-data $extArgs `
    --add-binary $ffmpegArgs `
    main.py