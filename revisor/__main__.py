import argparse
import sys

import uvicorn

from .servidor import criar_app


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("gerar-conf", "gerar_conf"):
        from .gerar_conf import main as gerar_main
        return gerar_main(argv[1:])

    p = argparse.ArgumentParser(
        prog="python -m revisor",
        description="Revisor web de anotações YOLO. Use 'gerar-conf' para criar as confianças.",
    )
    p.add_argument("--revisao", default="work/revisao", help="pasta da pré-anotação (images/, labels/, classes.txt)")
    p.add_argument("--revisado", default="work/revisado", help="onde gravar os rótulos revisados")
    p.add_argument("--modelo", default=None, help="pesos YOLO para pré-classificar fotos novas")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--porta", type=int, default=8765)
    args = p.parse_args(argv)

    print(f"Revisor em http://{args.host}:{args.porta}", flush=True)
    uvicorn.run(
        criar_app(args.revisao, args.revisado, modelo=args.modelo),
        host=args.host, port=args.porta, log_level="warning",
    )


if __name__ == "__main__":
    main()
