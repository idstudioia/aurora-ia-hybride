# One page of a PDF drawn as a JPEG image by Windows' own PDF reader (Windows.Data.Pdf, part of Windows 10 and 11).
# Run by tools/documents.py (page_image) with a file AURORA has just written in its own temporary folder and two
# numbers it has checked: nothing here comes from a model, a page or a document. The PDF is only read.
# Prints one line of JSON: {"ok":true,"pages":13,"width":905,"height":1280} or {"ok":false,"reason":"..."}.
# ASCII only: Windows PowerShell 5.1 reads a script without a mark as ANSI.
param([Parameter(Mandatory = $true)][string]$Pdf, [Parameter(Mandatory = $true)][string]$Image,
      [Parameter(Mandatory = $true)][int]$Page, [int]$Side = 1280)
$ErrorActionPreference = 'Stop'

function Say($value) {
    [Console]::Out.WriteLine(($value | ConvertTo-Json -Compress))
}

try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $methods = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 }
    $asTask = ($methods | Where-Object { $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
    $asTaskNoResult = ($methods | Where-Object { $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction' })[0]
    function Wait-Result($operation, [Type]$type) {
        $task = $asTask.MakeGenericMethod($type).Invoke($null, @($operation))
        $task.Wait(-1) | Out-Null
        $task.Result
    }
    function Wait-Done($action) {
        $task = $asTaskNoResult.Invoke($null, @($action))
        $task.Wait(-1) | Out-Null
    }
    [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime] | Out-Null
    [Windows.Data.Pdf.PdfDocument, Windows.Data.Pdf, ContentType = WindowsRuntime] | Out-Null
    [Windows.Storage.Streams.InMemoryRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime] | Out-Null
    [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime] | Out-Null
    [Windows.Graphics.Imaging.BitmapEncoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime] | Out-Null

    # The width is asked in screen points, which Windows enlarges by the display scale (175 % here gives 1.75
    # pixels a point): a first small drawing measures that scale, the second one has the pixels asked for.
    function Draw-Page($sheet, [double]$width) {
        $options = New-Object Windows.Data.Pdf.PdfPageRenderOptions
        $options.DestinationWidth = [uint32][Math]::Max(1, [Math]::Round($width))
        $options.BitmapEncoderId = [Windows.Graphics.Imaging.BitmapEncoder]::JpegEncoderId
        $stream = New-Object Windows.Storage.Streams.InMemoryRandomAccessStream
        Wait-Done ($sheet.RenderToStreamAsync($stream, $options))
        $stream
    }
    function Get-Pixels($stream) {
        $decoder = Wait-Result ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        @([int]$decoder.PixelWidth, [int]$decoder.PixelHeight)
    }

    $file = Wait-Result ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Pdf)) ([Windows.Storage.StorageFile])
    $document = Wait-Result ([Windows.Data.Pdf.PdfDocument]::LoadFromFileAsync($file)) ([Windows.Data.Pdf.PdfDocument])
    $count = [int]$document.PageCount
    if ($Page -lt 1 -or $Page -gt $count) {
        Say @{ ok = $false; reason = 'page'; pages = $count }
        exit 0
    }
    $sheet = $document.GetPage([uint32]($Page - 1))
    $scale = (Get-Pixels (Draw-Page $sheet 200))[0] / 200.0
    $size = $sheet.Size
    $longest = [Math]::Max([double]$size.Width, [double]$size.Height)
    $stream = Draw-Page $sheet ($Side * [double]$size.Width / $longest / $scale)
    $pixels = Get-Pixels $stream
    $reader = [System.IO.WindowsRuntimeStreamExtensions]::AsStreamForRead($stream.GetInputStreamAt(0))
    $target = [System.IO.File]::Create($Image)
    try { $reader.CopyTo($target) } finally { $target.Close() }
    Say @{ ok = $true; pages = $count; width = $pixels[0]; height = $pixels[1] }
} catch {
    $failure = $_.Exception      # the real reason is under the wrappers PowerShell and the waiting add
    while ($failure.InnerException) { $failure = $failure.InnerException }
    Say @{ ok = $false; reason = 'windows'; detail = "$($failure.Message)".Trim() }
}
