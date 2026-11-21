"""
Base comum da postagem pelo NAVEGADOR (TikTok, YouTube Studio, Instagram).

- 1 perfil de navegador por plataforma em ./navegador_<plataforma> (cookies/sessao; NUNCA compartilhar).
- Usa o Chrome instalado, janela visivel e ritmo humano.
- Login e SEMPRE feito por voce (o sistema nunca digita senha).
- CAPTCHA/verificacao: o sistema NAO tenta contornar. Para, avisa e devolve o post para a fila.
"""
import json, os, random, sys, time

BASE = os.path.dirname(os.path.abspath(__file__))
SHOTS = os.path.join(BASE, "work", "navegador")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


class PrecisaHumano(RuntimeError):
    """Login expirado, CAPTCHA ou verificacao: precisa de voce no navegador."""


def log(plat, *a):
    print(time.strftime("[%H:%M:%S]"), f"[{plat}-web]", *a, flush=True)


def pausa(a=0.6, b=1.6):
    time.sleep(random.uniform(a, b))


def _visivel():
    try:
        return json.load(open(os.path.join(BASE, "publicacao.json"), encoding="utf-8")).get("navegador_visivel", True)
    except Exception:
        return True


def abrir(pw, plat, headless=None):
    perfil = os.path.join(BASE, f"navegador_{plat}")
    os.makedirs(perfil, exist_ok=True)
    kw = dict(user_data_dir=perfil, headless=(not _visivel()) if headless is None else headless,
              viewport={"width": 1366, "height": 860}, locale="pt-BR",
              args=["--disable-blink-features=AutomationControlled"])
    try:
        ctx = pw.chromium.launch_persistent_context(channel="chrome", **kw)  # Chrome real instalado
    except Exception:
        ctx = pw.chromium.launch_persistent_context(**kw)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    return ctx, page


def shot(page, nome):
    try:
        os.makedirs(SHOTS, exist_ok=True)
        p = os.path.join(SHOTS, f"{time.strftime('%Y%m%d_%H%M%S')}_{nome}.png")
        page.screenshot(path=p, full_page=True)
        return p
    except Exception:
        return None


def captcha(page):
    for sel in ("#captcha-verify-container", "[id*='captcha']", "div.captcha_verify_container",
                "iframe[src*='captcha']", "iframe[src*='recaptcha']", "iframe[title*='challenge']"):
        try:
            if page.locator(sel).first.is_visible(timeout=300):
                return "CAPTCHA/verificacao na tela"
        except Exception:
            pass
    return None


def digitar(page, texto, hashtag_popup=False):
    """Digita como gente. hashtag_popup: fecha o popup de sugestao apos cada #tag (TikTok/Instagram)."""
    for i, parte in enumerate(texto.split(" ")):
        sep = "" if i == 0 else " "
        if hashtag_popup and parte.startswith("#"):
            page.keyboard.type(sep + parte, delay=random.randint(40, 90))
            pausa(0.7, 1.1)
            page.keyboard.press("Escape")
        else:
            for j, linha in enumerate((sep + parte).split("\n")):
                if j:
                    page.keyboard.press("Shift+Enter")
                page.keyboard.type(linha, delay=random.randint(25, 60))
    pausa(0.4, 0.9)


def login(plat, url, logado, avisar=""):
    """Abre o navegador para VOCE fazer login. logado(ctx, page) -> bool detecta quando terminou."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        ctx, page = abrir(pw, plat, headless=False)
        page.goto(url, wait_until="domcontentloaded")
        print(f"\n>>> Faca login no {plat} na janela do navegador. {avisar}")
        print(">>> Detecto sozinho quando terminar (ate 10 min).\n", flush=True)
        t0 = time.time()
        ok = False
        while time.time() - t0 < 600:
            try:
                if logado(ctx, page):
                    ok = True
                    break
            except Exception:
                pass
            time.sleep(2)
        pausa(2, 3)
        print(f"{plat}: " + ("OK, logado." if ok else "nao detectei o login; rode de novo."))
        ctx.close()
        return ok


def sessao(plat, url, bloqueio):
    """Abre a plataforma e confere se a sessao continua valida."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        ctx, page = abrir(pw, plat)
        page.goto(url, wait_until="domcontentloaded")
        pausa(4, 5)
        b = bloqueio(page)
        print(f"{plat}-web: " + (f"ERRO -> {b}" if b else "OK (logado)"))
        ctx.close()
        return not b
