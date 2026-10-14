# =====================================================================
#  forex-ai :: instalador + lanzador para Windows
#  Se ejecuta con doble clic en iniciar.bat (o: powershell -File iniciar.ps1)
#  Es seguro repetirlo: lo que ya esta listo se omite.
# =====================================================================
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$Root = $PSScriptRoot
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$LiveValue = "YES_I_ACCEPT_THE_RISK"

function Titulo($t) { Write-Host ""; Write-Host "=== $t ===" -ForegroundColor Cyan }
function Ok($t)     { Write-Host "  [OK] $t" -ForegroundColor Green }
function Aviso($t)  { Write-Host "  [!]  $t" -ForegroundColor Yellow }
function Falla($t)  { Write-Host "  [X]  $t" -ForegroundColor Red }
function Salir($code) { Write-Host ""; Read-Host "Presiona Enter para cerrar"; exit $code }
function Preguntar($texto, $defecto) {
    # defecto = "S" o "N". Devuelve $true si la respuesta es si.
    $hint = if ($defecto -eq "S") { "[S/n]" } else { "[s/N]" }
    $r = (Read-Host "$texto $hint").Trim().ToLower()
    if ($r -eq "") { return ($defecto -eq "S") }
    return ($r -eq "s" -or $r -eq "si" -or $r -eq "y" -or $r -eq "yes")
}

Write-Host ""
Write-Host "  forex-ai  -  configuracion e inicio" -ForegroundColor White
Write-Host "  Carpeta: $Root"

# ---------------------------------------------------------------- permisos
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($isAdmin) {
    Aviso "Esta ventana se abrio como ADMINISTRADOR. MT5 normalmente corre como usuario normal y"
    Aviso "esa diferencia de permisos causa el error 'IPC timeout'. Cierra esta ventana y abre iniciar.bat con doble clic normal."
    if (-not (Preguntar "Continuar de todos modos?" "N")) { Salir 1 }
}

# ================================================================ 1. PYTHON
Titulo "1/7  Python"
function Get-PythonInfo([string[]]$cmd) {
    try {
        $exe = $cmd[0]
        $rest = @()
        if ($cmd.Length -gt 1) { $rest = $cmd[1..($cmd.Length - 1)] }
        $out = & $exe @rest -c "import sys,struct;print(sys.version_info[0]*100+sys.version_info[1]);print(struct.calcsize('P')*8);print(sys.base_prefix)" 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
        return @{ Ver = [int]$out[0]; Bits = [int]$out[1]; Prefix = [string]$out[2]; Cmd = $cmd }
    } catch { return $null }
}
function Test-PythonValido($info) {
    if ($null -eq $info) { return $false }
    if ($info.Prefix -like "*WindowsApps*") { return $false }   # Python de la Microsoft Store: rompe MT5
    return ($info.Bits -eq 64 -and $info.Ver -ge 310 -and $info.Ver -le 312)
}
function Find-Python {
    $candidatos = @(
        @("py", "-3.12"), @("py", "-3.11"), @("py", "-3.10"),
        @("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"),
        @("python")
    )
    foreach ($c in $candidatos) {
        $i = Get-PythonInfo $c
        if (Test-PythonValido $i) { return $i }
    }
    return $null
}

$py = Find-Python
if ($null -eq $py) {
    Aviso "No encontre Python 3.10-3.12 de 64 bits (el de la Microsoft Store no sirve)."
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget -and (Preguntar "Instalo Python 3.12 ahora con winget?" "S")) {
        & winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
        $py = Find-Python
    }
}
if ($null -eq $py) {
    Falla "No se pudo preparar Python. Instala Python 3.12 (64 bits) desde https://www.python.org/downloads/"
    Falla "marcando 'Add Python to PATH', y vuelve a abrir iniciar.bat."
    Salir 1
}
Ok ("Python {0}.{1} de 64 bits" -f [math]::Floor($py.Ver / 100), ($py.Ver % 100))

