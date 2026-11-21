# Cortes Automáticos GTA 6 — lives viram Shorts, Reels e TikToks sozinhas

> Desenvolvido por **[C.O.R.E Enterprise](https://coreenterprise.com.br)** · sistemas internos, automações e sites premium que tiram trabalho manual da sua operação.
> Quer uma automação sob medida para o seu negócio? **comercial@coreenterprise.com.br**

Acompanha lives (YouTube e Twitch), detecta os momentos de hype pelo **chat + áudio**, corta em **9:16 1080p60** com **facecam em cima e jogo embaixo**, gera **legenda palavra por palavra** (Whisper), cria **título/descrição/hashtags** com IA e **posta sozinho** no YouTube Shorts, TikTok e Instagram Reels. Depois mede o desempenho dos vídeos e **aprende** o que funciona no seu canal.

- **Vários streamers ao mesmo tempo.** Se o streamer estiver ao vivo em GTA, corta a live em tempo real. Se estiver offline, corta a **última live de GTA gravada** (baixa só o áudio e os trechos escolhidos, não a live inteira).
- **Dois jeitos de postar** em cada rede:
  - **Navegador** (recomendado): usa a sua conta logada num Chrome próprio, digita e clica como uma pessoa. Fica fora das cotas e auditorias das APIs.
  - **API oficial**: YouTube Data API, TikTok Content Posting API, Instagram Graph API.
- **Anti-ban:** intervalo configurável entre posts com variação aleatória, ritmo humano de digitação, e pausa automática quando aparece login, CAPTCHA ou limite. **O sistema nunca contorna CAPTCHA e nunca digita senha.**
- **Aprendizado:** coleta views/likes (e retenção, se habilitada), compara os vídeos que deram certo com os que não deram e ajusta a ordem da fila e o estilo dos títulos.

> **Usando uma IA para instalar?** Peça: *"clone https://github.com/RickRtw/cortes-automaticos-gta6 e configure pra mim"*. A IA segue o guia em [`.claude/skills/cortes-automaticos/SKILL.md`](.claude/skills/cortes-automaticos/SKILL.md) (também referenciado em [`AGENTS.md`](AGENTS.md)).

---

## Sumário
1. [Requisitos](#1-requisitos)
2. [Instalação](#2-instalação)
3. [Escolher os streamers](#3-escolher-os-streamers)
4. [Conectar as redes](#4-conectar-as-redes) — [navegador](#41-modo-navegador-recomendado) · [API](#42-modo-api)
5. [Rodar](#5-rodar)
6. [Configurações de postagem](#6-configurações-de-postagem-publicacaojson)
7. [Aprendizado](#7-aprendizado)
8. [Evitando ban](#8-evitando-ban)
9. [Avisos legais](#9-avisos-legais)
10. [Créditos](#10-créditos)

---

## 1. Requisitos
- **Python 3.10+** (testado no 3.12)
- **FFmpeg** no PATH (`ffmpeg -version`)
- **Google Chrome** instalado (modo navegador)
- Windows 10/11 (principal). Linux/macOS funcionam via `iniciar.sh`.
- Opcional: **[Claude Code](https://claude.com/claude-code)** (`claude` no PATH) para gerar títulos melhores. Sem ele, o título é gerado por heurística.
- CPU razoável: a transcrição (Whisper) e a separação de voz (Demucs) rodam na CPU. Os renders são feitos **um por vez** mesmo com vários streamers.

## 2. Instalação
```bash
git clone https://github.com/RickRtw/cortes-automaticos-gta6
cd cortes-automaticos-gta6
python -m pip install -r requirements.txt
python -m playwright install chromium
```
Na primeira execução, `config.json` e `publicacao.json` são criados a partir dos arquivos `*.exemplo.json`.

## 3. Escolher os streamers
Edite `config.json` → `streamers`:
```json
"streamers": {
  "loud":     {"nome": "LOUD Coringa", "handle": "@LOUDCoringa", "youtube": "https://www.youtube.com/@LOUDCoringa", "facecam": {"x":0.0,"y":0.113,"w":0.241,"h":0.28}, "ativo": true},
  "alanzoka": {"nome": "alanzoka", "handle": "@alanzoka", "twitch": "alanzoka", "facecam": null, "ativo": true}
}
```
- `youtube`: link do canal (`/@handle` ou `/channel/UC...`). Para Twitch, use `twitch` com o login do canal.
- `facecam`: retângulo da webcam na tela, em frações de 0 a 1 (`x`, `y` do canto superior esquerdo; `w`, `h`). Com isso o corte fica com a cam em cima e o jogo embaixo. Use `null` para o vídeo inteiro centralizado com fundo desfocado.
  *Como medir:* tire um print da live, veja onde a cam começa e termina e divida pela largura e altura da imagem.
- `GTA_RE` em `cortador.py` define o filtro de jogo (padrão: títulos com `gta`, `vice city`, `leonida`, `grand theft`). Troque para outro jogo se quiser.
- Ajustes finos: `limiar_chat_z`, `limiar_audio_z`, `clip_antes_s`, `clip_depois_s`, `cooldown_s`, `checar_live_min` (minutos entre verificações) e `vod_max_cortes`.

## 4. Conectar as redes
Depois de conectar cada rede:
1. `python publicador.py checar`
2. Em `publicacao.json`, mude `"ativo": true` na plataforma.

### 4.1 Modo navegador (recomendado)
Cada rede tem um Chrome próprio (pasta `navegador_<rede>/`, **não compartilhe**). Você faz o login **uma vez**; o sistema detecta sozinho quando terminou.

| Rede | Login | Teste manual |
|---|---|---|
| TikTok | `python tiktok_web.py login` | `python tiktok_web.py postar video.mp4 "legenda #tag"` |
| YouTube (Studio) | `python youtube_web.py login` | `python youtube_web.py postar video.mp4 "título" "descrição"` |
| Instagram (Reels) | `python instagram_web.py login` | `python instagram_web.py postar video.mp4 "legenda #tag"` |

Em `publicacao.json` deixe `"modo": "navegador"` na plataforma. Se a sessão expirar ou aparecer CAPTCHA, a plataforma é pausada (padrão: 1h, `pausa_bloqueio_horas`), o corte volta para a fila e o log mostra o que fazer. Prints de erro ficam em `work/navegador/`.

### 4.2 Modo API
Credenciais ficam em `credenciais/` (está no `.gitignore`).

**YouTube Data API** (`"modo": "api"`)
1. [console.cloud.google.com](https://console.cloud.google.com) → criar projeto → ativar **YouTube Data API v3** (e **YouTube Analytics API**, opcional, para retenção no aprendizado).
2. Tela de permissão OAuth → Externo → adicione o seu Gmail em **Usuários de teste**.
3. Credenciais → ID do cliente OAuth → **App para computador** → baixe o JSON como `credenciais/youtube_client_secret.json`.
4. `python publicador.py login youtube`

Limite: a cota grátis permite **cerca de 6 uploads por dia**. O sistema pausa até a cota zerar. Com o app em "teste", o token expira em 7 dias.

**TikTok Content Posting API** (`"modo": "direto"` ou `"rascunho"`)
1. [developers.tiktok.com](https://developers.tiktok.com) → crie o app → produtos **Login Kit** (plataforma Desktop, redirect `http://localhost:8765/callback/`) e **Content Posting API** (Direct Post).
2. Escopos: `user.info.basic`, `video.upload`, `video.publish`.
3. Salve `credenciais/tiktok.json`: `{"client_key": "...", "client_secret": "...", "redirect_uri": "http://localhost:8765/callback/"}`
4. `python publicador.py login tiktok`

Limitação: apps **não auditados** só fazem Direct Post em **contas privadas**. Em conta pública use `"rascunho"` (o vídeo vai para a caixa de entrada do app **sem legenda**; as legendas ficam salvas em `tiktok_legendas.txt`). A auditoria do TikTok **não aprova apps de uso interno ou de postagem automática**, por isso o modo navegador é o caminho prático.

**Instagram Graph API** (`"modo": "api"`)
1. Conta Instagram **Profissional**. Em [developers.facebook.com](https://developers.facebook.com), crie um app → produto Instagram → "API com login do Instagram".
2. Gere o token com `instagram_business_basic` e `instagram_business_content_publish`.
3. `credenciais/instagram.json`: `{"ig_user_id": "...", "access_token": "..."}`

O token dura 60 dias. Limite: 50 posts a cada 24h.

## 5. Rodar
- **Windows:** dois cliques em `INICIAR.bat`. Abre uma janela de cortador por streamer e uma de postador.
- **Linux/macOS:** `./iniciar.sh`

Outros comandos:
```bash
python cortador.py auto <id>          # 1 streamer (ao vivo -> live; offline -> última live de GTA gravada)
python cortador.py live <url>         # uma live específica
python cortador.py vod <url> --max 10 # uma live gravada específica
python publicador.py listar           # fila e links
python publicador.py agora            # posta já tudo que está aprovado
python publicador.py aprovar <nome|todos> / rejeitar <nome>
python aprendizado.py                 # coleta métricas e gera APRENDIZADO.md
```

## 6. Configurações de postagem (`publicacao.json`)
| Chave | Padrão | O que faz |
|---|---|---|
| `modo` | `auto` | `auto` posta sozinho os cortes com `score >= score_minimo`; `aprovar` espera `publicador.py aprovar` |
| `intervalo_min` | `10` | minutos entre posts **em cada rede** |
| `variacao_pct` | `30` | sorteia ±30% no intervalo (padrão humano) |
| `pausa_bloqueio_horas` | `1` | pausa da rede quando aparece login, CAPTCHA ou limite |
| `validade_horas` | `72` | cortes mais velhos que isso não são postados |
| `navegador_visivel` | `true` | `false` roda o navegador escondido (aumenta a chance de bloqueio) |
| `plataformas.<rede>.ativo` | `false` | liga a rede |
| `plataformas.<rede>.modo` | `navegador` | `navegador` ou `api` (TikTok API: `direto` / `rascunho`) |
| `plataformas.<rede>.intervalo_min` | — | sobrescreve o intervalo só nessa rede |
| `plataformas.<rede>.max_por_dia` | `null` | teto diário opcional (`null` = sem teto) |

## 7. Aprendizado
A cada 6h (`aprendizado_horas`), o postador coleta as métricas do YouTube e:
- separa os 30% que **deram certo** dos 30% que **não deram**;
- mede o efeito de streamer, gatilho (chat ou áudio), duração, horário, estilo do título, palavras e hashtags;
- pede ao Claude regras curtas comparando os títulos (se o `claude` estiver instalado);
- com 8 ou mais vídeos maduros, **muda a ordem da fila** e **orienta os próximos títulos**.

O relatório fica em `APRENDIZADO.md`.

## 8. Evitando ban
- Automação de navegador vai contra os termos de uso das redes. **Use por sua conta e risco.**
- Comece com intervalos maiores e reduza aos poucos. Contas novas são mais vigiadas.
- Mantenha `navegador_visivel: true` e não use a conta manualmente enquanto o robô posta.
- Se aparecer CAPTCHA ou verificação, **resolva você mesmo** no navegador (`python <rede>_web.py login`).
- O YouTube Studio também tem limite diário de envios para contas não verificadas. Verifique a conta por telefone.

## 9. Avisos legais
- Cortes usam conteúdo de terceiros. Poste **apenas** de criadores que permitem cortes, sempre com crédito (o sistema coloca "Créditos: @streamer" na descrição), e respeite pedidos de remoção.
- Você é responsável pelo uso das suas contas e pelo cumprimento dos termos de cada plataforma.

## 10. Créditos
**Cortes Automáticos** foi criado pela **C.O.R.E Enterprise** — CORE ENTERPRISE DESENVOLVIMENTO DE SOFTWARE LTDA · CNPJ 70.163.846/0001-28.

A C.O.R.E faz **sites premium, painéis administrativos e automações pesadas** (bots de WhatsApp, integrações, robôs de operação) para negócios que querem menos trabalho manual e controle total nas mãos do dono.

- 🌐 **https://coreenterprise.com.br**
- ✉️ **comercial@coreenterprise.com.br**

Licença [MIT](LICENSE). Se usar ou derivar, mantenha os créditos.
