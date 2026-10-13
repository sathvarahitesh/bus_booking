# Copy this file to env.ps1, remove the leading # from the lines you need, then run:  . .\env.ps1
# env.ps1 is git-ignored. Never commit real passwords.

# --- Email. Leave unset in development: OTPs and emails print in this terminal ---
# $env:EMAIL_BACKEND       = "django.core.mail.backends.smtp.EmailBackend"
# $env:EMAIL_HOST          = "smtp.gmail.com"
# $env:EMAIL_PORT          = "587"
# $env:EMAIL_HOST_USER     = "yourname@gmail.com"
# $env:EMAIL_HOST_PASSWORD = "your-16-character-app-password"
# $env:DEFAULT_FROM_EMAIL  = "YatraBus <yourname@gmail.com>"

# --- Public address used in emails, SMS and QR codes ---
# $env:SITE_URL = "http://127.0.0.1:8000"

# --- SMS (optional): "console" = dev simulator, "twilio" = real delivery ---
# $env:SMS_PROVIDER       = "console"
# $env:TWILIO_ACCOUNT_SID = ""
# $env:TWILIO_AUTH_TOKEN  = ""
# $env:TWILIO_FROM_NUMBER = ""