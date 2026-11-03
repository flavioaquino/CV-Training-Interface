"""Testes das APIs de importar fotos e versionar rótulos (sem o modelo YOLO)."""

import io
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image
from fastapi.testclient import TestClient

from revisor.servidor import criar_app
from revisor.versoes import BancoVersoes


def jpg_bytes(cor=(40, 80, 120), size=(32, 24)):
    buf = io.BytesIO()
    Image.new("RGB", size, cor).save(buf, "JPEG", quality=90)
    return buf.getvalue()


def png_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (20, 20), (9, 9, 9)).save(buf, "PNG")
    return buf.getvalue()


class DatasetTmp:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.revisao = self.root / "revisao"
        self.revisado = self.root / "revisado"
        (self.revisao / "images").mkdir(parents=True)
        (self.revisao / "labels").mkdir()
        (self.revisao / "conf").mkdir()
        (self.revisado / "labels").mkdir(parents=True)
        (self.revisado / "meta").mkdir()
        (self.revisao / "classes.txt").write_text("a\nb\n", encoding="utf-8")
        Image.new("RGB", (32, 24), (1, 2, 3)).save(self.revisao / "images" / "orig.jpg", "JPEG")
        (self.revisao / "labels" / "orig.txt").write_text("0 0.5 0.5 0.2 0.2\n", encoding="utf-8")
        (self.revisao / "prioridade_revisao.csv").write_text(
            "stem,classe_original,sugestoes,incertas,conflitos,conf_min\norig,a,1,0,0,\n",
            encoding="utf-8",
        )
        self.app = criar_app(self.revisao, self.revisado, modelo=self.root / "nao_existe.pt", raiz=self.root)
        self.client = TestClient(self.app)

    def close(self):
        self.tmp.cleanup()


