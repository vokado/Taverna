from __future__ import annotations

import asyncio
import json
from pathlib import PurePosixPath
from uuid import UUID, uuid4

import httpx

from .core import AppError
from .llm import LOOPBACK_HTTP_CONTEXT, local_endpoint
from .media_probe import MAX_MEDIA_BYTES, probe_media

NODES = ("CheckpointLoaderSimple", "CLIPTextEncode", "EmptyLatentImage", "KSampler", "VAEDecode", "SaveImage")
TERMINAL_IMAGE = {"completed", "failed", "cancelled"}


class ComfyAdapter:
    """Built-in text-to-image nodes and modern per-UUID cancellation; no global interrupt."""

    def __init__(self, endpoint, *, transport=None):
        self.endpoint = local_endpoint(endpoint)
        self.transport = transport

    def client(self):
        return httpx.AsyncClient(
            base_url=self.endpoint,
            verify=LOOPBACK_HTTP_CONTEXT,
            transport=self.transport,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(10, connect=3),
        )

    async def request(self, method, path, *, body=None, params=None, allow_missing=False, binary=False):
        try:
            async with self.client() as client:
                async with client.stream(method, path, json=body, params=params) as response:
                    if allow_missing and response.status_code == 404:
                        return None
                    raw = bytearray()
                    limit = MAX_MEDIA_BYTES if binary else 2 * 1024 * 1024
                    async for chunk in response.aiter_bytes():
                        if len(raw) + len(chunk) > limit:
                            raise AppError(
                                "image_response_too_large",
                                "Ответ генератора превышает безопасный предел.",
                                413,
                            )
                        raw.extend(chunk)
                    if response.status_code == 404:
                        raise AppError(
                            "image_capability_missing",
                            "Движок не поддерживает требуемый современный API задач/отмены.",
                            409,
                        )
                    if response.status_code >= 300:
                        # Do not surface arbitrary HTML/tracebacks/secrets from the external service.
                        raise AppError(
                            "image_engine_rejected",
                            "Генератор отклонил запрос. Проверьте движок и доступные параметры.",
                            502,
                            http_status=response.status_code,
                        )
                    if binary:
                        return bytes(raw)
                    value = json.loads(raw)
                    if not isinstance(value, dict):
                        raise ValueError("object response required")
                    return value
        except AppError:
            raise
        except httpx.TimeoutException as exc:
            raise AppError(
                "image_engine_timeout",
                "Генератор не ответил вовремя; состояние отправленной задачи может быть неизвестно.",
                504,
            ) from exc
        except httpx.HTTPError as exc:
            raise AppError(
                "image_engine_offline", "Локальный генератор недоступен. Проверьте его запуск и адрес.", 503
            ) from exc
        except (ValueError, TypeError, KeyError) as exc:
            raise AppError(
                "image_protocol", "Ответ генератора не соответствует ожидаемому API.", 502
            ) from exc

    async def capabilities(self):
        names = await asyncio.gather(*(self.request("GET", "/object_info/" + node) for node in NODES))
        stats, jobs = await asyncio.gather(
            self.request("GET", "/system_stats"), self.request("GET", "/api/jobs", params={"limit": 1})
        )
        cancellation_probe = await self.cancel(str(uuid4()))
        if cancellation_probe["cancelled"]:
            raise AppError(
                "image_capability_missing",
                "Движок некорректно обработал отсутствующий UUID проверки отмены.",
                409,
            )
        try:
            nodes = dict(zip(NODES, names, strict=True))
            for name in NODES:
                if not isinstance(nodes[name].get(name), dict):
                    raise ValueError("missing node")

            def choices(node, key):
                items = nodes[node][node]["input"]["required"][key][0]
                if (
                    not isinstance(items, list)
                    or len(items) > 10000
                    or any(not isinstance(v, str) or not v or len(v) > 500 for v in items)
                ):
                    raise ValueError("invalid enum")
                return items

            checkpoints = choices("CheckpointLoaderSimple", "ckpt_name")
            samplers = choices("KSampler", "sampler_name")
            schedulers = choices("KSampler", "scheduler")
            # The modern job list has a pagination object and a jobs array.
            if not isinstance(jobs.get("jobs"), list):
                raise ValueError("modern jobs API absent")
            devices = []
            for item in stats.get("devices", []):
                if not isinstance(item, dict):
                    raise ValueError("device")
                device = {}
                for key in ("name", "type"):
                    if key in item:
                        if not isinstance(item[key], str) or len(item[key]) > 1000:
                            raise ValueError("invalid device label")
                        device[key] = item[key]
                for key in ("vram_total", "vram_free"):
                    if key in item:
                        if type(item[key]) is not int or not 0 <= item[key] <= 2**63 - 1:
                            raise ValueError("invalid memory statistic")
                        device[key] = item[key]
                devices.append(device)
            version = stats.get("system", {}).get("comfyui_version")
            return {
                "adapter": "comfyui-modern-v1",
                "checkpoints": checkpoints,
                "samplers": samplers,
                "schedulers": schedulers,
                "devices": devices,
                "scoped_cancel": True,
                "engine_version": str(version)[:100] if isinstance(version, (str, int)) else None,
                "hardware_verified": False,
            }
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise AppError(
                "image_capability_missing",
                "Движок не предоставляет требуемые узлы и современный API задач.",
                409,
            ) from exc

    @staticmethod
    def graph(request, capabilities, prompt_id):
        if str(UUID(prompt_id)) != prompt_id:
            raise AppError("image_protocol", "UUID изображения должен иметь канонический формат.", 422)
        checkpoint = request["checkpoint"]
        parts = checkpoint.replace("\\", "/").split("/")
        if (
            checkpoint not in capabilities["checkpoints"]
            or any(p in ("", ".", "..") for p in parts)
            or ":" in checkpoint
            or "\x00" in checkpoint
        ):
            raise AppError(
                "image_checkpoint_missing",
                "Выберите существующий локальный checkpoint из списка движка.",
                422,
            )
        if (
            request["sampler"] not in capabilities["samplers"]
            or request["scheduler"] not in capabilities["schedulers"]
        ):
            raise AppError(
                "image_parameter_missing", "Движок не поддерживает выбранный sampler/scheduler.", 422
            )
        return {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"text": request["prompt"], "clip": ["1", 1]}},
            "3": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": request["negative_prompt"], "clip": ["1", 1]},
            },
            "4": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": request["width"], "height": request["height"], "batch_size": 1},
            },
            "5": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": request["seed"],
                    "steps": request["steps"],
                    "cfg": request["cfg"],
                    "sampler_name": request["sampler"],
                    "scheduler": request["scheduler"],
                    "denoise": 1.0,
                    "model": ["1", 0],
                    "positive": ["2", 0],
                    "negative": ["3", 0],
                    "latent_image": ["4", 0],
                },
            },
            "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
            "7": {
                "class_type": "SaveImage",
                "inputs": {"images": ["6", 0], "filename_prefix": "Taverna/" + prompt_id},
            },
        }

    async def submit(self, prompt_id, graph):
        UUID(prompt_id)
        response = await self.request(
            "POST",
            "/prompt",
            body={"prompt_id": prompt_id, "client_id": "taverna-" + prompt_id, "prompt": graph},
        )
        if response.get("prompt_id") != prompt_id:
            raise AppError(
                "image_protocol",
                "Движок не сохранил переданный UUID задачи. Требуется проверка внешнего результата.",
                502,
            )
        return response

    async def job(self, prompt_id):
        UUID(prompt_id)
        result = await self.request("GET", "/api/jobs/" + prompt_id, allow_missing=True)
        if result is not None and (
            result.get("id") != prompt_id
            or result.get("status") not in {"pending", "in_progress", *TERMINAL_IMAGE}
        ):
            raise AppError(
                "image_protocol", "Движок вернул чужой UUID или неизвестное состояние задачи.", 502
            )
        return result

    async def cancel(self, prompt_id):
        UUID(prompt_id)
        result = await self.request("POST", "/api/jobs/" + prompt_id + "/cancel", body={})
        if type(result.get("cancelled")) is not bool:
            raise AppError("image_protocol", "Движок не подтвердил адресную обработку отмены.", 502)
        return result

    async def output(self, prompt_id):
        history = await self.request("GET", "/history/" + prompt_id)
        try:
            item = history[prompt_id]["outputs"]["7"]["images"][0]
            filename, subfolder, kind = (
                item["filename"],
                item.get("subfolder", ""),
                item.get("type", "output"),
            )
            if kind != "output" or not isinstance(filename, str) or not isinstance(subfolder, str):
                raise ValueError("output type")
            if (
                not filename
                or len(filename) > 300
                or len(subfolder) > 500
                or any(c in filename + subfolder for c in ("\\", "\x00", ":"))
                or "/" in filename
            ):
                raise ValueError("unsafe output")
            if (
                subfolder != "Taverna"
                or not filename.startswith(prompt_id + "_")
                or not filename.endswith(".png")
            ):
                raise ValueError("foreign output prefix")
            if subfolder.startswith("/") or any(p in (".", "..") for p in PurePosixPath(subfolder).parts):
                raise ValueError("unsafe folder")
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            raise AppError(
                "image_output_invalid",
                "Движок не предоставил корректный файл собственного выходного узла.",
                502,
            ) from exc
        raw = await self.request(
            "GET", "/view", params={"filename": filename, "subfolder": subfolder, "type": kind}, binary=True
        )
        info = probe_media(raw)
        if not info["type"].startswith("image/") or info["frames"] != 1:
            raise AppError(
                "image_output_invalid", "Генератор вернул не одиночное поддерживаемое изображение.", 502
            )
        return raw, info
