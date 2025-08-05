#!/usr/bin/env python3
"""
Telegram Bot 指令
  /callgraph  —— 读取 system.svg，用 CairoSVG 转成 PNG 后发送
  /ask <问题> —— 调用 ask_code.py --json 并格式化输出，同时兼容非 JSON 文本提示
  /syncneo   —— 触发 sync_to_neo4j.py，同步到 Neo4j（读取 /etc/kingbrain/sync_to_neo4j.env）
"""

import os
import io
import subprocess
import logging
import asyncio
import json
from telegram import Update
from telegram.ext import ContextTypes
from telegram.helpers import escape_markdown
from telegram.error import BadRequest
import cairosvg

# —— 路径配置 ——#
BASE_DIR    = os.path.dirname(__file__)
GRAPH_SVG   = os.path.join(BASE_DIR, "..", "graphs", "system.svg")
ASK_SCRIPT  = os.path.join(BASE_DIR, "ask_code.py")
PYBIN       = os.path.join(os.path.dirname(BASE_DIR), ".venv", "bin", "python")
SYNC_SCRIPT = "/srv/kingbrain/insight/scripts/sync_to_neo4j.py"
ENV_FILE    = "/etc/kingbrain/sync_to_neo4j.env"
ADMIN_IDS   = {int(i) for i in os.getenv("ADMIN_IDS", "").split(",") if i}

logging.basicConfig(
    level=logging.INFO,
    format="🪶 %(asctime)s [%(levelname)s] %(message)s"
)

# ───────────────────── /callgraph ───────────────────────────
async def cmd_callgraph(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not os.path.isfile(GRAPH_SVG):
        await update.message.reply_text(f"⚠️ SVG 不存在: {GRAPH_SVG}")
        logging.error("SVG 文件不存在：%s", GRAPH_SVG)
        return

    try:
        svg_bytes = open(GRAPH_SVG, "rb").read()
        png_bytes = cairosvg.svg2png(bytestring=svg_bytes)
    except Exception as e:
        logging.exception("CairoSVG 转 PNG 失败")
        await update.message.reply_text(f"❌ SVG→PNG 转换失败：{e}")
        return

    bio = io.BytesIO(png_bytes)
    bio.name = "system.png"
    try:
        await update.message.reply_photo(
            photo=bio,
            caption="🖼 最新依赖图"
        )
        logging.info("依赖图 PNG 发送成功，大小=%d bytes", len(png_bytes))
    except BadRequest as e:
        logging.exception("send_photo 失败")
        await update.message.reply_text(f"❌ send_photo 失败：{e}")

# ───────────────────── /ask ────────────────────────────────
async def cmd_ask(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if ADMIN_IDS and update.effective_user.id not in ADMIN_IDS:
        return await update.message.reply_text("⛔️ 仅管理员可用 /ask")
    if not ctx.args:
        return await update.message.reply_text("用法：/ask <自然语言问题>")

    await update.message.reply_text("🤔 正在检索…")
    env = os.environ.copy()
    profile = os.path.expanduser("~/.profile")
    if os.path.exists(profile):
        with open(profile) as pf:
            for line in pf:
                if line.startswith("export "):
                    k, v = line[len("export "):].strip().split("=", 1)
                    env[k] = v.strip().strip("'\"")

    cmd = [PYBIN, ASK_SCRIPT, "--json"] + ctx.args
    try:
        raw = subprocess.check_output(
            cmd, text=True, stderr=subprocess.STDOUT,
            timeout=150, env=env
        ).strip()
    except subprocess.CalledProcessError as e:
        logging.exception("ask_code.py 调用失败")
        err = escape_markdown(e.output or str(e), version=2)
        return await update.message.reply_markdown_v2(f"❌ ask_code 错误：\n{err}")
    except Exception as e:
        logging.exception("ask 处理异常")
        return await update.message.reply_text(f"❌ 处理失败：{e}")

    if not raw:
        return await update.message.reply_text("❌ ask_code 无任何输出，请检查输入或脚本。")
    if not raw.lstrip().startswith("{"):
        return await update.message.reply_text(raw)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logging.error("JSON 解析失败，原始输出：%s", raw)
        return await update.message.reply_text(
            "❌ JSON 解析失败，请检查 ask_code 输出：\n```\n" + raw + "\n```"
        )

    answer = escape_markdown(data.get("answer", "（无答案）"), version=2)
    chunks = data.get("chunks", [])[:3]
    snippet_blocks = []
    for c in chunks:
        header = f"*{escape_markdown(c['filePath'],2)}:{c['startLine']}-{c['endLine']}*"
        code   = c['content'][:350]
        snippet_blocks.append(f"{header}\n```python\n{escape_markdown(code,2)}\n```")

    msg = f"💡 *Answer*\n{answer}\n\n" + "\n\n".join(snippet_blocks)
    await update.message.reply_markdown_v2(msg[:4090])

# ───────────────────── /syncneo ─────────────────────────────
async def cmd_syncneo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if ADMIN_IDS and update.effective_user.id not in ADMIN_IDS:
        return await update.message.reply_text("⛔️ 仅管理员可用 /syncneo")

    await update.message.reply_text("🔄 开始同步到 Neo4j… 这可能需要几秒钟")

    # 读取环境文件并注入到子进程
    env = os.environ.copy()
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE) as ef:
            for line in ef:
                line=line.strip()
                if not line or line.startswith("#"):
                    continue
                k,v = line.split("=",1)
                env[k]=v
    else:
        return await update.message.reply_text(
            f"❌ 无法找到环境文件，请确保已创建\n{ENV_FILE}"
        )

    # 执行同步脚本
    proc = await asyncio.create_subprocess_exec(
        PYBIN, SYNC_SCRIPT,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=os.path.dirname(SYNC_SCRIPT),
        env=env
    )
    out, err = await proc.communicate()
    text = (out+err).decode(errors="ignore").strip()

    await update.message.reply_text(
        f"✅ 同步完成：\n```\n{text}\n```",
        parse_mode="Markdown"
    )