class ImportarTeste(unittest.TestCase):
    def setUp(self):
        self.d = DatasetTmp()

    def tearDown(self):
        self.d.close()

    def test_config_e_lista(self):
        cfg = self.d.client.get("/api/config").json()
        self.assertEqual(cfg["classes"], ["a", "b"])
        self.assertEqual(cfg["total"], 1)
        self.assertFalse(cfg["modelo_ok"])
        imgs = self.d.client.get("/api/imagens").json()
        self.assertEqual(imgs[0]["stem"], "orig")

    def test_importar_jpg_e_png(self):
        r = self.d.client.post(
            "/api/importar",
            files=[
                ("files", ("nova.jpg", jpg_bytes(), "image/jpeg")),
                ("files", ("outra.png", png_bytes(), "image/png")),
                ("files", ("nota.txt", b"nao", "text/plain")),
            ],
            data={"preclassificar": "false"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(sorted(body["inseridas"]), ["nova", "outra"])
        self.assertEqual(body["ignoradas"], ["nota.txt"])
        self.assertIsNone(body["job_id"])
        self.assertTrue((self.d.revisao / "images" / "nova.jpg").exists())
        self.assertTrue((self.d.revisao / "images" / "outra.jpg").exists())
        cfg = self.d.client.get("/api/config").json()
        self.assertEqual(cfg["total"], 3)
        self.assertEqual(cfg["n_importadas"], 2)
        imgs = {i["stem"]: i for i in self.d.client.get("/api/imagens").json()}
        self.assertEqual(imgs["nova"]["origem"], "importada")
        self.assertEqual(imgs["orig"]["origem"], "original")

    def test_stem_unico(self):
        self.d.client.post("/api/importar", files=[("files", ("nova.jpg", jpg_bytes(), "image/jpeg"))])
        r = self.d.client.post("/api/importar", files=[("files", ("nova.jpg", jpg_bytes((9, 9, 9)), "image/jpeg"))])
        self.assertEqual(r.json()["inseridas"], ["nova_1"])

    def test_preclass_sem_modelo_nao_trava_import(self):
        r = self.d.client.post(
            "/api/importar",
            files=[("files", ("x.jpg", jpg_bytes(), "image/jpeg"))],
            data={"preclassificar": "true"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json()["job_id"])
        self.assertTrue(any("Modelo não encontrado" in e for e in r.json()["erros"]))


class DatasetYoloTeste(unittest.TestCase):
    def setUp(self):
        self.d = DatasetTmp()
        src = self.d.root / "lote-yolo"
        (src / "images").mkdir(parents=True)
        (src / "labels").mkdir()
        (src / "data.yaml").write_text("names:\n  0: b\n", encoding="utf-8")
        Image.new("RGB", (32, 24), (8, 8, 8)).save(src / "images" / "foto_b.jpg", "JPEG")
        (src / "labels" / "foto_b.txt").write_text("0 0.5 0.5 0.2 0.2\n", encoding="utf-8")
        self.src = src

    def tearDown(self):
        self.d.close()

    def test_listar_e_importar_com_remap(self):
        lista = self.d.client.get("/api/datasets").json()
        nomes = [x["pasta"] for x in lista]
        self.assertIn("lote-yolo", nomes)
        r = self.d.client.post("/api/importar-dataset", json={
            "pasta": "lote-yolo",
            "preclassificar": False,
        })
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["inseridas"], ["foto_b"])
        self.assertEqual(body["n_gt"], 1)
        img = self.d.client.get("/api/imagens/foto_b").json()
        self.assertTrue(img["caixas"][0]["gt"])
        self.assertEqual(img["caixas"][0]["c"], 1)  # b é a classe 1 do revisor
        itens = {i["stem"]: i for i in self.d.client.get("/api/imagens").json()}
        self.assertEqual(itens["foto_b"]["origem"], "importada")
        self.assertEqual(itens["foto_b"]["classe_original"], "b")

    def test_nao_sobrescreve_stem_existente(self):
        Image.new("RGB", (32, 24), (1, 1, 1)).save(self.src / "images" / "orig.jpg", "JPEG")
        (self.src / "labels" / "orig.txt").write_text("0 0.1 0.1 0.1 0.1\n", encoding="utf-8")
        r = self.d.client.post("/api/importar-dataset", json={"pasta": "lote-yolo", "preclassificar": False})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("orig", r.json()["puladas"])
        orig = self.d.client.get("/api/imagens/orig").json()
        self.assertEqual(orig["caixas"][0]["c"], 0)


class VersoesTeste(unittest.TestCase):
    def setUp(self):
        self.d = DatasetTmp()

    def tearDown(self):
        self.d.close()

    def test_criar_restaurar_apagar(self):
        # revisão da foto original
        r = self.d.client.put("/api/imagens/orig", json={
            "caixas": [{"c": 1, "x": 0.4, "y": 0.4, "w": 0.3, "h": 0.3, "conf": None, "gt": False}],
        })
        self.assertEqual(r.status_code, 200)
        v1 = self.d.client.post("/api/versoes", json={"nome": "com review", "nota": "teste"}).json()
        self.assertEqual(v1["id"], "v001")
        self.assertEqual(v1["n_revisadas"], 1)

        # muda o rótulo e importa foto nova
        self.d.client.put("/api/imagens/orig", json={"caixas": []})
        self.d.client.post("/api/importar", files=[("files", ("depois.jpg", jpg_bytes(), "image/jpeg"))])
        self.d.client.put("/api/imagens/depois", json={
            "caixas": [{"c": 0, "x": 0.5, "y": 0.5, "w": 0.1, "h": 0.1}],
        })

        rest = self.d.client.post(f"/api/versoes/{v1['id']}/restaurar")
        self.assertEqual(rest.status_code, 200, rest.text)
        orig = self.d.client.get("/api/imagens/orig").json()
        self.assertEqual(orig["fonte"], "revisada")
        self.assertEqual(orig["caixas"][0]["c"], 1)
        depois = self.d.client.get("/api/imagens/depois").json()
        self.assertEqual(depois["fonte"], "revisada")
        self.assertEqual(len(depois["caixas"]), 1)

        lista = self.d.client.get("/api/versoes").json()
        self.assertEqual(len(lista), 1)
        ap = self.d.client.delete(f"/api/versoes/{v1['id']}")
        self.assertEqual(ap.status_code, 200)
        self.assertEqual(self.d.client.get("/api/versoes").json(), [])

    def test_banco_direto(self):
        banco = BancoVersoes(self.d.root / "versoes", self.d.revisao, self.d.revisado)
        info = banco.criar(nome="snap", nota="")
        self.assertTrue((self.d.root / "versoes" / info["pasta"] / "info.json").exists())
        manifesto = json.loads((self.d.root / "versoes" / "manifesto.json").read_text(encoding="utf-8"))
        self.assertEqual(manifesto[0]["id"], info["id"])


if __name__ == "__main__":
    unittest.main()
