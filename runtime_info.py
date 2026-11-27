"""Non-secret runtime provenance; audit keeps the exact stdout receipt."""
import subprocess
import time


def receipt(parser,rules,at=None):
    try:
        sha=subprocess.check_output(['git','rev-parse','HEAD'],text=True,stderr=subprocess.DEVNULL,timeout=.5).strip()
    except (OSError,subprocess.SubprocessError):sha='unknown'
    return {'sha':sha,'parser':parser,'rules':rules,'at':time.time() if at is None else at,
            'log_line':f'[runtime] commit={sha} PARSER_VERSION={parser} RULES_VERSION={rules}'}
