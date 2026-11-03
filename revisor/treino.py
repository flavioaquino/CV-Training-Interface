"""Monta dataset de treino a partir dos rótulos revisados, treina YOLO e lê métricas.

O export YOLO original e as fotos em work/revisao/images não são alterados:
as imagens entram no dataset final por hard link (ou cópia).
"""

from __future__ import annotations

import csv
import json
import os
import random
import re
import shutil
import threading
import uuid
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

SEED = 42
FRACAO_VAL_PADRAO = 0.15


def _gravar_json(caminho, obj):
    tmp = Path(caminho).with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(caminho)


def _linkar(src, dst):
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        return
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _ler_yolo(caminho):
    if not Path(caminho).exists():
        return []
    caixas = []
    for linha in Path(caminho).read_text(encoding="utf-8").splitlines():
        p = linha.split()
        if len(p) >= 5:
            caixas.append((int(p[0]), float(p[1]), float(p[2]), float(p[3]), float(p[4])))
    return caixas


def _escrever_yolo(caminho, caixas):
    Path(caminho).parent.mkdir(parents=True, exist_ok=True)
    linhas = [f"{c} {x:.6f} {y:.6f} {w:.6f} {h:.6f}" for c, x, y, w, h in caixas]
    Path(caminho).write_text("\n".join(linhas) + ("\n" if linhas else ""), encoding="utf-8")


def _slug(nome):
    s = re.sub(r"[^a-zA-Z0-9]+", "_", (nome or "").strip())[:40].strip("_").lower()
    return s or "exp"


def classes_de(revisao):
    arq = Path(revisao) / "classes.txt"
    if not arq.exists():
        return []
    return [l.strip() for l in arq.read_text(encoding="utf-8").splitlines() if l.strip()]


def origem_por_stem(revisao):
    """Classe confirmada pelo backend (para o split estratificado)."""
    csv_path = Path(revisao) / "prioridade_revisao.csv"
    out = {}
    if csv_path.exists():
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                if row.get("stem"):
                    out[row["stem"]] = row.get("classe_original") or "misc"
    return out


def resumo_treino(revisao, revisado):
    revisao, revisado = Path(revisao), Path(revisado)
    classes = classes_de(revisao)
    imgs = {p.stem for p in (revisao / "images").glob("*.jpg")}
    labels = {p.stem for p in (revisado / "labels").glob("*.txt") if p.name != "classes.txt"}
    prontas = sorted(imgs & labels)
    por_classe = Counter()
    for s in prontas:
        for c, *_ in _ler_yolo(revisado / "labels" / f"{s}.txt"):
            if 0 <= c < len(classes):
                por_classe[classes[c]] += 1
    return {
        "n_imagens": len(imgs),
        "n_revisadas": len(prontas),
        "n_pendentes": len(imgs - labels),
        "classes": classes,
        "caixas_por_classe": dict(por_classe),
    }


def montar_dataset(revisao, revisado, destino, fracao_val=FRACAO_VAL_PADRAO, seed=SEED):
    """Cria destino/{images,labels}/{train,val} + data.yaml. Só entra o que está revisado."""
    revisao, revisado, destino = Path(revisao), Path(revisado), Path(destino)
    classes = classes_de(revisao)
    if not classes:
        raise ValueError("work/revisao/classes.txt não encontrado")
    origem = origem_por_stem(revisao)
    imgs = {p.stem for p in (revisao / "images").glob("*.jpg")}
    labels = {p.stem for p in (revisado / "labels").glob("*.txt") if p.name != "classes.txt"}
    stems = sorted(imgs & labels)
    if len(stems) < 2:
        raise ValueError(f"preciso de ao menos 2 imagens revisadas (achei {len(stems)})")

    por_classe = defaultdict(list)
    for s in stems:
        por_classe[origem.get(s) or "misc"].append(s)

    rng = random.Random(seed)
    split = {}
    for _, lista in sorted(por_classe.items()):
        lista = sorted(lista)
        rng.shuffle(lista)
        n_val = max(1, round(len(lista) * fracao_val)) if len(lista) > 1 else (1 if len(lista) == 1 and len(stems) > 1 else 0)
        if len(lista) == 1 and len(stems) == 1:
            n_val = 0
        # garante pelo menos 1 no treino quando há 2+
        if n_val >= len(lista) and len(lista) > 1:
            n_val = len(lista) - 1
        split.update({s: "val" for s in lista[:n_val]})
        split.update({s: "train" for s in lista[n_val:]})

    if destino.exists():
        shutil.rmtree(destino)
    n_train = n_val = 0
    for s, parte in split.items():
        _linkar(revisao / "images" / f"{s}.jpg", destino / "images" / parte / f"{s}.jpg")
        _escrever_yolo(destino / "labels" / parte / f"{s}.txt",
                       _ler_yolo(revisado / "labels" / f"{s}.txt"))
        if parte == "train":
            n_train += 1
        else:
            n_val += 1

    import yaml
    yaml_path = destino / "data.yaml"
    yaml_path.write_text(yaml.safe_dump({
        "path": str(destino.resolve()),
        "train": "images/train",
        "val": "images/val",
        "names": {i: n for i, n in enumerate(classes)},
    }, allow_unicode=True, sort_keys=False), encoding="utf-8")

    info = {
        "destino": str(destino),
        "n_train": n_train,
        "n_val": n_val,
        "n_total": n_train + n_val,
        "fracao_val": fracao_val,
        "classes": classes,
        "criado_em": datetime.now().isoformat(timespec="seconds"),
    }
    _gravar_json(destino / "info.json", info)
    return info


