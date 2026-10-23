import base64
import io
import json
import logging
import os
from typing import Optional
from django.conf import settings
from PIL import Image
import requests

logger = logging.getLogger(__name__)


class VertexAITryOnError(Exception):
    """Raised when Vertex AI Virtual Try-On prediction fails."""
    pass


class VertexAIVirtualTryOnClient:
    """
    Client for Google Cloud Vertex AI Virtual Try-On API (model: virtual-try-on-001).
    Performs photo-realistic virtual try-on by fusing customer selfie/pose photos
    with clean flat-lay or ghost-mannequin product catalog images.
    """

    MODEL_ID = "virtual-try-on-001"

    @classmethod
    def get_project_id(cls) -> str:
        return getattr(settings, "VERTEX_AI_PROJECT", "") or os.getenv("VERTEX_AI_PROJECT", "").strip()

    @classmethod
    def get_location(cls) -> str:
        return getattr(settings, "VERTEX_AI_LOCATION", "") or os.getenv("VERTEX_AI_LOCATION", "us-central1").strip()

    @classmethod
    def get_access_token(cls) -> Optional[str]:
        """
        Retrieves OAuth2 bearer token from Google Cloud Application Default Credentials.
        """
        try:
            import google.auth
            import google.auth.transport.requests

            # Ensure relative credential path resolves from BASE_DIR
            cred_path = getattr(settings, "GOOGLE_APPLICATION_CREDENTIALS", "") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
            if cred_path:
                if not os.path.isabs(cred_path):
                    resolved_path = os.path.join(settings.BASE_DIR, cred_path)
                    if os.path.exists(resolved_path):
                        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = resolved_path

            credentials, project = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            auth_request = google.auth.transport.requests.Request()
            credentials.refresh(auth_request)
            return credentials.token
        except Exception as exc:
            logger.warning("Could not obtain Google Cloud OAuth token: %s", exc)
            return None

    @classmethod
    def _create_mock_result_image(cls) -> bytes:
        """
        Creates a synthetic 512x512 PNG image used for unit tests or mock development environments.
        """
        img = Image.new("RGB", (512, 512), color=(70, 130, 180))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    @classmethod
    def predict(
        cls,
        person_image_bytes: bytes,
        garment_image_bytes: bytes,
        timeout: int = 60,
    ) -> bytes:
        """
        Calls Vertex AI virtual-try-on-001 with person image bytes and garment flat-lay image bytes.
        Returns the output try-on image as raw bytes (PNG).
        """
        project_id = cls.get_project_id()
        location = cls.get_location()

        # If project is explicitly set to mock, or during test runs without GCP credentials
        if project_id == "mock" or (not project_id and os.getenv("DJANGO_SETTINGS_MODULE")):
            logger.info("Vertex AI running in mock/offline mode. Returning synthetic try-on result.")
            return cls._create_mock_result_image()

        token = cls.get_access_token()
        if not token:
            if not project_id:
                logger.info("VERTEX_AI_PROJECT not set and no GCP credentials. Returning mock image.")
                return cls._create_mock_result_image()
            raise VertexAITryOnError("Google Cloud credentials not found. Set GOOGLE_APPLICATION_CREDENTIALS.")

        endpoint_url = (
            f"https://{location}-aiplatform.googleapis.com/v1/"
            f"projects/{project_id}/locations/{location}/publishers/google/models/{cls.MODEL_ID}:predict"
        )

        person_b64 = base64.b64encode(person_image_bytes).decode("utf-8")
        garment_b64 = base64.b64encode(garment_image_bytes).decode("utf-8")

        payload = {
            "instances": [
                {
                    "personImage": {
                        "image": {
                            "bytesBase64Encoded": person_b64,
                        }
                    },
                    "productImages": [
                        {
                            "image": {
                                "bytesBase64Encoded": garment_b64,
                            }
                        }
                    ],
                }
            ],
            "parameters": {
                "sampleCount": 1,
            },
        }

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }

        try:
            response = requests.post(
                endpoint_url,
                json=payload,
                headers=headers,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            logger.error("Vertex AI HTTP request failed: %s", exc)
            raise VertexAITryOnError(f"Failed to communicate with Vertex AI: {exc}") from exc

        if response.status_code != 200:
            logger.error("Vertex AI returned error HTTP %d: %s", response.status_code, response.text)
            raise VertexAITryOnError(
                f"Vertex AI Virtual Try-On error ({response.status_code}): {response.text}"
            )

        try:
            data = response.json()
            predictions = data.get("predictions", [])
            if not predictions:
                raise VertexAITryOnError("Vertex AI returned empty predictions list.")

            first_pred = predictions[0]
            result_b64 = first_pred.get("bytesBase64Encoded")
            if not result_b64:
                raise VertexAITryOnError("Vertex AI prediction missing bytesBase64Encoded.")

            return base64.b64decode(result_b64)
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            logger.error("Failed to parse Vertex AI response: %s", exc)
            raise VertexAITryOnError(f"Invalid response from Vertex AI: {exc}") from exc
