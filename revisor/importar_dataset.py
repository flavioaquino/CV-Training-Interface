"""Importa um export YOLO (images/ + labels/ + data.yaml) para work/revisao.

Não mexe na pasta original: as fotos entram por hard link (ou cópia se o
disco não permitir). Os IDs de classe são traduzidos pelo nome em data.yaml
para as classes do revisor (ex.: no_seatbelt 0 → 9).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from .gerar_conf import ler_yolo


EXT_IMG = {".jpg", ".jpeg"}


def nomes_yaml(pasta):
    yaml_path = Path(pasta) / "data.yaml"
    if not yaml_path.exists():
        return {}
    import yaml
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    names = raw.get("names") or {}
    if isinstance(names, list):
        names = dict(enumerate(names))
    return {int(i): str(n) for i, n in names.items()}


def pasta_yolo(pasta):
    pasta = Path(pasta)
    imgs = pasta / "images"
    if not imgs.is_dir():
        imgs = pasta
    lbl = pasta / "labels"
    return pasta, imgs, lbl if lbl.is_dir() else None


def inspecionar(pasta):
    pasta, dir_imgs, dir_lbl = pasta_yolo(pasta)
    fotos = sorted(p for p in dir_imgs.iterdir() if p.suffix.lower() in EXT_IMG) if dir_imgs.exists() else []
    n_lbl = sum(1 for _ in dir_lbl.glob("*.txt")) if dir_lbl else 0
    nomes = nomes_yaml(pasta)
    return {
        "pasta": pasta.name,
        "caminho": str(pasta),
        "n_imagens": len(fotos),
        "n_labels": n_lbl,
        "classes": [nomes[i] for i in sorted(nomes)],
    }


def listar_na_raiz(raiz):
    raiz = Path(raiz)
    itens = []
    if not raiz.is_dir():
        return itens
    for p in sorted(raiz.iterdir()):
        if not p.is_dir():
            continue
        if p.name.startswith(".") or p.name in {".venv", "work", "models", "revisor", "datasets", "runs", "scratch"}:
            continue
        yaml_ok = (p / "data.yaml").exists()
        imgs_ok = (p / "images").is_dir()
        if yaml_ok or imgs_ok:
            itens.append(inspecionar(p))
    return itens


def remap(classes_revisor, nomes_origem):
    id_por_nome = {n: i for i, n in enumerate(classes_revisor)}
    tabela = {}
    ignoradas = []
    for i, n in nomes_origem.items():
        dest = id_por_nome.get(n)
        if dest is None:
            ignoradas.append(n)
        else:
            tabela[int(i)] = dest
    return tabela, ignoradas


def ligar_ou_copiar(src, dst):
    if dst.exists():
        return "existe"
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)
        return "link"
    except OSError:
        shutil.copy2(src, dst)
        return "copia"


def classe_do_stem(stem, classes):
    for nome in classes:
        if stem.endswith(f"_{nome}"):
            return nome
    return ""


def caixas_remapeadas(caminho, tabela):
    out, n_drop = [], 0
    for c, x, y, w, h in ler_yolo(caminho):
        novo = tabela.get(c)
        if novo is None:
            n_drop += 1
            continue
        out.append((novo, x, y, w, h))
    return out, n_drop
