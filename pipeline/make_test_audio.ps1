# Generates known-text test audio with Windows SAPI.
#
# The point: get the pipeline running before your own footage exists. Because we know the
# ground truth text, we can compare what Whisper's alignment produced against it.
#
# WARNING: synthesised speech is cleaner than real speech. This test verifies the
# pipeline's mechanics; it does NOT verify alignment quality on real material. That has
# to be measured on your own footage.
#
#   powershell -ExecutionPolicy Bypass -File make_test_audio.ps1

param(
    [string]$OutFile = "sample.wav",
    [int]$Rate = 0   # -10 (slow) .. 10 (fast)
)

$ErrorActionPreference = "Stop"

# The target line appears twice, which imitates "the same line in another take".
$script = "Okay, quiet on set. I never asked for this. Take it again from the top. I never asked for this."

Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $synth.Rate = $Rate
    $fullPath = Join-Path (Get-Location) $OutFile
    $synth.SetOutputToWaveFile($fullPath)
    $synth.Speak($script)
    $synth.SetOutputToNull()
}
finally {
    $synth.Dispose()
}

Write-Host "Written: $fullPath"
Write-Host ""
Write-Host "Ground truth text:"
Write-Host "  $script"
Write-Host ""
Write-Host 'The target line occurs twice: "I never asked for this"'
