"""
Açãozito 🐂 — Bot Telegram Monitor de Ações B3
Versão 2.0 — com alertas de preço exato, múltiplas ações,
simulação MXRF11, Dividend Yield e navegação automática.
Deploy: Railway (webhook) ou local (polling)
"""
 
import os
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
 
import yfinance as yf
from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode
 
# ─── Configuração ──────────────────────────────────────────────────────────────
TOKEN       = os.environ["TELEGRAM_BOT_TOKEN"]
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
PORT        = int(os.environ.get("PORT", 8443))
BR_TZ       = ZoneInfo("America/Sao_Paulo")
 
# Arquivo local para persistir sugestões de ações
SUGESTOES_FILE = Path("sugestoes.json")
 
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)
 
# ─── Estados da conversa (ConversationHandler) ─────────────────────────────────
(
    ESTADO_ALERTA_TICKER,
    ESTADO_ALERTA_TIPO,
    ESTADO_ALERTA_VALOR_EXATO,
    ESTADO_ALERTA_FAIXA_MIN,
    ESTADO_ALERTA_FAIXA_MAX,
    ESTADO_MONITORAR_ESCOLHA,
    ESTADO_MONITORAR_SUGESTAO,
    ESTADO_SIMULACAO_OPCAO,
    ESTADO_SIMULACAO_VALOR,
) = range(9)
 
# ─── Armazenamento em memória ──────────────────────────────────────────────────
# alertas_exatos: chat_id → lista de {ticker, valor, disparado}
alertas_exatos:  dict[int, list[dict]] = {}
# alertas_faixa:  chat_id → lista de {ticker, min, max, disparado}
alertas_faixa:   dict[int, list[dict]] = {}
# monitorados:    chat_id → set de tickers (ex: {"MXRF11.SA","PETR4.SA"})
monitorados:     dict[int, set]        = {}
 
# ─── Ações disponíveis para monitoramento ─────────────────────────────────────
ACOES_DISPONIVEIS = {
    # FIIs
    "MXRF11": "FII Maxi Renda",
    "KNRI11": "FII Kinea Renda Imobiliária",
    "HGLG11": "FII CSHG Logística",
    "XPML11": "FII XP Malls",
    "VISC11": "FII Vinci Shopping Centers",
    "BCFF11": "FII BTG Pactual Fundo de Fundos",
    "RZTR11": "FII Riza Terrax",
    # Ações B3
    "PETR4":  "Petrobras PN",
    "VALE3":  "Vale ON",
    "ITUB4":  "Itaú Unibanco PN",
    "BBDC4":  "Bradesco PN",
    "WEGE3":  "WEG ON",
    "MGLU3":  "Magazine Luiza ON",
    "BBAS3":  "Banco do Brasil ON",
    # Criptos (via Yahoo Finance)
    "BTC-USD": "Bitcoin (USD)",
    "ETH-USD": "Ethereum (USD)",
    "SOL-USD": "Solana (USD)",
}
 
 
# ─── Utilitários ──────────────────────────────────────────────────────────────
 
def _ticker_yf(ticker: str) -> str:
    """Adiciona .SA para ações B3; criptos já têm -USD."""
    if "-" in ticker:          # cripto ex: BTC-USD
        return ticker
    if ticker.endswith(".SA"):
        return ticker
    return ticker + ".SA"
 
 
def _get_quote(ticker: str) -> dict | None:
    """Busca cotação em tempo real. Retorna dict ou None."""
    try:
        t     = yf.Ticker(ticker)
        info  = t.fast_info
        price = info.last_price
        prev  = info.previous_close
        if price is None:
            return None
        change     = price - prev
        change_pct = (change / prev * 100) if prev else 0
        return {
            "ticker":     ticker,
            "price":      price,
            "prev":       prev,
            "change":     change,
            "change_pct": change_pct,
            "high":       info.day_high,
            "low":        info.day_low,
            "volume":     info.three_month_average_volume,
            "time":       datetime.now(BR_TZ).strftime("%H:%M:%S"),
        }
    except Exception as e:
        log.error("Erro ao buscar %s: %s", ticker, e)
        return None
 
 
