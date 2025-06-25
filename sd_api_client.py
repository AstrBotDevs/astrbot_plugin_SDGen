import asyncio
import base64
import os
import aiohttp
import httpx # 导入 httpx 库
from astrbot.api.all import logger
from . import messages

TEMP_PATH = os.path.abspath("data/temp")

class SDAPIClient:
    def __init__(self, config: dict):
        self.config = config
        self.session = None
        os.makedirs(TEMP_PATH, exist_ok=True)

    async def ensure_session(self):
        """确保会话连接"""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(self.config.get("session_timeout_time", 120))
            )

    async def download_image_to_base64(self, image_url: str) -> str:
        """
        下载图片并将其转换为 Base64 编码字符串。
        如果下载失败，将抛出 httpx.RequestError 或其他异常。
        """
        try:
            async with httpx.AsyncClient(timeout=self.config.get("session_timeout_time", 120)) as client:
                response = await client.get(image_url)
                response.raise_for_status() # 检查 HTTP 错误
                image_bytes = response.content
                return base64.b64encode(image_bytes).decode("utf-8")
        except httpx.RequestError as e:
            logger.error(f"{messages.MSG_IMG2IMG_DOWNLOAD_FAIL_LOG}: {e}")
            raise # 重新抛出异常，让调用者处理
        except Exception as e:
            logger.error(f"下载图片时发生未知错误: {e}")
            raise # 重新抛出异常

    async def _fetch_webui_resource(self, resource_type: str) -> list:
        """从 WebUI API 获取指定类型的资源列表"""
        endpoint_map = {
            "model": "/sdapi/v1/sd-models",
            "embedding": "/sdapi/v1/embeddings",
            "lora": "/sdapi/v1/loras",
            "sampler": "/sdapi/v1/samplers",
            "upscaler": "/sdapi/v1/upscalers"
        }
        if resource_type not in endpoint_map:
            logger.error(f"{messages.MSG_INVALID_RESOURCE_TYPE}: {resource_type}")
            return []

        try:
            await self.ensure_session()
            async with self.session.get(f"{self.config['webui_url']}{endpoint_map[resource_type]}") as resp:
                if resp.status == 200:
                    resources = await resp.json()

                    # 按不同类型解析返回数据
                    if resource_type == "model":
                        resource_names = [r["model_name"] for r in resources if "model_name" in r]
                    elif resource_type == "embedding":
                        resource_names = list(resources.get('loaded', {}).keys())
                    elif resource_type == "lora":
                        resource_names = [r["name"] for r in resources if "name" in r]
                    elif resource_type == "sampler":
                        resource_names = [r["name"] for r in resources if "name" in r]
                    elif resource_type == "upscaler":
                        resource_names = [r["name"] for r in resources if "name" in r]
                    else:
                        resource_names = []

                    logger.debug(f"从 WebUI 获取到的{resource_type}资源: {resource_names}")
                    return resource_names
        except Exception as e:
            logger.error(f"{messages.MSG_GET_RESOURCE_FAIL.format(resource_type=resource_type, error=e)}")

        return []

    async def get_sd_model_list(self):
        return await self._fetch_webui_resource("model")

    async def get_embedding_list(self):
        return await self._fetch_webui_resource("embedding")

    async def get_lora_list(self):
        return await self._fetch_webui_resource("lora")

    async def get_sampler_list(self):
        """获取可用的采样器列表"""
        return await self._fetch_webui_resource("sampler")

    async def get_upscaler_list(self):
        """获取可用的上采样算法列表"""
        return await self._fetch_webui_resource("upscaler")

    async def get_schedulers_list(self) -> list:
        """获取可用的调度器列表"""
        try:
            await self.ensure_session()
            async with self.session.get(f"{self.config['webui_url']}/sdapi/v1/schedulers") as resp:
                if resp.status == 200:
                    schedulers = await resp.json()
                    scheduler_names = [s["name"] for s in schedulers if "name" in s]
                    logger.debug(f"从 WebUI 获取到的调度器资源: {scheduler_names}")
                    return scheduler_names
        except Exception as e:
            logger.error(f"{messages.MSG_SCHEDULER_LIST_FAIL.format(error=e)}")
        return []

    async def call_sd_api(self, endpoint: str, payload: dict) -> dict:
        """通用API调用函数"""
        await self.ensure_session()
        try:
            async with self.session.post(
                    f"{self.config['webui_url']}{endpoint}",
                    json=payload
            ) as resp:
                if resp.status != 200:
                    error = await resp.text()
                    raise ConnectionError(f"{messages.MSG_API_ERROR_DETAIL.format(status=resp.status, error=error)}")
                return await resp.json()
        except aiohttp.ClientError as e:
            raise ConnectionError(f"{messages.MSG_CONNECTION_FAIL}: {str(e)}")

    async def call_t2i_api(self, payload: dict) -> dict:
        """调用 Stable Diffusion 文生图 API"""
        return await self.call_sd_api("/sdapi/v1/txt2img", payload)

    async def call_i2i_api(self, payload: dict) -> dict:
        """调用 Stable Diffusion 图生图 API"""
        return await self.call_sd_api("/sdapi/v1/img2img", payload)

    async def apply_image_processing(self, image_origin: str) -> str:
        """统一处理高分辨率修复与超分辨率放大"""
        params = self.config["default_params"]
        upscale_factor = params.get("upscale_factor", "2")
        upscaler = params.get("upscaler", "未设置")

        payload = {
            "image": image_origin,
            "upscaling_resize": upscale_factor,
            "upscaler_1": upscaler,
            "resize_mode": 0,
            "show_extras_results": True,
            "upscaling_resize_w": 1,
            "upscaling_resize_h": 1,
            "upscaling_crop": False,
            "gfpgan_visibility": 0,
            "codeformer_visibility": 0,
            "codeformer_weight": 0,
            "extras_upscaler_2_visibility": 0
        }

        resp = await self.call_sd_api("/sdapi/v1/extra-single-image", payload)
        return resp["image"]

    async def set_model(self, model_name: str) -> bool:
        """设置图像生成模型"""
        try:
            async with self.session.post(
                    f"{self.config['webui_url']}/sdapi/v1/options",
                    json={"sd_model_checkpoint": model_name}
            ) as resp:
                if resp.status == 200:
                    logger.debug(f"{messages.MSG_MODEL_SET_SUCCESS_LOG.format(model_name=model_name)}")
                    return True
                else:
                    logger.error(f"{messages.MSG_MODEL_SET_FAIL_LOG.format(status=resp.status)}")
                    return False
        except Exception as e:
            logger.error(f"{messages.MSG_MODEL_SET_EXCEPTION}: {e}")
            return False

    async def check_webui_available(self) -> tuple[bool, str]:
        """服务状态检查"""
        try:
            await self.ensure_session()
            url = f"{self.config['webui_url']}/sdapi/v1/progress"
            async with self.session.get(url) as resp:
                if resp.status == 200:
                    return True, 0
                else:
                    logger.debug(f"{messages.MSG_WEBUI_RETURN_ERROR.format(status=resp.status)}")
                    return False, resp.status
        except Exception as e:
            logger.error(f"{messages.MSG_CHECK_WEBUI_FAIL_LOG.format(error=e)}")
            return False, 0
