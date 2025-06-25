# messages.py

import json
import os

def get_llm_prompt_prefix():
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
            return config.get("LLM_PROMPT_PREFIX", DEFAULT_LLM_PROMPT_PREFIX)
    return DEFAULT_LLM_PROMPT_PREFIX

DEFAULT_LLM_PROMPT_PREFIX = (
    "请根据以下描述生成用于 Stable Diffusion WebUI 的英文提示词，请返回一条逗号分隔的 `prompt` 英文字符串，适用于 Stable Diffusion web UI，其中 1、应包含主体、风格、光照、色彩等方面的描述，避免解释性文本，不需要 “prompt:” 等内容，不需要双引号包裹（如果用户没有要求，避免添加chibi等Q版形象） 2、如果用词并不符合 Stable Diffusion WebUI 的常见词，请将其转换为常见prompt 3、如果用户有提供角色名，要以“角色名(作品名称)”的方式输入到绘图模型。例：“aris_(blue_archive)”，如果用户没提及，则不添加。4、若用户的关键词包含“补全”时，应当以用户提供的关键词来补全相关prompt（例如：如果用户的关键词有监狱，如果要你补全，那应该有镣铐）。5、直接返回 `prompt`，不要加任何额外说明。"
)

LLM_PROMPT_PREFIX = get_llm_prompt_prefix()

# 图像生成过程中的提示语
MSG_GENERATING = "在画了在画了"
MSG_PROCESSING_IMAGE = "🖼️ 处理图像阶段，即将结束..."

# 错误和状态提示语
MSG_WEBUI_UNAVAILABLE = "⚠️ 同webui无连接，目前无法生成图片！"
MSG_API_ERROR = "❌ 图像生成失败: 参数异常，API调用失败"
MSG_CONNECTION_ERROR = "⚠️ 生成失败! 请检查网络连接和WebUI服务是否运行正常"
MSG_TIMEOUT_ERROR = "⚠️ 请求超时，请稍后再试"
MSG_OTHER_ERROR = "❌ 图像生成失败: 发生其他错误"
MSG_ERROR_API_HIDDEN = "[错误详情已隐藏API地址]"
MSG_API_ERROR_DETAIL = "API调用失败，状态码: {status}，错误: {error}"
MSG_CONNECTION_FAIL = "连接失败"
MSG_API_RETURN_ERROR = "API返回数据异常"
MSG_POSITIVE_PROMPT_DISPLAY = "正向提示词"
MSG_LLM_RETURNED_TAG = "LLM返回"
MSG_NOT_SET = "未设置"

# 检查命令相关提示语
MSG_CHECK_WEBUI_NORMAL = "✅ 同Webui连接正常"
MSG_CHECK_WEBUI_FAIL = "❌ 同Webui无连接，请检查配置和Webui工作状态"
MSG_CHECK_ERROR = "❌ 检查可用性错误，请检查日志"
MSG_CHECK_ERROR_LOG = "检查可用性时发生错误"
MSG_WEBUI_RETURN_ERROR = "WebUI返回非200状态码: {status}"
MSG_CHECK_WEBUI_FAIL_LOG = "检查WebUI可用性失败"

# 模式切换提示语
MSG_VERBOSE_ON = "📢 详细输出模式已开启"
MSG_VERBOSE_OFF = "📢 详细输出模式已关闭"
MSG_VERBOSE_FAIL = "❌ 切换详细模式失败，请检查日志"
MSG_VERBOSE_FAIL_LOG = "切换详细模式失败"

MSG_UPSCALE_ON = "📢 图像增强模式已开启"
MSG_UPSCALE_OFF = "📢 图像增强模式已关闭"
MSG_UPSCALE_FAIL = "❌ 切换图像增强模式失败，请检查日志"
MSG_UPSCALE_FAIL_LOG = "切换图像增强模式失败"

MSG_LLM_PROMPT_ON = "📢 提示词生成功能已开启"
MSG_LLM_PROMPT_OFF = "📢 提示词生成功能已关闭"
MSG_LLM_PROMPT_FAIL = "❌ 切换生成提示词功能失败，请检查日志"
MSG_LLM_PROMPT_FAIL_LOG = "切换生成提示词功能失败"

MSG_SHOW_PROMPT_ON = "📢 显示正向提示词功能已开启"
MSG_SHOW_PROMPT_OFF = "📢 显示正向提示词功能已关闭"
MSG_SHOW_PROMPT_FAIL = "❌ 切换显示正向提示词功能失败，请检查日志"
MSG_SHOW_PROMPT_FAIL_LOG = "切换显示正向提示词功能失败"

# 参数设置提示语
MSG_TIMEOUT_RANGE_ERROR = "⚠️ 超时时间需设置在 10 到 300 秒范围内"
MSG_TIMEOUT_SET_SUCCESS = "⏲️ 会话超时时间已设置为 {time} 秒"
MSG_TIMEOUT_SET_FAIL = "❌ 设置会话超时时间失败，请检查日志"
MSG_TIMEOUT_SET_FAIL_LOG = "设置会话超时时间失败"

