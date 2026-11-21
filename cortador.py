"""
Cortador automatico de lives (YouTube) -> Shorts 9:16 com legenda.

Uso:
  python cortador.py live <url>          # acompanha a live em tempo real
  python cortador.py vod  <url> [--max N] # processa live ja gravada
  python cortador.py auto <streamer>       # loop: corta a live (ao vivo em GTA) ou a ultima live de GTA gravada
  python cortador.py todos                 # abre 1 janela 'auto' por streamer (config.json > streamers)

Sinais: pico de chat (msgs/s ponderado por palavras de hype) + pico de audio.
Saida: cortes/<data>_<n>.mp4  +  .json com titulo, transcricao, score.
"""
import json, os, re, sys, time, glob, queue, shutil, subprocess, threading, statistics, datetime as dt
from collections import defaultdict
import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
from creditos import carregar_config
CFG = carregar_config("config.json")
OUT = os.path.join(BASE, "cortes")
WORK = os.path.join(BASE, "work")
SEG_DUR = 10
YTDLP = [sys.executable, "-m", "yt_dlp", "--no-warnings", "-q"]
HYPE = [w.lower() for w in CFG["palavras_hype"]]
# streamer atual (cada processo cuida de 1 streamer; ver config.json > streamers)
ST = dict(CFG.get("streamer", {}), facecam=CFG.get("facecam"))
GTA_RE = re.compile(r"gta|vice city|leonida|grand theft", re.I)
FEITOS = os.path.join(BASE, "vods_feitos.json")


def usar_streamer(sid):
    global ST
    s = CFG.get("streamers", {}).get(sid)
    if not s:
        raise SystemExit(f"streamer '{sid}' nao esta em config.json > streamers")
    ST = dict(s, id=sid)


def peso(txt):
    w = 1.0
    if any(h in txt.lower() for h in HYPE) or re.search(r"(k|K){4,}|(ha){3,}|(rs){3,}", txt):
        w += 1.5
    return w


def log(*a):
    print(time.strftime("[%H:%M:%S]"), *a, flush=True)


def run(cmd, timeout=None, **kw):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, **kw)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", f"TIMEOUT apos {timeout}s")


# ---------------------------------------------------------------- chat
def parse_chat_line(line):
    """Retorna lista de (timestamp_s, offset_s|None, texto, peso)."""
    try:
        d = json.loads(line)
    except Exception:
        return []
    r = d.get("replayChatItemAction", d)
    off = r.get("videoOffsetTimeMsec")
    off = int(off) / 1000 if off is not None else None
    out = []
    for a in r.get("actions", []):
        it = a.get("addChatItemAction", {}).get("item", {})
        for kind, rend in it.items():
            if kind not in ("liveChatTextMessageRenderer", "liveChatPaidMessageRenderer"):
                continue
            runs = rend.get("message", {}).get("runs", [])
            txt = "".join(x.get("text") or (x.get("emoji", {}).get("shortcuts") or [""])[0] for x in runs)
            ts = int(rend.get("timestampUsec", 0)) / 1e6
            w = peso(txt)
            if kind == "liveChatPaidMessageRenderer":
                w += 2
            out.append((ts, off, txt, w))
    return out


def zscore(cur, hist):
    if len(hist) < 10:
        return 0.0
    med = statistics.median(hist)
    mad = statistics.median([abs(x - med) for x in hist])
    return (cur - med) / (1.4826 * mad + 1.0)


