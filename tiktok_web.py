"""
Postagem no TikTok pelo NAVEGADOR (TikTok Studio), usando a sua conta logada.

  python tiktok_web.py login              # abre o navegador: faca login UMA vez (voce digita a senha)
  python tiktok_web.py checar             # confere se a sessao continua logada
  python tiktok_web.py postar <video.mp4> "<legenda>"   # teste manual
"""
import random, sys, time
import navegador as nv
from navegador import PrecisaHumano  # noqa: F401  (publicador identifica pelo nome da classe)

P = "tiktok"
UPLOAD = "https://www.tiktok.com/tiktokstudio/upload?from=webapp"


def _bloqueio(page):
    if "/login" in page.url:
        return "sessao expirada (pagina de login)"
    return nv.captcha(page)


def login():
    return nv.login(P, "https://www.tiktok.com/login",
                    lambda ctx, page: any(c["name"] == "sessionid" and c["value"] for c in ctx.cookies("https://www.tiktok.com")))


def checar():
    return nv.sessao(P, UPLOAD, _bloqueio)


def postar(mp4, legenda, timeout_upload=600):
    """Posta o video PUBLICO na conta logada."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        ctx, page = nv.abrir(pw, P)
        try:
            page.goto(UPLOAD, wait_until="domcontentloaded")
            nv.pausa(3, 5)
            b = _bloqueio(page)
            if b:
                raise PrecisaHumano(f"{b} -> rode: python tiktok_web.py login")
            page.locator("input[type='file']").first.set_input_files(mp4)
            nv.log(P, "video enviado, aguardando upload...")
            ed = page.locator("div[contenteditable='true']").first
            ed.wait_for(state="visible", timeout=60000)
            ed.click(); nv.pausa(0.3, 0.6)
            page.keyboard.press("Control+A"); page.keyboard.press("Backspace"); nv.pausa(0.3, 0.6)
            nv.digitar(page, legenda[:2200], hashtag_popup=True)
            btn = page.locator("button[data-e2e='post_video_button'], button:has-text('Publicar'), button:has-text('Post')").last
            t0 = time.time()
            while True:
                b = _bloqueio(page)
                if b:
                    raise PrecisaHumano(b)
                try:
                    if btn.is_visible() and btn.is_enabled() and btn.get_attribute("aria-disabled") != "true" \
                            and btn.get_attribute("data-disabled") != "true":
                        break
                except Exception:
                    pass
                if time.time() - t0 > timeout_upload:
                    raise RuntimeError("upload nao terminou a tempo")
                time.sleep(3)
            nv.pausa(1.5, 3)
            btn.scroll_into_view_if_needed()
            btn.click()
            nv.log(P, "cliquei em Publicar, confirmando...")
            for txt in ("Publicar agora", "Post now"):
                try:
                    page.get_by_role("button", name=txt).click(timeout=4000)
                except Exception:
                    pass
            t0 = time.time()
            while time.time() - t0 < 120:
                if "/tiktokstudio/content" in page.url or "/upload" not in page.url:
                    return "tiktok (navegador): publicado"
                b = _bloqueio(page)
                if b:
                    raise PrecisaHumano(b)
                time.sleep(2)
            raise RuntimeError(f"nao confirmou a publicacao (print: {nv.shot(page, 'tiktok_sem_confirmacao')})")
        except PrecisaHumano:
            raise
        except Exception as ex:
            raise RuntimeError(f"{ex} | print: {nv.shot(page, 'tiktok_erro')}")
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
