"""Snapshots do dataset: rótulos e metadados, sem as fotos.

Cada versão é uma pasta em work/versoes/<id>/ com cópia de:
  revisado/labels, revisado/meta, revisao/labels, revisao/conf, classes.txt
Restaurar troca os rótulos atuais pelos da versão; as imagens no disco não mudam.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path

MANIFESTO = "manifesto.json"


def _gravar_json(caminho, obj):
    tmp = Path(caminho).with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(caminho)


def _copiar_pasta(src, dst, glob="*"):
    src, dst = Path(src), Path(dst)
    dst.mkdir(parents=True, exist_ok=True)
    if not src.exists():
        return 0
    n = 0
    for f in src.glob(glob):
        if f.is_file() and not f.name.endswith(".tmp") and f.name != "classes.txt":
            shutil.copy2(f, dst / f.name)
            n += 1
    return n


def _slug(nome):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (nome or "").strip())[:40].strip("-").lower()
    return s or "snapshot"


def _contar_caixas(pasta):
    n = 0
    pasta = Path(pasta)
    if not pasta.exists():
        return 0
    for f in pasta.glob("*.txt"):
        if f.name == "classes.txt":
            continue
        n += sum(1 for l in f.read_text(encoding="utf-8").splitlines() if l.strip())
    return n


class BancoVersoes:
    def __init__(self, raiz, revisao, revisado):
        self.raiz = Path(raiz)
        self.revisao = Path(revisao)
        self.revisado = Path(revisado)
        self.raiz.mkdir(parents=True, exist_ok=True)

    def _manifesto_path(self):
        return self.raiz / MANIFESTO

    def listar(self):
        p = self._manifesto_path()
        if not p.exists():
            return []
        return json.loads(p.read_text(encoding="utf-8"))

    def _salvar_manifesto(self, itens):
        _gravar_json(self._manifesto_path(), itens)

    def _proximo_id(self, itens):
        n = 0
        for it in itens:
            m = re.match(r"v(\d+)", it.get("id", ""))
            if m:
                n = max(n, int(m.group(1)))
        return f"v{n + 1:03d}"

    def criar(self, nome="", nota=""):
        itens = self.listar()
        vid = self._proximo_id(itens)
        pasta = self.raiz / f"{vid}_{_slug(nome)}"
        pasta.mkdir(parents=True, exist_ok=False)

        n_rev_lbl = _copiar_pasta(self.revisado / "labels", pasta / "revisado" / "labels", "*.txt")
        n_rev_meta = _copiar_pasta(self.revisado / "meta", pasta / "revisado" / "meta", "*.json")
        n_pre = _copiar_pasta(self.revisao / "labels", pasta / "revisao" / "labels", "*.txt")
        n_conf = _copiar_pasta(self.revisao / "conf", pasta / "revisao" / "conf", "*.txt")
        classes = self.revisao / "classes.txt"
        if classes.exists():
            shutil.copy2(classes, pasta / "classes.txt")
        csv_prio = self.revisao / "prioridade_revisao.csv"
        if csv_prio.exists():
            shutil.copy2(csv_prio, pasta / "prioridade_revisao.csv")
        imp = self.revisao / "importadas.txt"
        if imp.exists():
            shutil.copy2(imp, pasta / "importadas.txt")

        n_imgs = sum(1 for _ in (self.revisao / "images").glob("*.jpg")) if (self.revisao / "images").exists() else 0
        info = {
            "id": vid,
            "pasta": pasta.name,
            "nome": (nome or vid).strip(),
            "nota": (nota or "").strip(),
            "criada_em": datetime.now().isoformat(timespec="seconds"),
            "n_imagens": n_imgs,
            "n_revisadas": n_rev_lbl,
            "n_pre": n_pre,
            "n_caixas_revisadas": _contar_caixas(pasta / "revisado" / "labels"),
            "n_meta": n_rev_meta,
            "n_conf": n_conf,
        }
        _gravar_json(pasta / "info.json", info)
        itens.append(info)
        self._salvar_manifesto(itens)
        return info

    def _pasta(self, vid):
        for it in self.listar():
            if it["id"] == vid:
                p = self.raiz / it["pasta"]
                if p.exists():
                    return p, it
        return None, None

    def restaurar(self, vid):
        pasta, info = self._pasta(vid)
        if pasta is None:
            raise FileNotFoundError(f"versão desconhecida: {vid}")

        (self.revisado / "labels").mkdir(parents=True, exist_ok=True)
        (self.revisado / "meta").mkdir(parents=True, exist_ok=True)
        (self.revisao / "labels").mkdir(parents=True, exist_ok=True)
        (self.revisao / "conf").mkdir(parents=True, exist_ok=True)

        snap_rev = {f.stem for f in (pasta / "revisado" / "labels").glob("*.txt")} if (pasta / "revisado" / "labels").exists() else set()
        snap_pre = {f.stem for f in (pasta / "revisao" / "labels").glob("*.txt")} if (pasta / "revisao" / "labels").exists() else set()
        snap_stems = snap_rev | snap_pre

        # Rótulos da versão sobrescrevem os atuais das mesmas fotos.
        # Fotos importadas depois da versão mantêm os próprios rótulos.
        _copiar_pasta(pasta / "revisado" / "labels", self.revisado / "labels", "*.txt")
        _copiar_pasta(pasta / "revisado" / "meta", self.revisado / "meta", "*.json")
        for f in (self.revisado / "labels").glob("*.txt"):
            if f.stem in snap_stems and f.stem not in snap_rev:
                f.unlink()
                (self.revisado / "meta" / f"{f.stem}.json").unlink(missing_ok=True)
        _copiar_pasta(pasta / "revisao" / "labels", self.revisao / "labels", "*.txt")
        _copiar_pasta(pasta / "revisao" / "conf", self.revisao / "conf", "*.txt")
        if (pasta / "classes.txt").exists():
            shutil.copy2(pasta / "classes.txt", self.revisao / "classes.txt")
        self._mesclar_csv(pasta / "prioridade_revisao.csv", snap_stems)
        self._mesclar_importadas(pasta / "importadas.txt", snap_stems)
        return info

    def _mesclar_importadas(self, arq_versao, snap_stems):
        atual = self.revisao / "importadas.txt"
        depois = set()
        if atual.exists():
            depois = {l.strip() for l in atual.read_text(encoding="utf-8").splitlines() if l.strip() and l.strip() not in snap_stems}
        snap = set()
        if Path(arq_versao).exists():
            snap = {l.strip() for l in Path(arq_versao).read_text(encoding="utf-8").splitlines() if l.strip()}
        juntos = snap | depois
        if juntos:
            atual.write_text("\n".join(sorted(juntos)) + "\n", encoding="utf-8")

    def _mesclar_csv(self, csv_versao, snap_stems):
        """Restaura as linhas da versão; linhas de fotos posteriores (fora do snapshot) ficam."""
        import csv

        atual = self.revisao / "prioridade_revisao.csv"
        campos = ["stem", "classe_original", "sugestoes", "incertas", "conflitos", "conf_min"]
        por_stem = {}
        if atual.exists():
            with open(atual, encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    if row.get("stem") and row["stem"] not in snap_stems:
                        por_stem[row["stem"]] = row
        if Path(csv_versao).exists():
            with open(csv_versao, encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    if row.get("stem"):
                        por_stem[row["stem"]] = row
        if not por_stem:
            return
        with open(atual, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=campos, extrasaction="ignore")
            w.writeheader()
            for row in por_stem.values():
                w.writerow({k: row.get(k, "") for k in campos})

    def apagar(self, vid):
        pasta, info = self._pasta(vid)
        if pasta is None:
            raise FileNotFoundError(f"versão desconhecida: {vid}")
        shutil.rmtree(pasta)
        itens = [it for it in self.listar() if it["id"] != vid]
        self._salvar_manifesto(itens)
        return info