# ================================================================ 2. ENTORNO
Titulo "2/7  Entorno virtual y librerias"
$venvPy = Join-Path $Root ".venv\Scripts\python.exe"
if (Test-Path $venvPy) {
    $vi = Get-PythonInfo @($venvPy)
    if (-not (Test-PythonValido $vi)) {
        Aviso "El entorno .venv existente usa un Python no valido. Lo recreo."
        Remove-Item -Recurse -Force (Join-Path $Root ".venv")
    }
}
if (-not (Test-Path $venvPy)) {
    $exe = $py.Cmd[0]
    $rest = @()
    if ($py.Cmd.Length -gt 1) { $rest = $py.Cmd[1..($py.Cmd.Length - 1)] }
    & $exe @rest -m venv (Join-Path $Root ".venv")
    if (-not (Test-Path $venvPy)) { Falla "No se pudo crear el entorno virtual."; Salir 1 }
    Ok "Entorno .venv creado"
} else { Ok "Entorno .venv ya existe" }

& $venvPy -m pip install --upgrade pip -q 2>$null
& $venvPy -m pip install -r (Join-Path $Root "requirements.txt") -q
if ($LASTEXITCODE -ne 0) {
    Falla "Fallo la instalacion de librerias (revisa tu internet) y vuelve a intentar."
    Salir 1
}
& $venvPy -c "import MetaTrader5" 2>$null
if ($LASTEXITCODE -ne 0) { Aviso "La libreria MetaTrader5 no se pudo importar (solo funcionara el modo simulado)." }
else { Ok "Librerias instaladas (pandas, scikit-learn, MetaTrader5...)" }

# ================================================================ 3. .ENV
Titulo "3/7  Credenciales y configuracion (.env)"
function Read-EnvFile($path) {
    $h = [ordered]@{}
    if (Test-Path $path) {
        foreach ($line in (Get-Content $path -Encoding UTF8)) {
            $l = $line.Trim().TrimStart([char]0xFEFF)
            if ($l -eq "" -or $l.StartsWith("#") -or -not $l.Contains("=")) { continue }
            $i = $l.IndexOf("=")
            $h[$l.Substring(0, $i).Trim()] = $l.Substring($i + 1).Trim().Trim('"').Trim("'")
        }
    }
    return $h
}
function Write-EnvFile($path, $h) {
    $lines = @("# Generado por iniciar.ps1. NUNCA subas este archivo a Git ni lo compartas.")
    foreach ($k in $h.Keys) { $lines += ("{0}={1}" -f $k, $h[$k]) }
    $texto = ($lines -join "`r`n") + "`r`n"
    [System.IO.File]::WriteAllText($path, $texto, (New-Object System.Text.UTF8Encoding($false)))
}

$envPath = Join-Path $Root ".env"
$cfg = Read-EnvFile $envPath
foreach ($k in @("MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER", "MT5_PATH", "ALLOW_LIVE_TRADING", "MARKET_SYMBOL", "MARKET_TIMEFRAME")) {
    if (-not $cfg.Contains($k)) { $cfg[$k] = "" }
}

$tieneCuenta = ($cfg["MT5_LOGIN"] -ne "" -and $cfg["MT5_PASSWORD"] -ne "" -and $cfg["MT5_SERVER"] -ne "")
if ($tieneCuenta) {
    Write-Host ("  Cuenta: {0} | Servidor: {1} | Contrasena: guardada" -f $cfg["MT5_LOGIN"], $cfg["MT5_SERVER"])
    if (-not (Preguntar "Usar estos datos?" "S")) { $tieneCuenta = $false }
}
if (-not $tieneCuenta) {
    $cfg["MT5_LOGIN"]  = (Read-Host "  Numero de cuenta MT5 (login)").Trim()
    $cfg["MT5_SERVER"] = (Read-Host "  Servidor exacto (ej: XMGlobal-MT5 20)").Trim()
    $sec = Read-Host "  Contrasena de MT5 (no se vera al escribir)" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
    $cfg["MT5_PASSWORD"] = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}
