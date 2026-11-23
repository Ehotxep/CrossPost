import hashlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from app.publish import uploads


class UploadHashes(unittest.TestCase):
    def test_digest_survives_hydration_and_is_removed_with_upload(self):
        data = io.BytesIO()
        Image.new("RGB", (3, 3), "blue").save(data, format="PNG")
        raw = data.getvalue()
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"CROSSPOST_DATA": folder}):
            info = uploads.save("same.png", raw)
            self.assertEqual(info["digest"], hashlib.sha256(raw).hexdigest())
            hydrated = uploads.describe(uploads.path_of(info["id"]))
            self.assertEqual(hydrated["digest"], info["digest"])
            sidecar = Path(folder) / "uploads" / f"{info['id']}.sha256"
            self.assertTrue(sidecar.exists())
            uploads.remove(info["id"])
            self.assertFalse(sidecar.exists())


if __name__ == "__main__":
    unittest.main()
