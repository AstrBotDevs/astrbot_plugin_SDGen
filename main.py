import base64
import asyncio
import re
import json
import os
from astrbot.api.all import register, Context, AstrBotConfig, Star, logger, llm_tool, command_group, Image
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.event.filter import EventMessageType
from astrbot.core.utils.session_waiter import session_waiter, SessionController
from astrbot.api.all import BaseMessageComponent, Image as MessageImage, Plain as MessageText

from .sd_api_client import SDAPIClient
from .sd_utils import SDUtils
from . import messages
from .local_tag_utils import LocalTagManager

PLUGIN_VERSION = "1.1.7"

@register("SDGen", "Maoer", "SDGen_Maoer", PLUGIN_VERSION)
class SDGenerator(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._validate_config()

        self.max_concurrent_tasks = config.get("max_concurrent_tasks", 10)
        self.task_semaphore = asyncio.Semaphore(self.max_concurrent_tasks)

        self.client = SDAPIClient(self.config)
        self.utils = SDUtils(self.config, self.context)

        self.local_tag_mgr = LocalTagManager(os.path.join(os.path.dirname(__file__), "local_tags.json"))

    def _validate_config(self):
        """配置验证"""
        self.config["webui_url"] = self.config["webui_url"].strip()
        if not self.config["webui_url"].startswith(("http://", "https://")):
            raise ValueError(messages.MSG_WEBUI_URL_ERROR)

        if self.config["webui_url"].endswith("/"):
            self.config["webui_url"] = self.config["webui_url"].rstrip("/")
            # 只有在实际修改了配置时才保存
            self.config.save_config()

    @llm_tool("generate_image")
    async def generate_image(self, event: AstrMessageEvent, prompt: str):
        """Generate images using Stable Diffusion based on the given prompt.
        This function should only be called when the prompt contains keywords like "generate," "draw," or "create."
        It should not be mistakenly used for image searching.

        Args:
            prompt (string): The prompt or description used for generating images.
        """
        try:
            async for result in self._generate_image_impl(event, prompt):
                yield result
        except Exception as e:
            yield event.plain_result(f"下载图片时发生未知错误: {e}")
            return

        if not image_data:
            yield event.plain_result(messages.MSG_IMG2IMG_NO_IMAGE)
            return

        async for result in self._handle_llm_tool_error(event, self._img2img_impl, event, image_data, prompt):
            yield result

    @filter.command("画")
    async def draw(self, event: AstrMessageEvent):
        """直接处理 .画 指令，规避 LLM 前置拦截，完整保留用户输入"""
        raw_msg = event.message_str
        prompt_str = raw_msg.lstrip(".／/画").strip()
        prompt_str = self._replace_local_tags(prompt_str)
        async for result in self._generate_image_impl(event, prompt_str):
            yield result

    @filter.command("图生图", alias={"i2i_draw"})
    async def img2img_draw(self, event: AstrMessageEvent):
        """直接处理 .图生图 指令，规避 LLM 前置拦截，完整保留用户输入"""
        image_data = None
        if event.message_obj and event.message_obj.message:
            for comp in event.message_obj.message:
                if isinstance(comp, MessageImage) and hasattr(comp, 'url') and comp.url:
                    try:
                        image_data = await self.client.download_image_to_base64(comp.url)
                        break
                    except httpx.RequestError as e:
                        yield event.plain_result(f"{messages.MSG_IMG2IMG_DOWNLOAD_FAIL}: {e}")
                        return
                    except Exception as e:
                        yield event.plain_result(f"下载图片时发生未知错误: {e}")
                        return
                else:
                    logger.warning("MessageImage 组件没有 url 属性或 url 为空")
                    yield event.plain_result(messages.MSG_IMG2IMG_NO_IMAGE_URL)
                    return
        
        if not image_data:
            yield event.plain_result(messages.MSG_IMG2IMG_NO_IMAGE)
            return

        raw_msg = event.message_str
        # 移除命令前缀和图片信息，只保留提示词
        prompt_str = re.sub(r"^\s*[.／/]?图生图\s*", "", raw_msg).strip()
        # 移除消息链中的图片部分，只保留文本
        if event.message_obj and event.message_obj.message:
            # 过滤掉图片组件，只保留文本组件
            # 确保只处理 MessageText 组件
            text_components = [comp.text for comp in event.message_obj.message if isinstance(comp, MessageText)]
            prompt_str = " ".join(text_components).strip()

        async for result in self._img2img_impl(event, image_data, prompt_str):
            yield result

    @command_group("sd")
    def sd(self):
        pass

    @sd.command("check")
    async def check(self, event: AstrMessageEvent):
        """服务状态检查"""
        try:
            webui_available, status = await self.client.check_webui_available()
            if webui_available:
                yield event.plain_result(f"{messages.MSG_CHECK_WEBUI_NORMAL} | 插件版本: {PLUGIN_VERSION}")
            else:
                yield event.plain_result(f"{messages.MSG_CHECK_WEBUI_FAIL} | 插件版本: {PLUGIN_VERSION}")
        except Exception as e:
            logger.error(f"{messages.MSG_CHECK_ERROR_LOG}: {e}")
            yield event.plain_result(f"{messages.MSG_CHECK_ERROR} | 插件版本: {PLUGIN_VERSION}")
    
    async def _generate_image_impl(self, event: AstrMessageEvent, prompt: str):
        """实际的图像生成逻辑，供 generate_image/draw 调用"""
        async with self.task_semaphore:
            try:
                # 检查webui可用性
                if not (await self.client.check_webui_available())[0]:
                    yield event.plain_result(messages.MSG_WEBUI_UNAVAILABLE)
                    return

                verbose = self.config["verbose"]
                if verbose:
                    yield event.plain_result(messages.MSG_GENERATING)

                # 始终启用 LLM 自动生成 prompt
                generated_prompt = await self.utils.generate_prompt_with_llm(prompt)
                logger.debug(f"LLM generated prompt: {generated_prompt}")
                positive_prompt = self.config.get("positive_prompt_global", "") + generated_prompt

                #输出正向提示词
                if self.config.get("enable_show_positive_prompt", False):
                    yield event.plain_result(f"{messages.MSG_POSITIVE_PROMPT_DISPLAY}: {positive_prompt}")

                # 生成图像
                payload = await self.utils.generate_payload(positive_prompt)
                response = await self.client.call_t2i_api(payload)
                if not response.get("images"):
                    raise ValueError(messages.MSG_API_RETURN_ERROR)

                images = response["images"]

                if len(images) == 1:
                    image_data = response["images"][0]
                    image_bytes = base64.b64decode(image_data)
                    image_b64 = base64.b64encode(image_bytes).decode("utf-8")

                    # 图像处理
                    if self.config.get("enable_upscale"):
                        if verbose:
                            yield event.plain_result(messages.MSG_PROCESSING_IMAGE)
                        image_b64 = await self.client.apply_image_processing(image_b64)

                    yield event.chain_result([Image.fromBase64(image_b64)])
                else:
                    chain = []

                    if self.config.get("enable_upscale") and verbose:
                        yield event.plain_result(messages.MSG_PROCESSING_IMAGE)

                    for image_data in images:
                        image_b64 = base64.b64decode(image_data)
                        image_b64 = base64.b64encode(image_b64).decode("utf-8")

                        # 图像处理
                        if self.config.get("enable_upscale"):
                            image_b64 = await self.client.apply_image_processing(image_b64)

                        # 添加到链对象
                        chain.append(Image.fromBase64(image_b64))

                    # 将链式结果发送给事件
                    yield event.chain_result(chain)

            except ValueError as e:
                logger.error(f"{messages.MSG_API_RETURN_ERROR_LOG}: {e}")
                yield event.plain_result(f"{messages.MSG_API_ERROR}\n{e}")
            except ConnectionError as e:
                logger.error(f"{messages.MSG_CONNECTION_FAIL_LOG}: {e}")
                yield event.plain_result(f"{messages.MSG_CONNECTION_ERROR}\n{e}")
            except TimeoutError as e:
                logger.error(f"{messages.MSG_TIMEOUT_ERROR_LOG}: {e}")
                yield event.plain_result(f"{messages.MSG_TIMEOUT_ERROR}\n{e}")
            except Exception as e:
                logger.error(f"{messages.MSG_OTHER_ERROR_LOG}: {e}")
                # 过滤掉包含 http/https 的报错内容
                err_str = str(e)
                if "http" in err_str or "https" in err_str:
                    err_str = messages.MSG_ERROR_API_HIDDEN
                yield event.plain_result(f"{messages.MSG_OTHER_ERROR}\n{err_str}")
            finally:
                pass

    async def _img2img_impl(self, event: AstrMessageEvent, image_data: str, prompt: str):
        """实际的图生图逻辑，供 img2img_command/img2img_draw 调用"""
        async with self.task_semaphore:
            try:
                # 检查webui可用性
                if not (await self.client.check_webui_available())[0]:
                    yield event.plain_result(messages.MSG_WEBUI_UNAVAILABLE)
                    return

                verbose = self.config["verbose"]
                if verbose:
                    yield event.plain_result(messages.MSG_IMG2IMG_GENERATING)

                # 获取图片尺寸
                image_bytes = base64.b64decode(image_data)
                with io.BytesIO(image_bytes) as f:
                    pil_image = PILImage.open(f)
                    original_width, original_height = pil_image.size
                
                # 提示分辨率自动调整
                if verbose:
                    closest_width, closest_height = self.utils._get_closest_resolution(original_width, original_height)
                    yield event.plain_result(messages.MSG_IMG2IMG_RESOLUTION_AUTO_SET.format(width=closest_width, height=closest_height))

                # 根据配置决定是否使用 LLM 生成提示词
                enable_img2img_generate_prompt = self.config.get("enable_img2img_generate_prompt", True)
                final_prompt = prompt # 这里的 prompt 是用户输入的原始提示词
                
                if enable_img2img_generate_prompt:
                    generated_prompt = await self.utils.generate_prompt_with_llm(prompt)
                    logger.debug(f"LLM generated img2img prompt: {generated_prompt}")
                    if generated_prompt: # 如果 LLM 成功生成了提示词
                        final_prompt = self.config.get("positive_prompt_global", "") + generated_prompt
                    else: # 如果 LLM 没有生成有效提示词，回退到使用用户原始提示词
                        logger.warning("LLM 未能为图生图生成有效提示词，将使用用户原始提示词。")
                        final_prompt = self.config.get("positive_prompt_global", "") + prompt
                else:
                    final_prompt = self.config.get("positive_prompt_global", "") + prompt

                # 生成图像
                payload = await self.utils.generate_img2img_payload(image_data, final_prompt, original_width, original_height)
                logger.debug(f"Img2img API Payload: {json.dumps(payload, indent=2)}") # 添加日志输出 payload
                response = await self.client.call_i2i_api(payload)
                if not response.get("images"):
                    raise ValueError(messages.MSG_API_RETURN_ERROR)

                images = response["images"]

                if len(images) == 1:
                    image_data = response["images"][0]
                    image_bytes = base64.b64decode(image_data)
                    image = base64.b64encode(image_bytes).decode("utf-8")

                    # 图像处理
                    if self.config.get("enable_upscale"):
                        if verbose:
                            yield event.plain_result(messages.MSG_PROCESSING_IMAGE)
                        image = await self.client.apply_image_processing(image)

                    yield event.chain_result([Image.fromBase64(image)])
                else:
                    chain = []

                    if self.config.get("enable_upscale") and verbose:
                        yield event.plain_result(messages.MSG_PROCESSING_IMAGE)

                    for image_data in images:
                        image_bytes = base64.b64decode(image_data)
                        image = base64.b64encode(image_bytes).decode("utf-8")

                        # 图像处理
                        if self.config.get("enable_upscale"):
                            image = await self.client.apply_image_processing(image)

                        # 添加到链对象
                        chain.append(Image.fromBase64(image))

                    # 将链式结果发送给事件
                    yield event.chain_result(chain)

            except ValueError as e:
                logger.error(f"{messages.MSG_API_RETURN_ERROR_LOG}: {e}")
                yield event.plain_result(f"{messages.MSG_IMG2IMG_API_ERROR}\n{e}")
            except ConnectionError as e:
                logger.error(f"{messages.MSG_CONNECTION_FAIL_LOG}: {e}")
                yield event.plain_result(f"{messages.MSG_CONNECTION_ERROR}\n{e}")
            except TimeoutError as e:
                logger.error(f"{messages.MSG_TIMEOUT_ERROR_LOG}: {e}")
                yield event.plain_result(f"{messages.MSG_TIMEOUT_ERROR}\n{e}")
            except Exception as e:
                logger.error(f"{messages.MSG_OTHER_ERROR_LOG}: {e}")
                # 过滤掉包含 http/https 的报错内容
                err_str = str(e)
                if "http" in err_str or "https" in err_str:
                    err_str = messages.MSG_ERROR_API_HIDDEN
                yield event.plain_result(f"{messages.MSG_OTHER_ERROR}\n{err_str}")
            finally:
                pass

    @sd.command("gen")
    async def generate_image_command(self, event: AstrMessageEvent, prompt: str):
        """生成图像指令
        Args:
            prompt: 图像描述提示词
        """
        prompt = self._replace_local_tags(prompt)
        async for result in self._generate_image_impl(event, prompt):
            yield result

    @filter.command("i2i")
    async def img2img_command(self, event: AstrMessageEvent):
        """图生图指令"""
        image_data = None
        if event.message_obj and event.message_obj.message:
            for comp in event.message_obj.message:
                if isinstance(comp, MessageImage):
                    if hasattr(comp, 'url') and comp.url: # 检查是否有 url 属性
                        try:
                            async with httpx.AsyncClient() as client:
                                response = await client.get(comp.url)
                                response.raise_for_status() # 检查 HTTP 错误
                                image_bytes = response.content
                                image_data = base64.b64encode(image_bytes).decode("utf-8")
                                break
                        except httpx.RequestError as e:
                            logger.error(f"{messages.MSG_IMG2IMG_DOWNLOAD_FAIL_LOG}: {e}")
                            yield event.plain_result(f"{messages.MSG_IMG2IMG_DOWNLOAD_FAIL}: {e}")
                            return
                    else:
                        logger.warning("MessageImage 组件没有 url 属性或 url 为空")
                        yield event.plain_result(messages.MSG_IMG2IMG_NO_IMAGE_URL)
                        return

        if not image_data:
            yield event.plain_result(messages.MSG_IMG2IMG_NO_IMAGE)
            return

        # 明确停止事件传播，防止 LLM 再次尝试调用其工具
        event.stop_event()

        # 移除命令前缀和图片信息，只保留提示词
        raw_msg = event.message_str
        prompt_str = re.sub(r"^\s*[.／/]?i2i\s*", "", raw_msg).strip()
        # 移除消息链中的图片部分，只保留文本
        if event.message_obj and event.message_obj.message:
            # 过滤掉图片组件，只保留文本组件
            text_components = [comp.text for comp in event.message_obj.message if hasattr(comp, 'text') and not isinstance(comp, MessageImage)]
            prompt_str = " ".join(text_components).strip()

        # 现在继续执行实际的图生图逻辑
        async for result in self._img2img_impl(event, image_data, prompt_str):
            yield result

    @sd.group("i2i")
    def i2i(self):
        pass

    @i2i.command("denoising")
    async def set_denoising_strength(self, event: AstrMessageEvent, strength: float):
        """设置图生图重绘幅度"""
        try:
            if strength < 0.0 or strength > 1.0:
                yield event.plain_result(messages.MSG_DENOISING_STRENGTH_RANGE_ERROR)
                return

            self.config["img2img_params"]["denoising_strength"] = strength
            self.config.save_config()

            yield event.plain_result(messages.MSG_DENOISING_STRENGTH_SET_SUCCESS.format(strength=strength))
        except Exception as e:
            logger.error(f"{messages.MSG_DENOISING_STRENGTH_SET_FAIL_LOG}: {e}")
            yield event.plain_result(messages.MSG_DENOISING_STRENGTH_SET_FAIL)

    @sd.command("verbose")
    async def set_verbose(self, event: AstrMessageEvent):
        """切换详细输出模式（verbose）"""
        try:
            # 读取当前状态并取反
            current_verbose = self.config.get("verbose", True)
            new_verbose = not current_verbose

            # 更新配置
            self.config["verbose"] = new_verbose
            self.config.save_config()

            # 发送反馈消息
            status_msg = messages.MSG_VERBOSE_ON if new_verbose else messages.MSG_VERBOSE_OFF
            yield event.plain_result(status_msg)
        except Exception as e:
            logger.error(f"{messages.MSG_VERBOSE_FAIL_LOG}: {e}")
            yield event.plain_result(messages.MSG_VERBOSE_FAIL)

    @sd.command("upscale")
    async def set_upscale(self, event: AstrMessageEvent):
        """设置图像增强模式（enable_upscale）"""
        try:
            # 获取当前的 upscale 配置值
            current_upscale = self.config.get("enable_upscale", False)

            # 切换 enable_upscale 配置
            new_upscale = not current_upscale

            # 更新配置
            self.config["enable_upscale"] = new_upscale
            self.config.save_config()

            # 发送反馈消息
            status_msg = messages.MSG_UPSCALE_ON if new_upscale else messages.MSG_UPSCALE_OFF
            yield event.plain_result(status_msg)

        except Exception as e:
            logger.error(f"{messages.MSG_UPSCALE_FAIL_LOG}: {e}")
            yield event.plain_result(messages.MSG_UPSCALE_FAIL)

    @sd.command("LLM")
    async def set_generate_prompt(self, event: AstrMessageEvent):
        """切换生成提示词功能"""
        try:
            current_setting = self.config.get("enable_generate_prompt", False)
            new_setting = not current_setting
            self.config["enable_generate_prompt"] = new_setting
            self.config.save_config()

            status_msg = messages.MSG_LLM_PROMPT_ON if new_setting else messages.MSG_LLM_PROMPT_OFF
            yield event.plain_result(status_msg)
        except Exception as e:
            logger.error(f"{messages.MSG_LLM_PROMPT_FAIL_LOG}: {e}")
            yield event.plain_result(messages.MSG_LLM_PROMPT_FAIL)

    @sd.command("prompt")
    async def set_show_prompt(self, event: AstrMessageEvent):
        """切换显示正向提示词功能"""
        try:
            current_setting = self.config.get("enable_show_positive_prompt", False)
            new_setting = not current_setting
            self.config["enable_show_positive_prompt"] = new_setting
            self.config.save_config()

            status_msg = messages.MSG_SHOW_PROMPT_ON if new_setting else messages.MSG_SHOW_PROMPT_OFF
            yield event.plain_result(status_msg)
        except Exception as e:
            logger.error(f"{messages.MSG_SHOW_PROMPT_FAIL_LOG}: {e}")
            yield event.plain_result(messages.MSG_SHOW_PROMPT_FAIL)

    @sd.command("timeout")
    async def set_timeout(self, event: AstrMessageEvent, time: int):
        """设置会话超时时间"""
        try:
            if time < 10 or time > 300:
                yield event.plain_result(messages.MSG_TIMEOUT_RANGE_ERROR)
                return

            self.config["session_timeout_time"] = time
            self.config.save_config()

            yield event.plain_result(messages.MSG_TIMEOUT_SET_SUCCESS.format(time=time))
        except Exception as e:
            logger.error(f"{messages.MSG_TIMEOUT_SET_FAIL_LOG}: {e}")
            yield event.plain_result(messages.MSG_TIMEOUT_SET_FAIL)

    @sd.command("conf")
    async def show_conf(self, event: AstrMessageEvent):
        """打印当前图像生成参数，包括当前使用的模型"""
        try:
            gen_params = self.utils.get_generation_params_str()  # 获取当前图像参数
            scale_params = self.utils.get_upscale_params_str()   # 获取图像增强参数
            img2img_params_str = self.utils.get_img2img_params_str() # 获取图生图参数
            prompt_guidelines = self.config.get("prompt_guidelines").strip() or messages.MSG_NOT_SET  # 获取提示词限制

            verbose = self.config.get("verbose", True)  # 获取详略模式
            upscale = self.config.get("enable_upscale", False)  # 图像增强模式
            show_positive_prompt = self.config.get("enable_show_positive_prompt", False)  # 是否显示正向提示词
            generate_prompt = self.config.get("enable_generate_prompt", False)  # 是否启用生成提示词
            enable_img2img_generate_prompt = self.config.get("enable_img2img_generate_prompt", True) # 获取图生图LLM生成提示词开关

            conf_message = (
                f"{messages.MSG_GEN_PARAMS}:\n{gen_params}\n\n"
                f"{messages.MSG_UPSCALE_PARAMS}:\n{scale_params}\n\n"
                f"{messages.MSG_IMG2IMG_PARAMS}:\n{img2img_params_str}\n\n" # 添加图生图参数
                f"{messages.MSG_PROMPT_GUIDELINES}: {prompt_guidelines}\n\n"
                f"{messages.MSG_VERBOSE_MODE}: {'开启' if verbose else '关闭'}\n\n"
                f"{messages.MSG_UPSCALE_MODE}: {'开启' if upscale else '关闭'}\n\n"
                f"{messages.MSG_SHOW_PROMPT_MODE}: {'开启' if show_positive_prompt else '关闭'}\n\n"
                f"{messages.MSG_LLM_PROMPT_MODE}: {'开启' if generate_prompt else '关闭'}\n\n"
                f"{messages.MSG_LLM_IMG2IMG_PROMPT_MODE}: {'开启' if enable_img2img_generate_prompt else '关闭'}" # 添加图生图LLM生成提示词开关
            )

            yield event.plain_result(conf_message)
        except Exception as e:
            logger.error(f"{messages.MSG_CONF_FAIL_LOG}: {e}")
            yield event.plain_result(messages.MSG_CONF_FAIL)

    @sd.command("help")
    async def show_help(self, event: AstrMessageEvent):
        """显示SDGenerator插件所有可用指令及其描述"""
        help_msg = [
            messages.MSG_HELP_TITLE,
            messages.MSG_HELP_DESCRIPTION,
            "",
            messages.MSG_MAIN_COMMANDS_TITLE,
            messages.MSG_GEN_COMMAND,
            messages.MSG_CHECK_COMMAND,
            messages.MSG_CONF_COMMAND,
            messages.MSG_HELP_COMMAND,
            "",
            messages.MSG_ADVANCED_COMMANDS_TITLE,
            messages.MSG_VERBOSE_COMMAND,
            messages.MSG_UPSCALE_COMMAND,
            messages.MSG_LLM_COMMAND,
            messages.MSG_PROMPT_COMMAND,
            messages.MSG_TIMEOUT_COMMAND,
            messages.MSG_RES_COMMAND,
            messages.MSG_STEP_COMMAND,
            messages.MSG_BATCH_COMMAND,
            messages.MSG_ITER_COMMAND,
            "",
            messages.MSG_IMG2IMG_COMMANDS_TITLE, # 新增图生图命令标题
            messages.MSG_DENOISING_STRENGTH_COMMAND,
            messages.MSG_I2I_RES_COMMAND,
            messages.MSG_I2I_STEP_COMMAND,
            messages.MSG_I2I_BATCH_COMMAND,
            messages.MSG_I2I_ITER_COMMAND,
            messages.MSG_I2I_SAMPLER_COMMAND,
            messages.MSG_I2I_SCHEDULER_COMMAND,
            "",
            messages.MSG_MODEL_COMMANDS_TITLE,
            messages.MSG_MODEL_LIST_COMMAND,
            messages.MSG_MODEL_SET_COMMAND,
            messages.MSG_LORA_COMMAND,
            messages.MSG_EMBEDDING_COMMAND,
            "",
            messages.MSG_SAMPLER_UPSCALE_COMMANDS_TITLE,
            messages.MSG_SAMPLER_LIST_COMMAND,
            messages.MSG_SAMPLER_SET_COMMAND,
            messages.MSG_UPSCALER_LIST_COMMAND,
            messages.MSG_UPSCALER_SET_COMMAND,
            messages.MSG_SCHEDULER_LIST_COMMAND,
            messages.MSG_SCHEDULER_SET_COMMAND,
            "",
            messages.MSG_NOTES_TITLE,
            messages.MSG_NOTES_LLM_PROMPT,
            messages.MSG_NOTES_CUSTOM_PROMPT,
            messages.MSG_NOTES_INDEX_WARNING,
        ]
        yield event.plain_result("\n".join(help_msg))

    @sd.command("res")
    async def set_resolution(self, event: AstrMessageEvent, height: int, width: int):
        """设置文生图分辨率"""
        try:
            # 新增：支持最大1920x1920，且必须为64的倍数
            if (
                height < 64 or height > 1920 or height % 64 != 0 or
                width < 64 or width > 1920 or width % 64 != 0
            ):
                yield event.plain_result(messages.MSG_RESOLUTION_RANGE_ERROR)
                return

            self.config["default_params"]["height"] = height
            self.config["default_params"]["width"] = width
            self.config.save_config()

            yield event.plain_result(messages.MSG_RESOLUTION_SET_SUCCESS.format(width=width, height=height))
        except Exception as e:
            logger.error(f"{messages.MSG_RESOLUTION_SET_FAIL_LOG}: {e}")
            yield event.plain_result(messages.MSG_RESOLUTION_SET_FAIL)

    @sd.command("step")
    async def set_step(self, event: AstrMessageEvent, step: int):
        """设置文生图步数"""
        try:
            if step < 10 or step > 50:
                yield event.plain_result(messages.MSG_STEP_RANGE_ERROR)
                return

            self.config["default_params"]["steps"] = step
            self.config.save_config()

            yield event.plain_result(messages.MSG_STEP_SET_SUCCESS.format(step=step))
        except Exception as e:
            logger.error(f"{messages.MSG_STEP_SET_FAIL_LOG}: {e}")
            yield event.plain_result(messages.MSG_STEP_SET_FAIL)

    @sd.command("batch")
    async def set_batch_size(self, event: AstrMessageEvent, batch_size: int):
        """设置文生图批量生成的图片数量"""
        try:
            if batch_size < 1 or batch_size > 10:
                yield event.plain_result(messages.MSG_BATCH_RANGE_ERROR)
                return

            self.config["default_params"]["batch_size"] = batch_size
            self.config.save_config()

            yield event.plain_result(messages.MSG_BATCH_SET_SUCCESS.format(batch_size=batch_size))
        except Exception as e:
            logger.error(f"{messages.MSG_BATCH_SET_FAIL_LOG}: {e}")
            yield event.plain_result(messages.MSG_BATCH_SET_FAIL)

    @sd.command("iter")
    async def set_n_iter(self, event: AstrMessageEvent, n_iter: int):
        """设置文生图生成迭代次数"""
        try:
            if n_iter < 1 or n_iter > 5:
                yield event.plain_result(messages.MSG_ITER_RANGE_ERROR)
                return

            self.config["default_params"]["n_iter"] = n_iter
            self.config.save_config()

            yield event.plain_result(messages.MSG_ITER_SET_SUCCESS.format(n_iter=n_iter))
        except Exception as e:
            logger.error(f"{messages.MSG_ITER_SET_FAIL_LOG}: {e}")
            yield event.plain_result(messages.MSG_ITER_SET_FAIL)

    @sd.group("i2i")
    def i2i(self):
        pass

    @i2i.command("res")
    async def set_i2i_resolution(self, event: AstrMessageEvent, height: int, width: int):
        """设置图生图分辨率"""
        try:
            if (
                height < 64 or height > 1920 or height % 64 != 0 or
                width < 64 or width > 1920 or width % 64 != 0
            ):
                yield event.plain_result(messages.MSG_RESOLUTION_RANGE_ERROR)
                return

            self.config["img2img_params"]["height"] = height
            self.config["img2img_params"]["width"] = width
            self.config.save_config()

            yield event.plain_result(messages.MSG_RESOLUTION_SET_SUCCESS.format(width=width, height=height))
        except Exception as e:
            logger.error(f"{messages.MSG_RESOLUTION_SET_FAIL_LOG}: {e}")
            yield event.plain_result(messages.MSG_RESOLUTION_SET_FAIL)

    @i2i.command("step")
    async def set_i2i_step(self, event: AstrMessageEvent, step: int):
        """设置图生图步数"""
        try:
            if step < 10 or step > 50:
                yield event.plain_result(messages.MSG_STEP_RANGE_ERROR)
                return

            self.config["img2img_params"]["steps"] = step
            self.config.save_config()

            yield event.plain_result(messages.MSG_STEP_SET_SUCCESS.format(step=step))
        except Exception as e:
            logger.error(f"{messages.MSG_STEP_SET_FAIL_LOG}: {e}")
            yield event.plain_result(messages.MSG_STEP_SET_FAIL)

    @i2i.command("batch")
    async def set_i2i_batch_size(self, event: AstrMessageEvent, batch_size: int):
        """设置图生图批量生成的图片数量"""
        try:
            if batch_size < 1 or batch_size > 10:
                yield event.plain_result(messages.MSG_BATCH_RANGE_ERROR)
                return

            self.config["img2img_params"]["batch_size"] = batch_size
            self.config.save_config()

            yield event.plain_result(messages.MSG_BATCH_SET_SUCCESS.format(batch_size=batch_size))
        except Exception as e:
            logger.error(f"{messages.MSG_BATCH_SET_FAIL_LOG}: {e}")
            yield event.plain_result(messages.MSG_BATCH_SET_FAIL)

    @i2i.command("iter")
    async def set_i2i_n_iter(self, event: AstrMessageEvent, n_iter: int):
        """设置图生图生成迭代次数"""
        try:
            if n_iter < 1 or n_iter > 5:
                yield event.plain_result(messages.MSG_ITER_RANGE_ERROR)
                return

            self.config["img2img_params"]["n_iter"] = n_iter
            self.config.save_config()

            yield event.plain_result(messages.MSG_ITER_SET_SUCCESS.format(n_iter=n_iter))
        except Exception as e:
            logger.error(f"{messages.MSG_ITER_SET_FAIL_LOG}: {e}")
            yield event.plain_result(messages.MSG_ITER_SET_FAIL)


    @sd.group("model")
    def model(self):
        pass

    @model.command("list")
    async def list_model(self, event: AstrMessageEvent):
        """
        以“1. xxx.safetensors“形式打印可用的模型
        """
        try:
            models = await self.client.get_sd_model_list()
            if not models:
                yield event.plain_result(messages.MSG_NO_MODEL)
                return

            model_list = "\n".join(f"{i + 1}. {m}" for i, m in enumerate(models))
            yield event.plain_result(messages.MSG_MODEL_LIST_SUCCESS.format(model_list=model_list))

        except Exception as e:
            logger.error(f"{messages.MSG_MODEL_LIST_FAIL_LOG}: {e}")
            yield event.plain_result(messages.MSG_MODEL_LIST_FAIL)

    @model.command("set")
    async def set_base_model(self, event: AstrMessageEvent, model_index: int):
        """
        解析用户输入的索引，并设置对应的模型
        """
        try:
            models = await self.client.get_sd_model_list()
            if not models:
                yield event.plain_result(messages.MSG_NO_MODEL)
                return

            try:
                index = int(model_index) - 1
                if index < 0 or index >= len(models):
                    yield event.plain_result(messages.MSG_INVALID_MODEL_INDEX)
                    return

                selected_model = models[index]
                logger.debug(f"selected_model: {selected_model}")
                if await self.client.set_model(selected_model):
                    self.config["base_model"] = selected_model
                    self.config.save_config()
                    yield event.plain_result(messages.MSG_MODEL_SET_SUCCESS.format(selected_model=selected_model))
                else:
                    yield event.plain_result(messages.MSG_MODEL_SET_FAIL)

            except ValueError:
                yield event.plain_result(messages.MSG_INVALID_INDEX_INPUT)

        except Exception as e:
            logger.error(f"{messages.MSG_MODEL_SET_FAIL_LOG}: {e}")
            yield event.plain_result(messages.MSG_MODEL_SET_FAIL)

    @sd.command("lora")
    async def list_lora(self, event: AstrMessageEvent):
        """
        列出可用的 LoRA 模型
        """
        try:
            lora_models = await self.client.get_lora_list()
            if not lora_models:
                yield event.plain_result(messages.MSG_LORA_LIST_EMPTY)
            else:
                lora_model_list = "\n".join(f"{i + 1}. {lora}" for i, lora in enumerate(lora_models))
                yield event.plain_result(messages.MSG_LORA_LIST_SUCCESS.format(lora_model_list=lora_model_list))
        except Exception as e:
            yield event.plain_result(messages.MSG_LORA_LIST_FAIL.format(error=str(e)))

    @sd.group("sampler")
    def sampler(self):
        pass

    @sampler.command("list")
    async def list_sampler(self, event: AstrMessageEvent):
        """
        列出所有可用的采样器 (文生图)
        """
        try:
            samplers = await self.client.get_sampler_list()
            if not samplers:
                yield event.plain_result(messages.MSG_NO_SAMPLER)
                return

            sampler_list = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(samplers))
            yield event.plain_result(messages.MSG_SAMPLER_LIST_SUCCESS.format(sampler_list=sampler_list))
        except Exception as e:
            yield event.plain_result(messages.MSG_SAMPLER_LIST_FAIL.format(error=str(e)))

    @sampler.command("set")
    async def set_sampler(self, event: AstrMessageEvent, sampler_index: int):
        """
        设置采样器 (文生图)
        """
        try:
            samplers = await self.client.get_sampler_list()
            if not samplers:
                yield event.plain_result(messages.MSG_NO_SAMPLER)
                return

            try:
                index = int(sampler_index) - 1
                if index < 0 or index >= len(samplers):
                    yield event.plain_result(messages.MSG_INVALID_SAMPLER_INDEX)
                    return

                selected_sampler = samplers[index]
                self.config["default_params"]["sampler"] = selected_sampler
                self.config.save_config()

                yield event.plain_result(messages.MSG_SAMPLER_SET_SUCCESS.format(selected_sampler=selected_sampler))
            except ValueError:
                yield event.plain_result(messages.MSG_INVALID_INDEX_INPUT)
        except Exception as e:
            yield event.plain_result(messages.MSG_SAMPLER_SET_FAIL.format(error=str(e)))

    @sd.group("upscaler")
    def upscaler(self):
        pass

    @upscaler.command("list")
    async def list_upscaler(self, event: AstrMessageEvent):
        """
        列出所有可用的上采样算法
        """
        try:
            upscalers = await self.client.get_upscaler_list()
            if not upscalers:
                yield event.plain_result(messages.MSG_NO_UPSCALER)
                return

            upscaler_list = "\n".join(f"{i + 1}. {u}" for i, u in enumerate(upscalers))
            yield event.plain_result(messages.MSG_UPSCALER_LIST_SUCCESS.format(upscaler_list=upscaler_list))
        except Exception as e:
            yield event.plain_result(messages.MSG_UPSCALER_LIST_FAIL.format(error=str(e)))

    @upscaler.command("set")
    async def set_upscaler(self, event: AstrMessageEvent, upscaler_index: int):
        """
        设置上采样算法
        """
        try:
            upscalers = await self.client.get_upscaler_list()
            if not upscalers:
                yield event.plain_result(messages.MSG_NO_UPSCALER)
                return

            try:
                index = int(upscaler_index) - 1
                if index < 0 or index >= len(upscalers):
                    yield event.plain_result(messages.MSG_INVALID_UPSCALER_INDEX)
                    return

                selected_upscaler = upscalers[index]
                self.config["default_params"]["upscaler"] = selected_upscaler
                self.config.save_config()

                yield event.plain_result(messages.MSG_UPSCALER_SET_SUCCESS.format(selected_upscaler=selected_upscaler))
            except ValueError:
                yield event.plain_result(messages.MSG_INVALID_INDEX_INPUT)
        except Exception as e:
            yield event.plain_result(messages.MSG_UPSCALER_SET_FAIL.format(error=str(e)))

    @sd.group("scheduler")
    def scheduler(self):
        pass

    @scheduler.command("list")
    async def list_scheduler(self, event: AstrMessageEvent):
        """
        列出所有可用的调度器 (文生图)
        """
        try:
            schedulers = await self.client.get_schedulers_list()
            if not schedulers:
                yield event.plain_result(messages.MSG_NO_SCHEDULER)
                return

            scheduler_list = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(schedulers))
            yield event.plain_result(messages.MSG_SCHEDULER_LIST_SUCCESS.format(scheduler_list=scheduler_list))
        except Exception as e:
            yield event.plain_result(messages.MSG_SCHEDULER_LIST_FAIL.format(error=str(e)))

    @scheduler.command("set")
    async def set_scheduler(self, event: AstrMessageEvent, scheduler_index: int):
        """
        设置调度器 (文生图)
        """
        try:
            schedulers = await self.client.get_schedulers_list()
            if not schedulers:
                yield event.plain_result(messages.MSG_NO_SCHEDULER)
                return

            try:
                index = int(scheduler_index) - 1
                if index < 0 or index >= len(schedulers):
                    yield event.plain_result(messages.MSG_INVALID_SCHEDULER_INDEX)
                    return

                selected_scheduler = schedulers[index]
                self.config["default_params"]["scheduler"] = selected_scheduler
                self.config.save_config()

                yield event.plain_result(messages.MSG_SCHEDULER_SET_SUCCESS.format(selected_scheduler=selected_scheduler))
            except ValueError:
                yield event.plain_result(messages.MSG_INVALID_INDEX_INPUT)
        except Exception as e:
            yield event.plain_result(messages.MSG_SCHEDULER_SET_FAIL.format(error=str(e)))

    @sd.group("i2i_sampler")
    def i2i_sampler(self):
        pass

    @i2i_sampler.command("list")
    async def list_i2i_sampler(self, event: AstrMessageEvent):
        """
        列出所有可用的采样器 (图生图)
        """
        try:
            samplers = await self.client.get_sampler_list()
            if not samplers:
                yield event.plain_result(messages.MSG_NO_SAMPLER)
                return

            sampler_list = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(samplers))
            yield event.plain_result(messages.MSG_SAMPLER_LIST_SUCCESS.format(sampler_list=sampler_list))
        except Exception as e:
            yield event.plain_result(messages.MSG_SAMPLER_LIST_FAIL.format(error=str(e)))

    @i2i_sampler.command("set")
    async def set_i2i_sampler(self, event: AstrMessageEvent, sampler_index: int):
        """
        设置采样器 (图生图)
        """
        try:
            samplers = await self.client.get_sampler_list()
            if not samplers:
                yield event.plain_result(messages.MSG_NO_SAMPLER)
                return

            try:
                index = int(sampler_index) - 1
                if index < 0 or index >= len(samplers):
                    yield event.plain_result(messages.MSG_INVALID_SAMPLER_INDEX)
                    return

                selected_sampler = samplers[index]
                self.config["img2img_params"]["sampler"] = selected_sampler
                self.config.save_config()

                yield event.plain_result(messages.MSG_SAMPLER_SET_SUCCESS.format(selected_sampler=selected_sampler))
            except ValueError:
                yield event.plain_result(messages.MSG_INVALID_INDEX_INPUT)
        except Exception as e:
            yield event.plain_result(messages.MSG_SAMPLER_SET_FAIL.format(error=str(e)))

    @sd.group("i2i_scheduler")
    def i2i_scheduler(self):
        pass

    @i2i_scheduler.command("list")
    async def list_i2i_scheduler(self, event: AstrMessageEvent):
        """
        列出所有可用的调度器 (图生图)
        """
        try:
            schedulers = await self.client.get_schedulers_list()
            if not schedulers:
                yield event.plain_result(messages.MSG_NO_SCHEDULER)
                return

            scheduler_list = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(schedulers))
            yield event.plain_result(messages.MSG_SCHEDULER_LIST_SUCCESS.format(scheduler_list=scheduler_list))
        except Exception as e:
            yield event.plain_result(messages.MSG_SCHEDULER_LIST_FAIL.format(error=str(e)))

    @i2i_scheduler.command("set")
    async def set_i2i_scheduler(self, event: AstrMessageEvent, scheduler_index: int):
        """
        设置调度器 (图生图)
        """
        try:
            schedulers = await self.client.get_schedulers_list()
            if not schedulers:
                yield event.plain_result(messages.MSG_NO_SCHEDULER)
                return

            try:
                index = int(scheduler_index) - 1
                if index < 0 or index >= len(schedulers):
                    yield event.plain_result(messages.MSG_INVALID_SCHEDULER_INDEX)
                    return

                selected_scheduler = schedulers[index]
                self.config["img2img_params"]["scheduler"] = selected_scheduler
                self.config.save_config()

                yield event.plain_result(messages.MSG_SCHEDULER_SET_SUCCESS.format(selected_scheduler=selected_scheduler))
            except ValueError:
                yield event.plain_result(messages.MSG_INVALID_INDEX_INPUT)
        except Exception as e:
            yield event.plain_result(messages.MSG_SCHEDULER_SET_FAIL.format(error=str(e)))

    @sd.command("embedding")
    async def list_embedding(self, event: AstrMessageEvent):
        """
        列出可用的 Embedding 模型
        """
        try:
            embedding_models = await self.client.get_embedding_list()
            if not embedding_models:
                yield event.plain_result(messages.MSG_EMBEDDING_LIST_EMPTY)
            else:
                embedding_model_list = "\n".join(f"{i + 1}. {lora}" for i, lora in enumerate(embedding_models))
                yield event.plain_result(messages.MSG_EMBEDDING_LIST_SUCCESS.format(embedding_model_list=embedding_model_list))
        except Exception as e:
            yield event.plain_result(messages.MSG_EMBEDDING_LIST_FAIL.format(error=str(e)))

    @sd.command("set_llm_prompt_prefix")
    async def set_llm_prompt_prefix(self, event: AstrMessageEvent):
        """
        设置或查询 LLM_PROMPT_PREFIX 内容。
        用法：
        /sd set_llm_prompt_prefix [新内容]  # 设置
        /sd set_llm_prompt_prefix           # 查询当前内容
        """
        try:
            raw = event.message_str
            # 兼容各种前缀写法
            prefix_content = None
            for prefix in [".sd set_llm_prompt_prefix", "/sd set_llm_prompt_prefix", "sd set_llm_prompt_prefix"]:
                if raw.strip().lower().startswith(prefix):
                    prefix_content = raw.strip()[len(prefix):].strip()
                    break

            if not prefix_content:
                # 查询当前
                value = self.config.get("LLM_PROMPT_PREFIX")
                if value:
                    yield event.plain_result(f"当前 LLM_PROMPT_PREFIX：\n{value}")
                else:
                    # 如果配置中没有，则显示默认值
                    from .messages import MSG_DEFAULT_LLM_PROMPT_PREFIX
                    yield event.plain_result(f"当前 LLM_PROMPT_PREFIX：\n{MSG_DEFAULT_LLM_PROMPT_PREFIX}")
                return

            # 设置新内容
            self.config["LLM_PROMPT_PREFIX"] = prefix_content
            self.config.save_config()
            yield event.plain_result("✅ LLM_PROMPT_PREFIX 已更新")
        except Exception as e:
            logger.error(f"设置 LLM_PROMPT_PREFIX 失败: {e}")
            yield event.plain_result(f"❌ 设置失败: {e}")

    @sd.command("tag")
    async def tag_command(self, event: AstrMessageEvent, *, content: str = ""):
        """
        本地关键词替换管理
        用法：
        /sd tag 关键词:替换内容   # 添加或更新
        /sd tag del 关键词       # 删除
        /sd tag                 # 查询所有
        """
        msg = content
        # /sd tag del 关键词
        if msg.startswith("del "):
            key = msg[len("del "):].strip()
            if key in self.local_tag_mgr.tags:
                self.local_tag_mgr.del_tag(key)
                yield event.plain_result(f"已删除本地tag：{key}")
            else:
                yield event.plain_result(f"未找到本地tag：{key}")
            return
        # /sd tag 关键词:替换内容
        elif ":" in msg:
            key, value = msg.split(":", 1)
            key = key.strip()
            value = value.strip()
            if key:
                self.local_tag_mgr.set_tag(key, value)
                yield event.plain_result(f"已设置本地tag：{key} → {value}")
            else:
                yield event.plain_result("关键词不能为空")
            return
        # /sd tag 查询
        elif msg == "":
            tags = self.local_tag_mgr.get_all()
            if not tags:
                yield event.plain_result("暂无本地tag规则")
            else:
                rules = "\n".join([f"{k} → {v}" for k, v in tags.items()])
                yield event.plain_result(f"本地tag规则：(用法/sd tag 关键词:替换内容)\n{rules}")
            return
