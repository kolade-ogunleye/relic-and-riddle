"""Durable, per-operator image upload prompts. A file is not an approved asset."""
import json
import threading
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from .credits import license_allowed
from .telegram_images import MAX_BYTES, add_image, topic_for

STEPS = ("file", "source", "licence", "licence_url", "author")
PROMPTS = {
    "file": "Send the image as a photo or image document. Documents preserve resolution.",
    "source": "Send the public source page URL for this image, or tap I took this photo if it is your own.",
    "licence": "Choose the image's licence below. It must match the source; use Cancel if rights are uncertain.",
    "licence_url": "Send the public URL that documents this image's licence.",
    "author": "Send the photographer/creator's name for attribution.",
}


# Your own photograph: you hold the copyright, so the licence you grant is documented by its deed.
OWN_WORK_LICENCES = {"CC0": "https://creativecommons.org/publicdomain/zero/1.0/",
                     "CC BY 4.0": "https://creativecommons.org/licenses/by/4.0/"}

_LOCK = threading.RLock()

def serialized(method):
    @wraps(method)
    def call(*args, **kwargs):
        with _LOCK:
            return method(*args, **kwargs)
    return call


class ImageUpload:
    def __init__(self, db, root, assets):
        self.db, self.root, self.assets = db, Path(root), Path(assets)

    def key(self, chat, user):
        return f"guided_image:{chat}:{user}"

    def load(self, chat, user):
        try:
            state = json.loads(self.db.get_setting(self.key(chat, user)) or "null")
            if state is not None and (not isinstance(state, dict) or state.get("stage") not in STEPS + ("complete",)):
                return None
            return state
        except (ValueError, TypeError):
            return None

    def save(self, chat, user, state):
        self.db.set_setting(self.key(chat, user), json.dumps(state))

    @serialized
    def begin(self, chat, user, ref, message_id):
        state = self.load(chat, user)
        if state:
            state["message_id"] = message_id
        else:
            topic = topic_for(self.db, ref)
            state = {"chat": chat, "user": user, "id": uuid4().hex, "topic": topic["id"], "title": topic["title"], "stage": "file", "message_id": message_id, "rights": {}}
        self.save(chat, user, state)
        return state

    @serialized
    def file(self, chat, user, content, expected_id):
        state = self.load(chat, user)
        if not state or state["id"] != expected_id or state["stage"] != "file":
            return None  # A late download cannot resurrect a cancelled upload.
        if len(content) > MAX_BYTES:
            raise ValueError("Image exceeds 20 MiB")
        from PIL import Image
        import io
        with Image.open(io.BytesIO(content)) as image:
            image.verify()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / (state["id"] + ".image")).write_bytes(content)
        state["stage"] = "source"
        self.save(chat, user, state)
        return state

    @serialized
    def own_work(self, chat, user, expected_id):
        """The operator states they took the photo: no public source page; the licence is theirs to grant."""
        state = self.load(chat, user)
        if not state or state["id"] != expected_id or state["stage"] != "source":
            raise ValueError("This upload button is stale; use the current prompt")
        state["rights"]["source"] = "Own work (photographed by the operator)"
        state["rights"]["own_work"] = True
        state["stage"] = "licence"
        self.save(chat, user, state)
        return state

    @serialized
    def answer(self, chat, user, value, expected_id=None, expected_stage=None):
        state = self.load(chat, user)
        if not state:
            raise ValueError("No upload is active; open /photos")
        if (expected_id and state["id"] != expected_id) or (expected_stage and state["stage"] != expected_stage):
            raise ValueError("This upload button is stale; use the current prompt")
        stage, value = state["stage"], value.strip()
        if stage == "file":
            raise ValueError(PROMPTS[stage])
        if stage in ("source", "licence_url") and (urlparse(value).scheme not in {"http", "https"} or not urlparse(value).netloc):
            raise ValueError("Send a public HTTP(S) URL, or Cancel")
        if stage == "licence" and not license_allowed(value):
            raise ValueError("Supported licences are PD, CC0 and CC BY (CC BY-SA when enabled); approval still requires human review")
        if stage == "licence" and state["rights"].get("own_work"):
            if value not in OWN_WORK_LICENCES:
                raise ValueError("For your own photo choose CC0 or CC BY 4.0")
            state["rights"]["licence"], state["rights"]["licence_url"] = value, OWN_WORK_LICENCES[value]
            state["stage"] = "author"
            self.save(chat, user, state)
            return state
        if not value or len(value) > 1500:
            raise ValueError("Enter a non-empty value of at most 1,500 characters")
        state["rights"][stage] = value
        index = STEPS.index(stage)
        if index + 1 < len(STEPS):
            state["stage"] = STEPS[index + 1]
            self.save(chat, user, state)
            return state
        content = (self.root / (state["id"] + ".image")).read_bytes()
        topic_id = add_image(self.db, self.assets, state["topic"], state["rights"], content, user)
        self.cancel(chat, user)
        return {"stage": "complete", "topic": topic_id, "message_id": state["message_id"]}

    @serialized
    def cancel(self, chat, user, expected_id=None):
        state = self.load(chat, user)
        if expected_id and (not state or state["id"] != expected_id):
            raise ValueError("This upload button is stale; use the current prompt")
        if state:
            (self.root / (state["id"] + ".image")).unlink(missing_ok=True)
        self.save(chat, user, None)
