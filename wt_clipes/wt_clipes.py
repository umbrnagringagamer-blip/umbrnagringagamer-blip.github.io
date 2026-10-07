#!/usr/bin/env python3
"""Organizador de clipes de War Thunder + limpeza pós-OpusClip. Tudo local.

Comandos:
  testar     --n 30            classifica N clipes aleatórios e gera mosaicos p/ conferência
  avaliar    teste_30.csv      calcula o acerto depois de preencher a coluna 'real'
  organizar                    classifica tudo e COPIA para <HD>/Clipes_WarThunder/...
  revisao    decisoes.csv      aplica as decisões dos casos de baixa confiança
  limpar     --pastas P1 P2    limpeza pós-OpusClip (simulação por padrão; --executar p/ mover)
"""
import argparse, csv, datetime as dt, hashlib, json, logging, os, random, re, shutil, string, sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
EXT_VIDEO = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".flv", ".ts"}
RE_WT = re.compile(r"war\s*thunder|warthunder", re.I)
PASTA_SAIDA = "Clipes_WarThunder"
CATS = {"terrestre": "terrestres", "aviao": "aereos/avioes", "helicoptero": "aereos/helicopteros"}
IGNORAR_DIRS = {PASTA_SAIDA.lower(), "_quarentena", "$recycle.bin", "system volume information",
                "windows", "program files", "program files (x86)", "programdata", "appdata",
                "proc", "sys", "dev", "snap", "node_modules", ".git"}
LIMIAR = 0.55        # abaixo disto o clipe vai para revisão por mosaico
N_FRAMES = 8
MARGEM_LIVRE = 2 * 1024**3   # sempre deixa 2 GB livres no HD
QUARENTENA_DIAS = 14

log = logging.getLogger("wt")


def configurar_log(nome):
    AQUI.joinpath("logs").mkdir(exist_ok=True)
    arq = AQUI / "logs" / f"{nome}_{dt.datetime.now():%Y%m%d_%H%M%S}.log"
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(arq, encoding="utf-8"), logging.StreamHandler()])
    log.info("log: %s", arq)


# ---------------------------------------------------------------- descoberta

def listar_hds():
    raizes = set()
    try:
        import psutil
        for p in psutil.disk_partitions(all=False):
            if p.fstype.lower() in {"squashfs", "tmpfs", "devtmpfs", "overlay"} or "cdrom" in p.opts:
                continue
            raizes.add(Path(p.mountpoint))
    except ImportError:
        pass
    if os.name == "nt":
        raizes |= {Path(f"{l}:\\") for l in string.ascii_uppercase if Path(f"{l}:\\").exists()}
    return sorted(r for r in raizes if os.access(r, os.R_OK))


def raiz_do_hd(caminho, hds):
    c = str(Path(caminho).resolve())
    return max((h for h in hds if c.startswith(str(h))), key=lambda h: len(str(h)))


def achar_videos(hds, extras=()):
    """Vídeos de War Thunder: nome/caminho contém 'War Thunder' ou estão em --pasta-extra."""
    vistos, out = set(), []
    extras = [Path(e).resolve() for e in extras]
    for base in list(hds) + extras:
        tudo = base in extras
        for dirpath, dirs, files in os.walk(base, onerror=lambda e: None):
            dirs[:] = [d for d in dirs if d.lower() not in IGNORAR_DIRS and not d.startswith(".")]
            wt_dir = tudo or bool(RE_WT.search(dirpath))
            for f in files:
                p = Path(dirpath, f)
                if p.suffix.lower() in EXT_VIDEO and (wt_dir or RE_WT.search(f)):
                    rp = str(p.resolve())
                    if rp not in vistos:
                        vistos.add(rp); out.append(Path(rp))
    log.info("%d vídeos de War Thunder encontrados em %d HD(s)", len(out), len(hds))
    return out


# ---------------------------------------------------------------- classificação

