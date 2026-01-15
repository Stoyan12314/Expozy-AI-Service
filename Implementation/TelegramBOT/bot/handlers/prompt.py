from telegram import Update
from telegram.ext import ContextTypes
from bot.services import orchestrator


async def prompt_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/prompt <description> - Send to AI and get preview link"""
    user_id = str(update.effective_user.id)
    chat_id = str(update.effective_chat.id)
    
    # Get prompt text
    if not context.args:
        await update.message.reply_text(
            "❌ Please provide a description.\n\n"
            "Example: /prompt Create a website for cars"
        )
        return
    
    prompt = " ".join(context.args)
    
    if len(prompt) < 10:
        await update.message.reply_text("❌ Too short. Be more specific.")
        return
    
    if len(prompt) > 2000:
        await update.message.reply_text("❌ Too long. Max 2000 characters.")
        return
    
    # Send status
    msg = await update.message.reply_html(
        "🚀 <b>Generating your page...</b>\n\n"
        "This may take 1-2 minutes."
    )
    
    # Call orchestrator
    result = await orchestrator.generate(user_id, chat_id, prompt)
    
    if result.get("success") and result.get("preview_url"):
        await msg.edit_text(
            f"✅ <b>Your page is ready!</b>\n\n"
            f"🔗 <a href='{result['preview_url']}'>View Preview</a>",
            parse_mode="HTML",
        )
    elif result.get("success"):
        await msg.edit_text("✅ Submitted. You'll receive the link when ready.")
    else:
        await msg.edit_text(f"❌ {result.get('error', 'Unknown error')}")
