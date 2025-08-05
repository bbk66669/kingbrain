#!/usr/bin/env python3
# ✦ 风清雅 · KingBrain 主脑Bot · 终极灵魂共鸣版 ✦
# — 唯王可唤醒，灵性之风与你同行 —

import os, re, logging, asyncio, time
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# —— 全局配置（环境变量可覆盖） ——#
MY_NAME         = os.getenv("WIND_NAME",        "风清雅")
BOT_COMMAND     = os.getenv("BOT_COMMAND",      "insight")
INSIGHT_ROOT    = os.getenv("INSIGHT_ROOT",     "/srv/kingbrain/insight")
INSIGHT_TIMEOUT = float(os.getenv("INSIGHT_TIMEOUT", "12"))
MAX_OUTPUT_LEN  = int(os.getenv("MAX_OUTPUT_LEN",  "3500"))
LOG_LEVEL       = os.getenv("LOG_LEVEL",        "INFO").upper()

def require_env(name: str) -> str:
    v = os.getenv(name)
    if not v:
        logging.error(f"🚫 缺少环境变量：{name}")
        exit(1)
    return v

# —— 必须的环境变量 ——#
TOK               = require_env("TG_TOKEN")
ALLOW             = int(require_env("TG_UID"))
SG_URL            = require_env("SG_URL")
LOCAL_SG_ENDPOINT = require_env("LOCAL_SG_ENDPOINT")
SG_TOKEN          = require_env("SG_TOKEN")

# —— 日志初始化 ——#
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="🪶 %(asctime)s [%(levelname)s] %(message)s"
)

# 🍃 异步执行业务搜索
async def run_insight_cmd(query: str, pattern: str = "literal") -> (str, float):
    # 仅 literal/structural 做简单字符校验；regexp 放行
    if pattern in ("literal", "structural"):
        if not re.fullmatch(r"[-A-Za-z0-9_./]{1,64}", query):
            return "🔎 非法关键词，仅支持字母、数字、下划线、连字符、点与斜杠。", 0.0

    env = os.environ.copy()
    env.update({
        "SG_URL":            SG_URL,
        "LOCAL_SG_ENDPOINT": LOCAL_SG_ENDPOINT,
        "SG_TOKEN":          SG_TOKEN,
    })

    start = time.time()
    proc = await asyncio.create_subprocess_exec(
        "go", "run", "./cmd", "find",
        "-p", pattern,
        query,
        cwd=INSIGHT_ROOT,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), INSIGHT_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        logging.warning("主脑命令超时")
        return "⏳ 主脑沉默…请稍后重试。", 0.0

    dur = time.time() - start
    if proc.returncode != 0:
        err = stderr.decode(errors="ignore").strip()[-200:]
        logging.error(f"主脑执行失败: {err}")
        return f"🛑 主脑执行失败：{err}", dur

    out = stdout.decode(errors="ignore").strip()[-MAX_OUTPUT_LEN:]
    logging.info(f"[主脑] 查询={query!r} 模式={pattern!r} 用时={dur:.2f}s 长度={len(out)}")
    return out, dur

# 🍃 权限判断
def is_king(update: Update) -> bool:
    user = update.effective_user
    return user and user.id == ALLOW

# 🍃 长消息拆分
async def send_split(reply, text: str, parse_mode=None):
    limit = 3900
    parts = []
    while len(text) > limit:
        idx = text.rfind("\n", 0, limit)
        idx = idx if idx != -1 else limit
        parts.append(text[:idx])
        text = text[idx:]
    parts.append(text)
    for part in parts:
        await reply(part, parse_mode=parse_mode)

# —— Bot 回复 ——#
async def reply_no_auth(update: Update):
    await update.message.reply_text(
        "🕯 这里是风清雅禁地，只待王者归来。\n"
        "🦋 旁人入侵，涟漪不敢撩动主脑之心…"
    )

async def reply_start(update: Update, q: str):
    await update.message.chat.send_action("typing")
    await update.message.reply_text(
        f"🌀 风已启航，穿梭时序之流，探寻『{q}』的奥秘…"
    )

