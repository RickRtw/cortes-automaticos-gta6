"""
Postagem no YouTube pelo NAVEGADOR (YouTube Studio), usando a sua conta logada.
Fica fora da cota da YouTube Data API (que so permite ~6 uploads/dia).

  python youtube_web.py login
  python youtube_web.py importar [Perfil]   # usa o perfil do SEU Chrome ja logado (feche o Chrome antes)
  python youtube_web.py checar
  python youtube_web.py postar <video.mp4> "<titulo>" "<descricao>"
"""
import re, sys, time
import navegador as nv
from navegador import PrecisaHumano  # noqa: F401

P = "youtube"
UPLOAD = "https://www.youtube.com/upload"


def _bloqueio(page):
    if "accounts.google.com" in page.url:
        return "sessao expirada (login do Google)"
    try:
        if page.get_by_text(re.compile(r"limite diário de envios|daily upload limit", re.I)).first.is_visible(timeout=300):
            return "limite diario de envios do YouTube atingido"
    except Exception:
        pass
    return nv.captcha(page)


def login():
    return nv.login(P, "https://accounts.google.com/ServiceLogin?service=youtube&continue=https://studio.youtube.com",
                    lambda ctx, page: "studio.youtube.com" in page.url and "accounts.google" not in page.url)


def checar():
    return nv.sessao(P, "https://studio.youtube.com", _bloqueio)


# Detecta a janela "Video publicado" percorrendo o DOM inteiro, incluindo shadow roots.
_JS_PUBLICADO = r"""() => {
  const out = {ok: false, link: null};
  const re = /(v[íi]deo publicado|video published|ainda processando|still processing)/i;
  const walk = (root) => {
    const els = root.querySelectorAll('*');
    for (const el of els) {
      if (el.tagName === 'A' && /youtube\.com\/shorts\/[\w-]{11}|youtu\.be\/[\w-]{11}/.test(el.href || '')) out.link = el.href;
      if (/^(YTCP-VIDEO-SHARE-DIALOG|YTCP-UPLOADS-STILL-PROCESSING-DIALOG)$/.test(el.tagName) && el.offsetParent !== null) out.ok = true;
      if (!out.ok && el.childElementCount === 0 && re.test(el.textContent || '') && el.offsetParent !== null) out.ok = true;
      if (el.shadowRoot) walk(el.shadowRoot);
    }
  };
  walk(document);
  return out;
}"""


def _caixa(page, seletor, texto):
    box = page.locator(seletor).first
    box.wait_for(state="visible", timeout=60000)
    box.click(); nv.pausa(0.3, 0.6)
    page.keyboard.press("Control+A"); page.keyboard.press("Backspace"); nv.pausa(0.3, 0.6)
    nv.digitar(page, texto)


def postar(mp4, titulo, descricao="", timeout_upload=1200):
    """Envia como Short PUBLICO e retorna o link."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        ctx, page = nv.abrir(pw, P)
        try:
            page.goto(UPLOAD, wait_until="domcontentloaded")
            nv.pausa(4, 6)
            b = _bloqueio(page)
            if b:
                raise PrecisaHumano(f"{b} -> rode: python youtube_web.py login")
            page.locator("input[type='file']").first.set_input_files(mp4)
            nv.log(P, "video enviado, preenchendo detalhes...")
            nv.pausa(3, 5)
            _caixa(page, "#title-textarea #textbox, ytcp-social-suggestions-textbox#title-textarea div[contenteditable='true']",
                   titulo[:100])
            if descricao:
                _caixa(page, "#description-textarea #textbox, ytcp-social-suggestions-textbox#description-textarea div[contenteditable='true']",
                       descricao[:4900])
            nv.pausa(0.8, 1.5)
            nao_kids = page.locator("tp-yt-paper-radio-button[name='VIDEO_MADE_FOR_KIDS_NOT_MFK']").first
            nao_kids.scroll_into_view_if_needed(); nao_kids.click()
            nv.pausa(1, 2)
            for _ in range(3):  # Detalhes -> Elementos -> Verificacoes -> Visibilidade
                page.locator("#next-button").first.click()
                nv.pausa(1.5, 2.5)
            page.locator("tp-yt-paper-radio-button[name='PUBLIC']").first.click()
            nv.pausa(1, 2)
            link = None
            try:
                link = page.locator("a.ytcp-video-info, .video-url-fadeable a").first.get_attribute("href", timeout=10000)
            except Exception:
                pass
            # so publica depois que o ARQUIVO terminou de subir (fechar antes cancela o envio)
            t0 = time.time()
            while time.time() - t0 < timeout_upload:
                b = _bloqueio(page)
                if b:
                    raise PrecisaHumano(b)
                prog = ""
                try:
                    prog = page.locator("ytcp-video-upload-progress, .progress-label").first.inner_text(timeout=2000)
                except Exception:
                    pass
                if not re.search(r"\d+\s*%|enviando|uploading|carregando", prog, re.I):
                    break
                time.sleep(4)
            nv.pausa(1, 2)
            page.locator("#done-button").first.click()
            nv.log(P, "cliquei em Publicar, confirmando...")
            t0 = time.time()
            while time.time() - t0 < 90:
                try:
                    st = page.evaluate(_JS_PUBLICADO)  # varre tambem shadow DOM dos componentes do Studio
                    if st.get("ok"):
                        link = st.get("link") or link
                        vid = re.search(r"(?:shorts/|youtu\.be/|v=)([\w-]{11})", link or "")
                        return f"https://youtube.com/shorts/{vid.group(1)}" if vid else "youtube (navegador): publicado"
                except Exception:
                    pass
                b = _bloqueio(page)
                if b:
                    raise PrecisaHumano(b)
                time.sleep(2)
            raise RuntimeError(f"nao confirmou a publicacao (print: {nv.shot(page, 'youtube_sem_confirmacao')})")
        except PrecisaHumano:
            raise
        except Exception as ex:
            raise RuntimeError(f"{ex} | print: {nv.shot(page, 'youtube_erro')}")
        finally:
            nv.pausa(2, 3)
            ctx.close()


if __name__ == "__main__":
    a = sys.argv[1:] or ["checar"]
    if a[0] == "login":
        login()
    elif a[0] == "importar":  # usa o perfil do seu Chrome ja logado (Chrome fechado)
        nv.importar_perfil(P, a[1] if len(a) > 1 else "Default")
        checar()
    elif a[0] == "checar":
        checar()
    elif a[0] == "postar" and len(a) >= 3:
        print(postar(a[1], a[2], a[3] if len(a) > 3 else ""))
    else:
        print(__doc__)
