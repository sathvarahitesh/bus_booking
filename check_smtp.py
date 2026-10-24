"""Step-by-step email (SMTP) check for the YatraBus project.

Run from the project folder:   python check_smtp.py
To also send a test message:   python check_smtp.py --send someone@example.com

It reads the same .env file as the project. The password is NEVER printed.
"""
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def load_env():
    env_file = BASE_DIR / '.env'
    if not env_file.exists():
        print(f'!! No .env file found at {env_file}')
        return
    for line in env_file.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class Hiding:
    """Prints the SMTP conversation but hides everything sent after AUTH, because it contains the password."""
    hidden = False

    def _print_debug(self, *args):
        text = ' '.join(str(a) for a in args)
        if text.startswith('send:'):
            if 'AUTH' in text.upper():
                self.hidden = True
            if self.hidden:
                text = 'send: (hidden, login data)'
        print('     ', text.rstrip())


class PlainSMTP(Hiding, smtplib.SMTP):
    pass


class SecureSMTP(Hiding, smtplib.SMTP_SSL):
    pass


def explain(step, error):
    text = str(error)
    if isinstance(error, smtplib.SMTPAuthenticationError):
        if error.smtp_code == 534 or 'pplication-specific' in text:
            return 'Gmail needs an APP PASSWORD here, not your normal Google password.'
        if error.smtp_code == 454:
            return 'Too many login attempts. Wait 15 minutes, then try again.'
        return ('Gmail rejected the username or app password. Check EMAIL_HOST_USER is the SAME account the app '
                'password was created for, and create a new app password if unsure.')
    if isinstance(error, smtplib.SMTPServerDisconnected):
        if step == 'login':
            return ('The server hung up while logging in. The connection and encryption were fine, so look at: '
                    '(1) antivirus "Mail Shield" / email scanning or a firewall, (2) a blocked or flagged Google '
                    'sign-in (check Gmail for a "Critical security alert"), (3) try EMAIL_PORT=465 with EMAIL_USE_SSL=True, '
                    '(4) try your phone hotspot.')
        return ('The server hung up right after connecting. This is almost always the network or antivirus '
                'blocking mail. Try your phone hotspot, or port 465 with EMAIL_USE_SSL=True.')
    if isinstance(error, ssl.SSLError):
        return 'The encrypted connection failed. An antivirus or proxy is probably intercepting mail. Try port 465.'
    if isinstance(error, (TimeoutError, OSError)):
        return 'Could not reach the mail server. Your Wi-Fi or firewall blocks this port. Try your phone hotspot.'
    return 'Unexpected problem. Copy this whole output (it contains no password) when asking for help.'


def run(step, title, action):
    print(f'\n== {title}')
    try:
        result = action()
    except Exception as error:
        print(f'   FAILED: {type(error).__name__}: {error}')
        print(f'   WHY:    {explain(step, error)}')
        sys.exit(1)
    print('   ok')
    return result


def main():
    load_env()
    host = os.environ.get('EMAIL_HOST', 'smtp.gmail.com')
    use_ssl = os.environ.get('EMAIL_USE_SSL', 'False') == 'True'
    port = int(os.environ.get('EMAIL_PORT', '465' if use_ssl else '587'))
    user = os.environ.get('EMAIL_HOST_USER', '')
    password = os.environ.get('EMAIL_HOST_PASSWORD', '')
    backend = os.environ.get('EMAIL_BACKEND', '(not set: console mode)')
    recipient = sys.argv[sys.argv.index('--send') + 1] if '--send' in sys.argv else None

    print('Settings read from .env (password hidden)')
    print(f'   backend  : {backend}')
    print(f'   server   : {host}:{port}  ({"SSL" if use_ssl else "STARTTLS"})')
    print(f'   username : {user or "(empty)"}')
    print(f'   password : {len(password)} characters')

    notes = []
    if 'smtp' not in backend:
        notes.append('EMAIL_BACKEND is not the SMTP backend, so Django prints emails instead of sending them.')
    if not user or not password:
        notes.append('EMAIL_HOST_USER or EMAIL_HOST_PASSWORD is empty.')
    if password and password != password.strip() or ' ' in password:
        notes.append('The password contains a space. Remove all spaces.')
    if password and not password.isascii():
        notes.append('The password contains a strange character. Retype it by hand.')
    if host.endswith('gmail.com') and password and not (len(password) == 16 and password.isalpha() and password.islower()):
        notes.append('A Gmail app password is exactly 16 lowercase letters. Yours looks different.')
    for note in notes:
        print(f'   !! {note}')
    if not user or not password:
        sys.exit(1)

    smtp = (SecureSMTP if use_ssl else PlainSMTP)(timeout=20)
    smtp.set_debuglevel(1)
    run('connect', '1. Connecting to the mail server', lambda: smtp.connect(host, port))
    run('hello', '2. Saying hello (EHLO)', smtp.ehlo)
    if not use_ssl:
        if smtp.has_extn('starttls'):
            run('tls', '3. Switching to an encrypted connection (STARTTLS)', smtp.starttls)
            run('hello', '3b. Hello again over the encrypted connection', smtp.ehlo)
        else:
            print('\n== 3. This server offers no STARTTLS (only acceptable for local tests)')
    run('login', '4. Logging in', lambda: smtp.login(user, password))
    smtp.hidden = False  # login is over: later lines contain no password

    if recipient:
        message = EmailMessage()
        message['From'] = os.environ.get('DEFAULT_FROM_EMAIL', user)
        message['To'] = recipient
        message['Subject'] = 'YatraBus SMTP check'
        message.set_content('Your email settings work.')
        run('send', f'5. Sending a test message to {recipient}', lambda: smtp.send_message(message))
    try:
        smtp.quit()
    except Exception:
        pass
    print('\nRESULT: everything worked. Django can send email with these settings.')


if __name__ == '__main__':
    main()