# Fabrique le faux écran d'essai : une image 3840x2160 de ecran.html par Chrome sans fenêtre (profil jetable),
# puis ses points bruts (BGRA) pour Python, qui n'a pas de bibliothèque d'image. Rien de l'écran réel n'est capturé.
$ErrorActionPreference = 'Stop'
$ici = Split-Path -Parent $MyInvocation.MyCommand.Path
$png = Join-Path $ici 'ecran-4k.png'
$brut = Join-Path $ici 'ecran-4k.bgra'
$profil = Join-Path $env:TEMP "chrome-jetable-vision-$PID"
$chrome = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
if (Test-Path $png) { Remove-Item $png -Force }
$page = 'file:///' + ((Join-Path $ici 'ecran.html') -replace '\\', '/')
$arguments = @('--headless=new', '--disable-gpu', '--no-first-run', "--user-data-dir=$profil", "--screenshot=$png",
               '--window-size=3840,2160', '--force-device-scale-factor=1', '--hide-scrollbars', $page)
$p = Start-Process -FilePath $chrome -ArgumentList $arguments -WindowStyle Hidden -PassThru
$p.WaitForExit(60000) | Out-Null
Remove-Item $profil -Recurse -Force -ErrorAction SilentlyContinue
if (-not (Test-Path $png)) { Write-Host 'Chrome n''a pas fait l''image.'; exit 1 }
Add-Type -AssemblyName System.Drawing
$image = New-Object System.Drawing.Bitmap($png)
$zone = New-Object System.Drawing.Rectangle(0, 0, $image.Width, $image.Height)
$donnees = $image.LockBits($zone, [System.Drawing.Imaging.ImageLockMode]::ReadOnly, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
$octets = New-Object byte[] ($donnees.Stride * $image.Height)
[System.Runtime.InteropServices.Marshal]::Copy($donnees.Scan0, $octets, 0, $octets.Length)
$image.UnlockBits($donnees)
[IO.File]::WriteAllBytes($brut, $octets)
Write-Host "image : $($image.Width) x $($image.Height), pas de ligne $($donnees.Stride), $([int]((Get-Item $png).Length / 1KB)) Ko en PNG"
$image.Dispose()
