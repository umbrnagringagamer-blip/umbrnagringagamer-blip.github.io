# -*- coding: utf-8 -*-
"""Classifica os clipes do catalogo pela pasta/nome. So mexe no banco, nunca nos videos.

Coluna nova `tipo`:
  original_wt  - gravacao original do War Thunder (NVIDIA: "War Thunder AAAA.MM.DD - ... .DVR.mp4")
  vertical     - versao 9x16 gerada a partir de um clipe
  corte        - corte gerado (_corteNNN)
  editor       - arquivos de trabalho/saida de editores automaticos e videos de banco (pexels etc.)
  outro_jogo   - gravacoes de outros jogos (ex.: Squad)
  outro        - qualquer outra coisa
"""
import ntpath
import re
import sqlite3

DB = r"C:\CATALOGO\catalogo.db"
EDITOR = ("\\_auto_editor\\", "\\work\\", "\\output\\", "\\estudio\\", "pexels")


def tipo(caminho):
    low = caminho.lower()
    nome = ntpath.basename(caminho).lower()
    if any(e in low for e in EDITOR):
        return "editor"
    if nome.startswith("9x16_"):
        return "vertical"
    if "_corte" in nome:
        return "corte"
    if nome.startswith("war thunder "):
        return "original_wt"
    if re.search(r"\d{4}\.\d{2}\.\d{2} - .*\.dvr\.", nome):
        return "outro_jogo"
    return "outro"


con = sqlite3.connect(DB)
cols = [r[1] for r in con.execute("PRAGMA table_info(clipes)")]
if "tipo" not in cols:
    con.execute("ALTER TABLE clipes ADD COLUMN tipo TEXT")
con.executemany("UPDATE clipes SET tipo=? WHERE id=?",
                [(tipo(c), i) for i, c in con.execute("SELECT id, caminho FROM clipes")])
con.commit()

print(f"{'tipo':<12}{'unicos':>8}{'horas':>9}{'dup':>8}")
for t, n, h, d in con.execute("""
        SELECT tipo, SUM(duplicado=0), ROUND(SUM(CASE WHEN duplicado=0 THEN duracao_s END)/3600.0,1),
               SUM(duplicado=1)
        FROM clipes GROUP BY tipo ORDER BY 2 DESC"""):
    print(f"{t:<12}{n:>8}{h or 0:>9}{d:>8}")
print("\nExemplos de 'outro':")
for (c,) in con.execute("SELECT caminho FROM clipes WHERE tipo='outro' AND duplicado=0 "
                        "ORDER BY random() LIMIT 10"):
    print(" ", c)
