# -*- coding: utf-8 -*-
"""
Catalogo SEGURO de clipes (War Thunder) - Windows.

- So LE os videos (abre em modo 'rb'). Nunca move/renomeia/apaga/edita.
- Grava tudo em C:\\CATALOGO\\catalogo.db (SQLite).
- Pode rodar de novo: so adiciona clipes novos.
- Pausa sozinho quando um jogo da Steam ou a live (OBS/Streamlabs) esta rodando.
- Requer: Python 3.8+ e ffprobe (ffmpeg) no PATH ou na pasta C:\\CATALOGO.

Uso:  python catalogo_clipes.py              (procura as pastas sozinho)
      python catalogo_clipes.py D:\\Clipes E:\\Videos   (usa so essas pastas)
"""
import ctypes
import hashlib
import json
import os
import shutil
import sqlite3
import string
import subprocess
import sys
import time
from datetime import datetime

CATALOGO_DIR = r"C:\CATALOGO"
DB_PATH = os.path.join(CATALOGO_DIR, "catalogo.db")
EXTS = {".mp4", ".mov", ".mkv"}
HASH_MB = 1  # MB lidos do inicio e do fim para o hash rapido
CHECK_EVERY_S = 30  # de quanto em quanto tempo checa jogo/live

# Processos que contam como "live" (pausa enquanto rodarem)
LIVE_PROCS = {"obs64.exe", "obs32.exe", "obs.exe", "streamlabs obs.exe",
              "streamlabs desktop.exe", "xsplit.core.exe"}
# Jogos conhecidos (alem de qualquer .exe dentro de steamapps\common)
GAME_PROCS = {"aces.exe"}  # War Thunder

SKIP_DIRS = {"windows", "program files", "program files (x86)", "programdata",
             "$recycle.bin", "system volume information", "recovery", "msocache",
             "perflogs", "appdata", "$windows.~bt", "$windows.~ws", "catalogo",
             "steamapps", "node_modules", ".git", "intel", "amd", "nvidia"}

NO_WINDOW = 0x08000000 if os.name == "nt" else 0


# ---------------------------------------------------------------- utilidades
def baixa_prioridade():
    """Deixa o script com prioridade baixa para nao pesar no PC."""
    if os.name == "nt":
        try:
            h = ctypes.windll.kernel32.GetCurrentProcess()
            ctypes.windll.kernel32.SetPriorityClass(h, 0x00004000)  # BELOW_NORMAL
        except Exception:
            pass


def acha_ffprobe():
    p = shutil.which("ffprobe")
    if p:
        return p
    local = os.path.join(CATALOGO_DIR, "ffprobe.exe")
    return local if os.path.exists(local) else None


def drives():
    if os.name != "nt":
        return ["/"]
    return [f"{l}:\\" for l in string.ascii_uppercase if os.path.exists(f"{l}:\\")]


def pular(nome):
    n = nome.lower()
    return n in SKIP_DIRS or n.startswith("$") or n.startswith(".")


def tem_video(raiz):
    """Procura o 1o video dentro da pasta e para (nao le arquivos)."""
    for dirpath, dirnames, filenames in os.walk(raiz, onerror=lambda e: None):
        dirnames[:] = [d for d in dirnames if not pular(d)]
        for f in filenames:
            if os.path.splitext(f)[1].lower() in EXTS:
                return True
    return False


def acha_pastas():
    """Lista so as pastas de topo de cada HD que contem videos."""
    candidatas = []
    for d in drives():
        try:
            topo = [e for e in os.scandir(d) if e.is_dir(follow_symlinks=False)]
        except OSError:
            continue
        for e in topo:
            if pular(e.name):
                continue
            if e.name.lower() == "users":
                # C:\Users\<nome>\<Videos, Desktop, ...>
                try:
                    for u in os.scandir(e.path):
                        if not u.is_dir(follow_symlinks=False) or u.name.lower() in (
                                "public", "default", "default user", "all users"):
                            continue
                        for sub in os.scandir(u.path):
                            if sub.is_dir(follow_symlinks=False) and not pular(sub.name):
                                candidatas.append(sub.path)
                except OSError:
                    pass
            else:
                candidatas.append(e.path)
    pastas = []
    for c in candidatas:
        print(f"  verificando {c} ...", flush=True)
        try:
            if tem_video(c):
                pastas.append(c)
        except OSError:
            pass
    return pastas