class Classificador:
    PROMPTS = {
        "terrestre": ["a screenshot of War Thunder ground battle with a tank",
                      "a tank gunner sight crosshair view in a video game",
                      "a third person view of a tank on the ground in a video game"],
        "aviao": ["a screenshot of War Thunder air battle with a fighter airplane",
                  "a jet plane flying in the sky in a video game",
                  "an airplane cockpit view with flight HUD in a video game"],
        "helicoptero": ["a screenshot of War Thunder with an attack helicopter",
                        "a helicopter with rotor blades flying in a video game",
                        "a helicopter gunner thermal sight view in a video game"],
    }
    # palavras que aparecem no HUD (OCR opcional, se pytesseract estiver instalado)
    HUD_OCR = {"aviao": {"IAS", "TAS", "ALT", "THR", "WEP", "FLAPS", "GEAR", "MACH"},
               "helicoptero": {"ROTOR", "COLLECTIVE", "HOVER"},
               "terrestre": {"RPM", "GEAR", "CREW", "RELOAD", "AP", "APDS", "APFSDS", "HEAT", "HE"}}
    NOME = {"helicoptero": re.compile(r"heli|ka-?5|mi-?2|ah-?64|apache|tiger|cobra", re.I),
            "aviao": re.compile(r"avi[aã]o|plane|jet|f-?1[456]|mig|su-?2|cas\b|ca[cç]a", re.I),
            "terrestre": re.compile(r"tank|tanque|is-?7|leopard|abrams|t-?[789]0|ground|terrest", re.I)}

    def __init__(self):
        import torch
        from transformers import CLIPModel, CLIPProcessor
        self.torch = torch
        nome = "openai/clip-vit-base-patch32"
        self.modelo = CLIPModel.from_pretrained(nome).eval()
        self.proc = CLIPProcessor.from_pretrained(nome)
        self.cats = list(self.PROMPTS)
        textos = [t for c in self.cats for t in self.PROMPTS[c]]
        with torch.no_grad():
            e = self.modelo.get_text_features(**self.proc(text=textos, return_tensors="pt", padding=True))
        e = e / e.norm(dim=-1, keepdim=True)
        k = len(self.PROMPTS[self.cats[0]])
        self.txt = torch.stack([e[i * k:(i + 1) * k].mean(0) for i in range(len(self.cats))])
        self.txt = self.txt / self.txt.norm(dim=-1, keepdim=True)
        try:
            import pytesseract; pytesseract.get_tesseract_version(); self.ocr = pytesseract
        except Exception:
            self.ocr = None

    def _probs(self, imgs):
        t = self.torch
        with t.no_grad():
            e = self.modelo.get_image_features(**self.proc(images=imgs, return_tensors="pt"))
        e = e / e.norm(dim=-1, keepdim=True)
        return (100 * e @ self.txt.T).softmax(-1).numpy()

    def _hud(self, frame):
        """Sinal do HUD: recorte do canto superior-esquerdo (velocidade/altitude) e faixa inferior."""
        import numpy as np
        h, w = frame.shape[:2]
        s = np.zeros(len(self.cats))
        if self.ocr is None:
            return s
        for crop in (frame[: h // 3, : w // 3], frame[int(h * .75):, :]):
            try:
                txt = set(re.findall(r"[A-Z]{2,}", self.ocr.image_to_string(crop).upper()))
            except Exception:
                continue
            for i, c in enumerate(self.cats):
                s[i] += len(txt & self.HUD_OCR[c])
        return s / s.sum() if s.sum() else s

    def classificar(self, caminho):
        import cv2, numpy as np
        from PIL import Image
        frames = extrair_frames(caminho, N_FRAMES)
        if not frames:
            return "terrestre", 0.0, [], "sem_frames"
        cheio = self._probs([Image.fromarray(cv2.cvtColor(f, cv2.COLOR_BGR2RGB)) for f in frames])
        h, w = frames[0].shape[:2]
        centro = self._probs([Image.fromarray(cv2.cvtColor(f[h // 6:-h // 6, w // 6:-w // 6], cv2.COLOR_BGR2RGB)) for f in frames])
        votos = np.zeros(len(self.cats)); soma = np.zeros(len(self.cats))
        for i, f in enumerate(frames):
            p = 0.55 * cheio[i] + 0.30 * centro[i]
            hud = self._hud(f)
            p = p + 0.15 * (hud if hud.sum() else cheio[i])
            votos[p.argmax()] += 1; soma += p
        for i, c in enumerate(self.cats):            # dica fraca pelo nome do arquivo
            if self.NOME[c].search(Path(caminho).stem):
                soma[i] += 0.15 * len(frames)
        k = int(soma.argmax())
        conf = float(votos[k] / len(frames)) * float(soma[k] / soma.sum())
        conf = min(1.0, conf * 1.6)                 # normaliza p/ ~0..1 (3 classes)
        return self.cats[k], round(conf, 3), frames, "clip+hud+votacao"


def extrair_frames(caminho, n):
    import cv2
    cap = cv2.VideoCapture(str(caminho))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    out = []
    if total > 0:
        for i in range(n):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * (0.05 + 0.9 * i / max(1, n - 1))))
            ok, f = cap.read()
            if ok:
                out.append(cv2.resize(f, (640, int(640 * f.shape[0] / f.shape[1]))))
    cap.release()
    return out


def mosaico(frames, destino, titulo=""):
    """Grade 4x2 pequena (barata de olhar)."""
    import cv2, numpy as np
    fr = [cv2.resize(f, (320, 180)) for f in frames[:8]]
    while len(fr) < 8:
        fr.append(np.zeros((180, 320, 3), np.uint8))
    img = np.vstack([np.hstack(fr[:4]), np.hstack(fr[4:])])
    cv2.putText(img, titulo[:90], (6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)
    Path(destino).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(destino), img, [cv2.IMWRITE_JPEG_QUALITY, 70])


# ---------------------------------------------------------------- cópia

def impressao(caminho, bloco=4 * 1024**2):
    """Hash rápido: tamanho + início/meio/fim. Suficiente para detectar duplicados de vídeo."""
    p = Path(caminho); t = p.stat().st_size; h = hashlib.sha1(str(t).encode())
    with open(p, "rb") as f:
        for pos in (0, max(0, t // 2 - bloco // 2), max(0, t - bloco)):
            f.seek(pos); h.update(f.read(bloco))
    return h.hexdigest()


def copiar_seguro(orig, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    shutil.copy2(orig, tmp)
    if tmp.stat().st_size != Path(orig).stat().st_size:
        tmp.unlink(); raise IOError("tamanho difere após cópia")
    os.replace(tmp, dest)


def nome_livre(dest):
    i, d = 1, dest
    while d.exists():
        d = dest.with_name(f"{dest.stem} ({i}){dest.suffix}"); i += 1
    return d


def indice_destino(raiz_saida):
    """Impressões dos arquivos já presentes em Clipes_WarThunder (cache em .indice.json)."""
    arq = raiz_saida / ".indice.json"
    try:
        cache = json.loads(arq.read_text(encoding="utf-8"))
    except Exception:
        cache = {}
    vivos = {}
    for p in raiz_saida.rglob("*"):
        if p.is_file() and p.suffix.lower() in EXT_VIDEO:
            chave = f"{p}|{p.stat().st_size}|{int(p.stat().st_mtime)}"
            vivos[chave] = cache.get(chave) or impressao(p)
    raiz_saida.mkdir(parents=True, exist_ok=True)
    arq.write_text(json.dumps(vivos), encoding="utf-8")
    return {v: k.split("|")[0] for k, v in vivos.items()}


# ---------------------------------------------------------------- comandos de organização

def cmd_testar(a):
    configurar_log("teste")
    hds = listar_hds(); vids = achar_videos(hds, a.pasta_extra)
    amostra = random.Random(a.semente).sample(vids, min(a.n, len(vids)))
    clf = Classificador(); pasta = AQUI / f"teste_{a.n}"
    linhas = []
    for i, v in enumerate(amostra, 1):
        cat, conf, frames, met = clf.classificar(v)
        img = pasta / f"{i:02d}.jpg"
        if frames: mosaico(frames, img, f"{i:02d} {v.name}")
        linhas.append({"id": f"{i:02d}", "caminho_original": str(v), "previsto": cat,
                       "confianca": conf, "baixa_conf": conf < LIMIAR, "mosaico": str(img), "real": ""})
        log.info("[%d/%d] %s -> %s (%.2f)", i, len(amostra), v.name, cat, conf)
    escrever_csv(pasta / f"teste_{a.n}.csv", linhas)
    log.info("Pronto. Mosaicos e CSV em %s. Preencha 'real' (terrestre/aviao/helicoptero) e rode: avaliar", pasta)


def cmd_avaliar(a):
    linhas = list(csv.DictReader(open(a.csv, encoding="utf-8")))
    ok = [l for l in linhas if l["real"].strip()]
    acertos = sum(l["real"].strip() == l["previsto"] for l in ok)
    alta = [l for l in ok if l["baixa_conf"] == "False"]
    print(f"Acerto geral: {acertos}/{len(ok)} = {acertos / max(1, len(ok)):.0%}")
    print(f"Acerto nos de alta confiança: {sum(l['real'].strip() == l['previsto'] for l in alta)}/{len(alta)}")
    print(f"Baixa confiança (iriam p/ revisão): {len(ok) - len(alta)}")
    for l in ok:
        if l["real"].strip() != l["previsto"]:
            print(f"  ERRO {l['id']}: previsto={l['previsto']} real={l['real']} conf={l['confianca']}")


def cmd_organizar(a):
    configurar_log("organizar")
    hds = listar_hds(); vids = achar_videos(hds, a.pasta_extra)
    clf = Classificador()
    por_hd = {}
    for v in vids:
        por_hd.setdefault(raiz_do_hd(v, hds), []).append(v)
    linhas = []; revisao = AQUI / "revisao"
    for hd, lista in por_hd.items():
        saida = hd / PASTA_SAIDA
        for sub in CATS.values():
            (saida / sub).mkdir(parents=True, exist_ok=True)
        conhecidos = indice_destino(saida)
        livre = shutil.disk_usage(hd).free
        precisa = sum(v.stat().st_size for v in lista)
        log.info("HD %s: %d clipes, %.1f GB a copiar (máx.), %.1f GB livres", hd, len(lista), precisa / 1e9, livre / 1e9)
        for v in lista:
            linha = {"caminho_original": str(v), "hd": str(hd), "categoria": "", "confianca": "",
                     "metodo": "", "destino": "", "status_copia": ""}
            try:
                cat, conf, frames, met = clf.classificar(v)
                linha.update(categoria=CATS[cat], confianca=conf, metodo=met)
                if conf < LIMIAR:
                    linha["metodo"] = "revisar_mosaico"
                    if frames: mosaico(frames, revisao / f"{len(linhas):05d}.jpg", f"{len(linhas):05d} {v.name}")
                    linha["mosaico"] = str(revisao / f"{len(linhas):05d}.jpg")
                imp = impressao(v)
                if imp in conhecidos:
                    linha.update(status_copia="duplicado_pulado", destino=conhecidos[imp])
                else:
                    tam = v.stat().st_size
                    if shutil.disk_usage(hd).free - tam < MARGEM_LIVRE:
                        linha["status_copia"] = "sem_espaco"
                        log.warning("Sem espaço p/ %s", v)
                    else:
                        dest = nome_livre(saida / CATS[cat] / v.name)
                        copiar_seguro(v, dest)
                        conhecidos[imp] = str(dest)
                        linha.update(status_copia="copiado", destino=str(dest))
            except Exception as e:
                linha["status_copia"] = f"erro: {e}"; log.exception("Falha em %s", v)
            linhas.append(linha)
            log.info("%s -> %s %s (%s)", v.name, linha["categoria"], linha["confianca"], linha["status_copia"])
    salvar_planilha(linhas)
    n_rev = sum(l["metodo"] == "revisar_mosaico" for l in linhas)
    log.info("Concluído: %d clipes. %d de baixa confiança com mosaico em %s (já copiados na melhor categoria; "
             "corrija com: revisao decisoes.csv)", len(linhas), n_rev, revisao)


def cmd_revisao(a):
    """decisoes.csv: caminho_original,categoria (terrestre/aviao/helicoptero). Move só as CÓPIAS."""
    configurar_log("revisao")
    plan = AQUI / "Clipes_WarThunder_relatorio.csv"
    linhas = list(csv.DictReader(open(plan, encoding="utf-8")))
    dec = {r["caminho_original"]: r["categoria"].strip() for r in csv.DictReader(open(a.csv, encoding="utf-8"))}
    for l in linhas:
        nova = dec.get(l["caminho_original"])
        if not nova:
            continue
        if l["destino"] and l["status_copia"] == "copiado" and CATS[nova] != l["categoria"]:
            atual = Path(l["destino"]); hd = Path(l["hd"])
            novo = nome_livre(hd / PASTA_SAIDA / CATS[nova] / atual.name)
            try:
                novo.parent.mkdir(parents=True, exist_ok=True); shutil.move(str(atual), str(novo))
                l["destino"] = str(novo)
            except Exception as e:
                log.error("Não movi a cópia %s: %s", atual, e); continue
        l.update(categoria=CATS[nova], confianca="1.0", metodo="revisado_mosaico")
        log.info("Revisado: %s -> %s", l["caminho_original"], nova)
    salvar_planilha(linhas)


def escrever_csv(arq, linhas):
    Path(arq).parent.mkdir(parents=True, exist_ok=True)
    campos = list(dict.fromkeys(k for l in linhas for k in l))
    with open(arq, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, campos); w.writeheader(); w.writerows(linhas)


def salvar_planilha(linhas):
    escrever_csv(AQUI / "Clipes_WarThunder_relatorio.csv", linhas)
    try:
        from openpyxl import Workbook
        wb = Workbook(); ws = wb.active; ws.title = "clipes"
        campos = ["caminho_original", "categoria", "confianca", "metodo", "hd", "destino", "status_copia"]
        ws.append(campos)
        for l in linhas:
            ws.append([float(l[c]) if c == "confianca" and l.get(c) not in ("", None) else l.get(c, "") for c in campos])
        ws.auto_filter.ref = ws.dimensions; ws.freeze_panes = "A2"
        for col, larg in zip("ABCDEFG", (90, 22, 11, 18, 10, 90, 18)):
            ws.column_dimensions[col].width = larg
        wb.save(AQUI / "Clipes_WarThunder_relatorio.xlsx")
    except ImportError:
        log.warning("openpyxl ausente: só CSV gerado")


# ---------------------------------------------------------------- limpeza pós-OpusClip

def normalizar(nome):
    n = Path(nome).name
    while n.lower().startswith("9x16_"):
        n = n[5:]
    return n.strip().lower()


def protegido(p):
    return p.name.startswith("KEEP_") or any(x.upper() == "NAO_APAGAR" for x in p.parts)


def cmd_limpar(a):
    configurar_log("limpeza_" + ("EXECUCAO" if a.executar else "SIMULACAO"))
    pastas = [Path(p).resolve() for p in a.pastas]
    for p in pastas:
        if not p.is_dir():
            log.error("Pasta não existe: %s", p); return 1
    status = json.loads(Path(a.status).read_text(encoding="utf-8"))["projetos"]
    prontos = {normalizar(s["arquivo"]): s for s in status if s["processado"] and s["agendado_ou_postado"]}
    plano_arq = AQUI / "plano_limpeza.json"

    if not a.executar:
        plano = []
        for base in pastas:
            for p in base.rglob("*"):
                try:
                    if not p.is_file() or p.suffix.lower() not in EXT_VIDEO or "_quarentena" in p.parts:
                        continue
                    if protegido(p):
                        log.info("PROTEGIDO (ignorado): %s", p); continue
                    s = prontos.get(normalizar(p.name))
                    if not s:
                        continue
                    st = p.stat()
                    plano.append({"caminho": str(p), "pasta_base": str(base), "nome": p.name, "tamanho": st.st_size,
                                  "mtime": st.st_mtime, "projeto": s["project_id"], "posts": s.get("posts", [])})
                    log.info("SIMULAÇÃO moveria: %s (%.1f MB, projeto %s, %s)", p, st.st_size / 1e6,
                             s["project_id"], "/".join(s.get("posts", [])))
                except Exception as e:
                    log.error("Erro lendo %s (mantido): %s", p, e)
        plano_arq.write_text(json.dumps({"criado": dt.datetime.now().isoformat(), "pastas": [str(p) for p in pastas],
                                         "itens": plano}, ensure_ascii=False, indent=1), encoding="utf-8")
        for base in pastas:
            for q in vencidas(base):
                log.info("SIMULAÇÃO esvaziaria quarentena vencida: %s", q)
        log.info("SIMULAÇÃO: %d arquivo(s), %.2f GB. Nada foi alterado. Plano: %s",
                 len(plano), sum(i["tamanho"] for i in plano) / 1e9, plano_arq)
        return 0

    # ---- execução: só o que está no plano da simulação e continua idêntico
    if not plano_arq.exists():
        log.error("Rode primeiro em modo simulação (sem --executar)."); return 1
    plano = json.loads(plano_arq.read_text(encoding="utf-8"))
    if set(plano["pastas"]) != {str(p) for p in pastas}:
        log.error("As pastas diferem das da simulação. Rode a simulação de novo."); return 1
    hoje = dt.date.today().isoformat(); movidos = 0
    for it in plano["itens"]:
        p = Path(it["caminho"])
        try:
            if protegido(p):
                log.info("PROTEGIDO, mantido: %s", p); continue
            if not p.is_file():
                log.warning("Sumiu desde a simulação, ignorado: %s", p); continue
            st = p.stat()
            if p.name != it["nome"] or st.st_size != it["tamanho"] or abs(st.st_mtime - it["mtime"]) > 2:
                log.warning("Nome/tamanho/data mudaram desde a simulação, MANTIDO: %s", p); continue
            if normalizar(p.name) not in prontos:
                log.warning("Não está mais pronto no OpusClip, MANTIDO: %s", p); continue
            base = Path(it["pasta_base"])
            dest = nome_livre(base / "_quarentena" / hoje / p.relative_to(base))
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), str(dest))
            if not dest.is_file() or dest.stat().st_size != it["tamanho"]:
                raise IOError("verificação pós-movimento falhou")
            (dest.parent / (dest.name + ".origem.json")).write_text(json.dumps(it, ensure_ascii=False), encoding="utf-8")
            movidos += 1; log.info("MOVIDO p/ quarentena: %s -> %s", p, dest)
        except Exception as e:
            log.error("Falha com %s, arquivo MANTIDO: %s", p, e)
    for base in pastas:
        for q in vencidas(base):
            try:
                restos = [f for f in q.rglob("*") if f.is_file() and protegido(f)]
                if restos:
                    log.warning("Quarentena %s tem arquivos protegidos, não esvaziada", q); continue
                shutil.rmtree(q); log.info("Quarentena vencida esvaziada: %s", q)
            except Exception as e:
                log.error("Falha ao esvaziar %s (mantida): %s", q, e)
    plano_arq.rename(plano_arq.with_name(f"plano_limpeza_executado_{dt.datetime.now():%Y%m%d_%H%M%S}.json"))
    log.info("Execução: %d movido(s) para _quarentena.", movidos)


def vencidas(base):
    q = base / "_quarentena"
    if not q.is_dir():
        return []
    out = []
    for d in q.iterdir():
        try:
            if d.is_dir() and (dt.date.today() - dt.date.fromisoformat(d.name)).days > QUARENTENA_DIAS:
                out.append(d)
        except ValueError:
            pass
    return out


# ---------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    t = sp.add_parser("testar"); t.add_argument("--n", type=int, default=30); t.add_argument("--semente", type=int, default=42)
    o = sp.add_parser("organizar")
    for x in (t, o):
        x.add_argument("--pasta-extra", nargs="*", default=[], help="pastas onde TODO vídeo é de War Thunder")
    sp.add_parser("avaliar").add_argument("csv")
    sp.add_parser("revisao").add_argument("csv")
    l = sp.add_parser("limpar")
    l.add_argument("--pastas", nargs="+", required=True)
    l.add_argument("--status", default=str(AQUI / "opusclip_status.json"))
    l.add_argument("--executar", action="store_true", help="sem isto, só simula")
    a = ap.parse_args()
    return {"testar": cmd_testar, "avaliar": cmd_avaliar, "organizar": cmd_organizar,
            "revisao": cmd_revisao, "limpar": cmd_limpar}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main() or 0)