if ($cfg["MARKET_SYMBOL"] -eq "")    { $cfg["MARKET_SYMBOL"] = "EURUSD" }
if ($cfg["MARKET_TIMEFRAME"] -eq "") { $cfg["MARKET_TIMEFRAME"] = "H1" }
$ent = (Read-Host ("  Par a operar [Enter = {0}] (nombre EXACTO de tu broker)" -f $cfg["MARKET_SYMBOL"])).Trim()
if ($ent -ne "") { $cfg["MARKET_SYMBOL"] = $ent }
$ent = (Read-Host ("  Timeframe M5/M15/M30/H1/H4 [Enter = {0}]" -f $cfg["MARKET_TIMEFRAME"])).Trim().ToUpper()
if ($ent -ne "") { $cfg["MARKET_TIMEFRAME"] = $ent }
Write-EnvFile $envPath $cfg
Ok ".env guardado (la contrasena solo existe en ese archivo)"
$Symbol = $cfg["MARKET_SYMBOL"]
$Tf = $cfg["MARKET_TIMEFRAME"]

# ================================================================ 4. MT5
Titulo "4/7  MetaTrader 5"
function Find-Mt5Paths {
    $found = @()
    foreach ($p in (Get-Process -Name terminal64 -ErrorAction SilentlyContinue)) {
        try { if ($p.Path) { $found += $p.Path } } catch {}
    }
    foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, "$env:LOCALAPPDATA\Programs")) {
        if ($base -and (Test-Path $base)) {
            Get-ChildItem $base -Directory -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -match "MT5|MetaTrader|XM" } |
                ForEach-Object {
                    $exe = Join-Path $_.FullName "terminal64.exe"
                    if (Test-Path $exe) { $found += $exe }
                }
        }
    }
    return @($found | Select-Object -Unique)
}

$mt5Path = $cfg["MT5_PATH"]
if ($mt5Path -eq "" -or -not (Test-Path $mt5Path)) {
    $paths = Find-Mt5Paths
    if ($paths.Count -eq 1) { $mt5Path = $paths[0] }
    elseif ($paths.Count -gt 1) {
        Write-Host "  Encontre varios MetaTrader instalados:"
        for ($i = 0; $i -lt $paths.Count; $i++) { Write-Host ("   {0}) {1}" -f ($i + 1), $paths[$i]) }
        $n = 0
        while ($n -lt 1 -or $n -gt $paths.Count) { [void][int]::TryParse((Read-Host "  Cual usas? (numero)"), [ref]$n) }
        $mt5Path = $paths[$n - 1]
    } else {
        $mt5Path = (Read-Host "  No lo encontre. Pega la ruta completa de terminal64.exe (o Enter para omitir)").Trim().Trim('"')
    }
}
if ($mt5Path -ne "" -and (Test-Path $mt5Path)) {
    $cfg["MT5_PATH"] = $mt5Path
    Write-EnvFile $envPath $cfg
    Ok "MT5: $mt5Path"
} else {
    Aviso "Sin ruta de MT5: solo estara disponible el modo simulado."
    $mt5Path = ""
}

function Start-Mt5 {
    if ($mt5Path -eq "") { return }
    if (-not (Get-Process -Name terminal64 -ErrorAction SilentlyContinue)) {
        Aviso "Abriendo MetaTrader 5..."
        Start-Process $mt5Path | Out-Null
        Start-Sleep -Seconds 15
    }
}