def _get_dy(ticker: str) -> tuple[float | None, float | None]:
    """Retorna (dividend_yield %, valor_por_cota). Apenas para FIIs/ações."""
    try:
        t    = yf.Ticker(ticker)
        info = t.info
        dy   = info.get("dividendYield")          # ex: 0.1234 = 12,34%
        dps  = info.get("dividendRate")            # valor anual por cota
        dy_pct   = dy  * 100  if dy  else None
        dps_mensal = dps / 12 if dps else None
        return dy_pct, dps_mensal
    except Exception as e:
        log.error("Erro DY %s: %s", ticker, e)
        return None, None
 
 
def _fmt_quote(q: dict, dy_pct: float | None = None, dps_m: float | None = None) -> str:
    arrow = "🟢 ▲" if q["change"] >= 0 else "🔴 ▼"
    sinal = "+" if q["change"] >= 0 else ""
    nome  = q["ticker"].replace(".SA", "")
    txt = (
        f"📊 *{nome}* — {q['time']}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💰 Preço: *R$ {q['price']:.2f}*\n"
        f"{arrow} Variação: *{sinal}{q['change']:.2f} ({sinal}{q['change_pct']:.2f}%)*\n"
        f"📈 Máx.: R$ {q['high']:.2f}   📉 Mín.: R$ {q['low']:.2f}\n"
        f"📅 Fechamento anterior: R$ {q['prev']:.2f}\n"
        f"🔁 Vol. médio 3m: {int(q['volume'] or 0):,}".replace(",", ".")
    )
    if dy_pct is not None:
        txt += f"\n💸 Dividend Yield: *{dy_pct:.2f}% a.a.*"
    if dps_m is not None:
        txt += f"\n🪙 Proventos/cota: *R$ {dps_m:.4f}/mês*"
    return txt
 
 
def _salvar_sugestao(chat_id: int, sugestao: str) -> None:
    dados = {}
    if SUGESTOES_FILE.exists():
        try:
            dados = json.loads(SUGESTOES_FILE.read_text())
        except Exception:
            dados = {}
    lista = dados.get("sugestoes", [])
    lista.append({
        "chat_id": chat_id,
        "sugestao": sugestao,
        "data": datetime.now(BR_TZ).strftime("%Y-%m-%d %H:%M:%S"),
    })
    dados["sugestoes"] = lista
    SUGESTOES_FILE.write_text(json.dumps(dados, ensure_ascii=False, indent=2))
 
 
# ─── Teclados inline ──────────────────────────────────────────────────────────
 
def _menu_principal() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Cotação MXRF11",      callback_data="preco_mxrf11")],
        [InlineKeyboardButton("📈 Outras cotações",     callback_data="menu_cotacoes")],
        [InlineKeyboardButton("🔔 Criar alerta",        callback_data="menu_alerta")],
        [InlineKeyboardButton("📋 Meus alertas",        callback_data="listar_alertas")],
        [InlineKeyboardButton("👁️ Monitorar ações",     callback_data="menu_monitorar")],
        [InlineKeyboardButton("💰 Simulação MXRF11",    callback_data="menu_simulacao")],
        [InlineKeyboardButton("❓ Ajuda",               callback_data="ajuda")],
    ])
 
 
def _menu_cotacoes() -> InlineKeyboardMarkup:
    botoes = []
    row = []
    for ticker in ACOES_DISPONIVEIS:
        row.append(InlineKeyboardButton(ticker, callback_data=f"cot_{ticker}"))
        if len(row) == 3:
            botoes.append(row)
            row = []
    if row:
        botoes.append(row)
    botoes.append([InlineKeyboardButton("🔙 Voltar", callback_data="menu_principal")])
    return InlineKeyboardMarkup(botoes)
 
 
