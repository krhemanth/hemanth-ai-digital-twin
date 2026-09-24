"""Interactively renew the Gmail OAuth token used by the digital twin."""

from tools import gmail_service


if __name__ == "__main__":
    gmail_service(force_interactive=True)
    print("Gmail OAuth authorization completed successfully.")
