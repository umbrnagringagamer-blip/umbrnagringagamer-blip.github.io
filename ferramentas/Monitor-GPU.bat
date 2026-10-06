<# : Bloco em lote (cmd). O PowerShell ve isto como comentario.
@echo off
set "SELF=%~f0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "iex ([IO.File]::ReadAllText($env:SELF))"
exit /b
#>
# ======================================================================
#  Monitor-GPU  -  SOMENTE LEITURA. Nao muda nada no Windows.
#  Nao pede administrador, nao mexe em registro, driver ou inicializacao.
#  Apenas escreve o arquivo Monitor-GPU.txt na Area de Trabalho.
#  Para parar: feche a janela.
# ======================================================================

$arq = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Monitor-GPU.txt'
$smi = (Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue).Source
function L($t) { Add-Content -Path $arq -Value $t -Encoding UTF8; Write-Host $t }

L ''
L "################ MONITOR INICIADO $(Get-Date -Format 'dd/MM/yyyy HH:mm:ss') ################"

# ---------- Parte 1: historico de ligar / dormir / acordar / travar (14 dias) ----------
L ''
L '=== HISTORICO DOS ULTIMOS 14 DIAS (ligou, dormiu, acordou, travou) ==='
$nomes = @{
    'Microsoft-Windows-Kernel-Power|41'           = '!!! DESLIGOU SEM AVISO (queda)'
    'Microsoft-Windows-Kernel-Power|42'           = 'Entrou em suspensao (dormir)'
    'Microsoft-Windows-Kernel-Power|107'          = 'Voltou da suspensao'
    'Microsoft-Windows-Power-Troubleshooter|1'    = 'Acordou da suspensao'
    'Microsoft-Windows-Kernel-General|12'         = 'Windows LIGOU'
    'Microsoft-Windows-Kernel-General|13'         = 'Windows desligou normalmente'
    'EventLog|6008'                               = '!!! Desligamento anterior foi inesperado'
    'nvlddmkm|153'                                = '!!! PLACA DE VIDEO TRAVOU (TDR)'
    'nvlddmkm|13'                                 = '!   Erro interno da placa (FECS etc.)'
    'Display|4101'                                = '!!! Driver de video parou de responder'
    'Microsoft-Windows-WER-SystemErrorReporting|1001' = '!!! TELA AZUL'
}
$ev = Get-WinEvent -FilterHashtable @{ LogName='System'; StartTime=(Get-Date).AddDays(-14);
        ProviderName=@('Microsoft-Windows-Kernel-Power','Microsoft-Windows-Power-Troubleshooter',
                       'Microsoft-Windows-Kernel-General','EventLog','nvlddmkm','Display',
                       'Microsoft-Windows-WER-SystemErrorReporting') } -ErrorAction SilentlyContinue |
      Where-Object { $nomes.ContainsKey("$($_.ProviderName)|$($_.Id)") } |
      Sort-Object TimeCreated
# Junta erros repetidos da placa no mesmo minuto para nao encher o arquivo
$ultimo = ''
foreach ($e in $ev) {
    $linha = "$($e.TimeCreated.ToString('ddd dd/MM HH:mm'))  $($nomes["$($e.ProviderName)|$($e.Id)"])"
    if ($linha -ne $ultimo) { L $linha; $ultimo = $linha }
}

# ---------- Parte 2: registra a cada 1 minuto o que esta rodando ----------
L ''
L '=== REGISTRO A CADA MINUTO (a ultima linha antes de um buraco = hora da queda ou da suspensao) ==='
L 'hora     | GPU uso% temp W clockMHz memMB | processos que mais usam CPU | processos na GPU'
$antes = @{}
while ($true) {
    $gpu = 'sem nvidia-smi'
    $naGpu = ''
    if ($smi) {
        $gpu = ((& $smi --query-gpu=utilization.gpu,temperature.gpu,power.draw,clocks.gr,memory.used --format=csv,noheader,nounits 2>$null) -join ' ') -replace ',\s*',' '
        $naGpu = ((& $smi --query-compute-apps=process_name --format=csv,noheader 2>$null) |
                  ForEach-Object { Split-Path $_ -Leaf } | Select-Object -Unique) -join ', '
    }
    $agora = @{}
    $uso = foreach ($p in Get-Process) {
        try { $c = $p.TotalProcessorTime.TotalSeconds } catch { continue }
        $agora[$p.Id] = $c
        if ($antes.ContainsKey($p.Id)) { [pscustomobject]@{ N=$p.ProcessName; D=$c - $antes[$p.Id] } }
    }
    $antes = $agora
    $top = ($uso | Sort-Object D -Descending | Select-Object -First 3 |
            ForEach-Object { '{0}({1:N0}s)' -f $_.N, $_.D }) -join ' '
    L ("{0} | {1} | {2} | {3}" -f (Get-Date -Format 'dd/MM HH:mm'), $gpu, $top, $naGpu)
    Start-Sleep -Seconds 60
}
