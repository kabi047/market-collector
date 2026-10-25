"""Small wrapper around a private Hugging Face dataset repo (free, no card).
Used for: the daily access token (laptop -> cloud runner) and live data chunks (cloud runner -> you)."""
import logging
from pathlib import Path

from . import config as C

log = logging.getLogger("hf")


def enabled():
    return bool(C.HF_TOKEN and C.HF_REPO)


def _api():
    from huggingface_hub import HfApi
    return HfApi(token=C.HF_TOKEN)


def upload(local, path_in_repo, retries=4):
    import time
    for i in range(retries):
        try:
            _api().upload_file(path_or_fileobj=str(local), path_in_repo=path_in_repo, repo_id=C.HF_REPO,
                               repo_type="dataset", commit_message=f"add {path_in_repo}")
            return True
        except Exception as e:
            log.warning("upload %s failed (%s) attempt %d/%d", path_in_repo, e, i + 1, retries)
            time.sleep(5 * (i + 1))
    return False


def download(path_in_repo, dest):
    from huggingface_hub import hf_hub_download
    p = hf_hub_download(repo_id=C.HF_REPO, repo_type="dataset", filename=path_in_repo, token=C.HF_TOKEN,
                        force_download=True)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(Path(p).read_bytes())
    return dest


def push_token():
    from .kite_auth import TOKEN_FILE
    ok = upload(TOKEN_FILE, "state/access_token.json")
    log.info("access token %s to Hugging Face", "pushed" if ok else "NOT pushed")
    return ok


def pull_token():
    from .kite_auth import TOKEN_FILE
    return download("state/access_token.json", TOKEN_FILE)