$mt5Ok = $false
if ($mt5Path -ne "") {
    Start-Mt5
    Write-Host ""
    Write-Host "  En la ventana de MT5, comprueba que:" -ForegroundColor White
    Write-Host "   - Iniciaste sesion en tu cuenta (abajo a la derecha: barritas verdes con KB)."
    Write-Host "   - El boton 'Algo Trading' de arriba esta en VERDE."
    Write-Host "   - No hay cuadros de dialogo abiertos."
    [void](Read-Host "  Cuando este listo presiona Enter")

    Titulo "5/7  Probando la conexion con MT5"
    & $venvPy (Join-Path $Root "check_mt5.py")
    $mt5Ok = ($LASTEXITCODE -eq 0)

    if (-not $mt5Ok) {
        Aviso "No conecto. Causa tipica: procesos de MT5 colgados por intentos anteriores."
        if (Preguntar "Cierro MT5, lo vuelvo a abrir y reintento?" "S") {
            Get-Process -Name terminal64, metaeditor64 -ErrorAction SilentlyContinue | Stop-Process -Force
            Start-Sleep -Seconds 4
            Start-Mt5
            Write-Host "  Espera a que MT5 cargue e inicie sesion..."
            Start-Sleep -Seconds 20
            [void](Read-Host "  Si MT5 pide login, inicia sesion. Cuando este conectado presiona Enter")
            & $venvPy (Join-Path $Root "check_mt5.py")
            $mt5Ok = ($LASTEXITCODE -eq 0)
        }
    }
    if (-not $mt5Ok) {
        Falla "Python no logra comunicarse con ese MetaTrader 5."
        Write-Host "   Opciones:" -ForegroundColor Yellow
        Write-Host "   a) Instala el MetaTrader 5 oficial de MetaQuotes (metatrader5.com), inicia sesion con tu"
        Write-Host "      cuenta de XM y vuelve a ejecutar iniciar.bat (detectara el nuevo terminal)."
        Write-Host "   b) Pregunta al soporte de XM si su terminal permite conexion con Python en tu cuenta."
        Write-Host "   Mientras tanto puedes usar el modo simulado (no toca tu dinero)."
    } else { Ok "Conexion con MT5 funcionando" }
} else {
    Titulo "5/7  Conexion con MT5"
    Aviso "Omitido (sin ruta de MT5)."
}

# ================================================================ 6. TESTS
Titulo "6/7  Pruebas automaticas del proyecto"
& $venvPy -m pytest -q -p no:cacheprovider
if ($LASTEXITCODE -ne 0) { Aviso "Algunas pruebas fallaron. Puedes continuar, pero avisame con el mensaje de arriba." }
else { Ok "Todas las pruebas pasaron" }

# ================================================================ 7. DATOS + MODELO
Titulo "7/7  Datos, entrenamiento y backtest"
$csv = Join-Path $Root "data\market_data.csv"
$modelo = Join-Path $Root "models\forex_model.joblib"
$datosReales = $false
$btRetorno = $null

$hacer = $true
if ((Test-Path $modelo) -and (Test-Path $csv) -and $mt5Ok) {
    $hacer = Preguntar "Ya hay modelo entrenado. Descargo datos nuevos y reentreno?" "N"
} elseif ((Test-Path $modelo) -and (-not $mt5Ok)) {
    $hacer = $false
}
if ($hacer) {
    if ($mt5Ok) {
        & $venvPy (Join-Path $Root "app.py") download --source mt5 --symbol $Symbol --timeframe $Tf
        if ($LASTEXITCODE -eq 0) { $datosReales = $true }
        else { Aviso "No pude descargar datos de MT5; uso datos SINTETICOS (solo para probar, no son el mercado real)." }
    }
    if (-not $datosReales) {
        & $venvPy (Join-Path $Root "app.py") download --source synthetic
    }
    & $venvPy (Join-Path $Root "app.py") train --symbol $Symbol --timeframe $Tf
    if ($LASTEXITCODE -ne 0) { Falla "El entrenamiento fallo."; Salir 1 }
} else {
    Ok "Se reutiliza el modelo y los datos existentes"
    $datosReales = $mt5Ok
}

Write-Host ""
Write-Host "  Backtest fuera de muestra (30% mas reciente de los datos):" -ForegroundColor White
$btOut = @()
& $venvPy (Join-Path $Root "app.py") backtest --mode hybrid --symbol $Symbol --timeframe $Tf 2>&1 | ForEach-Object { $btOut += "$_"; Write-Host $_ }
$m = [regex]::Match(($btOut -join "`n"), "Retorno total \(%\)\s+(-?[\d\.]+)")
if ($m.Success) { $btRetorno = [double]::Parse($m.Groups[1].Value, [Globalization.CultureInfo]::InvariantCulture) }

