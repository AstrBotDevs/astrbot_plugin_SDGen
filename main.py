import logging
import tempfile

import aiohttp
from astrbot.api.all import *

logger = logging.getLogger("astrbot")

@register("SDGen", "buding", "Stable Diffusion图像生成器", "1.0.2")
class SDGenerator(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self.session = None
        self._validate_config()

    def _validate_config(self):
        """配置验证"""
        if not self.config["webui_url"].startswith(("http://", "https://")):
            raise ValueError("WebUI地址必须以http://或https://开头")

        if self.config["webui_url"].endswith("/"):
            self.config["webui_url"] = self.config["webui_url"].rstrip("/")

    async def ensure_session(self):
        """确保会话连接"""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=300)
            )

    async def on_disable(self):
        """清理资源"""
        if self.session and not self.session.closed:
            await self.session.close()
            self.session = None

    async def _get_model_list(self):
        """直接从 WebUI API 获取可用模型列表"""
        try:
            await self.ensure_session()
            async with self.session.get(f"{self.config['webui_url']}/sdapi/v1/sd-models") as resp:
                if resp.status == 200:
                    models = await resp.json()
                    logger.debug(f"models: {models}")
                    if isinstance(models, list):
                        model_names = [m["model_name"] for m in models if "model_name" in m]
                        logger.debug(f"可用模型: {model_names}")
                        return model_names  # 直接返回模型列表
        except Exception as e:
            logger.error(f"获取模型列表失败: {e}")

        return []

    async def _generate_payload(self, prompt: str) -> dict:
        """构建生成参数"""
        params = self.config["default_params"]

        return {
            "prompt": prompt,
            "negative_prompt": self.config["negative_prompt"],
            "width": params["width"],
            "height": params["height"],
            "steps": params["steps"],
            "sampler_name": params["sampler"],
            "cfg_scale": params["cfg_scale"],
        }

    async def _generate_prompt(self, prompt: str) -> str:
        provider = self.context.get_using_provider()
        if provider:
            prompt_guidelines = self.config["prompt_guidelines"]
            prompt_generate_text = (
                "请根据以下描述生成用于 Stable Diffusion WebUI 的提示词，"
                "请返回一条逗号分隔的 `prompt` 英文字符串，适用于 SD-WebUI，"
                "其中应包含主体、风格、光照、色彩等方面的描述，"
                "避免解释性文本，直接返回 `prompt`，不要加任何额外说明。"
                f"{prompt_guidelines}\n"
                "描述："
            )

            response = await provider.text_chat(f"{prompt_generate_text} {prompt}", session_id=None)
            if response.completion_text:
                generated_prompt = response.completion_text.strip()
                return generated_prompt

        return ""

    async def _call_sd_api(self, prompt: str) -> dict:
        """调用SD API"""
        await self.ensure_session()
        payload = await self._generate_payload(prompt)

        try:
            async with self.session.post(
                    f"{self.config['webui_url']}/sdapi/v1/txt2img",
                    json=payload
            ) as resp:
                if resp.status != 200:
                    error = await resp.text()
                    raise ConnectionError(f"API错误 ({resp.status}): {error}")

                return await resp.json()

        except aiohttp.ClientError as e:
            raise ConnectionError(f"连接失败: {str(e)}")

    @command_group("sd")
    def sd(self):
        pass

    @sd.command("gen")
    async def generate_image(self, event: AstrMessageEvent, prompt: str):
        """生成图像指令
        Args:
            prompt: 图像描述提示词
        """
        try:
            verbose = self.config["verbose"]
            if verbose:
                # 第一阶段：生成开始反馈
                yield event.plain_result("🖌️ 正在生成图像，这可能需要一段时间...")

            # 第二阶段：生成提示词
            generated_prompt = await self._generate_prompt(prompt)
            logger.debug(f"LLM generated prompt: {generated_prompt}")

            # 第三阶段：API调用
            response = await self._call_sd_api(generated_prompt)

            # 第四阶段：结果处理
            if not response.get("images"):
                raise ValueError("API返回数据异常")

            image_data = response["images"][0]
            logger.debug(f"img: {image_data}")

            info = json.loads(response["info"])
            logger.debug(f"info: {info}")

            image_bytes = base64.b64decode(image_data)

            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as temp_image:
                temp_image.write(image_bytes)
                temp_image_path = temp_image.name  # 获取临时文件路径

            yield event.image_result(temp_image_path)
            if verbose:
                yield event.plain_result(
                    f"✅ 生成成功\n"
                    f"尺寸: {info['width']}x{info['height']}\n"
                    f"采样器: {info['sampler_name']}\n"
                    f"种子: {info['seed']}"
                )

            os.remove(temp_image_path)
        except Exception as e:
            logger.error(f"Generate image failed, error: {e}")
            if "Cannot connect to host" in str(e):
                error_msg = "⚠️ 生成失败! 请检查：\n1. WebUI服务是否运行\n2. 防火墙设置\n3. 配置地址是否正确"
                yield event.plain_result(error_msg)

    async def set_model(self, model_name: str) -> bool:
        """设置 SD WebUI 的默认模型，并存入 config"""
        try:
            async with self.session.post(
                    f"{self.config['webui_url']}/sdapi/v1/options",
                    json={"sd_model_checkpoint": model_name}
            ) as resp:
                if resp.status == 200:
                    self.config["sd_model_checkpoint"] = model_name  # 存入 config
                    logger.debug(f"默认模型已设置为: {model_name}")
                    return True
                else:
                    logger.error(f"设置默认模型失败 (状态码: {resp.status})")
                    return False
        except Exception as e:
            logger.error(f"设置默认模型异常: {e}")
            return False

    @sd.command("check")
    async def check(self, event: AstrMessageEvent):
        """服务状态检查"""
        try:
            await self.ensure_session()
            async with self.session.get(f"{self.config['webui_url']}/sdapi/v1/progress") as resp:
                if resp.status == 200:
                    # 服务连接正常，获取可用模型列表
                    model_names = await self._get_model_list()

                    if model_names:
                        default_model = model_names[0]  # 选择第一个模型
                        if await self.set_model(default_model):
                            yield event.plain_result(f"✅ 服务连接正常，已设置默认模型：{default_model}")
                        else:
                            yield event.plain_result(f"✅ 服务连接正常，但默认模型设置失败")
                    else:
                        yield event.plain_result("⚠️ 服务连接正常，但未获取到可用模型")
                else:
                    yield event.plain_result(f"⚠️ 服务异常 (状态码: {resp.status})")
        except Exception as e:
            if "Cannot connect to host" in str(e):
                test_fail_msg = "❌ 连接测试失败! 请检查：\n1. WebUI服务是否运行\n2. 防火墙设置\n3. 配置地址是否正确"
                yield event.plain_result(test_fail_msg)

    def _get_generation_params(self):
        """获取当前图像生成的参数"""
        params = self.config.get("default_params", {})

        width = params.get("width") or "未设置"
        height = params.get("height") or "未设置"
        steps = params.get("steps") or "未设置"
        sampler = params.get("sampler") or "未设置"
        cfg_scale = params.get("cfg_scale") or "未设置"

        model_checkpoint = self.config.get("sd_model_checkpoint").strip() or "未设置"


        return (
            f"当前模型: {model_checkpoint}\n"
            f"图片尺寸: {width}x{height}\n"
            f"步数: {steps}\n"
            f"采样器: {sampler}\n"
            f"CFG比例: {cfg_scale}"
        )

    @sd.command("verbose")
    async def set_verbose(self, event: AstrMessageEvent):
        """切换详细模式（verbose）"""
        try:
            # 读取当前状态并取反
            current_verbose = self.config.get("verbose", True)
            new_verbose = not current_verbose

            # 更新配置
            self.config["verbose"] = new_verbose

            # 发送反馈消息
            status = "开启" if new_verbose else "关闭"
            yield event.plain_result(f"📢 详细模式已{status}")
        except Exception as e:
            logger.error(f"切换详细模式失败: {e}")
            yield event.plain_result("❌ 切换详细模式失败，请检查配置")

    @sd.command("conf")
    async def show_conf(self, event: AstrMessageEvent):
        """打印当前图像生成参数，包括当前使用的模型"""
        try:
            gen_params = self._get_generation_params()  # 获取当前图像参数
            prompt_guidelines = self.config.get("prompt_guidelines").strip() or "未设置"  # 获取提示词限制

            verbose = self.config.get("verbose", True)  # 获取详略模式

            conf_message = (
                f"📌 当前图像生成参数:\n{gen_params}\n\n"
                f"🛠️  提示词附加要求: {prompt_guidelines}\n\n"
                f"📢  详细模式: {'开启' if verbose else '关闭'}"
            )

            yield event.plain_result(conf_message)
        except Exception as e:
            logger.error(f"获取生成参数失败: {e}")
            yield event.plain_result("❌ 获取图像生成参数失败，请检查配置是否正确")

    @sd.command("help")
    async def show_help(self, event: AstrMessageEvent):
        """显示帮助信息"""
        help_msg = [
            "🖼️ Stable Diffusion 插件使用指南",
            "指令列表:",
            "/sd gen [提示词] - 生成图像（示例：/sd gen 星空下的城堡）",
            "/sd check - 检查服务连接状态（首次运行时获取可用模型列表）",
            "/sd conf - 打印图像生成参数",
            "/sd verbose - 设置详细模式"
            "/sd help - 显示本帮助信息",
            "/sd model list - 列出所有可用模型",
            "/sd model set [模型索引] - 设置当前模型（根据索引选择）",
        ]
        yield event.plain_result("\n".join(help_msg))

    @sd.group("model")
    def model(self):
        pass

    @model.command("list")
    async def list_model(self, event: AstrMessageEvent):
        """
        以“1. xxx.safetensors“形式打印可用的模型
        """
        try:
            models = await self._get_model_list()  # 使用统一方法获取模型列表
            if not models:
                yield event.plain_result("⚠️ 没有可用的模型")
                return

            model_list = "\n".join(f"{i + 1}. {m}" for i, m in enumerate(models))
            yield event.plain_result(f"🖼️ 可用模型列表:\n{model_list}")

        except Exception as e:
            logger.error(f"获取模型列表失败: {e}")
            yield event.plain_result("❌ 获取模型列表失败，请检查 WebUI 是否运行")

    @model.command("set")
    async def set_model_command(self, event: AstrMessageEvent, model_index: int):
        """
        解析用户输入的索引，并设置对应的模型
        """
        try:
            models = await self._get_model_list()
            if not models:
                yield event.plain_result("⚠️ 没有可用的模型")
                return

            try:
                index = int(model_index) - 1  # 转换为 0-based 索引
                if index < 0 or index >= len(models):
                    yield event.plain_result("❌ 无效的模型索引，请检查 /sd model list")
                    return

                selected_model = models[index]

                if await self.set_model(selected_model):
                    yield event.plain_result(f"✅ 模型已切换为: {selected_model}")
                else:
                    yield event.plain_result("⚠️ 切换模型失败，请检查 WebUI 状态")

            except ValueError:
                yield event.plain_result("❌ 请输入有效的数字索引")

        except Exception as e:
            logger.error(f"切换模型失败: {e}")
            yield event.plain_result("❌ 切换模型失败，请检查 WebUI 是否运行")

