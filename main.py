import base64
import asyncio
import re
import json
import os
import httpx
import io
from pathlib import Path # 导入 Path
from PIL import Image as PILImage
from astrbot.api.star import Context, Star, register, StarTools
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.event.filter import EventMessageType
from astrbot.core.utils.session_waiter import session_waiter, SessionController
from astrbot.api.all import AstrBotConfig, logger, llm_tool, command_group, Image, BaseMessageComponent, Image as MessageImage, Plain as MessageText

from .sd_api_client import SDAPIClient
from .sd_utils import SDUtils
from .messages import MSG_DEFAULT_LLM_PROMPT_PREFIX # 导入 MSG_DEFAULT_LLM_PROMPT_PREFIX
from . import messages
from .local_tag_utils import LocalTagManager

PLUGIN_VERSION = "1.1.8"

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

        # 获取插件数据目录并创建
        self.data_dir = StarTools.get_data_dir("SDGen")
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.local_tag_mgr = LocalTagManager(str(self.data_dir / "local_tags.json"))

        # 更新：prompt_prefix.json 路径
        self.prompt_prefix_path = self.data_dir / "prompt_prefix.json"
        self._prompt_prefix_cache = None

    async def terminate(self):
        """插件卸载/停用时调用，用于清理资源"""
        await self.client.close()

    def _validate_config(self):
        """配置验证"""
        self.config["webui_url"] = self.config["webui_url"].strip()
        if not self.config["webui_url"].startswith(("http://", "https://")):
            raise ValueError(messages.MSG_WEBUI_URL_ERROR)

        if self.config["webui_url"].endswith("/"):
            self.config["webui_url"] = self.config["webui_url"].rstrip("/")
            # 只有在实际修改了配置时才保存
            self.config.save_config()

    # 替换关键词
    def _replace_local_tags(self, text: str) -> tuple[str, list[str]]:
        replaced_text, changed_keys = self.local_tag_mgr.replace(text)
        if changed_keys:
            # 构造用于日志输出的 changed 列表
            changed_display = [f"{k}→{self.local_tag_mgr.tags[k]}" for k in changed_keys]
            logger.info(f"[本地tag替换] 替换了: {', '.join(changed_display)}")
        return replaced_text, changed_keys

    @llm_tool("generate_image")
    async def generate_image(self, event: AstrMessageEvent, prompt: str):
        """
        Generate images using Stable Diffusion based on the given prompt.
        This function should only be called when the prompt contains keywords like "generate," "draw," or "create."
        It should not be mistakenly used for image searching.

        Args:
            prompt (string): The prompt or description used for generating images.
        """
        prompt, _ = self._replace_local_tags(prompt) # 忽略 changed_keys
        try:
            async for result in self._generate_image_impl(event, prompt):
                yield result
        except Exception as e:
            yield event.plain_result(f"下载图片时发生未知错误: {e}")
            return

    @filter.command("画")
    async def draw(self, event: AstrMessageEvent):
        """直接处理 .画 指令，规避 LLM 前置拦截，完整保留用户输入"""
        raw_msg = event.message_str
        prompt_str = raw_msg.lstrip(".／/画").strip()
        
        # 记录替换前后内容
        prompt_str, changed_keys = self._replace_local_tags(prompt_str)
        
        # 构造用于消息显示的 changed 列表
        changed_display = [f"{k}→{self.local_tag_mgr.tags[k]}" for k in changed_keys]

        # 判断是否有“预设”相关tag
        preset_tags = [item for item in changed_display if "预设" in item]
        other_tags = [item for item in changed_display if "预设" not in item]
        
        msg = "在画了在画了"
        if changed_display:
            if preset_tags and not other_tags:
                msg += "，预设相关tag已替换"
            elif preset_tags and other_tags:
                msg += f"，为你替换了以下tag：{', '.join(other_tags)}，预设相关tag已替换"
            else:
                msg += f"，为你替换了以下tag：{', '.join(changed_display)}"
        await event.send(event.plain_result(msg))
        async for result in self._generate_image_impl(event, prompt_str, skip_verbose_msg=True):
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

        # 在 img2img_draw 和 img2img_command 里
        prompt_str, _ = self._replace_local_tags(prompt_str) # 忽略 changed_keys

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
    
    async def _generate_image_impl(self, event: AstrMessageEvent, prompt: str, skip_verbose_msg=False):
        """实际的图像生成逻辑，供 generate_image/draw 调用"""
        async with self.task_semaphore:
            # 检查webui可用性
            if not (await self.client.check_webui_available())[0]:
                yield event.plain_result(messages.MSG_WEBUI_UNAVAILABLE)
                return

            verbose = self.config["verbose"]
            if verbose and not skip_verbose_msg:
                yield event.plain_result(messages.MSG_GENERATING)

            # 始终启用 LLM 自动生成 prompt
            generated_prompt = await self.utils.generate_prompt_with_llm(prompt)
            logger.debug(f"LLM generated prompt: {generated_prompt}")
            # 文生图：始终用 positive_prompt_global
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

            async for result in self._process_and_yield_images(event, images, verbose):
                yield result

    async def _handle_api_errors(self, event: AstrMessageEvent, func, *args, **kwargs):
        """
        通用API错误处理辅助函数。
        """
        try:
            async for result in func(event, *args, **kwargs):
                yield result
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
            err_str = str(e)
            if "http" in err_str or "https" in err_str:
                err_str = messages.MSG_ERROR_API_HIDDEN
            yield event.plain_result(f"{messages.MSG_OTHER_ERROR}\n{err_str}")

    async def _process_and_yield_images(self, event: AstrMessageEvent, images: list, verbose: bool):
        """
        处理图像（如放大）并发送结果。
        """
        chain = []
        if self.config.get("enable_upscale") and verbose:
            yield event.plain_result(messages.MSG_PROCESSING_IMAGE)

        for image_data in images:
            image_bytes = base64.b64decode(image_data)
            image_b64 = base64.b64encode(image_bytes).decode("utf-8")

            # 图像处理
            if self.config.get("enable_upscale"):
                image_b64 = await self.client.apply_image_processing(image_b64)

            # 添加到链对象
            chain.append(Image.fromBase64(image_b64))

        # 将链式结果发送给事件
        yield event.chain_result(chain)

    async def _img2img_impl(self, event: AstrMessageEvent, image_data: str, prompt: str):
        """实际的图生图逻辑，供 img2img_command/img2img_draw 调用"""
        async with self.task_semaphore:
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

            # 这里不再调用 LLM，只用传入的 prompt
            # 图生图：优先用 prompt_prefix.json
            img2img_prefix = self._load_prompt_prefix()
            if img2img_prefix:
                final_prompt = img2img_prefix + prompt
            else:
                final_prompt = self.config.get("positive_prompt_global", "") + prompt

            # 生成图像
            payload = await self.utils.generate_img2img_payload(image_data, final_prompt, original_width, original_height)
            logger.debug(f"Img2img API Payload: {json.dumps(payload, indent=2)}") # 添加日志输出 payload
            response = await self.client.call_i2i_api(payload)
            if not response.get("images"):
                raise ValueError(messages.MSG_API_RETURN_ERROR)

            images = response["images"]

            async for result in self._process_and_yield_images(event, images, verbose):
                yield result

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
        """图生图指令，支持先发文本后补图，也支持一次性发图片+文本"""
        image_data = None
        prompt_str = ""
        # 提取文本描述
        if event.message_obj and event.message_obj.message:
            text_components = [comp.text for comp in event.message_obj.message if hasattr(comp, 'text') and not isinstance(comp, MessageImage)]
            prompt_str = " ".join(text_components).strip()
        prompt_str, _ = self._replace_local_tags(prompt_str) # 忽略 changed_keys

        # 检查是否有图片
        if event.message_obj and event.message_obj.message:
            for comp in event.message_obj.message:
                if isinstance(comp, MessageImage) and hasattr(comp, 'url') and comp.url:
                    try:
                        async with httpx.AsyncClient() as client:
                            response = await client.get(comp.url)
                            response.raise_for_status()
                            image_bytes = response.content
                            image_data = base64.b64encode(image_bytes).decode("utf-8")
                            break
                    except httpx.RequestError as e:
                        logger.error(f"{messages.MSG_IMG2IMG_DOWNLOAD_FAIL_LOG}: {e}")
                        yield event.plain_result(f"{messages.MSG_IMG2IMG_DOWNLOAD_FAIL}: {e}")
                        return
                    except Exception as e:
                        yield event.plain_result(f"图片下载失败: {e}")
                        return

        # 没有图片，立即提示并并发等待图片和LLM
        if not image_data:
            if not prompt_str:
                yield event.plain_result("⚠️ 图生图指令需要您附带一张图片或描述词！")
                return

            await event.send(event.plain_result("⚠️ 图生图指令需要您附带一张图片！请在2分钟内补发图片。"))

            # 并发等待图片和LLM
            async def wait_for_image():
                image_data2 = None
                @session_waiter(timeout=120)
                async def waiter(controller: SessionController, event2: AstrMessageEvent):
                    nonlocal image_data2
                    if event2.message_obj and event2.message_obj.message:
                        for comp in event2.message_obj.message:
                            if isinstance(comp, MessageImage) and hasattr(comp, 'url') and comp.url:
                                try:
                                    async with httpx.AsyncClient() as client:
                                        response = await client.get(comp.url)
                                        response.raise_for_status()
                                        image_bytes = response.content
                                        image_data2 = base64.b64encode(image_bytes).decode("utf-8")
                                        break
                                except Exception as e:
                                    await event2.send(event2.plain_result(f"图片下载失败: {e}"))
                                    controller.stop()
                                    return
                    if image_data2:
                        event2.stop_event()
                        controller.stop()
                        return
                    await event2.send(event2.plain_result("⚠️ 请发送一张图片以完成图生图。"))
                try:
                    await waiter(event)
                except TimeoutError:
                    return None
                return image_data2

            # 并发等待图片和LLM
            tasks = [
                asyncio.create_task(wait_for_image()),
                asyncio.create_task(self.utils.generate_prompt_with_llm(prompt_str)) if self.config.get("enable_img2img_generate_prompt", True) else asyncio.create_task(asyncio.sleep(0, result=prompt_str))
            ]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.ALL_COMPLETED)
            image_data2 = tasks[0].result()
            llm_prompt = tasks[1].result() if tasks[1].result() else prompt_str
            # 图生图：优先用 prompt_prefix.json
            img2img_prefix = self._load_prompt_prefix()
            if img2img_prefix:
                llm_prompt = img2img_prefix + llm_prompt
            else:
                llm_prompt = self.config.get("positive_prompt_global", "") + llm_prompt

            if not image_data2:
                yield event.plain_result("等待图片超时，已取消本次图生图。")
                return

            async for result in self._img2img_impl(event, image_data2, llm_prompt):
                yield result
            return

        # 有图片，立即处理文本（LLM），然后生成
        llm_prompt = prompt_str
        if self.config.get("enable_img2img_generate_prompt", True):
            llm_prompt2 = await self.utils.generate_prompt_with_llm(prompt_str)
            if llm_prompt2:
                llm_prompt = llm_prompt2
        img2img_prefix = self._load_prompt_prefix()
        if img2img_prefix:
            llm_prompt = img2img_prefix + llm_prompt
        else:
            llm_prompt = self.config.get("positive_prompt_global", "") + llm_prompt

        event.stop_event()
        async for result in self._img2img_impl(event, image_data, llm_prompt):
            yield result

    @sd.group("i2i")
    def i2i(self):
        pass

    @i2i.command("prompt_prefix")
    async def set_img2img_prompt_prefix(self, event: AstrMessageEvent):
        """
        设置或查询图生图正向提示词前缀。
        用法：
        /sd i2i prompt_prefix [新内容]  # 设置
        /sd i2i prompt_prefix           # 查询当前内容
        说明：你可以直接在命令后输入你的说明或前缀内容，支持长文本。
        """
        try:
            # 兼容各种前缀写法
            raw = event.message_str
            prefix_content = None
            for prefix in [".sd i2i prompt_prefix", "/sd i2i prompt_prefix", "sd i2i prompt_prefix"]:
                if raw.strip().lower().startswith(prefix):
                    prefix_content = raw.strip()[len(prefix):].strip()
                    break

            if not prefix_content:
                value = self._load_prompt_prefix()
                if value:
                    yield event.plain_result(f"当前图生图正向提示词前缀：\n{value}")
                else:
                    yield event.plain_result("当前图生图正向提示词前缀未设置，将使用文生图前缀。")
                return

            self._save_prompt_prefix(prefix_content)
            yield event.plain_result("✅ 图生图正向提示词前缀已更新")
        except Exception as e:
            logger.error(f"设置图生图正向提示词前缀失败: {e}")
            yield event.plain_result(f"❌ 设置失败: {e}")

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
            messages.MSG_IMG2IMG_COMMAND,
            messages.MSG_IMG2IMG_DRAW_COMMAND,
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
            messages.MSG_IMG2IMG_COMMANDS_TITLE,
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
            "",
            "- /sd i2i prompt_prefix [内容]：设置/查询图生图正向提示词前缀（永久保存，支持长文本，存储于 prompt_prefix.json）。",
            "- /sd set_llm_prompt_prefix [内容]：设置/查询LLM提示词前缀（保存到config.json）。",
            "- /sd tag 关键词:替换内容：添加/更新本地tag，/sd tag del 关键词 删除，/sd tag 查询所有。",
            "- /sd conf 可查看所有当前参数和前缀。",
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

    @i2i.command("denoising")
    async def set_i2i_denoising_strength(self, event: AstrMessageEvent, strength: float):
        """
        设置图生图重绘幅度（denoising_strength）。
        用法：
        /sd i2i denoising [0.0~1.0]
        例如：/sd i2i denoising 0.7
        """
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
                lora_list = "\n".join(f"{i + 1}. {lora}" for i, lora in enumerate(lora_models))
                yield event.plain_result(messages.MSG_LORA_LIST_SUCCESS.format(lora_list=lora_list))
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
                    yield event.plain_result(messages.MSG_INVALID_SAMPLER)
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
    async def tag_command(self, event: AstrMessageEvent):
        """
        本地关键词替换管理
        用法：
        /sd tag 关键词:替换内容   # 添加或更新
        /sd tag del 关键词       # 删除
        /sd tag                 # 查询所有
        /sd tag 改名 旧名:新名   # 修改tag名称（key重命名，value不变）
        """
        # 兼容各种前缀
        raw = event.message_str
        # 去除命令前缀
        for prefix in [".sd tag", "/sd tag", "sd tag"]:
            if raw.strip().lower().startswith(prefix):
                msg = raw.strip()[len(prefix):].strip()
                break
        else:
            msg = raw.strip()
        # 支持“改名 旧名:新名”
        if msg.startswith("改名 "):
            rename_part = msg[len("改名 "):].strip()
            if ":" not in rename_part:
                yield event.plain_result("用法：/sd tag改名 旧名:新名")
                return
            old_key, new_key = rename_part.split(":", 1)
            old_key = old_key.strip()
            new_key = new_key.strip()
            if not old_key or not new_key:
                yield event.plain_result("旧名和新名都不能为空")
                return
            if old_key not in self.local_tag_mgr.tags:
                yield event.plain_result(f"未找到tag：{old_key}")
                return
            if new_key in self.local_tag_mgr.tags:
                yield event.plain_result(f"新名称已存在：{new_key}")
                return
            value = self.local_tag_mgr.tags[old_key]
            self.local_tag_mgr.del_tag(old_key)
            self.local_tag_mgr.set_tag(new_key, value)
            yield event.plain_result(f"已将tag“{old_key}”重命名为“{new_key}”，内容为：{value}")
            return
        if msg.startswith("del "):
            key = msg[len("del "):].strip()
            if key in self.local_tag_mgr.tags:
                self.local_tag_mgr.del_tag(key)
                yield event.plain_result(f"已删除本地tag：{key}")
            else:
                yield event.plain_result(f"未找到本地tag：{key}")
            return
        elif ":" in msg:
            key, value = msg.split(":", 1)
            key = key.strip()
            value = value.strip()
            logger.info(f"[tag设置] key: '{key}', value: '{value}'")
            if key:
                self.local_tag_mgr.set_tag(key, value)
                logger.info(f"[tag设置] 已设置: {key} → {value}")
                yield event.plain_result(f"已设置本地tag：{key} → {value}")
            else:
                logger.info("[tag设置] 关键词不能为空")
                yield event.plain_result("关键词不能为空")
            return
        elif msg == "":
            tags = self.local_tag_mgr.get_all()
            if not tags:
                yield event.plain_result("暂无本地tag规则")
            else:
                rules = "\n".join([f"{k} → {v}" for k, v in tags.items()])
                yield event.plain_result(f"本地tag规则：(用法/sd tag 关键词:替换内容)\n{rules}")
            return

    @filter.command("搜索tag")
    async def search_tag(self, event: AstrMessageEvent):
        """模糊搜索本地tag，例：.搜索tag 岛风"""
        raw_msg = event.message_str
        # 去除命令前缀，支持.搜索tag/搜索tag
        keyword = raw_msg.lstrip(".／/搜索tag").strip()
        if not keyword:
            yield event.plain_result("请输入要搜索的关键词，例如 .搜索tag 岛风")
            return
        results = []
        for k, v in self.local_tag_mgr.tags.items():
            if keyword in k or keyword in v:
                results.append(f"{k} → {v}")
        if results:
            yield event.plain_result("搜索结果：\n" + "\n".join(results))
        else:
            yield event.plain_result("未找到包含该关键词的tag。")

    def _load_prompt_prefix(self):
        if self._prompt_prefix_cache is not None:
            return self._prompt_prefix_cache
        if os.path.exists(self.prompt_prefix_path):
            try:
                with open(self.prompt_prefix_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._prompt_prefix_cache = data.get("prompt_prefix", "")
                    return self._prompt_prefix_cache
            except Exception as e:
                logger.error(f"读取 prompt_prefix.json 失败: {e}")
        return ""

    def _save_prompt_prefix(self, value: str):
        try:
            with open(self.prompt_prefix_path, "w", encoding="utf-8") as f:
                json.dump({"prompt_prefix": value}, f, ensure_ascii=False, indent=2)
            self._prompt_prefix_cache = value
        except Exception as e:
            logger.error(f"写入 prompt_prefix.json 失败: {e}")
