"""
Postagem no Instagram (Reels) pelo NAVEGADOR, usando a sua conta logada.
Dispensa o painel da Meta / Graph API.

  python instagram_web.py login
  python instagram_web.py checar
  python instagram_web.py postar <video.mp4> "<legenda>"
"""
import re, sys, time
import navegador as nv
from navegador import PrecisaHumano  # noqa: F401

P = "instagram"
HOME = "https://www.instagram.com/"
R = lambda *pal: re.compile("^(" + "|".join(pal) + ")$", re.I)


def _bloqueio(page):
    if "/accounts/login" in page.url or "/challenge" in page.url:
        return "sessao expirada ou verificacao do Instagram"
    try:
        if page.get_by_text(re.compile(r"tente novamente mais tarde|try again later|action blocked|ação bloqueada", re.I)).first.is_visible(timeout=300):
            return "Instagram pediu para tentar mais tarde (limite de acoes)"
    except Exception:
        pass
    return nv.captcha(page)


def login():
    return nv.login(P, "https://www.instagram.com/accounts/login/",
                    lambda ctx, page: any(c["name"] == "sessionid" and c["value"] for c in ctx.cookies("https://www.instagram.com")))


def checar():
    return nv.sessao(P, HOME, _bloqueio)


def _botao(page, *nomes, timeout=15000):
    """Clica no 1o botao visivel com algum dos textos (o Instagram usa div role=button)."""
    loc = page.get_by_role("button", name=R(*nomes)).or_(page.locator("div[role='button']", has_text=R(*nomes)))
    loc.first.wait_for(state="visible", timeout=timeout)
    nv.pausa(0.6, 1.4)
    loc.first.click(timeout=timeout)


def postar(mp4, legenda, timeout_upload=600):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        ctx, page = nv.abrir(pw, P)
        try:
            page.goto(HOME, wait_until="domcontentloaded")
            nv.pausa(4, 6)
            b = _bloqueio(page)
            if b:
                raise PrecisaHumano(f"{b} -> rode: python instagram_web.py login")
            for txt in ("Agora não", "Not now"):  # popups de notificacao/salvar login
                try:
                    page.get_by_role("button", name=txt).click(timeout=2500)
                except Exception:
                    pass
            # menu lateral "Criar" -> "Publicacao"
            page.locator("svg[aria-label='Novo post'], svg[aria-label='Nova publicação'], svg[aria-label='New post']").first.click()
            nv.pausa(1, 2)
            for txt in ("Postar", "Publicação", "Post"):
                try:
                    page.locator("a, div[role='link'], div[role='button'], span", has_text=R(txt)).first.click(timeout=3000)
                    break
                except Exception:
                    pass
            nv.pausa(1, 2)
            page.locator("input[type='file']").first.set_input_files(mp4)
            nv.log(P, "video enviado, ajustando o Reel...")
            nv.pausa(3, 5)
            try:  # aviso "videos agora sao compartilhados como reels"
                _botao(page, "OK", timeout=5000)
            except Exception:
                pass
            try:  # corte: Original (mantem 9:16)
                page.locator("svg[aria-label='Selecionar corte'], svg[aria-label='Select crop']").first.click(timeout=8000)
                nv.pausa(0.6, 1.2)
                page.get_by_text(R("Original"), exact=True).first.click(timeout=5000)
                nv.pausa(0.6, 1.2)
            except Exception:
                pass
            _botao(page, "Avançar", "Next"); nv.pausa(2, 3)
            _botao(page, "Avançar", "Next"); nv.pausa(2, 3)
            cap = page.locator("div[aria-label='Escreva uma legenda...'], div[aria-label='Write a caption...'], "
                               "div[role='textbox'][contenteditable='true']").first
            cap.wait_for(state="visible", timeout=30000)
            cap.click(); nv.pausa(0.4, 0.8)
            nv.digitar(page, legenda[:2200])  # sem Escape: no Instagram o Esc abre "Descartar post?"
            nv.pausa(1, 2)
            try:  # defesa: se o aviso de descarte abrir, cancela
                page.get_by_role("button", name=R("Cancelar", "Cancel")).click(timeout=1500)
            except Exception:
                pass
            try:  # fecha a lista de sugestao de hashtag clicando no titulo do dialogo
                page.get_by_text(R("Novo reel", "New reel", "Criar novo post", "Create new post")).first.click(timeout=3000)
            except Exception:
                pass
            nv.pausa(0.8, 1.5)
            try:
                _botao(page, "Compartilhar", "Share", timeout=8000)
            except Exception:
                page.locator("div[role='dialog'] div[role='button']", has_text=R("Compartilhar", "Share")).first.click(force=True)
            nv.log(P, "cliquei em Compartilhar, aguardando o envio...")
            t0 = time.time()
            while time.time() - t0 < timeout_upload:
                try:
                    if page.get_by_text(re.compile(r"(reel|publicação|post).*(compartilhad|shared)|foi compartilhad|has been shared", re.I)).first.is_visible(timeout=1500):
                        return "instagram (navegador): reel publicado"
                except Exception:
                    pass
                b = _bloqueio(page)
                if b:
                    raise PrecisaHumano(b)
                time.sleep(3)
            raise RuntimeError(f"nao confirmou a publicacao (print: {nv.shot(page, 'instagram_sem_confirmacao')})")
        except PrecisaHumano:
            raise
        except Exception as ex:
            raise RuntimeError(f"{ex} | print: {nv.shot(page, 'instagram_erro')}")
        finally:
            nv.pausa(2, 3)
            ctx.close()


if __name__ == "__main__":
    a = sys.argv[1:] or ["checar"]
    if a[0] == "login":
        login()
    elif a[0] == "checar":
        checar()
    elif a[0] == "postar" and len(a) >= 3:
        print(postar(a[1], a[2]))
    else:
        print(__doc__)
