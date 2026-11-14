$ErrorActionPreference = 'Stop'
$work = $null
$code = 1
try {
    Add-Type -AssemblyName System.IO.Compression
    function Read-Checked([string]$path, [string]$sha) {
        $bytes = [IO.File]::ReadAllBytes($path)
        $hasher = [Security.Cryptography.SHA256]::Create()
        $hash = [BitConverter]::ToString($hasher.ComputeHash($bytes))
        $hash = $hash.Replace('-', '').ToLowerInvariant()
        if ($hash -ne $sha) { throw "$path is not the file that was downloaded" }
        return ,$bytes
    }
    function Read-Entry([byte[]]$zip, [string]$name) {
        $stream = New-Object IO.MemoryStream -ArgumentList @(, $zip)
        $archive = New-Object IO.Compression.ZipArchive -ArgumentList @(, $stream)
        try {
            $entry = $archive.GetEntry($name)
            if ($null -eq $entry) { throw "$name is not in its package" }
            $out = New-Object IO.MemoryStream
            $in = $entry.Open()
            try { $in.CopyTo($out) } finally { $in.Dispose() }
            [byte[]]$data = $out.ToArray()
            return ,$data
        } finally { $archive.Dispose() }
    }
    $nefconZip = Read-Checked {NEFCON} {NEFCON_SHA}
    $driverZip = Read-Checked {PACKAGE} {PACKAGE_SHA}
    $settings = Read-Checked {SETTINGS} {SETTINGS_SHA}
    $work = Join-Path $env:ProgramData ('LibrePanel-vdd-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $work | Out-Null
    $admins, $system = '*S-1-5-32-544:(OI)(CI)F', '*S-1-5-18:(OI)(CI)F'
    & icacls.exe $work /inheritance:r /grant:r $admins $system | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'the folder for the driver could not be closed to others' }
    $nefconExe = Join-Path $work 'nefconc.exe'
    [IO.File]::WriteAllBytes($nefconExe, (Read-Entry $nefconZip {NEFCON_PROGRAM}))
    foreach ($name in 'MttVDD.inf', 'MttVDD.dll', 'mttvdd.cat') {
        $bytes = Read-Entry $driverZip ('VirtualDisplayDriver/' + $name)
        [IO.File]::WriteAllBytes((Join-Path $work $name), $bytes)
    }
    $x509 = 'System.Security.Cryptography.X509Certificates'
    $certificates = New-Object "$x509.X509Certificate2Collection"
    [byte[]]$catalog = Read-Entry $driverZip 'VirtualDisplayDriver/mttvdd.cat'
    $certificates.Import($catalog)
    $basic = [type]"$x509.X509BasicConstraintsExtension"
    $store = New-Object "$x509.X509Store" -ArgumentList 'TrustedPublisher', 'LocalMachine'
    $store.Open('ReadWrite')
    try {
        foreach ($certificate in $certificates) {
            $authority = $false
            foreach ($extension in $certificate.Extensions) {
                $isAuthority = $extension -is $basic -and $extension.CertificateAuthority
                if ($isAuthority) { $authority = $true }
            }
            if (-not $authority) { $store.Add($certificate) }
        }
    } finally { $store.Close() }
    $target = {TARGET}
    New-Item -ItemType Directory -Force -Path $target | Out-Null
    [IO.File]::WriteAllBytes((Join-Path $target 'vdd_settings.xml'), $settings)
    $inf = Join-Path $work 'MttVDD.inf'
    & $nefconExe install $inf {HARDWARE_ID} --no-duplicates --remove-duplicates | Out-Null
    $code = $LASTEXITCODE
    [IO.File]::WriteAllText({SAID}, [string]$code)
} catch {
    $code = 1
    $line = $_.InvocationInfo.ScriptLineNumber  # (where: for whoever looks into it)
    [IO.File]::WriteAllText({SAID}, "error: $($_.Exception.Message) (line $line)")
} finally {
    if ($work) { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }
}
exit $code