# ---------------------------------------------------- pausa por jogo / live
def processos():
    """Retorna lista de (nome, caminho_exe) dos processos rodando."""
    if os.name != "nt":
        return []
    try:
        cmd = ["powershell", "-NoProfile", "-Command",
               "Get-Process | ForEach-Object { $_.ProcessName + '|' + $_.Path }"]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                             creationflags=NO_WINDOW).stdout
    except Exception:
        return []
    res = []
    for linha in out.splitlines():
        nome, _, caminho = linha.partition("|")
        res.append(((nome.strip() + ".exe").lower(), caminho.strip().lower()))
    return res


def motivo_pausa():
    for nome, caminho in processos():
        if nome in LIVE_PROCS:
            return f"live ({nome})"
        if nome in GAME_PROCS:
            return f"jogo ({nome})"
        if "\\steamapps\\common\\" in caminho:
            return f"jogo Steam ({nome})"
    return None


_ultimo_check = 0.0


def espera_se_preciso(con):
    global _ultimo_check
    if time.time() - _ultimo_check < CHECK_EVERY_S:
        return
    _ultimo_check = time.time()
    m = motivo_pausa()
    if not m:
        return
    con.commit()
    print(f"\n[PAUSADO] {m} rodando. Retomo sozinho quando fechar...", flush=True)
    while m:
        time.sleep(60)
        m = motivo_pausa()
    print("[RETOMANDO]", flush=True)
    _ultimo_check = time.time()


# --------------------------------------------------------------- leitura
def hash_rapido(path, tamanho):
    h = hashlib.blake2b(digest_size=16)
    h.update(str(tamanho).encode())
    n = HASH_MB * 1024 * 1024
    with open(path, "rb") as f:  # somente leitura
        h.update(f.read(n))
        if tamanho > n:
            f.seek(max(tamanho - n, n))
            h.update(f.read(n))
    return h.hexdigest()


def probe(ffprobe, path):
    if not ffprobe:
        return None, None, None
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height:format=duration",
             "-of", "json", path],
            capture_output=True, text=True, timeout=60, creationflags=NO_WINDOW).stdout
        j = json.loads(out or "{}")
        s = (j.get("streams") or [{}])[0]
        dur = j.get("format", {}).get("duration")
        return (float(dur) if dur else None), s.get("width"), s.get("height")
    except Exception:
        return None, None, None