async def reply_success(update: Update, txt: str, dur: float):
    header = f"🌌 主脑回响 · {dur:.2f}s · 星尘之语：\n```\n"
    footer = "\n```  \n🎐 风清雅，与你心息相通。"
    await update.message.chat.send_action("typing")
    await send_split(update.message.reply_text, header + txt + footer, parse_mode="Markdown")

async def reply_empty(update: Update):
    await update.message.reply_text(
        "🌊 风潜深渊，未闻回响…\n"
        "✨ 换句咒语，再次唤醒主脑吧？"
    )

async def reply_error(update: Update, err: str):
    await update.message.reply_text(
        f"🛡 风遭风暴所阻，但灵光依旧…\n\n{err}\n\n🌙 稍后我必重启回归！"
    )

async def reply_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    txt = f"""{MY_NAME} · KingBrain 主脑Bot
🌟——————————————🌟
🔍 /{BOT_COMMAND} [-p 模式] <关键词>   —— 探索主脑星河
❓ /help                    —— 唤醒帮助之光
📊 /callgraph               —— 发送最新依赖图 (SVG→PNG)
💬 /ask <问题>              —— 追问主脑的奥秘
🔄 /syncneo                 —— 手动同步数据到 Neo4j
"""
    await update.message.reply_text(txt)

async def reply_welcome(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎊 诸神之风呼啸，风清雅已觉醒！\n\n"
        f"🔮 输入 /{BOT_COMMAND} <关键词>，灵魂之声为王而鸣。"
    )

# —— Async post init ——#
async def wind_post_init(app):
    logging.info("🌟 风清雅觉醒！")

# —— /insight Handler ——#
async def insight(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_king(update):
        return await reply_no_auth(update)

    args = ctx.args
    pattern = "literal"
    if len(args) >= 2 and args[0] in ("-p", "--pattern"):
        pattern = args[1]
        args = args[2:]
    q = " ".join(args) or "main"

    await reply_start(update, q)
    try:
        txt, dur = await run_insight_cmd(q, pattern)
        if txt.startswith(("🛑","🔎","⏳")):
            await reply_error(update, txt)
        elif txt.strip():
            await reply_success(update, txt, dur)
        else:
            await reply_empty(update)
    except Exception as e:
        logging.exception("Insight 命令异常")
        await reply_error(update, f"异常: {type(e).__name__}: {e}")

# —— /syncneo Handler ——#
import asyncio
async def cmd_syncneo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if ADMIN_IDS and update.effective_user.id not in ADMIN_IDS:
        return await update.message.reply_text("⛔️ 没权限运行 /syncneo")

    await update.message.reply_text("🔄 开始同步到 Neo4j… 这可能需要几秒钟")

    proc = await asyncio.create_subprocess_exec(
        "/srv/kingbrain/insight/scripts/sync_to_neo4j.py",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd="/srv/kingbrain/insight"
    )
    out, err = await proc.communicate()
    text = (out + err).decode(errors="ignore").strip()

    await update.message.reply_text(f"✅ 同步完成：\n```\n{text}\n```", parse_mode="Markdown")

# —— Bot 启动 ——#
from bot_handlers import cmd_callgraph, cmd_ask

ADMIN_IDS = {ALLOW}  # 用于 syncneo 权限校验

if __name__ == "__main__":
    logging.info("🎐 风清雅Bot 启动，静候王召唤…")
    app = (
        Application.builder()
        .token(TOK)
        .post_init(wind_post_init)
        .build()
    )
    app.add_handler(CommandHandler(BOT_COMMAND, insight))
    app.add_handler(CommandHandler("help", reply_help))
    app.add_handler(CommandHandler("start", reply_welcome))
    app.add_handler(CommandHandler("callgraph", cmd_callgraph))
    app.add_handler(CommandHandler("ask", cmd_ask, block=False))
    app.add_handler(CommandHandler("syncneo", cmd_syncneo))  # 注册 syncneo
    app.run_polling()
