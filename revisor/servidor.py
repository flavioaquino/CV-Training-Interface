"""API do revisor: lê as pré-anotações de work/revisao e grava as revisões em work/revisado.

Cada imagem salva vira work/revisado/labels/<stem>.txt (YOLO), que é exatamente o que a
seção 5 do notebook usa. Em work/revisado/meta/<stem>.json ficam só dados de exibição
(qual caixa veio do backend, confiança da sugestão), que não vão para o treino.
"""

import csv
import json
import os
import re
import threading
import uuid
from datetime import datetime
from io import BytesIO
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .versoes import BancoVersoes

ESTATICOS = Path(__file__).parent / "static"
EXTENSOES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
MODELO_PADRAO = "models/fiscalizer_reconstruido.pt"


class Caixa(BaseModel):
    c: int
    x: float
    y: float
    w: float
    h: float
    conf: float | None = None
    gt: bool = False


class Anotacao(BaseModel):
    caixas: list[Caixa]


class NovaVersao(BaseModel):
    nome: str = ""
    nota: str = ""


class PedidoPreclass(BaseModel):
    stems: list[str]
    conf: float = 0.25
    manter_gt: bool = True


class PedidoDataset(BaseModel):
    pasta: str
    preclassificar: bool = True
    conf: float = 0.25


def ler_yolo(caminho):
    if not caminho.exists():
        return []
    caixas = []
    for linha in caminho.read_text().splitlines():
        p = linha.split()
        if len(p) >= 5:
            caixas.append({"c": int(p[0]), "x": float(p[1]), "y": float(p[2]), "w": float(p[3]), "h": float(p[4])})
    return caixas


def gravar_atomico(caminho, texto):
    """Grava num temporário e renomeia: um arquivo nunca fica pela metade se algo cair no meio."""
    tmp = caminho.with_name(caminho.name + ".tmp")
    tmp.write_text(texto, encoding="utf-8")
    os.replace(tmp, caminho)


def recortar(c):
    """Limita a caixa à imagem; devolve None se ela ficar degenerada."""
    x1, y1 = max(0.0, c.x - c.w / 2), max(0.0, c.y - c.h / 2)
    x2, y2 = min(1.0, c.x + c.w / 2), min(1.0, c.y + c.h / 2)
    if x2 - x1 < 1e-4 or y2 - y1 < 1e-4:
        return None
    return c.model_copy(update=dict(x=(x1 + x2) / 2, y=(y1 + y2) / 2, w=x2 - x1, h=y2 - y1))


def stem_seguro(nome, existentes):
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(nome).stem)[:80].strip("._") or "foto"
    s, i = base, 1
    while s in existentes:
        s = f"{base}_{i}"
        i += 1
    return s


def salvar_como_jpg(dados, destino):
    from PIL import Image
    im = Image.open(BytesIO(dados))
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    elif im.mode == "L":
        im = im.convert("RGB")
    destino.parent.mkdir(parents=True, exist_ok=True)
    im.save(destino, "JPEG", quality=92)


