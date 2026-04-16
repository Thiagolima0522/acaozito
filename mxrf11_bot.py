"""
Açãozito — Bot Telegram Monitor de Ações B3
Deploy: Railway (webhook mode)
"""
 
import os
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
 
import yfinance as yf
from telegram import Update, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)
from telegram.constants import ParseMode
 
# ─── Configuração ──────────────────────────────────────────────────────────────
TOKEN        = os.environ["TELEGRAM_BOT_TOKEN"]   # obrigatório via variável de ambiente
WEBHOOK_URL  = os.environ.get("WEBHOOK_URL", "")  # ex: https://acaozito.up.railway.app
PORT         = int(os.environ.get("PORT", 8443))  # Railway injeta PORT automaticamente
 
BR_TZ = ZoneInfo("America/Sao_Paulo")
ALERTA_CHAT_IDS: dict[int, dict] = {}
 
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)
 
 
# ─── Cotação ──────────────────────────────────────────────────────────────────
 
def _get_quote(ticker: str) -> dict | None:
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
 
 
def _fmt_quote(q: dict) -> str:
    arrow = "🟢 ▲" if q["change"] >= 0 else "🔴 ▼"
    sinal = "+" if q["change"] >= 0 else ""
    return (
        f"📊 *{q['ticker'].replace('.SA', '')}* — {q['time']}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💰 Preço: *R$ {q['price']:.2f}*\n"
        f"{arrow} Variação: *{sinal}{q['change']:.2f} ({sinal}{q['change_pct']:.2f}%)*\n"
        f"📈 Máx.: R$ {q['high']:.2f}   📉 Mín.: R$ {q['low']:.2f}\n"
        f"📅 Fechamento anterior: R$ {q['prev']:.2f}\n"
        f"🔁 Vol. médio 3m: {int(q['volume'] or 0):,}".replace(",", ".")
    )
 
 
# ─── Comandos ─────────────────────────────────────────────────────────────────
 
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    texto = (
        "👋 Olá! Sou o *Açãozito* 🐂\n"
        "Seu monitor de ações da B3 em tempo real!\n\n"
        "📋 *Comandos disponíveis:*\n"
        "/preco — cotação atual do MXRF11\n"
        "/preco PETR4 — cotação de qualquer ação\n"
        "/alerta 9.50 10.50 — avisa quando sair da faixa\n"
        "/alerta PETR4 34.00 38.00 — alerta para outro ticker\n"
        "/alertas — lista seus alertas ativos\n"
        "/parar — cancela todos os alertas\n"
        "/ajuda — exibe esta mensagem\n"
    )
    await update.message.reply_text(texto, parse_mode=ParseMode.MARKDOWN)
 
 
