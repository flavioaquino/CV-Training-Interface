"""Reconstrói um checkpoint .pt treinável do ultralytics a partir de um .onnx exportado por ele.

O export funde cada Conv+BatchNorm numa Conv com bias. Aqui recriamos a arquitetura original,
copiamos os pesos e "desfundimos" o BatchNorm: escolhemos parâmetros tais que BN(conv(x)) seja
exatamente igual à Conv fundida, usando estatísticas reais de ativação para que o BN também
se comporte bem em modo de treino.
"""

import ast
from pathlib import Path

import cv2
import numpy as np
import onnx
import torch
from onnx import numpy_helper
from ultralytics.data.augment import LetterBox
from ultralytics.nn.modules import Conv
from ultralytics.nn.tasks import DetectionModel, yaml_model_load

ESCALAS = {16: "n", 32: "s", 48: "m", 64: "l", 80: "x"}  # canais da 1ª conv -> escala do YOLOv8


def metadados(onnx_path):
    meta = {p.key: p.value for p in onnx.load(onnx_path, load_external_data=False).metadata_props}
    meta["names"] = ast.literal_eval(meta["names"])
    meta["imgsz"] = ast.literal_eval(meta["imgsz"])
    return meta


def preprocessar(img_path, imgsz=640):
    im = LetterBox((imgsz, imgsz), auto=False)(image=cv2.imread(str(img_path)))
    return torch.from_numpy(np.ascontiguousarray(im[..., ::-1].transpose(2, 0, 1))).float()[None] / 255


def _convs_ativadas(grafo):
    """Nomes dos módulos Conv cuja saída passa por SiLU (exportado como Sigmoid + Mul)."""
    entradas_sigmoid = {n.input[0] for n in grafo.graph.node if n.op_type == "Sigmoid"}
    return {n.input[1][:-len(".conv.weight")] for n in grafo.graph.node
            if n.op_type == "Conv" and n.input[1].endswith(".conv.weight") and n.output[0] in entradas_sigmoid}


def reconstruir(onnx_path, imagens_calibracao, yaml_base="yolov8{}.yaml"):
    """Retorna (DetectionModel em eval, metadados)."""
    grafo = onnx.load(onnx_path)
    meta = metadados(onnx_path)
    pesos = {i.name: torch.from_numpy(numpy_helper.to_array(i).copy()) for i in grafo.graph.initializer}
    ativadas = _convs_ativadas(grafo)
    escala = ESCALAS[pesos["model.0.conv.weight"].shape[0]]
    imgsz = meta["imgsz"][0]

    # Versões novas do ultralytics põem SiLU na cv1 do SPPF de YAMLs antigos; o modelo treinado pode não ter.
    cfg = yaml_model_load(yaml_base.format(escala))
    for i, linha in enumerate(cfg["backbone"]):
        if linha[2] == "SPPF":
            linha[3] = linha[3][:2] if f"model.{i}.cv1" in ativadas else [*linha[3][:2], 3, False]

    model = DetectionModel(cfg, nc=len(meta["names"]), verbose=False).eval()
    sd = model.state_dict()
    convs = {n: m for n, m in model.named_modules() if isinstance(m, Conv)}
    divergentes = [n for n, m in convs.items()
                   if (n in ativadas) != (not isinstance(m.act, torch.nn.Identity))]
    if divergentes:
        raise ValueError(f"Ativações diferentes do ONNX em: {divergentes}")
    bias_fundido = {}
    for k, v in pesos.items():
        prefixo = k.rsplit(".conv.", 1)[0]
        if prefixo in convs and k.endswith(".conv.weight"):
            sd[k].copy_(v)
        elif prefixo in convs and k.endswith(".conv.bias"):
            bias_fundido[prefixo] = v
        elif k in sd:
            if sd[k].shape != v.shape:
                raise ValueError(f"{k}: {tuple(sd[k].shape)} no modelo x {tuple(v.shape)} no ONNX")
            sd[k].copy_(v)
        elif k.startswith("model."):
            raise ValueError(f"Peso do ONNX sem destino no modelo: {k}")
    faltando = set(convs) - set(bias_fundido)
    if faltando:
        raise ValueError(f"Convs sem bias no ONNX (arquitetura diferente?): {sorted(faltando)[:5]}")

    for n, m in convs.items():  # BN = identidade + bias: já é exato em eval
        m.bn.running_mean.zero_()
        m.bn.running_var.fill_(1 - m.bn.eps)
        m.bn.weight.data.fill_(1.0)
        m.bn.bias.data.copy_(bias_fundido[n])

    soma = {n: 0 for n in convs}
    soma_q = {n: 0 for n in convs}
    contagem = {n: 0 for n in convs}

    def hook(nome):
        def f(_, __, saida):
            s = saida.detach().transpose(0, 1).reshape(saida.shape[1], -1).double()
            soma[nome] = soma[nome] + s.sum(1)
            soma_q[nome] = soma_q[nome] + (s * s).sum(1)
            contagem[nome] += s.shape[1]
        return f

    hooks = [m.conv.register_forward_hook(hook(n)) for n, m in convs.items()]
    with torch.no_grad():
        for p in imagens_calibracao:
            model(preprocessar(p, imgsz))
    for h in hooks:
        h.remove()

    for n, m in convs.items():
        mu = soma[n] / contagem[n]
        var = (soma_q[n] / contagem[n] - mu * mu).clamp_min(0)
        bn = m.bn
        bn.running_mean.copy_(mu.float())
        bn.running_var.copy_(var.float())
        bn.weight.data.copy_(torch.sqrt(var + bn.eps).float())
        bn.bias.data.copy_((mu.float() + bias_fundido[n]))

    model.names = meta["names"]
    model.args = {"imgsz": imgsz, "task": "detect"}
    return model, meta


def salvar(model, meta, destino):
    torch.save({
        "model": model,
        "train_args": {"model": f"yolov8{ESCALAS[model.model[0].conv.out_channels]}.yaml",
                       "imgsz": meta["imgsz"][0], "task": "detect"},
        "date": meta.get("date"),
        "version": meta.get("version"),
        "description": f"Reconstruído de ONNX: {meta.get('description', '')}",
    }, destino)
    return Path(destino)
