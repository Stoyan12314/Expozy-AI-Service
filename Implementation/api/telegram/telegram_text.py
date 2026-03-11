# Welcome message for /start command
WELCOME_MESSAGE = """👋 *Welcome to EXPOZY Template Generator!*

I can generate website templates from your descriptions.

*Commands:*
- `/newstore` - Create your Expozy storefront
- `/login` - Reconnect to an existing store
- `/logout` - Disconnect your active store
- `/logoutall` - Disconnect all stores
- `/mystore` - List & switch between stores
- `/status` - Check connection status
- `/prompt <description>` - Generate a website
- `/help` - Show help and examples

*Quick Start:*
1. Send `/newstore` and follow the steps
2. Then send a prompt to generate pages

*Quick Example:*
`/prompt Create a landing page for a pizza restaurant with hero section, menu, and contact form`"""

# Help message for /help command
HELP_MESSAGE = """🤖 *EXPOZY Template Generator*

*Store setup:*
- `/newstore` - Create a new Expozy storefront
- `/login` - Reconnect to your existing store
- `/logout` - Disconnect your active store
- `/logoutall` - Disconnect all stores
- `/mystore` - List your stores & switch active store
- `/status` - Check which store you're connected to

*Generating pages:*
`/prompt Your description here`

*Examples:*
- `/prompt Create a landing page for a pizza restaurant`
- `/prompt Build an online store for shoes`
- `/prompt Make a blog page about travel`
- `/prompt Create a contact page with form and map`

*Tips for better results:*
- Be specific about sections you want (hero, features, testimonials, etc.)
- Mention the industry or business type
- Describe the style or mood (modern, minimal, colorful, etc.)

*Multi-store support:*
- Connect multiple stores with `/login` or `/newstore`
- Switch between them: `switch:storename`
- See all stores: `/mystore`

*Commands:*
- `/start` - Welcome message
- `/help` - Show this help
- `/newstore` - Create a new Expozy storefront
- `/login` - Reconnect to your existing store
- `/logout` - Disconnect your active store
- `/logoutall` - Disconnect all stores
- `/mystore` - List & switch stores
- `/status` - Check connection status
- `/prompt <text>` - Generate template"""

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