if (-not $datosReales) {
    Aviso "Los resultados de arriba usan datos SINTETICOS: NO dicen nada sobre el mercado real."
}

# ================================================================ MENU
function Ejecutar-App($argsApp) {
    & $venvPy (Join-Path $Root "app.py") @argsApp
}

while ($true) {
    Write-Host ""
    Write-Host "================ QUE QUIERES HACER? ================" -ForegroundColor Cyan
    Write-Host "  1) Simular (paper): dinero ficticio, sin tocar MT5"
    if ($mt5Ok) {
        Write-Host "  2) Operar en MT5 con cuenta DEMO"
        Write-Host "  3) Operar en MT5 con DINERO REAL"
    } else {
        Write-Host "  2) (no disponible: MT5 sin conexion)" -ForegroundColor DarkGray
        Write-Host "  3) (no disponible: MT5 sin conexion)" -ForegroundColor DarkGray
    }
    Write-Host "  4) Ver las ultimas operaciones registradas"
    Write-Host "  5) Salir"
    $op = (Read-Host "  Opcion").Trim()

    if ($op -eq "1") {
        Ejecutar-App @("demo", "--broker", "paper", "--mode", "hybrid", "--symbol", $Symbol, "--timeframe", $Tf)
    }
    elseif ($op -eq "2" -and $mt5Ok) {
        Write-Host "  Bot en cuenta DEMO. Ctrl+C para detener." -ForegroundColor Yellow
        Ejecutar-App @("demo", "--broker", "mt5", "--mode", "hybrid", "--symbol", $Symbol, "--timeframe", $Tf)
    }
    elseif ($op -eq "3" -and $mt5Ok) {
        Write-Host ""
        Write-Host "  !!! DINERO REAL !!!" -ForegroundColor Red
        if (-not $datosReales) {
            Falla "El modelo se entreno con datos sinteticos. No se permite operar real asi."
            continue
        }
        if ($null -ne $btRetorno -and $btRetorno -le 0) {
            Aviso ("El backtest con datos reales dio {0}% de retorno (no es positivo): la estrategia no mostro ventaja." -f $btRetorno)
            $c = (Read-Host "  Escribe CONTINUAR si aun asi quieres seguir (Enter para cancelar)").Trim()
            if ($c -ne "CONTINUAR") { continue }
        }
        if ($cfg["ALLOW_LIVE_TRADING"] -ne $LiveValue) {
            Write-Host "  El modo real esta bloqueado en tu .env (es una proteccion a proposito)."
            $c = (Read-Host "  Escribe HABILITAR para desbloquearlo (Enter para cancelar)").Trim()
            if ($c -ne "HABILITAR") { continue }
            $cfg["ALLOW_LIVE_TRADING"] = $LiveValue
            Write-EnvFile $envPath $cfg
        }
        Write-Host "  Limites en real: 0.25% de riesgo por operacion, 1% perdida diaria, 5% drawdown max, 0.5 lotes max."
        Write-Host "  El programa te pedira ademas escribir la frase de confirmacion. Ctrl+C para detener."
        Ejecutar-App @("demo", "--broker", "mt5", "--live", "--mode", "hybrid", "--symbol", $Symbol, "--timeframe", $Tf)
    }
    elseif ($op -eq "4") {
        $t = Join-Path $Root "logs\trades.csv"
        if (Test-Path $t) { Import-Csv $t | Select-Object -Last 15 | Format-Table source, symbol, side, lots, pnl, exit_reason, exit_time -AutoSize }
        else { Write-Host "  Aun no hay operaciones." }
    }
    elseif ($op -eq "5") { break }
    else { Write-Host "  Opcion no valida." }
}
Write-Host ""
Write-Host "  Registros: logs\app.log y logs\trades.csv"
Salir 0