def _menu_monitorar(chat_id: int) -> InlineKeyboardMarkup:
    ativos = monitorados.get(chat_id, set())
    botoes = []
    row = []
    for ticker, nome in ACOES_DISPONIVEIS.items():
        marcado = "✅ " if _ticker_yf(ticker) in ativos else ""
        row.append(InlineKeyboardButton(f"{marcado}{ticker}", callback_data=f"mon_{ticker}"))
        if len(row) == 3:
            botoes.append(row)
            row = []
    if row:
        botoes.append(row)
    botoes.append([InlineKeyboardButton("📡 Ver todos monitorados", callback_data="mon_ver")])
    botoes.append([InlineKeyboardButton("💡 Sugerir nova ação",      callback_data="mon_sugerir")])
    botoes.append([InlineKeyboardButton("🔙 Voltar",                 callback_data="menu_principal")])
    return InlineKeyboardMarkup(botoes)
 
 
# ─── /start e menu principal ──────────────────────────────────────────────────
 
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    texto = (
        "👋 Olá! Sou o *Açãozito* 🐂\n"
        "Seu monitor de ações da B3 em tempo real\\!\n\n"
        "Escolha uma opção abaixo:"
    )
    if update.message:
        await update.message.reply_text(
            texto, parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=_menu_principal()
        )
    else:
        await update.callback_query.edit_message_text(
            texto, parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=_menu_principal()
        )
 
 
