import base64
import json
import logging
import mimetypes
import os
import requests
from typing import Any, Optional

logger = logging.getLogger(__name__)

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"


class GeminiVisionService:
    """
    Service client for Gemini Flash Vision API.
    Inspects product catalog images to classify clothing categories,
    presentation style (flat-lay vs on-model), angle/view (front/back/side),
    and background cleanliness.
    """

    PROMPT = """Analyze this e-commerce image for an apparel Virtual Try-On catalog.
Classify the clothing item and return a JSON object with the following fields:
- "is_valid_clothing": boolean (true if the image depicts an apparel/fashion product, false if blank, meme, invoice, text, or non-clothing object)
- "detected_category": string, one of ["TOP", "BOTTOM", "ONE_PIECE", "OUTERWEAR", "OTHER"]
- "presentation_type": string, one of ["FLAT_LAY", "MANNEQUIN", "ON_MODEL", "IRRELEVANT"] (FLAT_LAY is laid flat on a surface; MANNEQUIN is on a mannequin/ghost mannequin; ON_MODEL is worn by a human model)
- "detected_view": string, one of ["FRONT", "BACK", "SIDE", "UNKNOWN"] (the camera angle/view of the garment)
- "has_neutral_background": boolean (true if white, grey, neutral studio, or transparent; false if cluttered room, outdoor street, busy background)
- "confidence": float between 0.0 and 1.0

Return ONLY the raw JSON object adhering to this schema."""

    @classmethod
    def get_api_key(cls) -> str:
        return os.getenv("GEMINI_API_KEY", "").strip()

    @classmethod
    def inspect_clothing_image(
        cls,
        image_bytes: bytes,
        filename: str = "image.jpg",
    ) -> dict[str, Any]:
        """
        Submits an image to Gemini 1.5 Flash Vision and returns parsed classification data.
        """
        api_key = cls.get_api_key()
        if not api_key:
            logger.warning("GEMINI_API_KEY is not set. Returning uninspected fallback.")
            return {
                "is_valid_clothing": False,
                "detected_category": "UNKNOWN",
                "presentation_type": "IRRELEVANT",
                "detected_view": "UNKNOWN",
                "has_neutral_background": False,
                "confidence": 0.0,
                "error": "GEMINI_API_KEY_NOT_CONFIGURED",
            }

        mime_type, _ = mimetypes.guess_type(filename)
        if not mime_type or not mime_type.startswith("image/"):
            mime_type = "image/jpeg"

        b64_image = base64.b64encode(image_bytes).decode("utf-8")

        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": cls.PROMPT},
                        {
                            "inline_data": {
                                "mime_type": mime_type,
                                "data": b64_image,
                            }
                        },
                    ]
                }
            ],
            "generationConfig": {
                "response_mime_type": "application/json",
                "temperature": 0.1,
            },
        }

        try:
            response = requests.post(
                f"{GEMINI_API_URL}?key={api_key}",
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=15,
            )

            if response.status_code != 200:
                logger.error(
                    f"Gemini API returned HTTP {response.status_code}: {response.text}"
                )
                return {
                    "is_valid_clothing": False,
                    "detected_category": "UNKNOWN",
                    "presentation_type": "IRRELEVANT",
                    "detected_view": "UNKNOWN",
                    "has_neutral_background": False,
                    "confidence": 0.0,
                    "error": f"API_ERROR_{response.status_code}",
                }

            data = response.json()
            candidates = data.get("candidates", [])
            if not candidates:
                return {
                    "is_valid_clothing": False,
                    "detected_category": "UNKNOWN",
                    "presentation_type": "IRRELEVANT",
                    "detected_view": "UNKNOWN",
                    "has_neutral_background": False,
                    "confidence": 0.0,
                    "error": "NO_CANDIDATES",
                }

            text_content = (
                candidates[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
            )
            parsed = json.loads(text_content.strip())
            return {
                "is_valid_clothing": bool(parsed.get("is_valid_clothing")),
                "detected_category": str(parsed.get("detected_category", "UNKNOWN")).upper(),
                "presentation_type": str(parsed.get("presentation_type", "IRRELEVANT")).upper(),
                "detected_view": str(parsed.get("detected_view", "UNKNOWN")).upper(),
                "has_neutral_background": bool(parsed.get("has_neutral_background")),
                "confidence": float(parsed.get("confidence", 0.0)),
            }

        except (requests.RequestException, json.JSONDecodeError, KeyError, IndexError) as exc:
            logger.error(f"Failed to inspect clothing image with Gemini: {exc}")
            return {
                "is_valid_clothing": False,
                "detected_category": "UNKNOWN",
                "presentation_type": "IRRELEVANT",
                "detected_view": "UNKNOWN",
                "has_neutral_background": False,
                "confidence": 0.0,
                "error": str(exc),
            }
