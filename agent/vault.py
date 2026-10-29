"""Secrets in the Windows vault (Credential Manager), never in a file.

Each secret is stored under orchestra/mail-adagio/<name>, as Orchestra asks of its agents:
- gmail_mot_de_passe_application: the Gmail app password (16 letters), never the Google password;
- cle_api_modele: the API key of a cloud model (Anthropic, OpenRouter), used outside Orchestra only.
Only the functions below touch the vault. Library: keyring (MIT).
"""
AGENT_ID = "mail-adagio"
GMAIL = "gmail_mot_de_passe_application"
API_KEY = "cle_api_modele"
NAMES = (GMAIL, API_KEY)


def service(name):
    return f"orchestra/{AGENT_ID}/{name}"


def secret_read(name, account):
    """The secret saved under orchestra/mail-adagio/<name> for this account, "" if none (or no vault)."""
    if name not in NAMES or not account:
        return ""
    try:
        import keyring
        found = keyring.get_password(service(name), str(account))
        if not found:  # saved by Orchestra under another account name: the one secret of this service
            cred = keyring.get_credential(service(name), None)
            found = cred.password if cred else ""
        return found or ""
    except Exception:  # keyring missing, or no vault on this system
        return ""


def secret_save(name, account, value):
    """Save a secret in the vault. True if saved; False if there is no vault here (keyring missing)."""
    if name not in NAMES or not account or not value:
        return False
    try:
        import keyring
        keyring.set_password(service(name), str(account), str(value))
        return True
    except Exception:
        return False