MSG_CONF_FAIL = "❌ 获取图像生成参数失败，请检查配置是否正确"
MSG_CONF_FAIL_LOG = "获取图像生成参数失败"

MSG_RESOLUTION_RANGE_ERROR = "⚠️ 分辨率需为64的倍数，且范围为64~1920"
MSG_RESOLUTION_SET_SUCCESS = "✅ 分辨率已设置为: {width}x{height}"
MSG_RESOLUTION_SET_FAIL = "❌ 设置分辨率失败，请检查日志"
MSG_RESOLUTION_SET_FAIL_LOG = "设置分辨率失败"

MSG_STEP_RANGE_ERROR = "⚠️ 步数需设置在 10 到 50 之间"
MSG_STEP_SET_SUCCESS = "✅ 步数已设置为: {step}"
MSG_STEP_SET_FAIL = "❌ 设置步数失败，请检查日志"
MSG_STEP_SET_FAIL_LOG = "设置步数失败"

MSG_BATCH_RANGE_ERROR = "⚠️ 图片生成的批数量需设置在 1 到 10 之间"
MSG_BATCH_SET_SUCCESS = "✅ 图片生成批数量已设置为: {batch_size}"
MSG_BATCH_SET_FAIL = "❌ 设置批量生成数量失败，请检查日志"
MSG_BATCH_SET_FAIL_LOG = "设置批量生成数量失败"

MSG_ITER_RANGE_ERROR = "⚠️ 图片生成的迭代次数需设置在 1 到 5 之间"
MSG_ITER_SET_SUCCESS = "✅ 图片生成的迭代次数已设置为: {n_iter}"
MSG_ITER_SET_FAIL = "❌ 设置图片生成的迭代次数失败，请检查日志"
MSG_ITER_SET_FAIL_LOG = "设置图片生成的迭代次数失败"

MSG_DENOISING_STRENGTH_RANGE_ERROR = "⚠️ 重绘幅度需设置在 0.0 到 1.0 之间"
MSG_DENOISING_STRENGTH_SET_SUCCESS = "✅ 重绘幅度已设置为: {strength}"
MSG_DENOISING_STRENGTH_SET_FAIL = "❌ 设置重绘幅度失败，请检查日志"
MSG_DENOISING_STRENGTH_SET_FAIL_LOG = "设置重绘幅度失败"

# 模型/资源列表提示语
MSG_NO_MODEL = "⚠️ 没有可用的模型"
MSG_MODEL_LIST_FAIL = "❌ 获取模型列表失败，请检查 WebUI 是否运行"
MSG_MODEL_LIST_SUCCESS = "🖼️ 可用模型列表:\n{model_list}"
MSG_INVALID_MODEL_INDEX = "❌ 无效的模型索引，请使用 /sd model list 获取"
MSG_MODEL_SET_SUCCESS = "✅ 模型已切换为: {selected_model}"
MSG_MODEL_SET_FAIL = "⚠️ 切换模型失败，请检查 WebUI 状态"
MSG_INVALID_INDEX_INPUT = "❌ 请输入有效的数字索引"
MSG_MODEL_LIST_FAIL_LOG = "获取模型列表失败"
MSG_MODEL_SET_SUCCESS_LOG = "模型已成功设置为: {model_name}"
MSG_MODEL_SET_FAIL_LOG = "切换模型失败"
MSG_MODEL_SET_EXCEPTION = "设置模型时发生异常"

MSG_LORA_LIST_EMPTY = "没有可用的 LoRA 模型。"
MSG_LORA_LIST_SUCCESS = "可用的 LoRA 模型:\n{lora_model_list}"
MSG_LORA_LIST_FAIL = "获取 LoRA 模型列表失败: {error}"

MSG_NO_SAMPLER = "⚠️ 没有可用的采样器"
MSG_SAMPLER_LIST_SUCCESS = "🖌️ 可用采样器列表:\n{sampler_list}"
MSG_SAMPLER_LIST_FAIL = "获取采样器列表失败: {error}"
MSG_INVALID_SAMPLER_INDEX = "❌ 无效的采样器索引，请使用 /sd sampler list 获取"
MSG_SAMPLER_SET_SUCCESS = "✅ 已设置采样器为: {selected_sampler}"
MSG_SAMPLER_SET_FAIL = "设置采样器失败: {error}"

MSG_NO_UPSCALER = "⚠️ 没有可用的上采样算法"
MSG_UPSCALER_LIST_SUCCESS = "🖌️ 可用上采样算法列表:\n{upscaler_list}"
MSG_UPSCALER_LIST_FAIL = "获取上采样算法列表失败: {error}"
MSG_INVALID_UPSCALER_INDEX = "❌ 无效的上采样算法索引，请检查 /sd upscaler list"
MSG_UPSCALER_SET_SUCCESS = "✅ 已设置上采样算法为: {selected_upscaler}"
MSG_UPSCALER_SET_FAIL = "设置上采样算法失败: {error}"

