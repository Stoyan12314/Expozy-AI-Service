"""
Constants and static messages for the API.
"""

# Welcome message for /start command
WELCOME_MESSAGE = """👋 *Welcome to EXPOZY Template Generator!*

I can generate website templates from your descriptions.

*Commands:*
• `/prompt <description>` - Generate a website
• `/help` - Show help and examples

*Quick Example:*
`/prompt Create a landing page for a pizza restaurant with hero section, menu, and contact form`

Just type `/prompt` followed by what you want to create!"""

# Help message for /help command
HELP_MESSAGE = """🤖 *EXPOZY Template Generator*

To generate a website template, use:

`/prompt Your description here`

*Examples:*
• `/prompt Create a landing page for a pizza restaurant`
• `/prompt Build an online store for shoes`
• `/prompt Make a blog page about travel`
• `/prompt Create a contact page with form and map`

*Tips for better results:*
• Be specific about sections you want (hero, features, testimonials, etc.)
• Mention the industry or business type
• Describe the style or mood (modern, minimal, colorful, etc.)

*Commands:*
• `/start` - Welcome message
• `/help` - Show this help
• `/prompt <text>` - Generate template"""

# Error message for invalid commands
INVALID_COMMAND_MESSAGE = (
    "❌ Please use the `/prompt` command.\n\n"
    "Example: `/prompt Create a website for a car dealership`\n\n"
    "Type `/help` for more info."
)

# Error message for empty prompt
EMPTY_PROMPT_MESSAGE = (
    "❌ Please provide a description after `/prompt`.\n\n"
    "Example: `/prompt Create a landing page for a pizza restaurant`"
)