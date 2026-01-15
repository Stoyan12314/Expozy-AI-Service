from telegram import Update
from telegram.ext import ContextTypes


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/start - Welcome"""
    await update.message.reply_html(
        "👋 <b>EXPOZY Page Generator</b>\n\n"
        "Send me a description and I'll generate a webpage for you.\n\n"
        "<b>Usage:</b>\n"
        "<code>/prompt Create a website for cars</code>"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/help - Show help"""
    await update.message.reply_html(
        "📖 <b>How to use:</b>\n\n"
        "Just describe what you want:\n"
        "<code>/prompt Create a landing page for a car dealership</code>\n\n"
        "I'll send you the preview link when ready!"
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/status - Check status"""
    await update.message.reply_text("ℹ️ Ready to generate. Use /prompt <description>")


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/cancel - Cancel job"""
    await update.message.reply_text("ℹ️ No active job to cancel.")
