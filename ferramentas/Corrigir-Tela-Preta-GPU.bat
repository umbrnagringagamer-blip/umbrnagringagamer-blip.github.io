<# : Bloco em lote (cmd). O PowerShell ve isto como comentario.
@echo off
set "SELF=%~f0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "iex ([IO.File]::ReadAllText($env:SELF))"
exit /b
#>
# ======================================================================
#  Corrigir-Tela-Preta-GPU  -  para RTX 4070 SUPER com "BusReset TDR"
#  Tudo e reversivel (opcao 6). Log em C:\ProgramData\CorrecaoGPU\log.txt
# ======================================================================

$ErrorActionPreference = 'Continue'

# --- Pede permissao de administrador ---
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not ([Security.Principal.WindowsPrincipal]$id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process -FilePath 'cmd.exe' -ArgumentList "/c `"`"$env:SELF`"`"" -Verb RunAs
    exit
}

$Pasta = 'C:\ProgramData\CorrecaoGPU'
New-Item -ItemType Directory -Force -Path $Pasta | Out-Null
Start-Transcript -Path "$Pasta\log.txt" -Append | Out-Null

$RegGPU   = 'HKLM:\SYSTEM\CurrentControlSet\Control\GraphicsDrivers'
$RegPower = 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power'
$Tarefa   = 'CorrecaoGPU-LimiteEnergia'
$Smi      = (Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue).Source
if (-not $Smi -and (Test-Path "$env:WINDIR\System32\nvidia-smi.exe")) { $Smi = "$env:WINDIR\System32\nvidia-smi.exe" }

function Ok($t)    { Write-Host "  [OK] $t" -ForegroundColor Green }
function Aviso($t) { Write-Host "  [!]  $t" -ForegroundColor Yellow }
function Pausa     { Write-Host ''; Read-Host '  Aperte ENTER para voltar ao menu' | Out-Null }

function Ler-ASPM {
    $txt = (powercfg /query SCHEME_CURRENT SUB_PCIEXPRESS ASPM) -join "`n"
    $m = [regex]::Matches($txt, '0x([0-9a-fA-F]{8})')
    if ($m.Count -ge 2) {
        return @([Convert]::ToInt32($m[$m.Count-2].Groups[1].Value,16), [Convert]::ToInt32($m[$m.Count-1].Groups[1].Value,16))
    }
    return $null
}

function Relatorio {
    Write-Host "`n  Lendo os registros do Windows (ultimos 30 dias)..." -ForegroundColor Cyan
    $desde = (Get-Date).AddDays(-30)
    $buscas = @(
        @{ Nome='Placa de video reiniciada/travou (nvlddmkm)'; F=@{LogName='System'; ProviderName='nvlddmkm'; StartTime=$desde} },
        @{ Nome='Driver de video parou de responder (Display 4101)'; F=@{LogName='System'; ProviderName='Display'; Id=4101; StartTime=$desde} },
        @{ Nome='Desligamento inesperado (Kernel-Power 41)'; F=@{LogName='System'; ProviderName='Microsoft-Windows-Kernel-Power'; Id=41; StartTime=$desde} },
        @{ Nome='Tela azul (BugCheck 1001)'; F=@{LogName='System'; Id=1001; StartTime=$desde} },
        @{ Nome='Erros WHEA (hardware)'; F=@{LogName='System'; ProviderName='Microsoft-Windows-WHEA-Logger'; StartTime=$desde} }
    )
    $saida = @()
    $saida += "RELATORIO GPU - $(Get-Date -Format 'dd/MM/yyyy HH:mm')"
    $saida += "Computador: $env:COMPUTERNAME"
    $saida += ''
    foreach ($b in $buscas) {
        $ev = @(Get-WinEvent -FilterHashtable $b.F -ErrorAction SilentlyContinue)
        $saida += "== $($b.Nome): $($ev.Count) evento(s)"
        $ev | Group-Object { $_.TimeCreated.ToString('yyyy-MM-dd') } | Sort-Object Name -Descending |
            ForEach-Object { $saida += "   $($_.Name): $($_.Count)" }
        $ev | Select-Object -First 5 | ForEach-Object {
            $msg = ($_.Message -replace '\s+',' ')
            if ($msg.Length -gt 160) { $msg = $msg.Substring(0,160) + '...' }
            $saida += "   - $($_.TimeCreated.ToString('dd/MM HH:mm'))  $msg"
        }
        $saida += ''
    }
    $saida += '== Placa de video'
    Get-CimInstance Win32_VideoController | ForEach-Object { $saida += "   $($_.Name)  driver $($_.DriverVersion)  ($($_.DriverDate))" }
    if ($Smi) {
        $saida += ''
        $saida += '== nvidia-smi'
        $saida += (& $Smi --query-gpu=name,driver_version,temperature.gpu,power.draw,power.limit,power.default_limit,clocks.gr,clocks.mem --format=csv 2>&1)
    }
    $saida += ''
    $saida += '== Correcoes deste programa'
    $saida += "   TdrDelay: $((Get-ItemProperty $RegGPU -ErrorAction SilentlyContinue).TdrDelay)"
    $a = Ler-ASPM; $saida += "   PCIe ASPM (tomada/bateria): $($a -join ' / ')   (0 = desligado)"
    $saida += "   Inicializacao rapida: $((Get-ItemProperty $RegPower -ErrorAction SilentlyContinue).HiberbootEnabled)   (0 = desligada)"
    $saida += "   Tarefa de limite de energia: $([bool](Get-ScheduledTask -TaskName $Tarefa -ErrorAction SilentlyContinue))"

    $arq = Join-Path ([Environment]::GetFolderPath('Desktop')) ("Relatorio-GPU-{0}.txt" -f (Get-Date -Format 'yyyyMMdd-HHmm'))
    $saida | Out-File -FilePath $arq -Encoding UTF8
    $saida | ForEach-Object { Write-Host "  $_" }
    Ok "Relatorio salvo em: $arq"
}

