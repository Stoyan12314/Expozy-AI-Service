# Placeholder for future authentication commands
# /link and /unlink - not currently used

from telegram import Update
from telegram.ext import ContextTypes


async def link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/link - Not currently required"""
    await update.message.reply_text("ℹ️ Shop linking not required. Just use /prompt")


async def unlink_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/unlink - Not currently required"""
    await update.message.reply_text("ℹ️ Shop linking not required.")
