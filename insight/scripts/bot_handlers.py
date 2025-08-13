#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Bot 指令
  /callgraph  —— 读取 system.svg，用 CairoSVG 转成 PNG 后发送
  /ask <问题> —— 调用 ask_code.py --json，仅发送“答案文本”（不带日志/代码片段）
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
from telegram.error import BadRequest
import cairosvg

# —— 路径配置 —— #
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

TG_MAX = 4096  # Telegram 文本消息字符上限（保守值）

async def send_long_text(message, text: str, *, chunk_size: int = TG_MAX - 64):
    """把超长文本按段落切片发送（纯文本，无 Markdown 解析风险）。"""
    if not text:
        return
    start = 0
    n = len(text)
    while start < n:
        end = min(start + chunk_size, n)
        cut = text.rfind("\n", start, end)  # 优先在换行处截断
        if cut != -1 and cut > start + 50:
            end = cut + 1
        chunk = text[start:end]
        await message.reply_text(chunk, disable_web_page_preview=True)
        start = end

async def reply_as_document(message, text: str, filename: str = "answer.txt", caption: str = "结果较长，已作为文件发送"):
    """当答案极长时，直接发文件最稳。"""
    from io import BytesIO
    bio = BytesIO(text.encode("utf-8"))
    bio.name = filename
    await message.reply_document(document=bio, filename=filename, caption=caption)

# ───────────────────── /callgraph ───────────────────────────
async def cmd_callgraph(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not os.path.isfile(GRAPH_SVG):
        await update.message.reply_text(f"⚠️ SVG 不存在: {GRAPH_SVG}")
        logging.error("SVG 文件不存在：%s", GRAPH_SVG)
        return

    try:
        with open(GRAPH_SVG, "rb") as f:
            svg_bytes = f.read()
        png_bytes = cairosvg.svg2png(bytestring=svg_bytes)
    except Exception as e:
        logging.exception("CairoSVG 转 PNG 失败")
        await update.message.reply_text(f"❌ SVG→PNG 转换失败：{e}")
        return

    bio = io.BytesIO(png_bytes)
    bio.name = "system.png"
    try:
        await update.message.reply_photo(photo=bio, caption="🖼 最新依赖图")
        logging.info("依赖图 PNG 发送成功，大小=%d bytes", len(png_bytes))
    except BadRequest as e:
        logging.exception("send_photo 失败")
        await update.message.reply_text(f"❌ send_photo 失败：{e}")

# ───────────────────── /ask ────────────────────────────────
async def cmd_ask(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    只发送“答案文本”本身：
    - 调用 ask_code.py --json
    - 只取 JSON 里的 data['answer'] 纯文本返回
    - 不带日志、不带代码片段，超长自动分片或发文件
    """
    if ADMIN_IDS and update.effective_user.id not in ADMIN_IDS:
        return await update.message.reply_text("⛔️ 仅管理员可用 /ask")
    if not ctx.args:
        return await update.message.reply_text("用法：/ask <自然语言问题>")

    await update.message.reply_text("🤔 正在检索…")
    # 继承环境变量（可读 ~/.profile 的 export）
    env = os.environ.copy()
    profile = os.path.expanduser("~/.profile")
    if os.path.exists(profile):
        try:
            with open(profile) as pf:
                for line in pf:
                    if line.startswith("export "):
                        k, v = line[len("export "):].strip().split("=", 1)
                        env[k] = v.strip().strip("'\"")
        except Exception:
            pass

    cmd = [PYBIN, ASK_SCRIPT, "--json"] + ctx.args

    try:
        # 用 run + capture_output 分离 stdout/stderr，避免日志污染 JSON
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=180, env=env)
    except Exception as e:
        logging.exception("ask 调用异常")
        return await update.message.reply_text(f"❌ 处理失败：{e}")

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()

    if proc.returncode != 0:
        # 下游脚本报错，返回简短错误，不回传整段日志
        if stderr:
            snippet = stderr.splitlines()[-1][:300]  # 取最后一行的前 300 字符
            return await update.message.reply_text(f"❌ ask_code 失败：{snippet}")
        return await update.message.reply_text("❌ ask_code 执行失败（无错误输出）")

    if not stdout:
        return await update.message.reply_text("❌ ask_code 无输出，请检查脚本。")

    # 尝试从 stdout 中定位 JSON —— 兼容前面可能存在的少量非 JSON 内容
    raw = stdout.lstrip()
    if not raw.startswith("{"):
        idx = raw.find("{")
        if idx != -1:
            raw = raw[idx:]
        else:
            # 完全不是 JSON：不回日志，给一句简短提示
            return await update.message.reply_text("⚠️ 未获得结构化答案，请稍后再试。")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return await update.message.reply_text("⚠️ 答案解析失败，请稍后再试。")

    answer = (data.get("answer") or "").strip()
    if not answer:
        return await update.message.reply_text("（无答案）")

    # 仅发送“答案文本”；超长自动处理
    if len(answer) > 3800:
        # 很长的答案：分片发送，避免 TG 限制；如更偏好文档，换成 reply_as_document 即可
        return await send_long_text(update.message, answer)
        # 或者：
        # return await reply_as_document(update.message, answer, filename="answer.txt")

    return await update.message.reply_text(answer, disable_web_page_preview=True)

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
                if "=" in line:
                    k,v = line.split("=",1)
                    env[k]=v
    else:
        return await update.message.reply_text(
            f"❌ 无法找到环境文件，请确保已创建\n{ENV_FILE}"
        )

    # 执行同步脚本
    try:
        proc = await asyncio.create_subprocess_exec(
            PYBIN, SYNC_SCRIPT,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=os.path.dirname(SYNC_SCRIPT),
            env=env
        )
        out, err = await proc.communicate()
        text = (out + err).decode(errors="ignore").strip()
    except Exception as e:
        logging.exception("sync_to_neo4j 调用异常")
        return await update.message.reply_text(f"❌ 同步失败：{e}")

    # 仅发精简结果
    brief = "\n".join(text.splitlines()[-40:])  # 取最后 40 行
    if len(brief) > 3800:
        return await reply_as_document(update.message, brief, filename="sync_result.txt", caption="✅ 同步完成（结果较长，发文件）")
    return await update.message.reply_text(f"✅ 同步完成：\n{brief}")