class Dataset:
    def __init__(self, revisao, revisado):
        self.dir_revisao = Path(revisao)
        self.dir_revisado = Path(revisado)
        self.dir_imgs = self.dir_revisao / "images"
        self.dir_pre = self.dir_revisao / "labels"
        self.dir_conf = self.dir_revisao / "conf"
        self.dir_rev = self.dir_revisado / "labels"
        self.dir_meta = self.dir_revisado / "meta"
        for d in (self.dir_imgs, self.dir_pre, self.dir_conf, self.dir_rev, self.dir_meta):
            d.mkdir(parents=True, exist_ok=True)
        self.trava = threading.Lock()
        self.itens = {}
        self.classes = []
        self.recarregar()

    def recarregar(self):
        classes_arq = self.dir_revisao / "classes.txt"
        if classes_arq.exists():
            self.classes = [l.strip() for l in classes_arq.read_text(encoding="utf-8").splitlines() if l.strip()]
        else:
            self.classes = []
        prioridade = {}
        csv_prio = self.dir_revisao / "prioridade_revisao.csv"
        if csv_prio.exists():
            with open(csv_prio, encoding="utf-8-sig") as f:
                for i, linha in enumerate(csv.DictReader(f)):
                    prioridade[linha["stem"]] = dict(
                        ordem=i,
                        classe_original=linha.get("classe_original") or "",
                        origem=linha.get("origem") or "",
                        conflitos=int(float(linha.get("conflitos") or 0)),
                        incertas=int(float(linha.get("incertas") or 0)),
                    )
        importadas = set()
        arq_imp = self.dir_revisao / "importadas.txt"
        if arq_imp.exists():
            importadas = {l.strip() for l in arq_imp.read_text(encoding="utf-8").splitlines() if l.strip()}
        stems = sorted(p.stem for p in self.dir_imgs.glob("*.jpg"))
        self.itens = {}
        for n, s in enumerate(stems):
            p = prioridade.get(s, {})
            origem = p.get("origem") or (
                "importada" if (s in importadas or p.get("classe_original") == "importada") else "original"
            )
            self.itens[s] = dict(
                stem=s,
                ordem=p.get("ordem", len(prioridade) + n),
                classe_original=p.get("classe_original", ""),
                conflitos=p.get("conflitos", 0),
                incertas=p.get("incertas", 0),
                revisada=(self.dir_rev / f"{s}.txt").exists(),
                contagem=self._contagem(self._caixas_em_disco(s)[0]),
                origem=origem,
            )

    def _contagem(self, caixas):
        n = [0] * len(self.classes)
        for c in caixas:
            if 0 <= c["c"] < len(n):
                n[c["c"]] += 1
        return n

    def _caixas_em_disco(self, stem):
        rev = self.dir_rev / f"{stem}.txt"
        if rev.exists():
            return ler_yolo(rev), "revisada"
        return ler_yolo(self.dir_pre / f"{stem}.txt"), "pre"

    def _flags(self, stem, fonte):
        if fonte == "revisada":
            arq = self.dir_meta / f"{stem}.json"
            return json.loads(arq.read_text(encoding="utf-8")).get("caixas", []) if arq.exists() else []
        arq = self.dir_conf / f"{stem}.txt"
        if not arq.exists():
            return []
        flags = []
        for l in arq.read_text(encoding="utf-8").splitlines():
            s = l.strip()
            if not s:
                continue
            if s == "gt":
                flags.append({"gt": True, "conf": None})
            elif s in ("—", "-", "?", "na", "NA"):
                flags.append({"gt": False, "conf": None})
            else:
                flags.append({"gt": False, "conf": float(s)})
        return flags

    def _marcar_importada(self, stem):
        arq = self.dir_revisao / "importadas.txt"
        with open(arq, "a", encoding="utf-8") as f:
            f.write(stem + "\n")

    def _append_prio(self, stem, classe="importada", origem="importada"):
        csv_path = self.dir_revisao / "prioridade_revisao.csv"
        novo = not csv_path.exists()
        campos = ["stem", "classe_original", "sugestoes", "incertas", "conflitos", "conf_min", "origem"]
        with open(csv_path, "a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=campos, extrasaction="ignore")
            if novo:
                w.writeheader()
            w.writerow(dict(stem=stem, classe_original=classe, sugestoes=0, incertas=0,
                            conflitos=0, conf_min="", origem=origem))

    def registrar(self, stem, origem="importada", classe_original=None):
        ordem = max((i["ordem"] for i in self.itens.values()), default=-1) + 1
        if classe_original is None:
            classe_original = "importada" if origem == "importada" else ""
        item = dict(
            stem=stem,
            ordem=ordem,
            classe_original=classe_original,
            conflitos=0,
            incertas=0,
            revisada=(self.dir_rev / f"{stem}.txt").exists(),
            contagem=self._contagem(self._caixas_em_disco(stem)[0]),
            origem=origem,
        )
        self.itens[stem] = item
        return item

    def atualizar_contagem(self, stem):
        item = self.itens[stem]
        item["contagem"] = self._contagem(self._caixas_em_disco(stem)[0])
        item["revisada"] = (self.dir_rev / f"{stem}.txt").exists()
        return item

    def carregar(self, stem):
        caixas, fonte = self._caixas_em_disco(stem)
        flags = self._flags(stem, fonte)
        if len(flags) == len(caixas):
            for c, f in zip(caixas, flags):
                c.update(f)
        return dict(stem=stem, fonte=fonte, caixas=caixas)

    def salvar(self, stem, caixas):
        for c in caixas:
            if not 0 <= c.c < len(self.classes):
                raise HTTPException(400, f"classe {c.c} inexistente")
        caixas = [r for r in map(recortar, caixas) if r is not None]
        yolo = "".join(f"{c.c} {c.x:.6f} {c.y:.6f} {c.w:.6f} {c.h:.6f}\n" for c in caixas)
        meta = {"revisada_em": datetime.now().isoformat(timespec="seconds"),
                "caixas": [{"gt": c.gt, "conf": c.conf} for c in caixas]}
        with self.trava:
            gravar_atomico(self.dir_rev / f"{stem}.txt", yolo)
            gravar_atomico(self.dir_meta / f"{stem}.json", json.dumps(meta))
            item = self.itens[stem]
            item.update(revisada=True, contagem=self._contagem([c.model_dump() for c in caixas]))
        return item

    def restaurar(self, stem):
        with self.trava:
            (self.dir_rev / f"{stem}.txt").unlink(missing_ok=True)
            (self.dir_meta / f"{stem}.json").unlink(missing_ok=True)
            item = self.itens[stem]
            item.update(revisada=False, contagem=self._contagem(self._caixas_em_disco(stem)[0]))
        return item

    def importar_bytes(self, nome, dados):
        """Grava a foto como JPG, cria rótulo vazio e registra no índice. Devolve o item."""
        with self.trava:
            stem = stem_seguro(nome, self.itens)
            destino = self.dir_imgs / f"{stem}.jpg"
            salvar_como_jpg(dados, destino)
            lbl = self.dir_pre / f"{stem}.txt"
            if not lbl.exists():
                lbl.write_text("", encoding="utf-8")
            self._append_prio(stem, "importada", origem="importada")
            self._marcar_importada(stem)
            item = self.registrar(stem, origem="importada", classe_original="importada")
        return item

    def importar_pasta_yolo(self, pasta):
        """Liga as fotos e copia rótulos remapeados. Não sobrescreve stem já existente."""
        from .importar_dataset import (
            caixas_remapeadas, classe_do_stem, inspecionar, ligar_ou_copiar,
            nomes_yaml, pasta_yolo, remap,
        )
        from .preclass import escrever_yolo

        pasta, dir_imgs, dir_lbl = pasta_yolo(pasta)
        info = inspecionar(pasta)
        nomes = nomes_yaml(pasta)
        tabela, classes_ign = remap(self.classes, nomes)
        if not nomes:
            tabela = {i: i for i in range(len(self.classes))}
        elif not tabela:
            raise HTTPException(400, f"Nenhuma classe de {pasta.name} bate com as do revisor: {classes_ign}")

        inseridas, puladas, n_gt, n_drop = [], [], 0, 0
        fotos = sorted(p for p in dir_imgs.iterdir() if p.suffix.lower() in {".jpg", ".jpeg"})
        with self.trava:
            for src in fotos:
                stem = src.stem
                if stem in self.itens:
                    puladas.append(stem)
                    continue
                destino = self.dir_imgs / f"{stem}.jpg"
                ligar_ou_copiar(src, destino)
                caixas = []
                if dir_lbl:
                    caixas, drop = caixas_remapeadas(dir_lbl / f"{stem}.txt", tabela)
                    n_drop += drop
                escrever_yolo(self.dir_pre / f"{stem}.txt", caixas)
                flags = "\n".join("gt" for _ in caixas)
                (self.dir_conf / f"{stem}.txt").write_text(flags + ("\n" if flags else ""), encoding="utf-8")
                classe = classe_do_stem(stem, self.classes) or (self.classes[caixas[0][0]] if caixas else "importada")
                self._append_prio(stem, classe, origem="importada")
                self._marcar_importada(stem)
                self.registrar(stem, origem="importada", classe_original=classe)
                inseridas.append(stem)
                n_gt += len(caixas)
        return {
            "inseridas": inseridas,
            "puladas": puladas,
            "n_gt": n_gt,
            "n_classes_descartadas": n_drop,
            "classes_ignoradas": classes_ign,
            "origem": info,
        }


def criar_app(revisao, revisado, modelo=None, raiz=None):
    ds = Dataset(revisao, revisado)
    raiz = Path(raiz) if raiz else Path(revisao).resolve().parent.parent
    modelo_path = Path(modelo) if modelo else raiz / MODELO_PADRAO
    banco = BancoVersoes(Path(revisao).resolve().parent / "versoes", revisao, revisado)
    jobs = {}
    jobs_lock = threading.Lock()
    app = FastAPI(title="Revisor de anotações")

    def checar(stem):
        if stem not in ds.itens:
            raise HTTPException(404, f"imagem desconhecida: {stem}")

    def resumo():
        n_conf = sum(1 for _ in ds.dir_conf.glob("*.txt")) if ds.dir_conf.exists() else 0
        n_imp = sum(1 for i in ds.itens.values() if i.get("origem") == "importada")
        return {
            "classes": ds.classes,
            "total": len(ds.itens),
            "n_revisadas": sum(1 for i in ds.itens.values() if i["revisada"]),
            "n_importadas": n_imp,
            "tem_conf": n_conf > 0,
            "n_conf": n_conf,
            "modelo": str(modelo_path),
            "modelo_ok": modelo_path.exists(),
        }

    @app.get("/api/config")
    def config():
        return resumo()

    @app.get("/api/imagens")
    def imagens():
        return list(ds.itens.values())

    @app.get("/api/imagens/{stem}")
    def imagem(stem: str):
        checar(stem)
        return ds.carregar(stem)

    @app.put("/api/imagens/{stem}")
    def salvar(stem: str, anotacao: Anotacao):
        checar(stem)
        return ds.salvar(stem, anotacao.caixas)

    @app.delete("/api/imagens/{stem}")
    def restaurar_img(stem: str):
        checar(stem)
        return ds.restaurar(stem)

    @app.post("/api/importar")
    async def importar(files: list[UploadFile] = File(...),
                       preclassificar: str = Form("false"),
                       conf: float = Form(0.25)):
        if not files:
            raise HTTPException(400, "nenhum arquivo enviado")
        inseridas, ignoradas, erros = [], [], []
        for f in files:
            ext = Path(f.filename or "").suffix.lower()
            if ext not in EXTENSOES:
                ignoradas.append(f.filename or "(sem nome)")
                continue
            try:
                dados = await f.read()
                item = ds.importar_bytes(f.filename, dados)
                inseridas.append(item["stem"])
            except Exception as e:
                erros.append(f"{f.filename}: {e}")
        out = {"inseridas": inseridas, "ignoradas": ignoradas, "erros": erros, "job_id": None}
        quer_pre = str(preclassificar).strip().lower() in ("1", "true", "on", "yes", "sim")
        if quer_pre and inseridas:
            if not modelo_path.exists():
                out["erros"].append(f"Modelo não encontrado ({modelo_path}); fotos inseridas sem pré-classificar.")
            else:
                out["job_id"] = _iniciar_preclass(inseridas, conf)
        return out

    def _iniciar_preclass(stems, conf):
        jid = uuid.uuid4().hex[:8]
        with jobs_lock:
            jobs[jid] = dict(id=jid, status="rodando", feito=0, total=len(stems),
                             msg="carregando modelo…", erro=None, stems=list(stems), n_caixas=0)

        def rodando():
            from .preclass import preclassificar as rodar

            def progresso(feito, total, stem):
                with jobs_lock:
                    jobs[jid].update(feito=feito, total=total,
                                     msg=f"{feito}/{total} {stem}".strip())

            try:
                r = rodar(stems, ds.dir_imgs, ds.dir_pre, ds.dir_conf,
                          modelo_path, conf=conf, progresso=progresso, manter_gt=True)
                with ds.trava:
                    for s in stems:
                        if s in ds.itens:
                            ds.atualizar_contagem(s)
                with jobs_lock:
                    jobs[jid].update(status="ok", n_caixas=r["n_caixas"], msg="pré-classificação concluída")
            except Exception as e:
                with jobs_lock:
                    jobs[jid].update(status="erro", erro=str(e), msg="falhou")

        threading.Thread(target=rodando, daemon=True).start()
        return jid

    @app.post("/api/preclassificar")
    def preclassificar_stems(pedido: PedidoPreclass):
        stems = [s for s in pedido.stems if s in ds.itens]
        if not stems:
            raise HTTPException(400, "nenhuma imagem válida")
        if not modelo_path.exists():
            raise HTTPException(400, f"modelo não encontrado: {modelo_path}")
        return {"job_id": _iniciar_preclass(stems, pedido.conf)}

    def _resolver_pasta(pasta):
        p = Path(pasta)
        if not p.is_absolute():
            p = raiz / pasta
        p = p.resolve()
        try:
            p.relative_to(raiz.resolve())
        except ValueError:
            raise HTTPException(400, "pasta fora do projeto")
        if not p.is_dir():
            raise HTTPException(404, f"pasta não encontrada: {pasta}")
        return p

    @app.get("/api/datasets")
    def datasets_locais():
        from .importar_dataset import listar_na_raiz
        itens = listar_na_raiz(raiz)
        conhecidos = set(ds.itens)
        for it in itens:
            it["n_ja_no_revisor"] = sum(
                1 for img in Path(it["caminho"]).joinpath("images").glob("*.jpg")
                if img.stem in conhecidos
            ) if Path(it["caminho"]).joinpath("images").is_dir() else 0
        return itens

    @app.post("/api/importar-dataset")
    def importar_dataset(pedido: PedidoDataset):
        pasta = _resolver_pasta(pedido.pasta)
        try:
            r = ds.importar_pasta_yolo(pasta)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(400, str(e))
        out = {**r, "job_id": None}
        if pedido.preclassificar and r["inseridas"]:
            if not modelo_path.exists():
                out.setdefault("erros", []).append(
                    f"Modelo não encontrado ({modelo_path}); dataset importado sem pré-classificar."
                )
            else:
                out["job_id"] = _iniciar_preclass(r["inseridas"], pedido.conf)
        return out

    @app.get("/api/jobs/{jid}")
    def status_job(jid: str):
        with jobs_lock:
            job = jobs.get(jid)
        if not job:
            raise HTTPException(404, "job desconhecido")
        return job

    @app.get("/api/versoes")
    def listar_versoes():
        return banco.listar()

    @app.post("/api/versoes")
    def criar_versao(body: NovaVersao):
        try:
            return banco.criar(nome=body.nome, nota=body.nota)
        except FileExistsError:
            raise HTTPException(409, "já existe uma versão com esse nome neste segundo; tente de novo")

    @app.post("/api/versoes/{vid}/restaurar")
    def restaurar_versao(vid: str):
        try:
            info = banco.restaurar(vid)
        except FileNotFoundError as e:
            raise HTTPException(404, str(e))
        ds.recarregar()
        return info

    @app.delete("/api/versoes/{vid}")
    def apagar_versao(vid: str):
        try:
            return banco.apagar(vid)
        except FileNotFoundError as e:
            raise HTTPException(404, str(e))

    @app.get("/img/{stem}")
    def arquivo(stem: str):
        checar(stem)
        return FileResponse(ds.dir_imgs / f"{stem}.jpg", headers={"Cache-Control": "max-age=3600"})

    @app.get("/")
    def index():
        return FileResponse(ESTATICOS / "index.html")

    app.mount("/static", StaticFiles(directory=ESTATICOS), name="static")
    return app