async def cmd_ajuda(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, ctx)
 
 
async def cmd_preco(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    ticker = (ctx.args[0].upper() if ctx.args else "MXRF11")
    if not ticker.endswith(".SA"):
        ticker += ".SA"
    msg = await update.message.reply_text("⏳ Buscando cotação…")
    q = _get_quote(ticker)
    if q:
        await msg.edit_text(_fmt_quote(q), parse_mode=ParseMode.MARKDOWN)
    else:
        await msg.edit_text(
            f"❌ Não encontrei cotação para *{ticker.replace('.SA', '')}*.",
            parse_mode=ParseMode.MARKDOWN,
        )
 
 
async def cmd_alerta(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    args = ctx.args
    if len(args) == 2:
        ticker, minv, maxv = "MXRF11.SA", args[0], args[1]
    elif len(args) == 3:
        ticker = args[0].upper()
        if not ticker.endswith(".SA"):
            ticker += ".SA"
        minv, maxv = args[1], args[2]
    else:
        await update.message.reply_text(
            "⚠️ Uso:\n`/alerta 9.50 10.50`\n`/alerta PETR4 34.00 38.00`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    try:
        minv, maxv = float(minv.replace(",", ".")), float(maxv.replace(",", "."))
    except ValueError:
        await update.message.reply_text("❌ Valores inválidos. Use números (ex: 9.50).")
        return
    ALERTA_CHAT_IDS[chat_id] = {"ticker": ticker, "min": minv, "max": maxv, "disparado": False}
    nome = ticker.replace(".SA", "")
    await update.message.reply_text(
        f"✅ Alerta configurado para *{nome}*:\n"
        f"🔔 Aviso se sair de *R$ {minv:.2f} – R$ {maxv:.2f}*",
        parse_mode=ParseMode.MARKDOWN,
    )
 
 
async def cmd_alertas(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    cfg = ALERTA_CHAT_IDS.get(chat_id)
    if not cfg:
        await update.message.reply_text("ℹ️ Nenhum alerta ativo. Use /alerta para criar.")
        return
    nome = cfg["ticker"].replace(".SA", "")
    await update.message.reply_text(
        f"🔔 *Alerta ativo:*\nTicker: *{nome}*\n"
        f"Faixa: R$ {cfg['min']:.2f} – R$ {cfg['max']:.2f}",
        parse_mode=ParseMode.MARKDOWN,
    )
 
 
async def cmd_parar(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if chat_id in ALERTA_CHAT_IDS:
        del ALERTA_CHAT_IDS[chat_id]
        await update.message.reply_text("🛑 Alertas cancelados.")
    else:
        await update.message.reply_text("ℹ️ Não há alertas ativos.")
 
 
# ─── Job de alertas ───────────────────────────────────────────────────────────
 
async def job_verificar_alertas(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not ALERTA_CHAT_IDS:
        return
    tickers_chats: dict[str, list[int]] = {}
    for chat_id, cfg in ALERTA_CHAT_IDS.items():
        tickers_chats.setdefault(cfg["ticker"], []).append(chat_id)
 
    for ticker, chats in tickers_chats.items():
        q = _get_quote(ticker)
        if not q:
            continue
        nome = ticker.replace(".SA", "")
        for chat_id in chats:
            cfg = ALERTA_CHAT_IDS.get(chat_id)
            if not cfg:
                continue
            price = q["price"]
            fora  = price < cfg["min"] or price > cfg["max"]
            if fora and not cfg.get("disparado"):
                direcao = "⬇️ ABAIXO do mínimo" if price < cfg["min"] else "⬆️ ACIMA do máximo"
                await ctx.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"🚨 *ALERTA — {nome}*\n"
                        f"Preço *R$ {price:.2f}* está {direcao}\n"
                        f"Faixa: R$ {cfg['min']:.2f} – R$ {cfg['max']:.2f}\n\n"
                        + _fmt_quote(q)
                    ),
                    parse_mode=ParseMode.MARKDOWN,
                )
                cfg["disparado"] = True
            elif not fora:
                cfg["disparado"] = False
 
 
# ─── Main ─────────────────────────────────────────────────────────────────────
 
async def post_init(app: Application) -> None:
    await app.bot.set_my_commands([
        BotCommand("preco",   "Cotação em tempo real (ex: /preco MXRF11)"),
        BotCommand("alerta",  "Alerta de preço (ex: /alerta 9.50 10.50)"),
        BotCommand("alertas", "Lista alertas ativos"),
        BotCommand("parar",   "Cancela todos os alertas"),
        BotCommand("ajuda",   "Exibe ajuda"),
    ])
 
 
def main() -> None:
    app = Application.builder().token(TOKEN).post_init(post_init).build()
 
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("ajuda",   cmd_ajuda))
    app.add_handler(CommandHandler("help",    cmd_ajuda))
    app.add_handler(CommandHandler("preco",   cmd_preco))
    app.add_handler(CommandHandler("alerta",  cmd_alerta))
    app.add_handler(CommandHandler("alertas", cmd_alertas))
    app.add_handler(CommandHandler("parar",   cmd_parar))
 
    app.job_queue.run_repeating(
        job_verificar_alertas,
        interval=60,
        first=10,
        name="monitor_alertas",
    )
 
    # ── Webhook (Railway) ou Polling (local/teste) ─────────────────────────────
    if WEBHOOK_URL:
        log.info("🚀 Modo WEBHOOK — %s | porta %s", WEBHOOK_URL, PORT)
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            webhook_url=f"{WEBHOOK_URL}/webhook",
            url_path="/webhook",
        )
    else:
        log.info("🖥️  Modo POLLING (local)")
        app.run_polling(allowed_updates=Update.ALL_TYPES)
 
 
if __name__ == "__main__":
    main()