function Aplicar {
    Write-Host "`n  Aplicando correcoes seguras..." -ForegroundColor Cyan

    # 1. Ponto de restauracao
    try {
        Enable-ComputerRestore -Drive "$env:SystemDrive\" -ErrorAction SilentlyContinue
        Checkpoint-Computer -Description 'Antes da CorrecaoGPU' -RestorePointType MODIFY_SETTINGS -ErrorAction Stop -WarningAction Stop
        Ok 'Ponto de restauracao criado'
    } catch { Aviso "Nao foi possivel criar ponto de restauracao (o Windows so deixa 1 a cada 24h). Seguindo com backup proprio." }

    # 2. Backup do estado atual (so na primeira vez, para o Desfazer voltar ao original)
    if (-not (Test-Path "$Pasta\estado-original.json")) {
        $g = Get-ItemProperty $RegGPU -ErrorAction SilentlyContinue
        $p = Get-ItemProperty $RegPower -ErrorAction SilentlyContinue
        $a = Ler-ASPM
        [pscustomobject]@{
            TdrDelay = $g.TdrDelay; TdrDdiDelay = $g.TdrDdiDelay
            HiberbootEnabled = $p.HiberbootEnabled
            AspmAC = $(if ($a) { $a[0] } else { 1 }); AspmDC = $(if ($a) { $a[1] } else { 1 })
        } | ConvertTo-Json | Out-File "$Pasta\estado-original.json" -Encoding UTF8
        reg export 'HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers' "$Pasta\backup-GraphicsDrivers.reg" /y | Out-Null
        Ok "Backup salvo em $Pasta"
    }

    # 3. Desliga a economia de energia do PCIe (causa comum de a placa "sumir" do barramento)
    powercfg /setacvalueindex SCHEME_CURRENT SUB_PCIEXPRESS ASPM 0 | Out-Null
    powercfg /setdcvalueindex SCHEME_CURRENT SUB_PCIEXPRESS ASPM 0 | Out-Null
    powercfg /setactive SCHEME_CURRENT | Out-Null
    Ok 'Economia de energia do PCIe (ASPM) desligada'

    # 4. Da mais tempo para a placa responder antes do Windows reinicia-la
    Set-ItemProperty -Path $RegGPU -Name TdrDelay    -Value 10 -Type DWord
    Set-ItemProperty -Path $RegGPU -Name TdrDdiDelay -Value 20 -Type DWord
    Ok 'Tempo limite da placa (TdrDelay) aumentado de 2s para 10s'

    # 5. Desliga a inicializacao rapida (ajuda na tela preta antes da senha)
    Set-ItemProperty -Path $RegPower -Name HiberbootEnabled -Value 0 -Type DWord
    Ok 'Inicializacao rapida desligada'

    # 6. Limita a energia da placa a 85% (reduz picos que derrubam a fonte)
    if ($Smi) {
        $script = @"
`$smi = '$Smi'
`$d = (& `$smi --query-gpu=power.default_limit --format=csv,noheader,nounits | Select-Object -First 1).Trim()
`$w = [math]::Round([double]::Parse(`$d, [Globalization.CultureInfo]::InvariantCulture) * 0.85)
& `$smi -pl `$w
"@
        $script | Out-File "$Pasta\limite-energia.ps1" -Encoding ASCII
        $res = & powershell -NoProfile -ExecutionPolicy Bypass -File "$Pasta\limite-energia.ps1" 2>&1
        Write-Host "     $($res -join ' ')"
        $acao = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Pasta\limite-energia.ps1`""
        $gat  = New-ScheduledTaskTrigger -AtStartup
        $gat.Delay = 'PT30S'
        Register-ScheduledTask -TaskName $Tarefa -Action $acao -Trigger $gat -User 'SYSTEM' -RunLevel Highest -Force | Out-Null
        Ok 'Limite de energia da placa em 85% (reaplicado a cada inicializacao)'
    } else {
        Aviso 'nvidia-smi nao encontrado: limite de energia nao aplicado. Use o MSI Afterburner (Power Limit 85%).'
    }

    Write-Host ''
    Aviso 'REINICIE o computador para tudo valer.'
    Aviso 'Isto reduz o problema, mas NAO conserta cabo solto ou fonte fraca. Confira os cabos!'
}

function Desfazer {
    Write-Host "`n  Desfazendo as correcoes..." -ForegroundColor Cyan
    $o = $null
    if (Test-Path "$Pasta\estado-original.json") { $o = Get-Content "$Pasta\estado-original.json" -Raw | ConvertFrom-Json }

    foreach ($n in 'TdrDelay','TdrDdiDelay') {
        if ($o -and $null -ne $o.$n) { Set-ItemProperty -Path $RegGPU -Name $n -Value $o.$n -Type DWord }
        else { Remove-ItemProperty -Path $RegGPU -Name $n -ErrorAction SilentlyContinue }
    }
    Ok 'TdrDelay restaurado'

    $hb = if ($o -and $null -ne $o.HiberbootEnabled) { $o.HiberbootEnabled } else { 1 }
    Set-ItemProperty -Path $RegPower -Name HiberbootEnabled -Value $hb -Type DWord
    Ok 'Inicializacao rapida restaurada'

    $ac = if ($o) { $o.AspmAC } else { 1 }; $dc = if ($o) { $o.AspmDC } else { 1 }
    powercfg /setacvalueindex SCHEME_CURRENT SUB_PCIEXPRESS ASPM $ac | Out-Null
    powercfg /setdcvalueindex SCHEME_CURRENT SUB_PCIEXPRESS ASPM $dc | Out-Null
    powercfg /setactive SCHEME_CURRENT | Out-Null
    Ok 'ASPM do PCIe restaurado'

    Unregister-ScheduledTask -TaskName $Tarefa -Confirm:$false -ErrorAction SilentlyContinue
    if ($Smi) {
        $d = (& $Smi --query-gpu=power.default_limit --format=csv,noheader,nounits | Select-Object -First 1).Trim()
        & $Smi -pl ([math]::Round([double]::Parse($d, [Globalization.CultureInfo]::InvariantCulture))) | Out-Null
    }
    Ok 'Limite de energia da placa voltou ao padrao'
    Remove-Item "$Pasta\estado-original.json" -ErrorAction SilentlyContinue
    Aviso 'Reinicie o computador.'
}

function Reparar-Windows {
    Write-Host "`n  Verificando arquivos do Windows (pode levar 15-30 min)..." -ForegroundColor Cyan
    DISM /Online /Cleanup-Image /RestoreHealth
    sfc /scannow
    Ok 'Verificacao concluida'
}

while ($true) {
    Clear-Host
    Write-Host ''
    Write-Host '  ==========================================================' -ForegroundColor Cyan
    Write-Host '   CORRECAO TELA PRETA / PLACA DE VIDEO TRAVANDO (NVIDIA)'     -ForegroundColor Cyan
    Write-Host '  ==========================================================' -ForegroundColor Cyan
    Write-Host ''
    Write-Host '   1  Aplicar correcoes seguras (RECOMENDADO)'
    Write-Host '        - ponto de restauracao e backup'
    Write-Host '        - desliga economia de energia do PCIe'
    Write-Host '        - aumenta o tempo limite da placa (TDR)'
    Write-Host '        - desliga inicializacao rapida'
    Write-Host '        - limita a energia da placa a 85%'
    Write-Host '   2  Gerar relatorio dos travamentos (salva na Area de Trabalho)'
    Write-Host '   3  Reparar arquivos do Windows (DISM + SFC)'
    Write-Host '   4  Testar a memoria RAM (reinicia o PC)'
    Write-Host '   5  Abrir paginas para baixar driver NVIDIA e DDU'
    Write-Host '   6  DESFAZER tudo que este programa mudou'
    Write-Host '   0  Sair'
    Write-Host ''
    Write-Host '   Dica: com a tela preta, aperte Win + Ctrl + Shift + B' -ForegroundColor DarkGray
    Write-Host ''
    switch (Read-Host '  Escolha') {
        '1' { Aplicar; Pausa }
        '2' { Relatorio; Pausa }
        '3' { Reparar-Windows; Pausa }
        '4' { Start-Process mdsched.exe; Pausa }
        '5' {
            Start-Process 'https://www.nvidia.com/pt-br/drivers/'
            Start-Process 'https://www.guru3d.com/download/display-driver-uninstaller-download/'
            Write-Host ''
            Write-Host '  Passo a passo da reinstalacao limpa:'
            Write-Host '   a) Baixe o driver NVIDIA (Game Ready ou Studio) e o DDU. Nao instale ainda.'
            Write-Host '   b) Desconecte a internet (cabo ou Wi-Fi).'
            Write-Host '   c) Reinicie em Modo de Seguranca: Shift + clicar em Reiniciar >'
            Write-Host '      Solucao de problemas > Opcoes avancadas > Configuracoes de inicializacao > 4.'
            Write-Host '   d) Abra o DDU, escolha GPU / NVIDIA, clique "Clean and restart".'
            Write-Host '   e) De volta ao Windows normal, instale o driver (Instalacao limpa).'
            Write-Host '   f) Reconecte a internet e rode a opcao 1 de novo.'
            Pausa
        }
        '6' { Desfazer; Pausa }
        '0' { Stop-Transcript | Out-Null; exit }
    }
}
