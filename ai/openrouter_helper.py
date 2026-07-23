# ai/openrouter_helper.py
# Author         : SWATH Agent
# Responsibility : Shared OpenRouter API client for all AI features.
#                  Both methodology_engine.py and report_generator.py
#                  use this helper instead of any local AI SDK.
# ------------------------------------------------------------

import os
import json
import requests
from loguru import logger


class OpenRouterHelper:
    """
    Thin wrapper around the OpenRouter API (/api/v1/chat/completions).

    Usage:
        helper = OpenRouterHelper()
        text = helper.generate("Write a haiku about recon")
    """

    def __init__(
        self,
        api_url: str = None,
        model: str = None,
        timeout: int = 60,
    ):
        self.api_url = (
            api_url
            or os.environ.get("OPENROUTER_API_URL", "https://openrouter.ai/api/v1/chat/completions")
        )
        self.model = (
            model
            or os.environ.get("OPENROUTER_MODEL", "google/gemini-2.5-flash")
        )
        self.api_key = os.environ.get("OPENROUTER_API_KEY")
        self.timeout = timeout

    # ----------------------------------------------------------------
    # Public API
    # ----------------------------------------------------------------

    def generate(self, prompt: str, system: str = None) -> str:
        """
        Send a prompt to OpenRouter and return the full generated text.

        Args:
            prompt:  The user/task prompt.
            system:  Optional system-level instruction.

        Returns:
            The model's complete text response.

        Raises:
            RuntimeError — if the API key is missing or an error payload is returned.
        """
        if not self.api_key:
            msg = "OPENROUTER_API_KEY environment variable is not set. Please set it to your OpenRouter API key."
            logger.error(msg)
            raise RuntimeError(msg)

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": 4096,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://github.com/SWATH/SWATH", # Optional but recommended by OpenRouter
            "X-Title": "SWATH Recon Framework",
            "Content-Type": "application/json"
        }

        logger.info(
            f"OpenRouter request → model={self.model}  "
            f"prompt_len={len(prompt)} chars"
        )

        try:
            resp = requests.post(
                self.api_url,
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )
        except requests.exceptions.RequestException as e:
            msg = f"Failed to connect to OpenRouter API: {e}"
            logger.error(msg)
            raise RuntimeError(msg)

        # ----------------------------------------------------------
        # Parse response
        # ----------------------------------------------------------
        if resp.status_code != 200:
            error_detail = resp.text[:500]
            msg = f"OpenRouter returned HTTP {resp.status_code}: {error_detail}"
            logger.error(msg)
            raise RuntimeError(msg)

        try:
            data = resp.json()
        except json.JSONDecodeError:
            msg = "OpenRouter returned non-JSON response. Is the endpoint correct?"
            logger.error(msg)
            raise RuntimeError(msg)

        if "error" in data:
             msg = f"OpenRouter API error: {data['error']}"
             logger.error(msg)
             raise RuntimeError(msg)

        choices = data.get("choices", [])
        if not choices:
            msg = "OpenRouter returned an empty response (no choices)."
            logger.error(msg)
            raise RuntimeError(msg)
            
        message = choices[0].get("message", {})
        response_text = message.get("content", "")

        logger.info(
            f"OpenRouter response received — {len(response_text)} chars"
        )

        return response_text

    # ----------------------------------------------------------------
    # Health check
    # ----------------------------------------------------------------

    def is_available(self) -> bool:
        """Quick connectivity check — just ensures API key is set."""
        return bool(self.api_key)