# ---------------------------------------------------------------- audio
def audio_rms_db(path, start=None, dur=None):
    cmd = ["ffmpeg", "-loglevel", "error"]
    if start is not None:
        cmd += ["-ss", str(start)]
    cmd += ["-i", path]
    if dur is not None:
        cmd += ["-t", str(dur)]
    cmd += ["-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    n = len(a) // 8000
    if n == 0:
        return []
    a = a[: n * 8000].reshape(n, 8000)
    return [float(x) for x in 20 * np.log10(np.sqrt((a ** 2).mean(axis=1)) + 1)]


# ---------------------------------------------------------------- render
def probe_wh(path):
    r = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", path])
    w, h = [ln for ln in r.stdout.splitlines() if "," in ln][0].split(",")[:2]
    return int(w), int(h)


def ass_time(t):
    t = max(0, t)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def build_ass(words, path, sub_y):
    head = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: S,Arial Black,78,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,7,3,5,40,40,0,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    groups, cur = [], []
    for w in words:
        if cur and (len(cur) >= 3 or w["start"] - cur[-1]["end"] > 0.6):
            groups.append(cur); cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    lines = []
    for g in groups:
        for i, w in enumerate(g):
            end = g[i + 1]["start"] if i + 1 < len(g) else w["end"] + 0.15
            parts = []
            for j, x in enumerate(g):
                t = x["word"].strip().upper().replace("{", "").replace("}", "")
                parts.append("{\\c&H00E5FF&}" + t + "{\\c&HFFFFFF&}" if j == i else t)
            lines.append(f"Dialogue: 0,{ass_time(w['start'])},{ass_time(end)},S,,0,0,0,,{{\\an5\\pos(540,{sub_y})}}" + " ".join(parts))
    open(path, "w", encoding="utf-8").write(head + "\n".join(lines) + "\n")


class Whisper:
    _m = None
    _lock = threading.Lock()

    @classmethod
    def transcribe(cls, wav):
        with cls._lock:
            if cls._m is None:
                from faster_whisper import WhisperModel
                log("carregando Whisper", CFG["whisper_modelo"], "(1a vez baixa o modelo)...")
                cls._m = WhisperModel(CFG["whisper_modelo"], device="cpu", compute_type="int8", cpu_threads=os.cpu_count())
            segs, _ = cls._m.transcribe(wav, language=CFG["idioma"], word_timestamps=True, beam_size=5,
                                        vad_filter=True, vad_parameters={"min_silence_duration_ms": 300},
                                        condition_on_previous_text=False, initial_prompt=CFG.get("whisper_prompt"),
                                        no_speech_threshold=0.5, compression_ratio_threshold=2.2)
            words, text = [], []
            for s in segs:
                text.append(s.text.strip())
                for w in s.words or []:
                    words.append({"word": w.word, "start": w.start, "end": w.end})
            return words, " ".join(text)


def make_title(transcript, chat_msgs):
    model = CFG.get("ollama_modelo")
    if model:
        try:
            import urllib.request
            prompt = ("Crie UM titulo curto (max 70 caracteres), chamativo, em portugues BR, para um Short de GTA 6. "
                      "Sem aspas, sem hashtags. Fala do streamer: " + transcript[:800] +
                      "\nReacao do chat: " + " | ".join(chat_msgs[:25]))
            req = urllib.request.Request("http://localhost:11434/api/generate",
                                         data=json.dumps({"model": model, "prompt": prompt, "stream": False}).encode(),
                                         headers={"Content-Type": "application/json"})
            t = json.loads(urllib.request.urlopen(req, timeout=60).read())["response"].strip().strip('"').split("\n")[0]
            if t:
                return t[:90]
        except Exception as e:
            log("ollama falhou:", e)
    frase = max(re.split(r"[.!?]", transcript) or [""], key=lambda s: len(s.split()) if len(s.split()) <= 12 else 0).strip()
    return ("GTA 6: " + frase[:60]).strip() if frase else "MOMENTO INSANO NO GTA 6"


def render_clip(src, start, dur, name, meta):
    """Varios streamers rodam em paralelo: so 1 render por vez (Demucs/Whisper/ffmpeg pesam na CPU)."""
    from filelock import FileLock
    os.makedirs(WORK, exist_ok=True)
    meta["streamer"] = ST.get("id") or ST.get("nome")
    with FileLock(os.path.join(WORK, "render.lock")):
        return _render_clip(src, start, dur, name, meta)


def _render_clip(src, start, dur, name, meta):
    """src: arquivo de video; start/dur em segundos dentro de src."""
    os.makedirs(OUT, exist_ok=True)
    tmpd = os.path.join(WORK, "clip_" + name)
    os.makedirs(tmpd, exist_ok=True)
    ss, tt = f"{max(0, start):.3f}", f"{dur:.3f}"
    wav = os.path.join(tmpd, "a.wav")
    run(["ffmpeg", "-loglevel", "error", "-y", "-ss", ss, "-i", src, "-t", tt, "-vn",
         "-af", "aresample=async=1", "-ac", "1", "-ar", "16000", wav])
    if CFG.get("separar_voz", True):
        # isola a voz do streamer (tira tiro/musica/carro do jogo) so para transcrever
        st_wav = os.path.join(tmpd, "st.wav")
        run(["ffmpeg", "-loglevel", "error", "-y", "-ss", ss, "-i", src, "-t", tt, "-vn",
             "-af", "aresample=async=1", "-ac", "2", "-ar", "44100", st_wav])
        r = run([sys.executable, "-m", "demucs", "--two-stems=vocals", "-n", "htdemucs", "-d", "cpu", "-o", tmpd, st_wav])
        voc = os.path.join(tmpd, "htdemucs", "st", "vocals.wav")
        if r.returncode == 0 and os.path.exists(voc):
            run(["ffmpeg", "-loglevel", "error", "-y", "-i", voc, "-ac", "1", "-ar", "16000", wav])
        else:
            log("separacao de voz falhou, transcrevendo audio original:", r.stderr[-200:])
    words, transcript = Whisper.transcribe(wav)
    raw = src
    W, H = probe_wh(raw)
    fc = ST.get("facecam")
    if fc:
        cw, ch = int(W * fc["w"]) // 2 * 2, int(H * fc["h"]) // 2 * 2
        cx, cy = int(W * fc["x"]), int(H * fc["y"])
        cam_h = round(1080 * ch / cw / 2) * 2
        game_h = 1920 - cam_h
        gw = min(W, int(H * 1080 / game_h) // 2 * 2)
        vf = (f"[0:v]crop={cw}:{ch}:{cx}:{cy},scale=1080:{cam_h}:flags=lanczos,unsharp=5:5:0.7:5:5:0[cam];"
              f"[0:v]crop={gw}:{H}:{(W - gw) // 2}:0,scale=1080:{game_h}:flags=lanczos,unsharp=3:3:0.3:3:3:0[game];"
              f"[cam][game]vstack=inputs=2,setsar=1[v0]")
        sub_y = cam_h + 120
    else:
        vf = ("[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:2[bg];"
              "[0:v]scale=1080:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v0]")
        sub_y = 1450
    build_ass(words, os.path.join(tmpd, "subs.ass"), sub_y)
    vf += ";[v0]subtitles=subs.ass[v]"
    final = os.path.join(OUT, name + ".mp4")
    enc = CFG.get("encoder", "libx264")
    X264 = ["-c:v", "libx264", "-preset", "faster", "-crf", "18", "-pix_fmt", "yuv420p"]
    venc = ["-c:v", "h264_amf", "-quality", "quality", "-rc", "hqvbr", "-b:v", "16M", "-maxrate", "24M", "-bufsize", "32M",
            "-pix_fmt", "yuv420p"] if enc == "h264_amf" else X264
    base = ["ffmpeg", "-loglevel", "error", "-y", "-ss", ss, "-i", os.path.abspath(raw), "-t", tt,
            "-filter_complex", vf, "-map", "[v]", "-map", "0:a"]
    tail = ["-g", "120", "-fps_mode", "cfr", "-af", "aresample=async=1,loudnorm=I=-14:TP=-1.5", "-ar", "48000",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", final]
    # encoder da GPU (AMF) as vezes trava sem erro -> limite de tempo e cai p/ CPU
    r = run(base + venc + tail, cwd=tmpd, timeout=max(90, dur * 3))
    if r.returncode != 0 and enc == "h264_amf":
        log("AMF falhou, usando libx264:", r.stderr[-300:])
        r = run(base + X264 + tail, cwd=tmpd)
    if r.returncode != 0:
        log("ERRO render:", r.stderr[-800:]); return None
    meta["transcricao"] = transcript
    md = gerar_metadados(transcript, meta.get("chat_amostra", []))
    meta.update(md)
    # nome proprio: data_hora_slug-da-cena
    prefix = name[:16] if re.match(r"\d{8}_\d{6}", name) else time.strftime("%Y%m%d_%H%M%S_")
    novo = (prefix if prefix.endswith("_") else prefix + "_") + md["nome_arquivo"]
    k, dest = 1, os.path.join(OUT, novo + ".mp4")
    while os.path.exists(dest):
        k += 1; dest = os.path.join(OUT, f"{novo}-{k}.mp4")
    os.replace(final, dest)
    final = dest
    meta["arquivo"] = final
    jpath = os.path.splitext(final)[0] + ".json"
    json.dump(meta, open(jpath, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    shutil.rmtree(tmpd, ignore_errors=True)
    log(f"✅ CORTE PRONTO: {final}\n      titulo: {meta['titulo']}")
    try:
        import publicador
        publicador.enfileirar(jpath)
    except Exception as e:
        log("nao foi possivel enfileirar p/ postagem:", e)
    return final


def renomear(json_path, enfileirar=False):
    """Gera metadados + nome proprio para um corte ja renderizado (mp4+json)."""
    meta = json.load(open(json_path, encoding="utf-8"))
    mp4 = os.path.splitext(json_path)[0] + ".mp4"
    md = gerar_metadados(meta.get("transcricao", ""), meta.get("chat_amostra", []))
    meta.update(md)
    pasta, base = os.path.split(mp4)
    m = re.match(r"(\d{8}_\d{6})", base)
    novo = (m.group(1) if m else time.strftime("%Y%m%d_%H%M%S")) + "_" + md["nome_arquivo"]
    k, dest = 1, os.path.join(pasta, novo + ".mp4")
    while os.path.exists(dest) and os.path.abspath(dest) != os.path.abspath(mp4):
        k += 1; dest = os.path.join(pasta, f"{novo}-{k}.mp4")
    os.replace(mp4, dest)
    meta["arquivo"] = dest
    jnew = os.path.splitext(dest)[0] + ".json"
    json.dump(meta, open(jnew, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    if os.path.abspath(jnew) != os.path.abspath(json_path):
        os.remove(json_path)
    log(f"renomeado -> {os.path.basename(dest)} | {meta['titulo']}")
    if enfileirar:
        import publicador
        publicador.enfileirar(jnew)
    return dest


def _slug(s):
    import unicodedata
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:60] or "corte"


def gerar_metadados(transcript, chat_msgs):
    """Titulo/descricao/hashtags/nome via Claude (claude -p). Fallback heuristico."""
    st = ST
    nome, handle = st.get("nome", "streamer"), st.get("handle", "")
    prompt = (
        "Voce cria metadados virais para cortes curtos (YouTube Shorts / Instagram Reels / TikTok) de live de GTA 6, "
        f"em portugues do Brasil. Streamer: {nome} ({handle}).\n"
        f"Fala do streamer no corte (transcricao automatica, pode ter erros): \"{transcript[:1200]}\"\n"
        f"Reacao do chat no momento: {' | '.join(chat_msgs[-30:])[:1500]}\n"
        "Regras: nao invente fatos que nao estejam na fala/chat; gancho forte e curioso; sem clickbait enganoso.\n"
        "Responda SOMENTE um JSON valido com as chaves:\n"
        "nome_arquivo (slug kebab-case sem acento, 3-6 palavras, descreve a cena),\n"
        "titulo (max 70 caracteres, pode ter 1 emoji, sem hashtag),\n"
        f"descricao (2-3 frases curtas, termine com \"Creditos: {handle or nome}\"),\n"
        "hashtags (lista de 6-10 com #, misture amplas e de nicho; inclua #gta6)."
    )
    try:  # o que o canal aprendeu com as metricas reais dos videos postados (aprendizado.py)
        import aprendizado
        prompt += aprendizado.dicas_prompt()
    except Exception as e:
        log("aprendizado indisponivel:", e)
    claude = shutil.which("claude")
    if claude:
        try:
            r = subprocess.run([claude, "-p", "--model", CFG.get("modelo_metadados", "haiku"), "--output-format", "text"],
                               input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
            j = json.loads(re.search(r"\{.*\}", r.stdout, re.S).group(0))
            tags = [t if t.startswith("#") else "#" + t for t in j.get("hashtags", [])][:10]
            return {"titulo": j["titulo"].strip()[:90], "descricao": j.get("descricao", "").strip(),
                    "hashtags": tags, "nome_arquivo": _slug(j.get("nome_arquivo") or j["titulo"])}
        except Exception as e:
            log("metadados via Claude falharam, usando heuristica:", e)
    t = make_title(transcript, chat_msgs)
    return {"titulo": t, "descricao": f"Momento da live de GTA 6. Creditos: {handle or nome}",
            "hashtags": CFG["hashtags"].split(), "nome_arquivo": _slug(t.replace("GTA 6:", ""))}


# ================================================================ LIVE
class Live:
    def __init__(self, url):
        self.twitch = "twitch.tv" in url
        m = re.search(r"(?:v=|youtu\.be/|live/)([\w-]{11})(?:[&?#]|$)", url)
        if self.twitch:
            self.vid = re.search(r"twitch\.tv/([\w]+)", url).group(1).lower()
        elif m:
            self.vid = m.group(1)
        else:  # link de canal (ex: youtube.com/@LOUDCoringa/live) -> resolve a live atual
            r = run([*YTDLP, "--print", "id", "--playlist-items", "1", url])
            ids = re.findall(r"^[\w-]{11}$", r.stdout, re.M)
            if not ids:
                raise SystemExit(f"Nao achei live no ar em {url}. O canal esta ao vivo?")
            self.vid = ids[0]
            log("live atual do canal:", self.vid)
        self.url = f"https://www.twitch.tv/{self.vid}" if self.twitch else f"https://www.youtube.com/watch?v={self.vid}"
        self.segdir = os.path.join(WORK, "seg_" + self.vid)
        os.makedirs(self.segdir, exist_ok=True)
        self.chat = defaultdict(float)      # segundo(wall) -> peso
        self.chat_txt = defaultdict(list)   # segundo -> msgs
        self.audio = {}                     # segundo(wall) -> dB
        self.seen_seg = set()
        self.jobs = queue.Queue()
        self.alive = True
        self.t0 = time.time()
        self.last_clip = 0
        self.n = 0
        self.lock = threading.Lock()
        self.cand_log = open(os.path.join(BASE, f"momentos_{self.vid}.jsonl"), "a", encoding="utf-8")

    # --- gravacao em segmentos de 10s nomeados com hora de parede
    def recorder(self):
        while self.alive:
            fmt = "best" if self.twitch else "312+234/311+234/301/300/bv*+ba/b"  # twitch: 1 HLS com audio+video
            r = run([*YTDLP, "-g", "-f", fmt, self.url])
            urls = [u for u in r.stdout.split() if u.startswith("http")]
            if not urls:
                log("live indisponivel/encerrada:", r.stderr[-200:]); self.alive = False; break
            inp = sum([["-i", u] for u in urls], [])
            maps = ["-map", "0:v", "-map", f"{len(urls) - 1}:a"]
            log("gravando live...")
            # -copyts: video e audio vem de HLS separados que compartilham o relogio do YouTube;
            # sem isso o ffmpeg zera cada um separado e o audio fica defasado (segundos).
            subprocess.run(["ffmpeg", "-loglevel", "error", "-copyts", *inp, *maps, "-c", "copy", "-muxdelay", "0", "-f", "segment",
                            "-segment_time", str(SEG_DUR), "-strftime", "1",
                            os.path.join(self.segdir, "%Y%m%d_%H%M%S.ts")])
            if self.alive:
                log("ffmpeg caiu, reconectando em 3s"); time.sleep(3)

    def segments(self):
        out = []
        for p in sorted(glob.glob(os.path.join(self.segdir, "*.ts"))):
            t = dt.datetime.strptime(os.path.basename(p)[:15], "%Y%m%d_%H%M%S").timestamp()
            out.append((t, p))
        return out

    def _ingest(self, actions):
        with self.lock:
            for ln in actions:
                for ts, _, txt, w in parse_chat_line(ln):
                    s = int(ts)
                    self.chat[s] += w
                    self.chat_txt[s].append(txt)

    # --- chat completo ("Chat ao vivo", sem filtro) via endpoint do proprio player web
    def chat_reader(self):
        if self.twitch:
            return self.chat_reader_twitch()
        import urllib.request
        UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36",
              "Accept-Language": "pt-BR"}
        try:
            html = urllib.request.urlopen(urllib.request.Request(
                f"https://www.youtube.com/live_chat?v={self.vid}&is_popout=1", headers=UA), timeout=20).read().decode()
            data = re.search(r"(?:window\[\"ytInitialData\"\]|var ytInitialData)\s*=\s*(\{.+?\});\s*</script>", html).group(1)
            key = re.search(r'"INNERTUBE_API_KEY":"([^"]+)"', html).group(1)
            ver = re.search(r'"INNERTUBE_CLIENT_VERSION":"([^"]+)"', html).group(1)
            subs = re.findall(r'"title": "([^"]+)", "selected": (?:true|false), "continuation": \{"reloadContinuationData": \{"continuation": "([^"]+)"',
                              json.dumps(json.loads(data)))
            cont = subs[-1][1]
        except Exception as e:
            log("chat completo indisponivel, usando chat principal (yt-dlp):", e)
            return self.chat_reader_ytdlp()
        log("chat conectado (completo)")
        fails = 0
        while self.alive:
            try:
                body = {"context": {"client": {"clientName": "WEB", "clientVersion": ver, "hl": "pt"}}, "continuation": cont}
                r = json.loads(urllib.request.urlopen(urllib.request.Request(
                    f"https://www.youtube.com/youtubei/v1/live_chat/get_live_chat?key={key}&prettyPrint=false",
                    data=json.dumps(body).encode(), headers={**UA, "Content-Type": "application/json"}), timeout=20).read())
                lc = r["continuationContents"]["liveChatContinuation"]
                self._ingest([json.dumps({"actions": lc.get("actions", [])})])
                c = next(iter(lc["continuations"][0].values()))
                cont = c["continuation"]
                fails = 0
                time.sleep(min(max(c.get("timeoutMs", 1000), 1000), 3000) / 1000)
            except Exception as e:
                fails += 1
                if fails >= 5:
                    log("chat completo falhou 5x, trocando p/ yt-dlp:", e)
                    return self.chat_reader_ytdlp()
                time.sleep(3)

    # --- chat da Twitch: IRC em modo anonimo (somente leitura, nick justinfan)
    def chat_reader_twitch(self):
        import socket, random
        while self.alive:
            try:
                s = socket.create_connection(("irc.chat.twitch.tv", 6667), timeout=30)
                s.sendall(f"NICK justinfan{random.randint(10000, 99999)}\r\nJOIN #{self.vid}\r\n".encode())
                log("chat twitch conectado")
                buf = ""
                while self.alive:
                    data = s.recv(8192).decode("utf-8", "replace")
                    if not data:
                        break
                    buf += data
                    *lines, buf = buf.split("\r\n")
                    for ln in lines:
                        if ln.startswith("PING"):
                            s.sendall(b"PONG :tmi.twitch.tv\r\n")
                        elif " PRIVMSG " in ln:
                            txt = ln.split(" :", 1)[1] if " :" in ln else ""
                            t = int(time.time())
                            with self.lock:
                                self.chat[t] += peso(txt)
                                self.chat_txt[t].append(txt)
                s.close()
            except Exception as e:
                log("chat twitch caiu, reconectando:", e)
                time.sleep(5)

    # --- chat via yt-dlp (reserva; so "Principais mensagens")
    def chat_reader_ytdlp(self):
        base = os.path.join(self.segdir, "chat")
        for f in glob.glob(base + "*"):
            os.remove(f)
        proc = subprocess.Popen([*YTDLP, "--skip-download", "--write-subs", "--sub-langs", "live_chat", "-o", base, self.url],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        path = base + ".live_chat.json.part"
        while self.alive and not os.path.exists(path):
            time.sleep(1)
        log("chat conectado")
        with open(path, encoding="utf-8", errors="replace") as fh:
            buf = ""
            while self.alive:
                chunk = fh.read()
                if not chunk:
                    if proc.poll() is not None:
                        log("chat encerrou"); break
                    time.sleep(0.5); continue
                buf += chunk
                *lines, buf = buf.split("\n")
                with self.lock:
                    for ln in lines:
                        for ts, _, txt, w in parse_chat_line(ln):
                            s = int(ts)
                            self.chat[s] += w
                            self.chat_txt[s].append(txt)

    # --- audio por segmento + limpeza
    def audio_worker(self):
        while self.alive:
            segs = self.segments()
            for t, p in segs[:-1]:  # ultimo ainda gravando
                if p in self.seen_seg:
                    continue
                self.seen_seg.add(p)
                for i, db in enumerate(audio_rms_db(p)):
                    self.audio[int(t) + i] = db
            keep = time.time() - CFG["manter_segmentos_min"] * 60
            for t, p in segs:
                if t < keep:
                    try: os.remove(p)
                    except OSError: pass
            time.sleep(2)

    # --- detector
    def detector(self):
        J = CFG["janela_s"]
        hist_c, hist_a = [], []
        while self.alive:
            time.sleep(5)
            now = int(time.time()) - 6  # chat chega com atraso
            with self.lock:
                cur_c = sum(self.chat.get(s, 0) for s in range(now - J, now))
                msgs = [m for s in range(now - J - 5, now) for m in self.chat_txt.get(s, [])]
            a_vals = [self.audio[s] for s in range(now - 40, now) if s in self.audio]
            cur_a = max(a_vals) if a_vals else 0
            zc, za = float(zscore(cur_c, hist_c)), float(zscore(cur_a, hist_a))
            hist_c = (hist_c + [cur_c])[-180:]
            if a_vals:
                hist_a = (hist_a + [cur_a])[-180:]
            warm = time.time() - self.t0 > CFG["aquecimento_s"]
            score = zc + 0.5 * max(0, za)
            if int(time.time()) % 30 < 5:
                log(f"chat={cur_c:.0f}/10s z={zc:+.1f} | audio z={za:+.1f} | score={score:.1f}" + ("" if warm else " (aquecendo)"))
            hit = warm and (zc >= CFG["limiar_chat_z"] or za >= CFG["limiar_audio_z"])
            if hit and time.time() - self.last_clip > CFG["cooldown_s"]:
                self.last_clip = time.time()
                self.n += 1
                T = now - J // 2
                job = dict(n=self.n, start=T - CFG["clip_antes_s"], end=T + CFG["clip_depois_s"], score=round(score, 2),
                           chat_z=round(zc, 2), audio_z=round(za, 2), chat_amostra=msgs[-40:])
                self.cand_log.write(json.dumps(job, ensure_ascii=False) + "\n"); self.cand_log.flush()
                log(f"🔥 MOMENTO #{self.n} detectado (score {score:.1f}) — gerando corte...")
                self.jobs.put(job)

    # --- montagem dos cortes
    def clipper(self):
        while self.alive or not self.jobs.empty():
            try:
                job = self.jobs.get(timeout=2)
            except queue.Empty:
                continue
            while self.alive and not [1 for t, _ in self.segments() if t > job["end"] + 1]:
                time.sleep(2)
            segs = [(t, p) for t, p in self.segments() if t + SEG_DUR + 2 > job["start"] and t < job["end"]]
            if not segs:
                log("segmentos nao encontrados p/ corte", job["n"]); continue
            cat = os.path.join(WORK, f"cat_{self.vid}_{job['n']}.ts")
            with open(cat, "wb") as o:  # MPEG-TS com timestamps continuos: concatenar bytes e valido
                for _, p in segs:
                    o.write(open(p, "rb").read())
            name = time.strftime("%Y%m%d_%H%M%S_", time.localtime(job["start"])) + self.vid + f"_{job['n']:03d}"
            meta = dict(job, fonte=self.url, inicio_hora=time.strftime("%H:%M:%S", time.localtime(job["start"])))
            try:
                render_clip(cat, job["start"] - segs[0][0], job["end"] - job["start"], name, meta)
            except Exception as e:
                log("erro no corte:", e)
            os.remove(cat)

    def run(self):
        log(f"acompanhando live {self.vid} — cortes em {OUT}")
        def guard(f):
            def loop():
                while self.alive:
                    try:
                        f(); return
                    except Exception:
                        import traceback
                        log(f"ERRO na thread {f.__name__} (reiniciando em 3s):\n" + traceback.format_exc()[-600:])
                        time.sleep(3)
            return loop
        ths = [threading.Thread(target=guard(f), daemon=True) for f in (self.recorder, self.chat_reader, self.audio_worker, self.detector)]
        for t in ths:
            t.start()
        clip_t = threading.Thread(target=self.clipper, daemon=True); clip_t.start()
        try:
            while self.alive:
                time.sleep(1)
        except KeyboardInterrupt:
            log("parando..."); self.alive = False
        clip_t.join(timeout=600)


# ================================================================ VOD
def _vod_id(url):
    m = re.search(r"twitch\.tv/videos/(\d+)", url)
    if m:
        return "tw" + m.group(1)
    return re.search(r"(?:v=|youtu\.be/|live/)([\w-]{11})", url).group(1)


def vod(url, max_clips=15):
    """Live gravada: baixa SO audio (+chat no YouTube), escolhe os momentos e baixa SO os trechos
    escolhidos em 1080p (lives de 10h+ nao precisam ser baixadas inteiras)."""
    vid = _vod_id(url)
    d = os.path.join(WORK, "vod_" + vid); os.makedirs(d, exist_ok=True)
    if not glob.glob(os.path.join(d, "audio.*")):
        log("baixando audio + chat da live gravada (pode demorar em lives longas)...")
        subprocess.run([*YTDLP, "-f", "ba/b", "-x", "--audio-format", "m4a", "--write-subs", "--sub-langs", "live_chat",
                        "-o", os.path.join(d, "audio.%(ext)s"), url])
    cands = [p for p in glob.glob(os.path.join(d, "audio.*")) if not p.endswith((".json", ".part"))]
    if not cands:
        log("falha ao baixar o audio da live gravada"); return 0
    audio_f = cands[0]
    chat = defaultdict(float); txt = defaultdict(list)
    cf = os.path.join(d, "audio.live_chat.json")
    if os.path.exists(cf):
        for ln in open(cf, encoding="utf-8", errors="replace"):
            for _, off, t, w in parse_chat_line(ln):
                if off is not None and off >= 0:
                    chat[int(off)] += w; txt[int(off)].append(t)
    else:
        log("sem chat gravado (Twitch) -> escolhendo momentos so pelo audio")
    log("analisando audio...")
    audio = audio_rms_db(audio_f)
    N = len(audio); J = CFG["janela_s"]
    if not N:
        return 0
    c = np.array([chat.get(s, 0) for s in range(N)])
    cw = np.convolve(c, np.ones(J), "same")
    a = np.array(audio)
    aw = np.array([a[max(0, i - 30): i + 1].max() for i in range(N)])

    def robust_z(x):
        med = np.median(x); mad = np.median(np.abs(x - med))
        return (x - med) / (1.4826 * mad + 1.0)
    zc, za = robust_z(cw), robust_z(aw)
    score = (zc + 0.5 * np.clip(za, 0, None)) if chat else za
    score[:CFG.get("aquecimento_s", 150)] = -9  # comeco da live (tela de espera/abertura)
    order = np.argsort(-score); picked = []
    for i in order:
        if len(picked) >= max_clips or score[i] < 2.0:
            break
        if all(abs(i - p) > 90 for p in picked):
            picked.append(int(i))
    log(f"{len(picked)} momentos escolhidos")
    feitos = 0
    for k, T in enumerate(sorted(picked), 1):
        ini, fim = max(0, T - CFG["clip_antes_s"]), T + CFG["clip_depois_s"]
        seg = os.path.join(d, f"trecho_{k:03d}.mp4")
        if not os.path.exists(seg):
            subprocess.run([*YTDLP, "-f", "bv*[height<=1080]+ba/b", "--merge-output-format", "mp4",
                            "--download-sections", f"*{ini}-{fim}", "--force-keyframes-at-cuts", "-o", seg, url])
        if not os.path.exists(seg):
            log(f"trecho {k} nao baixou, pulando"); continue
        msgs = [m for s in range(T - J, T + 5) for m in txt.get(s, [])]
        meta = dict(n=k, fonte=url, momento_s=T, score=round(float(score[T]), 2), chat_amostra=msgs[-40:],
                    chat_z=round(float(zc[T]), 2) if chat else 0.0, audio_z=round(float(za[T]), 2),
                    start=0, end=fim - ini)
        if render_clip(seg, 0, fim - ini, time.strftime("%Y%m%d_%H%M%S_") + f"vod_{vid}_{k:03d}", meta):
            feitos += 1
        try:
            os.remove(seg)
        except OSError:
            pass
    shutil.rmtree(d, ignore_errors=True)
    return feitos


# ================================================================ AUTO (live ou ultima live gravada)
def _feitos():
    try:
        return json.load(open(FEITOS, encoding="utf-8"))
    except Exception:
        return {}


def _marcar_feito(vid, titulo=""):
    f = _feitos(); f[vid] = {"quando": time.time(), "titulo": titulo, "streamer": ST.get("id")}
    json.dump(f, open(FEITOS, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def _ao_vivo():
    """(url, titulo) se o streamer esta AO VIVO, senao None."""
    url = f"https://www.twitch.tv/{ST['twitch']}" if ST.get("twitch") else ST["youtube"].rstrip("/") + "/live"
    campo = "%(description)s" if ST.get("twitch") else "%(title)s"  # twitch: titulo real vem em description
    r = run([*YTDLP, "--print", f"%(id)s\t%(live_status)s\t%(is_live)s\t{campo}", "--playlist-items", "1", url], timeout=90)
    for ln in r.stdout.splitlines():
        p = ln.split("\t")
        if len(p) >= 4 and (p[1] == "is_live" or p[2] == "True"):
            return (url if ST.get("twitch") else f"https://www.youtube.com/watch?v={p[0]}"), p[3]
    return None


def _ultimas_gravadas(n=8):
    """Lives gravadas mais recentes: [(vid, url, titulo)]."""
    if ST.get("twitch"):
        url = f"https://www.twitch.tv/{ST['twitch']}/videos?filter=archives&sort=time"
    else:
        url = ST["youtube"].rstrip("/") + "/streams"
    r = run([*YTDLP, "--flat-playlist", "--playlist-items", f"1:{n}", "--print", "%(id)s\t%(live_status)s\t%(title)s", url], timeout=120)
    out = []
    for ln in r.stdout.splitlines():
        p = ln.split("\t")
        if len(p) < 3 or p[1] in ("is_live", "is_upcoming"):
            continue
        if ST.get("twitch"):
            n_id = p[0].lstrip("v")
            out.append(("tw" + n_id, f"https://www.twitch.tv/videos/{n_id}", p[2]))
        else:
            out.append((p[0], f"https://www.youtube.com/watch?v={p[0]}", p[2]))
    return out


def auto(sid):
    """Loop de 1 streamer: ao vivo em GTA -> corta a live; offline -> corta a ultima live de GTA gravada."""
    usar_streamer(sid)
    log(f"modo automatico: {ST['nome']} ({'twitch/' + ST['twitch'] if ST.get('twitch') else ST.get('youtube')})")
    while True:
        try:
            viva = _ao_vivo()
            if viva and GTA_RE.search(viva[1]):
                log("AO VIVO em GTA:", viva[1])
                Live(viva[0]).run()
                if not ST.get("twitch"):
                    _marcar_feito(_vod_id(viva[0]), viva[1])
                else:  # a live que acabou de ser cortada vira o VOD mais recente
                    g = _ultimas_gravadas(1)
                    if g:
                        _marcar_feito(g[0][0], g[0][1])
                continue
            if viva:
                log("ao vivo, mas nao e GTA:", viva[1])
            else:
                gta = [x for x in _ultimas_gravadas() if GTA_RE.search(x[2])]
                if gta and gta[0][0] not in _feitos():
                    vid, url, tit = gta[0]
                    log("offline -> cortando a ultima live de GTA gravada:", tit)
                    n = vod(url, CFG.get("vod_max_cortes", 15))
                    _marcar_feito(vid, tit)
                    log(f"live gravada concluida: {n} cortes")
                else:
                    log("offline; ultima live de GTA ja foi cortada" if gta else "offline; nenhuma live de GTA gravada")
        except Exception:
            import traceback
            log("erro no modo automatico:", traceback.format_exc()[-500:])
        time.sleep(CFG.get("checar_live_min", 10) * 60)


def todos():
    """Abre uma janela por streamer (config.json > streamers com ativo=true)."""
    for sid, s in CFG.get("streamers", {}).items():
        if not s.get("ativo", True):
            continue
        cmd = [sys.executable, "-u", os.path.abspath(__file__), "auto", sid]
        if os.name == "nt":  # Windows: 1 janela por streamer
            subprocess.Popen(["cmd", "/k", "title", f"CORTADOR {s['nome']}", "&", *cmd],
                             cwd=BASE, creationflags=subprocess.CREATE_NEW_CONSOLE)
            log("iniciado:", s["nome"])
        else:  # Linux/macOS: processo em segundo plano com log proprio
            lf = open(os.path.join(BASE, f"cortador_{sid}.log"), "a", encoding="utf-8")
            subprocess.Popen(cmd, cwd=BASE, stdout=lf, stderr=subprocess.STDOUT, start_new_session=True)
            log("iniciado:", s["nome"], f"(log em cortador_{sid}.log)")
        time.sleep(2)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "renomear":
        alvo = sys.argv[2]
        js = sorted(glob.glob(os.path.join(alvo, "*.json"))) if os.path.isdir(alvo) else [alvo]
        for j in js:
            if os.path.basename(j).startswith(("teste", "job")):
                continue
            renomear(j, enfileirar="--fila" in sys.argv)
        sys.exit(0)
    os.makedirs(WORK, exist_ok=True)
    import creditos
    creditos.mostrar()
    if len(sys.argv) >= 2 and sys.argv[1] == "todos":
        todos(); sys.exit(0)
    if len(sys.argv) < 3 or sys.argv[1] not in ("live", "vod", "auto"):
        print(__doc__); sys.exit(1)
    if "--streamer" in sys.argv:
        usar_streamer(sys.argv[sys.argv.index("--streamer") + 1])
    if sys.argv[1] == "auto":
        auto(sys.argv[2])
    elif sys.argv[1] == "live":
        Live(sys.argv[2]).run()
    else:
        mx = int(sys.argv[sys.argv.index("--max") + 1]) if "--max" in sys.argv else 15
        vod(sys.argv[2], mx)