def bases_disponiveis(raiz):
    raiz = Path(raiz)
    itens = []
    candidatos = [raiz / "models" / "fiscalizer_reconstruido.pt"]
    if (raiz / "models").is_dir():
        candidatos.extend(sorted((raiz / "models").glob("*.pt")))
    vistos = set()
    for p in candidatos:
        if not p.exists() or p.resolve() in vistos:
            continue
        vistos.add(p.resolve())
        itens.append({"id": p.name, "caminho": str(p), "nome": p.stem, "tipo": "base"})
    # experimentos anteriores também servem de ponto de partida
    runs = raiz / "runs"
    if runs.is_dir():
        for best in sorted(runs.glob("*/weights/best.pt")):
            if best.resolve() in vistos:
                continue
            vistos.add(best.resolve())
            itens.append({
                "id": f"run:{best.parent.parent.name}",
                "caminho": str(best),
                "nome": f"{best.parent.parent.name}/best",
                "tipo": "experimento",
            })
    return itens


def _ler_results_csv(caminho):
    if not Path(caminho).exists():
        return [], {}
    with open(caminho, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return [], {}
    # chaves típicas do ultralytics: metrics/mAP50(B), metrics/mAP50-95(B), ...
    def num(row, *keys):
        for k in keys:
            for col, v in row.items():
                if col.strip() == k or col.strip().endswith(k):
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        pass
        return None

    ultima = rows[-1]
    melhor = max(rows, key=lambda r: num(r, "metrics/mAP50(B)", "mAP50") or -1)
    resumo = {
        "epochs_feitas": len(rows),
        "epoca_ultima": int(float(ultima.get("epoch", len(rows) - 1) or 0)),
        "map50": num(melhor, "metrics/mAP50(B)", "mAP50"),
        "map50_95": num(melhor, "metrics/mAP50-95(B)", "mAP50-95"),
        "precisao": num(melhor, "metrics/precision(B)", "precision"),
        "recall": num(melhor, "metrics/recall(B)", "recall"),
        "epoca_melhor": int(float(melhor.get("epoch", 0) or 0)),
    }
    curva = []
    for r in rows:
        curva.append({
            "epoch": int(float(r.get("epoch", 0) or 0)),
            "map50": num(r, "metrics/mAP50(B)", "mAP50"),
            "map50_95": num(r, "metrics/mAP50-95(B)", "mAP50-95"),
            "box_loss": num(r, "train/box_loss"),
            "cls_loss": num(r, "train/cls_loss"),
        })
    return curva, resumo


def _ler_args(pasta):
    for nome in ("args.yaml", "args.yml"):
        arq = Path(pasta) / nome
        if arq.exists():
            import yaml
            return yaml.safe_load(arq.read_text(encoding="utf-8")) or {}
    return {}


def listar_experimentos(runs):
    runs = Path(runs)
    if not runs.is_dir():
        return []
    itens = []
    for pasta in sorted(runs.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not pasta.is_dir():
            continue
        best = pasta / "weights" / "best.pt"
        last = pasta / "weights" / "last.pt"
        if not best.exists() and not last.exists() and not (pasta / "results.csv").exists():
            continue
        curva, resumo = _ler_results_csv(pasta / "results.csv")
        args = _ler_args(pasta)
        plots = [p.name for p in pasta.glob("*.png")]
        itens.append({
            "id": pasta.name,
            "pasta": str(pasta),
            "best": str(best) if best.exists() else None,
            "last": str(last) if last.exists() else None,
            "criado_em": datetime.fromtimestamp(pasta.stat().st_mtime).isoformat(timespec="seconds"),
            "args": {
                "epochs": args.get("epochs"),
                "imgsz": args.get("imgsz"),
                "batch": args.get("batch"),
                "model": args.get("model"),
                "data": args.get("data"),
                "patience": args.get("patience"),
            },
            "metricas": resumo,
            "n_epochs_curva": len(curva),
            "plots": plots,
        })
    return itens


def detalhe_experimento(runs, exp_id):
    pasta = Path(runs) / exp_id
    if not pasta.is_dir():
        raise FileNotFoundError(f"experimento desconhecido: {exp_id}")
    curva, resumo = _ler_results_csv(pasta / "results.csv")
    args = _ler_args(pasta)
    best = pasta / "weights" / "best.pt"
    last = pasta / "weights" / "last.pt"
    return {
        "id": exp_id,
        "pasta": str(pasta),
        "best": str(best) if best.exists() else None,
        "last": str(last) if last.exists() else None,
        "args": args,
        "metricas": resumo,
        "curva": curva,
        "plots": [p.name for p in pasta.glob("*.png")],
        "criado_em": datetime.fromtimestamp(pasta.stat().st_mtime).isoformat(timespec="seconds"),
    }


def caminho_modelo(raiz, modelo_id):
    """Resolve id de base (arquivo) ou run:nome / nome de experimento."""
    raiz = Path(raiz)
    if modelo_id.startswith("run:"):
        nome = modelo_id[4:]
        p = raiz / "runs" / nome / "weights" / "best.pt"
        if p.exists():
            return p
        raise FileNotFoundError(modelo_id)
    # experimento direto
    p = raiz / "runs" / modelo_id / "weights" / "best.pt"
    if p.exists():
        return p
    p = raiz / "models" / modelo_id
    if p.exists():
        return p
    p = raiz / "models" / f"{modelo_id}.pt"
    if p.exists():
        return p
    raise FileNotFoundError(modelo_id)


def inferir(modelo_path, imagem, conf=0.25, imgsz=640):
    from ultralytics import YOLO
    yolo = YOLO(str(modelo_path))
    r = yolo.predict(str(imagem), conf=conf, imgsz=imgsz, verbose=False)[0]
    nomes = yolo.names if isinstance(yolo.names, dict) else dict(enumerate(yolo.names))
    caixas = []
    if r.boxes is not None and len(r.boxes):
        for (x, y, w, h), c, cf in zip(r.boxes.xywhn.tolist(), r.boxes.cls.tolist(), r.boxes.conf.tolist()):
            cid = int(c)
            caixas.append({
                "c": cid,
                "nome": nomes.get(cid, str(cid)),
                "x": float(x), "y": float(y), "w": float(w), "h": float(h),
                "conf": float(cf),
            })
    return {"classes": [nomes[i] for i in sorted(nomes)], "caixas": caixas,
            "imgsz": imgsz, "modelo": str(modelo_path)}


class GerenteTreino:
    def __init__(self, raiz, revisao, revisado, runs=None, datasets=None):
        self.raiz = Path(raiz)
        self.revisao = Path(revisao)
        self.revisado = Path(revisado)
        self.runs = Path(runs) if runs else self.raiz / "runs"
        self.datasets = Path(datasets) if datasets else self.raiz / "datasets"
        self.jobs = {}
        self.lock = threading.Lock()
        self.runs.mkdir(parents=True, exist_ok=True)
        self.datasets.mkdir(parents=True, exist_ok=True)

    def _atualizar_progresso_csv(self, job):
        """Lê results.csv do run (quando existir) e atualiza feito/métricas."""
        pasta = job.get("run_dir")
        if not pasta:
            return
        csv_path = Path(pasta) / "results.csv"
        if not csv_path.exists():
            return
        _, resumo = _ler_results_csv(csv_path)
        if not resumo:
            return
        job["metricas"] = resumo
        feitas = int(resumo.get("epochs_feitas") or 0)
        # não regride se o callback já avançou mais
        job["feito"] = max(int(job.get("feito") or 0), feitas)
        total = int(job.get("epochs") or job.get("total") or 0) or feitas
        job["msg"] = (
            f"época {resumo.get('epoca_ultima', feitas)}/{total} · "
            f"mAP50={resumo.get('map50')}"
        )

    def status(self, jid):
        with self.lock:
            job = self.jobs.get(jid)
            if not job:
                raise FileNotFoundError(jid)
            # cópia rasa para não deixar o cliente mutar o dict vivo
            out = dict(job)
            if out["status"] == "rodando":
                self._atualizar_progresso_csv(out)
                # espelha de volta no job oficial
                job["feito"] = out.get("feito", job.get("feito"))
                job["metricas"] = out.get("metricas", job.get("metricas"))
                job["msg"] = out.get("msg", job.get("msg"))
            return dict(job)

    def iniciar(self, nome="", base="fiscalizer_reconstruido.pt", epochs=60, patience=15,
                imgsz=640, batch=-1, fracao_val=FRACAO_VAL_PADRAO, workers=0):
        resumo = resumo_treino(self.revisao, self.revisado)
        if resumo["n_revisadas"] < 2:
            raise ValueError(f"só há {resumo['n_revisadas']} imagem(ns) revisada(s); revise mais antes de treinar")

        modelo = caminho_modelo(self.raiz, base)
        slug = _slug(nome) if nome else f"ft_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        jid = uuid.uuid4().hex[:8]
        run_dir = self.runs / slug
        job = dict(
            id=jid, status="rodando", feito=0, total=int(epochs), epochs=int(epochs),
            msg="montando dataset…", erro=None, nome=slug, base=str(modelo),
            run_dir=str(run_dir), dataset=None, metricas=None, best=None,
            early_stop=False,
        )
        with self.lock:
            # um treino por vez: GPU
            if any(j["status"] == "rodando" for j in self.jobs.values()):
                raise RuntimeError("já existe um treino em andamento")
            self.jobs[jid] = job

        def rodando():
            try:
                dest = self.datasets / slug
                info = montar_dataset(self.revisao, self.revisado, dest,
                                      fracao_val=fracao_val, seed=SEED)
                with self.lock:
                    job.update(dataset=info,
                               msg=f"dataset pronto ({info['n_train']} train / {info['n_val']} val) · carregando modelo…")

                from ultralytics import YOLO
                yolo = YOLO(str(modelo))

                def on_fit_epoch_end(trainer):
                    # trainer.epoch é 0-based; epochs é o total planejado
                    epoca = int(trainer.epoch) + 1
                    total = int(getattr(trainer, "epochs", epochs) or epochs)
                    with self.lock:
                        job["feito"] = epoca
                        job["total"] = total
                        job["epochs"] = total
                        job["run_dir"] = str(Path(trainer.save_dir))
                        job["msg"] = f"época {epoca}/{total}"
                        self._atualizar_progresso_csv(job)
                        if job.get("metricas", {}).get("map50") is not None:
                            job["msg"] = (
                                f"época {epoca}/{total} · mAP50={job['metricas']['map50']}"
                            )

                yolo.add_callback("on_fit_epoch_end", on_fit_epoch_end)

                with self.lock:
                    job["msg"] = f"treinando… (0/{epochs})"

                results = yolo.train(
                    data=str(dest / "data.yaml"),
                    epochs=int(epochs),
                    patience=int(patience),
                    imgsz=int(imgsz),
                    batch=int(batch),
                    workers=int(workers),
                    seed=SEED,
                    project=str(self.runs),
                    name=slug,
                    exist_ok=True,
                    plots=True,
                    verbose=True,
                )
                save_dir = Path(yolo.trainer.save_dir) if getattr(yolo, "trainer", None) else run_dir
                best = save_dir / "weights" / "best.pt"
                # cópia estável em models/
                if best.exists():
                    alvo = self.raiz / "models" / f"{slug}_best.pt"
                    alvo.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(best, alvo)
                _, met = _ler_results_csv(save_dir / "results.csv")
                feitas = int((met or {}).get("epochs_feitas") or job.get("feito") or 0)
                early = feitas < int(epochs)
                with self.lock:
                    job.update(
                        status="ok",
                        feito=int(epochs),  # barra em 100% mesmo com early stop
                        total=int(epochs),
                        run_dir=str(save_dir),
                        best=str(best) if best.exists() else None,
                        metricas=met,
                        early_stop=early,
                        epochs_rodadas=feitas,
                        msg=(
                            f"treino concluído · {feitas}/{epochs} épocas"
                            + (" (early stop)" if early else "")
                        ),
                        resultado=str(results) if results is not None else None,
                    )
            except Exception as e:
                with self.lock:
                    job.update(status="erro", erro=str(e), msg="falhou")

        threading.Thread(target=rodando, daemon=True, name=f"treino-{jid}").start()
        return job