# --------------------------------------------------------------- banco
def abre_db():
    os.makedirs(CATALOGO_DIR, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS clipes (
        id            INTEGER PRIMARY KEY,
        caminho       TEXT UNIQUE NOT NULL,
        pasta_origem  TEXT,
        tamanho       INTEGER,
        hash          TEXT,
        duracao_s     REAL,
        largura       INTEGER,
        altura        INTEGER,
        resolucao     TEXT,
        data_arquivo  TEXT,
        veiculo       TEXT,
        nacao         TEXT,
        qtd_kills     INTEGER,
        postado       INTEGER DEFAULT 0,
        data_postagem TEXT,
        duplicado     INTEGER DEFAULT 0,
        duplicado_de  INTEGER,
        erro          TEXT,
        catalogado_em TEXT
    );
    CREATE INDEX IF NOT EXISTS ix_hash ON clipes(hash);
    CREATE TABLE IF NOT EXISTS pastas (
        caminho TEXT PRIMARY KEY, lida_em TEXT
    );
    """)
    return con


def marca_duplicados(con):
    con.execute("UPDATE clipes SET duplicado=0, duplicado_de=NULL")
    con.execute("""
        UPDATE clipes SET duplicado=1,
            duplicado_de=(SELECT MIN(c2.id) FROM clipes c2 WHERE c2.hash=clipes.hash)
        WHERE hash IS NOT NULL
          AND id > (SELECT MIN(c2.id) FROM clipes c2 WHERE c2.hash=clipes.hash)
    """)
    con.commit()


def resumo(con, pastas_lidas, status):
    total, = con.execute("SELECT COUNT(*) FROM clipes").fetchone()
    dups, = con.execute("SELECT COUNT(*) FROM clipes WHERE duplicado=1").fetchone()
    seg, = con.execute("SELECT COALESCE(SUM(duracao_s),0) FROM clipes").fetchone()
    seg_u, = con.execute(
        "SELECT COALESCE(SUM(duracao_s),0) FROM clipes WHERE duplicado=0").fetchone()
    erros, = con.execute("SELECT COUNT(*) FROM clipes WHERE erro IS NOT NULL").fetchone()
    todas = [r[0] for r in con.execute("SELECT caminho FROM pastas ORDER BY caminho")]
    linhas = [
        "=" * 60,
        f"STATUS: {status}   ({datetime.now():%Y-%m-%d %H:%M})",
        f"Total de clipes:     {total}",
        f"Duplicados:          {dups}",
        f"Horas de video:      {seg/3600:.1f} h  (sem duplicados: {seg_u/3600:.1f} h)",
        f"Arquivos com erro:   {erros}",
        f"Banco:               {DB_PATH}",
        "Pastas lidas:",
    ] + [f"  - {p}" for p in (todas or pastas_lidas)] + ["=" * 60]
    txt = "\n".join(linhas)
    print(txt)
    with open(os.path.join(CATALOGO_DIR, "resumo.txt"), "w", encoding="utf-8") as f:
        f.write(txt + "\n")


# --------------------------------------------------------------- principal
def main():
    baixa_prioridade()
    con = abre_db()
    ffprobe = acha_ffprobe()
    if not ffprobe:
        print("AVISO: ffprobe nao encontrado -> duracao/resolucao ficam vazias.\n"
              "       Instale: winget install Gyan.FFmpeg  (e rode de novo; ele completa).")

    if len(sys.argv) > 1:
        pastas = [p for p in sys.argv[1:] if os.path.isdir(p)]
    else:
        print("Procurando pastas com clipes nos HDs...")
        pastas = acha_pastas()
    print(f"{len(pastas)} pasta(s) com clipes.")

    ja = {r[0] for r in con.execute(
        "SELECT caminho FROM clipes WHERE erro IS NULL AND "
        "(duracao_s IS NOT NULL OR ? IS NULL)", (ffprobe,))}
    novos = 0
    status = "TERMINOU"
    try:
        for pasta in pastas:
            print(f"\n>> {pasta}", flush=True)
            for dirpath, dirnames, filenames in os.walk(pasta, onerror=lambda e: None):
                dirnames[:] = [d for d in dirnames if not pular(d)]
                for fn in filenames:
                    if os.path.splitext(fn)[1].lower() not in EXTS:
                        continue
                    path = os.path.join(dirpath, fn)
                    if path in ja:
                        continue
                    espera_se_preciso(con)
                    erro = h = dur = w = ht = None
                    tam = dt = None
                    try:
                        st = os.stat(path)
                        tam = st.st_size
                        dt = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                        h = hash_rapido(path, tam)
                        dur, w, ht = probe(ffprobe, path)
                    except Exception as e:
                        erro = str(e)[:200]
                    con.execute("""
                        INSERT INTO clipes (caminho, pasta_origem, tamanho, hash, duracao_s,
                            largura, altura, resolucao, data_arquivo, erro, catalogado_em)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(caminho) DO UPDATE SET
                            tamanho=excluded.tamanho, hash=excluded.hash,
                            duracao_s=excluded.duracao_s, largura=excluded.largura,
                            altura=excluded.altura, resolucao=excluded.resolucao,
                            data_arquivo=excluded.data_arquivo, erro=excluded.erro
                    """, (path, pasta, tam, h, dur, w, ht,
                          f"{w}x{ht}" if w and ht else None, dt, erro,
                          datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                    ja.add(path)
                    novos += 1
                    if novos % 50 == 0:
                        con.commit()
                        print(f"  {novos} novos catalogados...", flush=True)
            con.execute("INSERT OR REPLACE INTO pastas VALUES (?,?)",
                        (pasta, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            con.commit()
    except KeyboardInterrupt:
        status = "INTERROMPIDO (Ctrl+C) - rode de novo para continuar"
    except Exception as e:
        status = f"PAROU POR ERRO: {e} - rode de novo para continuar"
    finally:
        con.commit()
        marca_duplicados(con)
        print(f"\nNovos nesta rodada: {novos}")
        resumo(con, pastas, status)
        con.close()


if __name__ == "__main__":
    main()
