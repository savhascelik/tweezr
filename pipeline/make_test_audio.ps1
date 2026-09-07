# Windows SAPI ile bilinen metinli test sesi üretir.
#
# Amaç: kendi çekimin hazır olmadan pipeline'ı ayağa kaldırmak. Ground truth metni
# elimizde olduğu için Whisper'ın hizalamasını karşılaştırabiliyoruz.
#
# UYARI: TTS sesi gerçek konuşmadan temiz. Bu test pipeline mekaniğini doğrular,
# gerçek materyalde hizalama kalitesini DOĞRULAMAZ. Onu kendi çekiminle ölçeceksin.
#
#   powershell -ExecutionPolicy Bypass -File make_test_audio.ps1

param(
    [string]$OutFile = "sample.wav",
    [int]$Rate = 0   # -10 (yavaş) .. 10 (hızlı)
)

$ErrorActionPreference = "Stop"

# Hedef replik iki kere geçiyor: "aynı repliğin başka take'i" senaryosunu taklit ediyor.
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

Write-Host "Yazıldı: $fullPath"
Write-Host ""
Write-Host "Ground truth metin:"
Write-Host "  $script"
Write-Host ""
Write-Host 'Hedef replik 2 kere geciyor: "I never asked for this"'