async def cb_menu_principal(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    await cmd_start(update, ctx)
 
 
# ─── Cotações via callback ─────────────────────────────────────────────────────
 
async def cb_preco_mxrf11(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("⏳ Buscando cotação…")
    ticker = "MXRF11.SA"
    quote  = _get_quote(ticker)
    dy_pct, dps_m = _get_dy(ticker)
    if quote:
        txt = _fmt_quote(quote, dy_pct, dps_m)
    else:
        txt = "❌ Não foi possível buscar a cotação agora. Tente novamente."
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Simular lucro", callback_data="menu_simulacao")],
        [InlineKeyboardButton("🔔 Criar alerta",  callback_data="menu_alerta")],
        [InlineKeyboardButton("🔙 Menu",          callback_data="menu_principal")],
    ])
    await q.edit_message_text(txt, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
 
 
async def cb_menu_cotacoes(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "📈 *Escolha uma ação ou cripto:*",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_menu_cotacoes(),
    )
 
 
async def cb_cotacao_ticker(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q      = update.callback_query
    await q.answer()
    ticker = q.data.replace("cot_", "")
    yf_tk  = _ticker_yf(ticker)
    await q.edit_message_text(f"⏳ Buscando {ticker}…")
    quote  = _get_quote(yf_tk)
    dy_pct, dps_m = _get_dy(yf_tk)
    if quote:
        txt = _fmt_quote(quote, dy_pct, dps_m)
    else:
        txt = f"❌ Não encontrei cotação para *{ticker}*."
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Atualizar",    callback_data=f"cot_{ticker}")],
        [InlineKeyboardButton("🔔 Criar alerta", callback_data=f"alerta_ticker_{ticker}")],
        [InlineKeyboardButton("🔙 Voltar",       callback_data="menu_cotacoes")],
    ])
    await q.edit_message_text(txt, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
 
 
# ─── Monitoramento simultâneo ─────────────────────────────────────────────────
 
async def cb_menu_monitorar(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    chat_id = q.from_user.id
    await q.edit_message_text(
        "👁️ *Monitorar ações simultâneas*\n\n"
        "Toque para ativar ✅ ou desativar uma ação.\n"
        "O bot atualizará os preços a cada 5 minutos.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_menu_monitorar(chat_id),
    )
 
 
async def cb_toggle_monitorar(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q       = update.callback_query
    await q.answer()
    chat_id = q.from_user.id
    ticker  = q.data.replace("mon_", "")
    yf_tk   = _ticker_yf(ticker)
    ativos  = monitorados.setdefault(chat_id, set())
    if yf_tk in ativos:
        ativos.discard(yf_tk)
        await q.answer(f"❌ {ticker} removido do monitoramento", show_alert=False)
    else:
        ativos.add(yf_tk)
        await q.answer(f"✅ {ticker} adicionado ao monitoramento", show_alert=False)
    await q.edit_message_reply_markup(reply_markup=_menu_monitorar(chat_id))
 
 
async def cb_mon_ver(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q       = update.callback_query
    await q.answer()
    chat_id = q.from_user.id
    ativos  = monitorados.get(chat_id, set())
    if not ativos:
        await q.edit_message_text(
            "ℹ️ Você não tem ações monitoradas.\nUse o menu para adicionar.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Voltar", callback_data="menu_monitorar")
            ]]),
        )
        return
    await q.edit_message_text("⏳ Buscando cotações…")
    linhas = []
    for tk in sorted(ativos):
        quote = _get_quote(tk)
        if quote:
            nome  = tk.replace(".SA", "")
            arrow = "🟢▲" if quote["change"] >= 0 else "🔴▼"
            sinal = "+" if quote["change"] >= 0 else ""
            linhas.append(
                f"{arrow} *{nome}* R$ {quote['price']:.2f} "
                f"({sinal}{quote['change_pct']:.2f}%)"
            )
        else:
            linhas.append(f"❓ {tk.replace('.SA','')} — sem dados")
    txt = (
        f"📡 *Painel — {datetime.now(BR_TZ).strftime('%H:%M:%S')}*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        + "\n".join(linhas)
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Atualizar", callback_data="mon_ver")],
        [InlineKeyboardButton("🔙 Voltar",    callback_data="menu_monitorar")],
    ])
    await q.edit_message_text(txt, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
 
 
async def cb_mon_sugerir(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "💡 *Sugerir nova ação ou cripto*\n\n"
        "Digite o código da ação ou cripto que você quer ver no bot "
        "(ex: `AAPL`, `NVDA`, `DOGE-USD`).\n\n"
        "Vou registrar a sugestão para análise!",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ESTADO_MONITORAR_SUGESTAO
 
 
async def receber_sugestao(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    sugestao = update.message.text.strip()
    _salvar_sugestao(update.effective_chat.id, sugestao)
    await update.message.reply_text(
        f"✅ Sugestão *{sugestao}* registrada! Obrigado 🙌\n"
        "Vou analisar e adicionar em breve.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_menu_principal(),
    )
    return ConversationHandler.END
 
 
# ─── Alertas de preço exato ───────────────────────────────────────────────────
 
async def cb_menu_alerta(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "🔔 *Criar alerta de preço*\n\n"
        "Digite o código da ação (ex: `MXRF11`, `PETR4`, `BTC-USD`)\n"
        "ou `/cancelar` para sair.",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ESTADO_ALERTA_TICKER
 
 
async def cb_alerta_ticker_predef(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Quando o usuário clica em 'Criar alerta' direto de uma cotação."""
    q = update.callback_query
    await q.answer()
    ticker = q.data.replace("alerta_ticker_", "")
    ctx.user_data["alerta_ticker"] = _ticker_yf(ticker)
    await q.edit_message_text(
        f"🔔 Alerta para *{ticker}*\n\n"
        "Qual tipo de alerta?\n",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🎯 Preço exato",    callback_data="alerta_tipo_exato")],
            [InlineKeyboardButton("📏 Faixa min/máx",  callback_data="alerta_tipo_faixa")],
        ]),
    )
    return ESTADO_ALERTA_TIPO
 
 
async def receber_ticker_alerta(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ticker = update.message.text.strip().upper()
    yf_tk  = _ticker_yf(ticker)
    ctx.user_data["alerta_ticker"] = yf_tk
    await update.message.reply_text(
        f"✅ Ticker: *{ticker}*\n\nQual tipo de alerta?",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🎯 Preço exato",   callback_data="alerta_tipo_exato")],
            [InlineKeyboardButton("📏 Faixa min/máx", callback_data="alerta_tipo_faixa")],
        ]),
    )
    return ESTADO_ALERTA_TIPO
 
 
async def cb_alerta_tipo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q    = update.callback_query
    await q.answer()
    tipo = q.data  # "alerta_tipo_exato" ou "alerta_tipo_faixa"
    ctx.user_data["alerta_tipo"] = tipo
    if tipo == "alerta_tipo_exato":
        await q.edit_message_text(
            "🎯 *Alerta de preço exato*\n\n"
            "Digite o valor que você quer ser avisado\n"
            "(ex: `9.75` ou `9,75`):",
            parse_mode=ParseMode.MARKDOWN,
        )
        return ESTADO_ALERTA_VALOR_EXATO
    else:
        await q.edit_message_text(
            "📏 *Alerta de faixa*\n\n"
            "Digite o valor *mínimo* da faixa (ex: `9.00`):",
            parse_mode=ParseMode.MARKDOWN,
        )
        return ESTADO_ALERTA_FAIXA_MIN
 
 
async def receber_valor_exato(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        valor  = float(update.message.text.strip().replace(",", "."))
    except ValueError:
        await update.message.reply_text("❌ Valor inválido. Digite um número (ex: 9.75).")
        return ESTADO_ALERTA_VALOR_EXATO
    chat_id = update.effective_chat.id
    ticker  = ctx.user_data["alerta_ticker"]
    alertas_exatos.setdefault(chat_id, []).append({
        "ticker": ticker, "valor": valor, "disparado": False
    })
    nome = ticker.replace(".SA", "")
    await update.message.reply_text(
        f"✅ *Alerta criado!*\n"
        f"📌 {nome} → aviso quando chegar em *R$ {valor:.2f}*",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_menu_principal(),
    )
    return ConversationHandler.END
 
 
async def receber_faixa_min(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        ctx.user_data["alerta_min"] = float(update.message.text.strip().replace(",", "."))
    except ValueError:
        await update.message.reply_text("❌ Valor inválido. Tente novamente.")
        return ESTADO_ALERTA_FAIXA_MIN
    await update.message.reply_text("Agora o valor *máximo* da faixa (ex: `10.50`):",
                                    parse_mode=ParseMode.MARKDOWN)
    return ESTADO_ALERTA_FAIXA_MAX
 
 
async def receber_faixa_max(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        maxv = float(update.message.text.strip().replace(",", "."))
    except ValueError:
        await update.message.reply_text("❌ Valor inválido. Tente novamente.")
        return ESTADO_ALERTA_FAIXA_MAX
    chat_id = update.effective_chat.id
    ticker  = ctx.user_data["alerta_ticker"]
    minv    = ctx.user_data["alerta_min"]
    alertas_faixa.setdefault(chat_id, []).append({
        "ticker": ticker, "min": minv, "max": maxv, "disparado": False
    })
    nome = ticker.replace(".SA", "")
    await update.message.reply_text(
        f"✅ *Alerta de faixa criado!*\n"
        f"📌 {nome} → aviso se sair de *R$ {minv:.2f} – R$ {maxv:.2f}*",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_menu_principal(),
    )
    return ConversationHandler.END
 
 
async def cb_listar_alertas(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q       = update.callback_query
    await q.answer()
    chat_id = q.from_user.id
    linhas  = ["📋 *Seus alertas ativos:*\n"]
 
    exatos = alertas_exatos.get(chat_id, [])
    if exatos:
        linhas.append("🎯 *Preço exato:*")
        for i, a in enumerate(exatos, 1):
            nome = a["ticker"].replace(".SA", "")
            linhas.append(f"  {i}. {nome} → R$ {a['valor']:.2f}")
 
    faixas = alertas_faixa.get(chat_id, [])
    if faixas:
        linhas.append("\n📏 *Faixa:*")
        for i, a in enumerate(faixas, 1):
            nome = a["ticker"].replace(".SA", "")
            linhas.append(f"  {i}. {nome} → R$ {a['min']:.2f}–{a['max']:.2f}")
 
    if not exatos and not faixas:
        linhas = ["ℹ️ Nenhum alerta ativo.\nUse o menu para criar um!"]
 
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑️ Apagar todos", callback_data="alertas_apagar")],
        [InlineKeyboardButton("🔙 Menu",         callback_data="menu_principal")],
    ])
    await q.edit_message_text("\n".join(linhas), parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
 
 
async def cb_alertas_apagar(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q       = update.callback_query
    await q.answer()
    chat_id = q.from_user.id
    alertas_exatos.pop(chat_id, None)
    alertas_faixa.pop(chat_id, None)
    await q.edit_message_text(
        "🗑️ Todos os alertas foram removidos.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Menu", callback_data="menu_principal")
        ]]),
    )
 
 
async def cancelar_conversa(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❌ Operação cancelada.", reply_markup=_menu_principal())
    return ConversationHandler.END
 
 
# ─── Simulação MXRF11 ─────────────────────────────────────────────────────────
 
async def cb_menu_simulacao(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "💰 *Simulação de lucro — MXRF11*\n\n"
        "Quer informar um valor de investimento para simulação?",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Sim, quero simular",       callback_data="sim_sim")],
            [InlineKeyboardButton("📊 Só ver dados do MXRF11",  callback_data="sim_nao")],
            [InlineKeyboardButton("🔙 Voltar",                  callback_data="menu_principal")],
        ]),
    )
    return ESTADO_SIMULACAO_OPCAO
 
 
async def cb_sim_nao(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Apenas exibe dados do MXRF11 sem simulação."""
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("⏳ Buscando dados do MXRF11…")
    ticker = "MXRF11.SA"
    quote  = _get_quote(ticker)
    dy_pct, dps_m = _get_dy(ticker)
    if quote:
        txt = _fmt_quote(quote, dy_pct, dps_m)
    else:
        txt = "❌ Não foi possível buscar os dados agora."
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Simular investimento", callback_data="sim_sim")],
        [InlineKeyboardButton("🔙 Menu",                callback_data="menu_principal")],
    ])
    await q.edit_message_text(txt, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    return ConversationHandler.END
 
 
async def cb_sim_sim(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "💸 *Quanto você pretende investir?*\n\n"
        "Digite o valor em reais (ex: `1000` ou `5000,50`):",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ESTADO_SIMULACAO_VALOR
 
 
async def receber_valor_simulacao(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        investimento = float(update.message.text.strip().replace(",", ".").replace("R$", "").replace(" ", ""))
    except ValueError:
        await update.message.reply_text("❌ Valor inválido. Digite um número (ex: 1000).")
        return ESTADO_SIMULACAO_VALOR
 
    await update.message.reply_text("⏳ Calculando…")
    ticker = "MXRF11.SA"
    quote  = _get_quote(ticker)
    dy_pct, dps_m = _get_dy(ticker)
 
    if not quote:
        await update.message.reply_text(
            "❌ Não consegui buscar o preço atual do MXRF11.",
            reply_markup=_menu_principal()
        )
        return ConversationHandler.END
 
    preco_cota   = quote["price"]
    cotas        = investimento / preco_cota
    dps_mes_real = dps_m if dps_m else (preco_cota * (dy_pct / 100 / 12) if dy_pct else None)
 
    if dps_mes_real:
        rendimento_mes = cotas * dps_mes_real
        rendimento_ano = rendimento_mes * 12
        dy_mes_pct     = (dps_mes_real / preco_cota) * 100
    else:
        rendimento_mes = rendimento_ano = dy_mes_pct = None
 
    txt = (
        f"💰 *Simulação MXRF11 — {datetime.now(BR_TZ).strftime('%d/%m/%Y')}*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💵 Investimento: *R$ {investimento:,.2f}*\n"
        f"📌 Preço/cota: *R$ {preco_cota:.2f}*\n"
        f"📦 Cotas adquiridas: *{cotas:.0f} cotas*\n"
    ).replace(",", "X").replace(".", ",").replace("X", ".")
 
    if rendimento_mes:
        sim = (
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📅 Rendimento/mês: *R$ {rendimento_mes:,.2f}*\n"
            f"📆 Rendimento/ano: *R$ {rendimento_ano:,.2f}*\n"
            f"📊 DY mensal: *{dy_mes_pct:.2f}%*\n"
            f"📊 DY anual: *{(dy_mes_pct*12):.2f}%*\n\n"
            f"_Baseado no dividend yield atual. "
            f"Valores podem variar mês a mês._"
        ).replace(",", "X").replace(".", ",").replace("X", ".")
    else:
        sim = "\n_⚠️ Dados de dividendos não disponíveis no momento._"
 
    await update.message.reply_text(
        txt + sim,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_menu_principal(),
    )
    return ConversationHandler.END
 
 
# ─── Ajuda via callback ────────────────────────────────────────────────────────
 
async def cb_ajuda(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "❓ *Ajuda — Açãozito* 🐂\n\n"
        "📊 *Cotação* — preço em tempo real de ações, FIIs e criptos\n"
        "🔔 *Alerta exato* — aviso quando a ação chegar a um preço específico\n"
        "📏 *Alerta faixa* — aviso quando sair de um intervalo\n"
        "👁️ *Monitorar* — acompanhe várias ações ao mesmo tempo\n"
        "💰 *Simulação* — calcule seu retorno mensal no MXRF11\n"
        "💸 *Dividend Yield* — exibido automaticamente na cotação\n\n"
        "⚠️ Dados com ~15 min de delay (Yahoo Finance gratuito).",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Menu", callback_data="menu_principal")
        ]]),
    )
 
 
# ─── Job de monitoramento automático ─────────────────────────────────────────
 
async def job_verificar_alertas(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Roda a cada 60s — verifica alertas exatos e de faixa."""
    todos_tickers: dict[str, list] = {}
 
    for chat_id, lista in alertas_exatos.items():
        for a in lista:
            todos_tickers.setdefault(a["ticker"], [])
 
    for chat_id, lista in alertas_faixa.items():
        for a in lista:
            todos_tickers.setdefault(a["ticker"], [])
 
    for ticker in todos_tickers:
        q = _get_quote(ticker)
        if not q:
            continue
        price = q["price"]
        nome  = ticker.replace(".SA", "")
 
        # alertas exatos
        for chat_id, lista in alertas_exatos.items():
            for a in lista:
                if a["ticker"] != ticker:
                    continue
                atingiu = abs(price - a["valor"]) / a["valor"] <= 0.005  # 0,5% de tolerância
                if atingiu and not a["disparado"]:
                    await ctx.bot.send_message(
                        chat_id=chat_id,
                        text=(
                            f"🎯 *ALERTA DE PREÇO — {nome}*\n"
                            f"Atingiu *R$ {price:.2f}* "
                            f"(alvo: R$ {a['valor']:.2f})\n\n"
                            + _fmt_quote(q)
                        ),
                        parse_mode=ParseMode.MARKDOWN,
                    )
                    a["disparado"] = True
                elif not atingiu:
                    a["disparado"] = False
 
        # alertas faixa
        for chat_id, lista in alertas_faixa.items():
            for a in lista:
                if a["ticker"] != ticker:
                    continue
                fora = price < a["min"] or price > a["max"]
                if fora and not a["disparado"]:
                    direcao = "⬇️ ABAIXO do mínimo" if price < a["min"] else "⬆️ ACIMA do máximo"
                    await ctx.bot.send_message(
                        chat_id=chat_id,
                        text=(
                            f"🚨 *ALERTA DE FAIXA — {nome}*\n"
                            f"Preço *R$ {price:.2f}* está {direcao}\n"
                            f"Faixa: R$ {a['min']:.2f}–{a['max']:.2f}\n\n"
                            + _fmt_quote(q)
                        ),
                        parse_mode=ParseMode.MARKDOWN,
                    )
                    a["disparado"] = True
                elif not fora:
                    a["disparado"] = False
 
 
async def job_painel_monitorados(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """A cada 5 minutos envia painel automático para quem tem ações monitoradas."""
    for chat_id, ativos in monitorados.items():
        if not ativos:
            continue
        linhas = []
        for tk in sorted(ativos):
            quote = _get_quote(tk)
            if quote:
                nome  = tk.replace(".SA", "")
                arrow = "🟢▲" if quote["change"] >= 0 else "🔴▼"
                sinal = "+" if quote["change"] >= 0 else ""
                linhas.append(
                    f"{arrow} *{nome}* R$ {quote['price']:.2f} "
                    f"({sinal}{quote['change_pct']:.2f}%)"
                )
        if linhas:
            txt = (
                f"📡 *Painel automático — {datetime.now(BR_TZ).strftime('%H:%M')}*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                + "\n".join(linhas)
            )
            try:
                await ctx.bot.send_message(chat_id=chat_id, text=txt,
                                           parse_mode=ParseMode.MARKDOWN)
            except Exception as e:
                log.warning("Não enviou painel para %s: %s", chat_id, e)
 
 
# ─── Registro de comandos e Main ──────────────────────────────────────────────
 
async def post_init(app: Application) -> None:
    await app.bot.set_my_commands([
        BotCommand("start",  "Abre o menu principal"),
        BotCommand("ajuda",  "Exibe ajuda"),
    ])
 
 
def main() -> None:
    app = Application.builder().token(TOKEN).post_init(post_init).build()
 
    # ConversationHandler unificado para alertas + sugestão + simulação
    conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cb_menu_alerta,         pattern="^menu_alerta$"),
            CallbackQueryHandler(cb_alerta_ticker_predef, pattern="^alerta_ticker_"),
            CallbackQueryHandler(cb_mon_sugerir,         pattern="^mon_sugerir$"),
            CallbackQueryHandler(cb_menu_simulacao,      pattern="^menu_simulacao$"),
        ],
        states={
            ESTADO_ALERTA_TICKER:     [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_ticker_alerta)],
            ESTADO_ALERTA_TIPO:       [CallbackQueryHandler(cb_alerta_tipo, pattern="^alerta_tipo_")],
            ESTADO_ALERTA_VALOR_EXATO:[MessageHandler(filters.TEXT & ~filters.COMMAND, receber_valor_exato)],
            ESTADO_ALERTA_FAIXA_MIN:  [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_faixa_min)],
            ESTADO_ALERTA_FAIXA_MAX:  [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_faixa_max)],
            ESTADO_MONITORAR_SUGESTAO:[MessageHandler(filters.TEXT & ~filters.COMMAND, receber_sugestao)],
            ESTADO_SIMULACAO_OPCAO:   [
                CallbackQueryHandler(cb_sim_sim, pattern="^sim_sim$"),
                CallbackQueryHandler(cb_sim_nao, pattern="^sim_nao$"),
                CallbackQueryHandler(cb_menu_principal, pattern="^menu_principal$"),
            ],
            ESTADO_SIMULACAO_VALOR:   [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_valor_simulacao)],
        },
        fallbacks=[CommandHandler("cancelar", cancelar_conversa)],
        per_message=False,
    )
 
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("ajuda",  cb_ajuda))
    app.add_handler(conv)
 
    # Callbacks simples (fora da conversa)
    app.add_handler(CallbackQueryHandler(cb_menu_principal,   pattern="^menu_principal$"))
    app.add_handler(CallbackQueryHandler(cb_preco_mxrf11,     pattern="^preco_mxrf11$"))
    app.add_handler(CallbackQueryHandler(cb_menu_cotacoes,    pattern="^menu_cotacoes$"))
    app.add_handler(CallbackQueryHandler(cb_cotacao_ticker,   pattern="^cot_"))
    app.add_handler(CallbackQueryHandler(cb_menu_monitorar,   pattern="^menu_monitorar$"))
    app.add_handler(CallbackQueryHandler(cb_toggle_monitorar, pattern="^mon_[A-Z0-9]"))
    app.add_handler(CallbackQueryHandler(cb_mon_ver,          pattern="^mon_ver$"))
    app.add_handler(CallbackQueryHandler(cb_listar_alertas,   pattern="^listar_alertas$"))
    app.add_handler(CallbackQueryHandler(cb_alertas_apagar,   pattern="^alertas_apagar$"))
    app.add_handler(CallbackQueryHandler(cb_ajuda,            pattern="^ajuda$"))
 
    # Jobs
    app.job_queue.run_repeating(job_verificar_alertas,  interval=60,  first=15)
    app.job_queue.run_repeating(job_painel_monitorados, interval=300, first=30)
 
    if WEBHOOK_URL:
        log.info("🚀 WEBHOOK — %s | porta %s", WEBHOOK_URL, PORT)
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            webhook_url=f"{WEBHOOK_URL}/webhook",
            url_path="/webhook",
        )
    else:
        log.info("🖥️  POLLING (local)")
        app.run_polling(allowed_updates=Update.ALL_TYPES)
 
 
if __name__ == "__main__":
    main()
    