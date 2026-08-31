param(
    [string]$Voice = "Microsoft Haruka Desktop"
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech
$root = Split-Path -Parent $PSScriptRoot
$fixtureDir = Join-Path $root "tests\fixtures\benchmark"
$textPath = Join-Path $fixtureDir "japanese_reference.txt"
$wavPath = Join-Path $fixtureDir "japanese_reference.wav"
New-Item -ItemType Directory -Path $fixtureDir -Force | Out-Null
$text = [IO.File]::ReadAllText($textPath, [Text.Encoding]::UTF8).Trim()
$synth = [System.Speech.Synthesis.SpeechSynthesizer]::new()
try {
    $synth.SelectVoice($Voice)
    $format = [System.Speech.AudioFormat.SpeechAudioFormatInfo]::new(
        16000,
        [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
        [System.Speech.AudioFormat.AudioChannel]::Mono
    )
    $synth.SetOutputToWaveFile($wavPath, $format)
    $synth.Speak($text)
}
finally {
    $synth.Dispose()
}
Write-Output $wavPath
