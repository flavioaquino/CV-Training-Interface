"""Pré-classificação de fotos novas com o modelo YOLO.

Grava work/revisao/labels/<stem>.txt e work/revisao/conf/<stem>.txt, no mesmo
formato da pré-anotação original. Fotos novas não têm caixa confirmada pelo
backend, então todas as linhas de conf são a confiança da sugestão.
"""

from __future__ import annotations

from pathlib import Path


def escrever_yolo(caminho, caixas):
    linhas = [f"{c} {x:.6f} {y:.6f} {w:.6f} {h:.6f}" for c, x, y, w, h in caixas]
    Path(caminho).write_text("\n".join(linhas) + ("\n" if linhas else ""), encoding="utf-8")


def _flags_gt(dir_conf, dir_lbl, stem):
    """Caixas já gravadas que devem sobreviver à pré-classificação (✓ do backend)."""
    from .gerar_conf import ler_yolo
    caixas = ler_yolo(dir_lbl / f"{stem}.txt")
    arq = Path(dir_conf) / f"{stem}.txt"
    if not arq.exists():
        return [(c, True) for c in caixas]
    flags = []
    for l in arq.read_text(encoding="utf-8").splitlines():
        s = l.strip()
        if not s:
            continue
        flags.append(s == "gt")
    if len(flags) != len(caixas):
        return [(c, True) for c in caixas]
    return [(c, gt) for c, gt in zip(caixas, flags) if gt]


def preclassificar(stems, dir_imgs, dir_lbl, dir_conf, modelo, conf=0.25, imgsz=640,
                   progresso=None, manter_gt=True, iou_min=0.5):
    """Roda o modelo nas fotos de `stems` e grava rótulos + confiança.

    Se manter_gt, as caixas ✓ do backend ficam e o modelo só acrescenta o que
    não cobre essas caixas. progresso(feito, total, stem) após cada imagem.
    """
    from ultralytics import YOLO
    from .gerar_conf import iou

    dir_imgs, dir_lbl, dir_conf = Path(dir_imgs), Path(dir_lbl), Path(dir_conf)
    dir_lbl.mkdir(parents=True, exist_ok=True)
    dir_conf.mkdir(parents=True, exist_ok=True)
    modelo = Path(modelo)
    if not modelo.exists():
        raise FileNotFoundError(f"Modelo não encontrado: {modelo}")

    yolo = YOLO(str(modelo))
    total = len(stems)
    if progresso:
        progresso(0, total, "")
    n_caixas = 0
    for i, stem in enumerate(stems, 1):
        img = dir_imgs / f"{stem}.jpg"
        if not img.exists():
            if progresso:
                progresso(i, total, stem)
            continue
        gts = _flags_gt(dir_conf, dir_lbl, stem) if manter_gt else []
        r = yolo.predict(str(img), conf=conf, imgsz=imgsz, verbose=False)[0]
        caixas, confs = [], []
        for (c, x, y, w, h), _eh_gt in gts:
            caixas.append((int(c), float(x), float(y), float(w), float(h)))
            confs.append("gt")
        if r.boxes is not None and len(r.boxes):
            for (x, y, w, h), c, cf in zip(r.boxes.xywhn.tolist(), r.boxes.cls.tolist(), r.boxes.conf.tolist()):
                pred = (float(x), float(y), float(w), float(h))
                if any(iou(pred, (gx, gy, gw, gh)) >= iou_min for (_, gx, gy, gw, gh) in caixas):
                    continue
                caixas.append((int(c), float(x), float(y), float(w), float(h)))
                confs.append(f"{float(cf):.4f}")
        escrever_yolo(dir_lbl / f"{stem}.txt", caixas)
        (dir_conf / f"{stem}.txt").write_text("\n".join(confs) + ("\n" if confs else ""), encoding="utf-8")
        n_caixas += len(caixas)
        if progresso:
            progresso(i, total, stem)
    return {"stems": list(stems), "n_caixas": n_caixas}
