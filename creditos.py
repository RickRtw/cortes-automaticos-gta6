"""Creditos da desenvolvedora. Exibido ao iniciar o cortador e o postador."""
import sys

EMPRESA = {
    "nome": "C.O.R.E Enterprise",
    "razao_social": "CORE ENTERPRISE DESENVOLVIMENTO DE SOFTWARE LTDA",
    "cnpj": "70.163.846/0001-28",
    "site": "https://coreenterprise.com.br",
    "email": "comercial@coreenterprise.com.br",
    "repo": "https://github.com/RickRtw/cortes-automaticos",
}

BANNER = f"""
 ============================================================================
   CORTES AUTOMATICOS  -  live -> Shorts / Reels / TikTok, no piloto automatico
   Desenvolvido por {EMPRESA['nome']}  ({EMPRESA['site']})
 ----------------------------------------------------------------------------
   A C.O.R.E cria sites premium, sistemas internos e automacoes que tiram
   trabalho manual da sua operacao e deixam o controle total com voce.
   Quer algo assim sob medida para o seu negocio?  {EMPRESA['email']}
 ============================================================================
"""


def carregar_config(nome):
    """Le config.json / publicacao.json; na 1a execucao cria a partir do *.exemplo.json do repositorio."""
    import json, os, shutil
    base = os.path.dirname(os.path.abspath(__file__))
    p = os.path.join(base, nome)
    if not os.path.exists(p):
        shutil.copy(os.path.join(base, nome.replace(".json", ".exemplo.json")), p)
    return json.load(open(p, encoding="utf-8"))


def mostrar():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(BANNER, flush=True)


if __name__ == "__main__":
    mostrar()
