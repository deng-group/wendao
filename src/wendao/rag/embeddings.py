"""Text embedding engines for meaning-based search.

Two engines produce the same embeddings for the same model:

- `OnnxEncoder` (default): runs the model's ONNX export on the CPU with ONNX Runtime.
  Small install, no PyTorch needed.
- `TorchEncoder`: runs the model with sentence-transformers and PyTorch, on a GPU if one
  is available. Needs the optional `gpu` extra: `pip install "wendao[gpu]"`.

Both download the model from the Hugging Face Hub on first use and cache it there.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

import numpy as np

ENGINES = ("auto", "onnx", "torch")
DEVICES = ("auto", "cpu", "cuda", "mps")


class Encoder(Protocol):
    name: str

    def encode(self, texts: list[str], batch_size: int = 32) -> np.ndarray:
        """Return L2-normalized float32 embeddings, one row per text."""


def torch_available() -> bool:
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        return False
    return True


def make_encoder(model_name: str, engine: str = "auto", device: str = "auto", model_path: str | None = None) -> Encoder:
    """Pick the embedding engine. "auto" uses PyTorch if the `gpu` extra is installed, else ONNX.

    `model_path` is a local folder with the ONNX model files (as stored in a course pack);
    without it the model is downloaded from the Hugging Face Hub.
    """
    if engine not in ENGINES:
        raise ValueError(f"Unknown search engine `{engine}`; expected one of {', '.join(ENGINES)}.")
    if device not in DEVICES:
        raise ValueError(f"Unknown device `{device}`; expected one of {', '.join(DEVICES)}.")
    if engine == "torch" or (engine == "auto" and torch_available() and not model_path):
        return TorchEncoder(model_name, device)
    return OnnxEncoder(model_name, model_path)


class OnnxEncoder:
    """Sentence-transformers model run through its ONNX export (mean pooling + normalize)."""

    name = "onnx"

    def __init__(self, model_name: str, model_path: str | None = None):
        import onnxruntime
        from tokenizers import Tokenizer

        def fetch(filename: str) -> str:
            if model_path:
                local = Path(model_path) / filename
                if not local.exists():
                    raise FileNotFoundError(f"{local} is missing")
                return str(local)
            try:
                from huggingface_hub import hf_hub_download
                from huggingface_hub.utils import logging as hub_logging
            except ImportError as exc:
                raise RuntimeError("Downloading the search model needs huggingface-hub: pip install -U wendao") from exc
            hub_logging.set_verbosity_error()
            return download(hf_hub_download, filename)

        def download(hf_hub_download, filename: str) -> str:
            # Use the cached copy when there is one, so servers without internet keep working.
            try:
                return hf_hub_download(model_name, filename, local_files_only=True)
            except Exception:  # noqa: BLE001 - not cached yet
                return hf_hub_download(model_name, filename)

        try:
            onnx_file = fetch("onnx/model.onnx")
            tokenizer_file = fetch("tokenizer.json")
        except Exception as exc:  # noqa: BLE001 - surface any download problem plainly
            raise RuntimeError(
                f"Could not download the ONNX version of `{model_name}` ({exc}). "
                'Pick a model that ships onnx/model.onnx, or install "wendao[gpu]" to use PyTorch.'
            ) from exc
        max_length = 256
        try:
            config = json.loads(open(fetch("sentence_bert_config.json"), encoding="utf-8").read())
            max_length = int(config.get("max_seq_length", max_length))
        except Exception:  # noqa: BLE001 - optional file; keep the default length
            pass

        self.tokenizer = Tokenizer.from_file(tokenizer_file)
        self.tokenizer.enable_truncation(max_length=max_length)
        self.tokenizer.enable_padding()
        options = onnxruntime.SessionOptions()
        options.log_severity_level = 3
        self.session = onnxruntime.InferenceSession(onnx_file, options, providers=["CPUExecutionProvider"])
        self.input_names = {item.name for item in self.session.get_inputs()}

    def encode(self, texts: list[str], batch_size: int = 32) -> np.ndarray:
        rows = []
        for start in range(0, len(texts), batch_size):
            batch = self.tokenizer.encode_batch(texts[start : start + batch_size])
            ids = np.array([item.ids for item in batch], dtype=np.int64)
            mask = np.array([item.attention_mask for item in batch], dtype=np.int64)
            feeds = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self.input_names:
                feeds["token_type_ids"] = np.array([item.type_ids for item in batch], dtype=np.int64)
            hidden = self.session.run(None, feeds)[0]
            weights = mask[:, :, None].astype(np.float32)
            pooled = (hidden * weights).sum(axis=1) / np.clip(weights.sum(axis=1), 1e-9, None)
            rows.append(pooled / np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12, None))
        if not rows:
            return np.zeros((0, 0), dtype=np.float32)
        return np.vstack(rows).astype(np.float32)


class TorchEncoder:
    """Sentence-transformers with PyTorch; uses the GPU when available."""

    name = "torch"

    def __init__(self, model_name: str, device: str = "auto"):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError('The PyTorch search engine needs the gpu extra: pip install "wendao[gpu]"') from exc
        self.model = SentenceTransformer(model_name, device=None if device == "auto" else device)
        self.device = str(self.model.device)

    def encode(self, texts: list[str], batch_size: int = 32) -> np.ndarray:
        embeddings = self.model.encode(texts, normalize_embeddings=True, batch_size=batch_size, show_progress_bar=len(texts) > 256)
        return np.asarray(embeddings, dtype=np.float32)