MSG_NO_SCHEDULER = "⚠️ 没有可用的调度器"
MSG_SCHEDULER_LIST_SUCCESS = "⏱️ 可用调度器列表:\n{scheduler_list}"
MSG_SCHEDULER_LIST_FAIL = "获取调度器列表失败: {error}"
MSG_INVALID_SCHEDULER_INDEX = "❌ 无效的调度器索引，请使用 /sd scheduler list 获取"
MSG_SCHEDULER_SET_SUCCESS = "✅ 已设置调度器为: {selected_scheduler}"
MSG_SCHEDULER_SET_FAIL = "设置调度器失败: {error}"

MSG_EMBEDDING_LIST_EMPTY = "没有可用的 Embedding 模型。"
MSG_EMBEDDING_LIST_SUCCESS = "可用的 Embedding 模型:\n{embedding_model_list}"
MSG_EMBEDDING_LIST_FAIL = "获取 Embedding 模型列表失败: {error}"

# Help messages
MSG_HELP_TITLE = "🖼️ **Stable Diffusion 插件帮助指南**"
MSG_HELP_DESCRIPTION = "该插件用于调用 Stable Diffusion WebUI 的 API 生成图像并管理相关模型资源。"

MSG_MAIN_COMMANDS_TITLE = "📜 **主要功能指令**:"
MSG_GEN_COMMAND = "- `/sd gen [提示词]`：生成图片，例如 `/sd gen 星空下的城堡`。"
MSG_CHECK_COMMAND = "- `/sd check`：检查 WebUI 的连接状态。"
MSG_CONF_COMMAND = "- `/sd conf`：显示当前使用配置，包括模型、参数和提示词设置。"
MSG_HELP_COMMAND = "- `/sd help`：显示本帮助信息。"

MSG_ADVANCED_COMMANDS_TITLE = "🔧 **高级功能指令**:"
MSG_VERBOSE_COMMAND = "- `/sd verbose`：切换详细输出模式，用于显示图像生成步骤。"
MSG_UPSCALE_COMMAND = "- `/sd upscale`：切换图像增强模式（用于超分辨率放大或高分修复）。"
MSG_LLM_COMMAND = "- `/sd LLM`：切换是否使用 LLM 自动生成提示词。"
MSG_PROMPT_COMMAND = "- `/sd prompt`：切换是否在生成过程显示正向提示词。"
MSG_TIMEOUT_COMMAND = "- `/sd timeout [秒数]`：设置连接超时时间（范围：10 到 300 秒）。"
MSG_RES_COMMAND = "- `/sd res [高度] [宽度]`：设置图像生成的分辨率（支持: 512, 768, 1024）。"
MSG_STEP_COMMAND = "- `/sd step [步数]`：设置图像生成的步数（范围：10 到 50 步）。"
MSG_BATCH_COMMAND = "- `/sd batch [数量]`：设置生成图像的批数量（范围： 1 到 10 张）。"
MSG_ITER_COMMAND = "- `/sd iter [次数]`：设置迭代次数（范围： 1 到 5 次）。"
MSG_DENOISING_STRENGTH_COMMAND = "- `/sd denoise [幅度]`：设置重绘幅度（范围：0.0 到 1.0）。"

MSG_MODEL_COMMANDS_TITLE = "🖼️ **基本模型与微调模型指令**:"
MSG_MODEL_LIST_COMMAND = "- `/sd model list`：列出 WebUI 当前可用的模型。"
MSG_MODEL_SET_COMMAND = "- `/sd model set [索引]`：根据索引设置模型，索引可通过 `model list` 查询。"
MSG_LORA_COMMAND = "- `/sd lora`：列出所有可用的 LoRA 模型。"
MSG_EMBEDDING_COMMAND = "- `/sd embedding`：显示所有已加载的 Embedding 模型。"

MSG_SAMPLER_UPSCALE_COMMANDS_TITLE = "🎨 **采样器与上采样算法指令**:"
MSG_SAMPLER_LIST_COMMAND = "- `/sd sampler list`：列出支持的采样器。"
MSG_SAMPLER_SET_COMMAND = "- `/sd sampler set [索引]`：根据索引配置采样器，用于调整生成效果。"
MSG_UPSCALER_LIST_COMMAND = "- `/sd upscaler list`：列出支持的上采样算法。"
MSG_UPSCALER_SET_COMMAND = "- `/sd upscaler set [索引]`：根据索引设置上采样算法。"
MSG_SCHEDULER_LIST_COMMAND = "- `/sd scheduler list`：列出支持的调度器。"
MSG_SCHEDULER_SET_COMMAND = "- `/sd scheduler set [索引]`：根据索引设置调度器。"

MSG_NOTES_TITLE = "ℹ️ **注意事项**:"
MSG_NOTES_LLM_PROMPT = "- 如启用自动生成提示词功能，则会使用 LLM 根据提供的信息随机生成提示词。"
MSG_NOTES_CUSTOM_PROMPT = "- 如未启用自动生成提示词功能，若自定义的提示词包含空格，则应使用 `_` 替代提示词中的空格。"
MSG_NOTES_INDEX_WARNING = "- 模型、采样器和其他资源的索引需要使用对应 `list` 命令获取后设置！"
