"""Gera work/revisao/conf/*.txt a partir do modelo, casando detecções com as caixas já gravadas.

Formato de cada linha (mesma ordem de work/revisao/labels/<stem>.txt):
  gt          → caixa confirmada pelo backend (bate com o rótulo original)
  0.8731      → confiança da sugestão do modelo
  —           → sem correspondência (fica sem número no revisor)

Assim o revisor mostra confiança e aplica limiar sem precisar refazer a pré-anotação.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def iou(a, b):
    ax1, ay1 = a[0] - a[2] / 2, a[1] - a[3] / 2
    ax2, ay2 = a[0] + a[2] / 2, a[1] + a[3] / 2
    bx1, by1 = b[0] - b[2] / 2, b[1] - b[3] / 2
    bx2, by2 = b[0] + b[2] / 2, b[1] + b[3] / 2
    inter = max(0, min(ax2, bx2) - max(ax1, bx1)) * max(0, min(ay2, by2) - max(ay1, by1))
    uniao = a[2] * a[3] + b[2] * b[3] - inter
    return inter / uniao if uniao > 0 else 0.0


def ler_yolo(caminho):
    if not Path(caminho).exists():
        return []
    caixas = []
    for linha in Path(caminho).read_text().splitlines():
        p = linha.split()
        if len(p) >= 5:
            caixas.append((int(p[0]), float(p[1]), float(p[2]), float(p[3]), float(p[4])))
    return caixas


def gerar(revisao: Path, modelo: Path, dataset_original: Path | None, conf_min: float, imgsz: int):
    from ultralytics import YOLO
    import yaml

    revisao = Path(revisao)
    dir_lbl = revisao / "labels"
    dir_img = revisao / "images"
    dir_conf = revisao / "conf"
    dir_conf.mkdir(parents=True, exist_ok=True)
    classes = [l.strip() for l in (revisao / "classes.txt").read_text(encoding="utf-8").splitlines() if l.strip()]
    id_por_nome = {n: i for i, n in enumerate(classes)}

    # IDs do dataset original → IDs finais (pelo nome). Classes descartadas (WRONG_WAY, faixa de ônibus) viram None.
    remap_gt = {}
    dir_gt = None
    if dataset_original and Path(dataset_original).exists():
        dir_gt = Path(dataset_original) / "labels"
        with open(Path(dataset_original) / "data.yaml", encoding="utf-8") as f:
            nomes_orig = yaml.safe_load(f)["names"]
        if isinstance(nomes_orig, list):
            nomes_orig = dict(enumerate(nomes_orig))
        for i, n in nomes_orig.items():
            remap_gt[int(i)] = id_por_nome.get(n)

    modelo_yolo = YOLO(str(modelo))
    stems = sorted(p.stem for p in dir_img.glob("*.jpg") if (dir_lbl / f"{p.stem}.txt").exists())
    stems_ok = set(stems)
    print(f"{len(stems)} imagens | modelo={modelo.name} | conf_min={conf_min}", flush=True)

    def gts_finais(stem):
        if not dir_gt:
            return []
        out = []
        for c, x, y, w, h in ler_yolo(dir_gt / f"{stem}.txt"):
            novo = remap_gt.get(c)
            if novo is not None:
                out.append((novo, x, y, w, h))
        return out

    n_gt = n_match = n_sem = 0
    stream = modelo_yolo.predict(str(dir_img), stream=True, conf=conf_min, imgsz=imgsz, verbose=False)
    feitos = 0
    for r in stream:
        stem = Path(r.path).stem
        if stem not in stems_ok:
            continue
        caixas = ler_yolo(dir_lbl / f"{stem}.txt")
        preds = []
        if r.boxes is not None and len(r.boxes):
            for (x, y, w, h), c, conf in zip(r.boxes.xywhn.tolist(), r.boxes.cls.tolist(), r.boxes.conf.tolist()):
                preds.append((int(c), x, y, w, h, float(conf)))

        gts = gts_finais(stem)
        linhas = []
        usados = set()
        for c, x, y, w, h in caixas:
            if any(g[0] == c and iou(g[1:], (x, y, w, h)) > 0.5 for g in gts):
                linhas.append("gt")
                n_gt += 1
                continue
            melhor, melhor_i = None, -1
            for j, p in enumerate(preds):
                if j in usados or p[0] != c:
                    continue
                score = iou(p[1:5], (x, y, w, h))
                if score > 0.5 and (melhor is None or score > melhor[0]):
                    melhor, melhor_i = (score, p[5]), j
            if melhor is not None:
                usados.add(melhor_i)
                linhas.append(f"{melhor[1]:.4f}")
                n_match += 1
            else:
                linhas.append("—")
                n_sem += 1
        (dir_conf / f"{stem}.txt").write_text("\n".join(linhas) + ("\n" if linhas else ""), encoding="utf-8")
        feitos += 1
        if feitos % 500 == 0 or feitos == len(stems):
            print(f"{feitos}/{len(stems)}  gt={n_gt} match={n_match} sem={n_sem}", flush=True)

    print(f"Pronto em {dir_conf}: {n_gt} caixas gt, {n_match} com confiança, {n_sem} sem correspondência")
    return dir_conf


def main(argv=None):
    p = argparse.ArgumentParser(description="Gera confianças para o revisor a partir do modelo .pt")
    p.add_argument("--revisao", default="work/revisao")
    p.add_argument("--modelo", default="models/fiscalizer_reconstruido.pt")
    p.add_argument("--dataset-original", default="infractions-yolo-2026-09-24T21-32-33-882Z",
                   help="para marcar as caixas confirmadas pelo backend como 'gt'")
    p.add_argument("--conf-min", type=float, default=0.01, help="limiar baixo na inferência: só serve para casar")
    p.add_argument("--imgsz", type=int, default=640)
    args = p.parse_args(argv)
    gerar(Path(args.revisao), Path(args.modelo),
          Path(args.dataset_original) if args.dataset_original else None,
          args.conf_min, args.imgsz)


if __name__ == "__main__":
    main()
