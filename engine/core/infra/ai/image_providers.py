"""Image provider adapters and registry."""

from abc import ABC, abstractmethod
import io
import math
from typing import Any, Dict

import requests

from core.config import config
from core.infra.ai.llm_client import text_to_image_doubao, text_to_image_google


def seedream_request_size(size: str, model: str) -> str:
    """The largest size Seedream 5 accepts with the same shape: it rejects images over
    SEEDREAM_V5_MAX_PIXELS in area, such as the 3200x1800 and 2250x3000 cover presets."""
    width, height = map(int, size.lower().replace("×", "x").split("x"))
    if "seedream-5" not in model.lower() or width * height <= config.SEEDREAM_V5_MAX_PIXELS:
        return f"{width}x{height}"
    scale = math.sqrt(config.SEEDREAM_V5_MAX_PIXELS / (width * height))
    return f"{int(width * scale) // 16 * 16}x{int(height * scale) // 16 * 16}"


class ImageProvider(ABC):
    @abstractmethod
    def generate(self, *, prompt: str, size: str, model: str) -> Dict[str, Any]:
        """Generate an image and return normalized image data."""


class DoubaoImageProvider(ImageProvider):
    def generate(self, *, prompt: str, size: str, model: str) -> Dict[str, Any]:
        request = seedream_request_size(size, model)
        image_url = text_to_image_doubao(
            prompt=prompt,
            size=request,
            model=model,
        )
        if not image_url:
            raise ValueError("图像生成返回空URL")
        if request == size.lower().replace("×", "x"):
            return {"type": "url", "data": image_url}
        # A smaller request is scaled back to the size that was asked for.
        from PIL import Image
        response = requests.get(image_url, timeout=60)
        response.raise_for_status()
        width, height = map(int, size.lower().replace("×", "x").split("x"))
        image = Image.open(io.BytesIO(response.content)).convert("RGB").resize((width, height), Image.LANCZOS)
        output = io.BytesIO()
        image.save(output, format="PNG")
        return {"type": "bytes", "data": output.getvalue()}


class GoogleImageProvider(ImageProvider):
    def __init__(self, *, use_adc: bool = False):
        self.use_adc = use_adc

    def generate(self, *, prompt: str, size: str, model: str) -> Dict[str, Any]:
        return text_to_image_google(
            prompt=prompt,
            size=size,
            model=model,
            use_adc=self.use_adc,
        )


IMAGE_PROVIDERS: Dict[str, ImageProvider] = {
    "doubao": DoubaoImageProvider(),
    "google": GoogleImageProvider(),
    "google_adc": GoogleImageProvider(use_adc=True),
}
