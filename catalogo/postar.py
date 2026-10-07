# -*- coding: utf-8 -*-
"""Lista e marca clipes para postar. So mexe no banco, nunca nos videos.

  python postar.py              -> gera C:\\CATALOGO\\para_postar.csv (originais unicos nao postados,
                                   do mais novo pro mais antigo) e mostra os 20 primeiros
  python postar.py ver 123      -> abre o clipe 123 no player (so assiste)
  python postar.py marcar 123 456  -> marca como postado (hoje), incluindo as copias duplicadas
"""
import csv
import ntpath
import os
import re
import sqlite3
import sys
from datetime import date

DIR = r"C:\CATALOGO"
DB = os.path.join(DIR, "catalogo.db")
CSV = os.path.join(DIR, "para_postar.csv")
RE_DATA = re.compile(r"(\d{4})\.(\d{2})\.(\d{2}) - (\d{2})\.(\d{2})\.(\d{2})")

con = sqlite3.connect(DB)
args = sys.argv[1:]

if args[:1] == ["ver"]:
    for i in args[1:]:
        r = con.execute("SELECT caminho FROM clipes WHERE id=?", (int(i),)).fetchone()
        if r:
            os.startfile(r[0])
        else:
            print(f"id {i} nao existe")

elif args[:1] == ["marcar"]:
    hoje = date.today().isoformat()
    for i in args[1:]:
        r = con.execute("SELECT hash FROM clipes WHERE id=?", (int(i),)).fetchone()
        if not r:
            print(f"id {i} nao existe")
            continue
        n = con.execute("UPDATE clipes SET postado=1, data_postagem=? WHERE id=? OR hash=?",
                        (hoje, int(i), r[0])).rowcount
        print(f"id {i} marcado como postado ({n} arquivo(s) com copias)")
    con.commit()
    total, = con.execute("SELECT COUNT(*) FROM clipes WHERE tipo='original_wt' AND duplicado=0 "
                         "AND postado=1").fetchone()
    print(f"Originais ja postados: {total}")

else:
    linhas = []
    for i, c, dur, res, dt in con.execute(
            "SELECT id, caminho, duracao_s, resolucao, data_arquivo FROM clipes "
            "WHERE tipo='original_wt' AND duplicado=0 AND COALESCE(postado,0)=0"):
        m = RE_DATA.search(ntpath.basename(c))
        gravado = (f"{m[1]}-{m[2]}-{m[3]} {m[4]}:{m[5]}:{m[6]}" if m else dt) or ""
        linhas.append((gravado, i, round(dur or 0), res or "", ntpath.basename(c), c))
    linhas.sort(reverse=True)
    with open(CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["gravado_em", "id", "duracao_s", "resolucao", "arquivo", "caminho"])
        w.writerows(linhas)
    print(f"{len(linhas)} originais nao postados -> {CSV}\n")
    print(f"{'gravado_em':<20}{'id':>7}{'seg':>5}  resolucao")
    for g, i, d, r, _, _ in linhas[:20]:
        print(f"{g:<20}{i:>7}{d:>5}  {r}")
    print("\nAssistir:  python C:\\CATALOGO\\postar.py ver <id>"
          "\nPostou:    python C:\\CATALOGO\\postar.py marcar <id> [<id> ...]")
