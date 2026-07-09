from __future__ import annotations

import os
import threading
from typing import Optional

from .base import LLMEngine


class HuggingFaceEngine(LLMEngine):
    def __init__(self, model_id: str):
        self.model_id = model_id
        self._lock = threading.Lock()
        self._model = None
        self._tokenizer = None

    def load(self) -> None:
        if self._model is not None:
            return

        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self._tokenizer = AutoTokenizer.from_pretrained(self.model_id, use_fast=True)

        if self._tokenizer.pad_token is None and self._tokenizer.eos_token is not None:
            self._tokenizer.pad_token = self._tokenizer.eos_token

        self._model = AutoModelForCausalLM.from_pretrained(
            self.model_id,
            torch_dtype=torch.float16,
            device_map="auto",
        )
        self._model.eval()

    def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_new_tokens: int = 256,
        temperature: float = 0.7,
    ) -> str:
        if self._model is None:
            self.load()

        import torch

        full_prompt = prompt if not system else f"{system}\n\n{prompt}"

        with self._lock:
            inputs = self._tokenizer(full_prompt, return_tensors="pt")
            inputs = {k: v.to(self._model.device) for k, v in inputs.items()}

            with torch.no_grad():
                output_ids = self._model.generate(
                    **inputs,
                    max_new_tokens=max_new_tokens,
                    do_sample=temperature > 0,
                    temperature=temperature,
                    pad_token_id=self._tokenizer.pad_token_id,
                    eos_token_id=self._tokenizer.eos_token_id,
                )

        generated = output_ids[0][inputs["input_ids"].shape[-1]:]
        return self._tokenizer.decode(generated, skip_special_tokens=True).strip()
