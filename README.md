# astrbot_plugin_SDGen

## 介绍
用于图片生成的AstrBot插件，使用Stable Diffusion WebUI的API进行图片生成

## 安装
- 插件商店直接安装
- 复制 `https://github.com/zouyonghe/astrbot_plugin_SDGen` 导入

## 三种工作流程
**1.使用 /sd gen 指令让LLM生成提示词 -> 自动添加全局正负提示词和用户预设正负提示词（可选） -> 提示词送入 Stable diffusion 生成图像 -> 图像增强（可选） -> 输出图像**

**2.由 LLM 依照对话情况自主选择调用函数工具生成提示词 -> 自动添加全局正负提示词和用户预设正负提示词（可选） -> 提示词送入 Stable diffusion 生成图像 -> 图像增强（可选） -> 输出图像**

**3.由用户输入正面提示词 -> 自动添加全局正负提示词和用户预设正负提示词（可选） -> 提示词送入 Stable diffusion 生成图像 -> 图像增强（可选） -> 输出图像**

（假设用户输入或 LLM 生成的正面提示词为A，全局正面提示词为 B，生效的用户预设正面提示词为 C，则最终输入 Stable diffusion 的正面提示词将为 A+B+C 或 B+C+A 按次序连接在一起，两种连接顺序取决于——全局/用户正面提示词加在头还是加在尾？——这个开关控制）

（假如全局负面提示词为 A，生效的用户预设负面提示词为B，则最终输入 Stable diffusion 的负面提示词将为 A+B 按次序连接在一起）


## 配置参数说明（按照顺序）

### WebUI API地址

- **类型**: `string`
- **描述**: WebUI API地址
- **默认值**: `http://127.0.0.1:7860`
- **提示**: 需要包含 `http://` 或 `https://` 前缀

### 控制回复的详略程度

- **类型**: `bool`
- **描述**: 设置为`true`时，将实时输出AI生图进行到了哪个阶段，否则仅输出最终图片
- **默认值**: `true`

### 会话判定超时时间，单位秒（s）

- **类型**: `int`
- **描述**: 填写正整数，默认为120 秒，根据需要修改。如果在这个时间内图片未能生成完毕，则终止本次请求，并发送提示消息。
- **默认值**: `120`

### 最大并发任务数

- **类型**: `int`
- **描述**: 填写正整数，决定同一时间能处理的AI生图请求数量
- **默认值**: `10`
- **提示**: 请根据GPU显存大小和其他AI生图设置来酌情设定，免得在高频AI生图请求下爆显存导致程序运行缓慢甚至卡死

### 启用使用LLM生成正面提示词

- **类型**: `bool`
- **描述**: 设置为`true`时启用，开启时，当使用sd gen `[提示词]`指令时，将`[提示词]`先发送给LLM，再由LLM来生成正面提示词；关闭时，`[提示词]`内容将直接作为提示词送入Stable diffusion
- **提示**: 该开关仅影响 `/sd gen` 指令；LLM 调用生图工具时会直接使用传入的提示词
- **默认值**: `true`

### 启用高分辨率处理

- **类型**: `bool`
- **描述**: 设置为`true`时启用高分辨率处理
- **默认值**: `false`

### 启用输出正面提示词

- **类型**: `bool`
- **描述**: 设置为`true`时启用，开启时，用户发起AI生图请求后，将发送一条消息，内容为送入到Stable diffusion的正面提示词
- **默认值**: `false`

### 全局正负提示词

#### 全局正面提示词开关

- **类型**: `bool`
- **描述**: 设置为`true`时启用，决定是否启用全局正面提示词
- **默认值**: `"false"`

#### 全局/用户正面提示词加在头还是加在尾？

- **类型**: `bool`
- **描述**: 此开关设置为`true`时，全局正面提示词将会附着在用户输入的提示词组的开头，起到最大权重，如定义画风、设置安全等级。否则附着在尾部，权重偏低，适合用于修饰画面、提高出图质量、设定不重要的背景等。此开关也会影响用户预设正面提示词添加位置。
- **默认值**: `"false"`

#### 全局正面提示词

- **类型**: `string`
- **描述**: 会自动附加到所有AI生图请求的提示词词组中
- **默认值**: `""`

#### 全局负面提示词开关

- **类型**: `bool`
- **描述**: 设置为`true`时启用，决定是否启用全局负面提示词
- **默认值**: `"false"`

#### 全局负面提示词

- **类型**: `string`
- **描述**: 用户只能在QQ等操作界面中只能输入正面提示词，无法输入负面提示词。所以请在这里设定全局负面提示词，它会自动附加到AI生图请求中，用于降低画面中特定的元素比例。
- **默认值**: `(worst quality),(low quality:1.4),deformed,bad anatomy`

### 用户预设正负提示词组合（可设置项较多，请下载插件后详细查看）

#### 用户预设正面提示词组合

#### 生效的用户预设正面提示词编号

#### 用户预设正面提示词 0、1、2、3

#### 生效的用户预设负面提示词编号

#### 用户预设正面提示词 0、1、2、3

- **（以上为用户预设正负提示词组合部分）**

### 默认生成参数

#### 图像宽度 (`width`)

- **类型**: `int`
- **默认值**: `512`
- **可选值**: `1-2048之间的整数`

#### 图像高度 (`height`)

- **类型**: `int`
- **默认值**: `512`
- **可选值**: `1-2048之间的整数]`

#### 采样步数 (`steps`)

- **类型**: `int`
- **描述**: 采样步数
- **默认值**: `20`
- **范围**: `10 - 50`

#### 采样方法 (`sampler`)

- **类型**: `string`
- **描述**: 图像生成的采样方法
- **默认值**: `DPM++ 2M`
- **可选值**: `Euler a`, `Euler`, `Heun`, `DPM2`, `DPM2 a`, `DPM++ 2M`, `DPM++ 2M SDE`, `DPM++ 2M SDE Heun`,
  `DPM++ 2S a`, `DPM++ 3M SDE`, `DDIM`, `LMS`, `PLMS`, `UniPC`

#### 提示词权重 (`cfg_scale`)

- **类型**: `int`
- **描述**: CFG比例
- **默认值**: `7`
- **范围**: `1 - 20`

#### 上采样算法 (`upscaler`)

- **类型**: `string`
- **描述**: 放大的上采样算法
- **默认值**: `ESRGAN_4x`
- **可选值**: `Latent`, `Latent(antialiased)`, `Latent(bicubic)`, `Latent(bicubic antialiased)`, `Latent(nearest)`,
  `Latent(nearest-exact)`, `None`, `Lanczos`, `Nearest`, `DAT x2`, `DAT x3`, `DAT x4`, `ESRGAN_4x`, `LDSR`,
  `R-ESRGAN 4x+`, `R-ESRGAN 4x+ Anime6B`, `ScuNET`, `SCUNET PSNR`, `SwinlR_4x`
- **提示**: 常见算法如ESRGAN、R-ESRGAN等

#### 图像放大倍数 (`upscale_factor`)

- **类型**: `int`
- **描述**: 图像放大倍数
- **默认值**: `2`
- **范围**: `1 - 8`
- **提示**: 常见值为 `2`, `4` 等

### 基础模型

- **类型**: `string`
- **描述**: 选择生成图像的基础模型
- **默认值**: `""`
- **提示**: 默认为空，可通过 `/sd model list` 获取可用模型

### LMM生成提示词的附加限制

- **类型**: `string`
- **描述**: LMM生成提示词时的附加限制
- **默认值**: `""`
- **提示**: 例如 `任何被判断为色情的提示词都应该被替换，避免出现色情内容`


### 关于Stable Diffusion WebUI的部署建议
1. 克隆仓库
```bash
git clone https://github.com/AUTOMATIC1111/stable-diffusion-webui
cd stable-diffusion-webui
```

2. 检查python版本
<span style="color:red">（不要直接运行webui.sh)</span>

```bash
python -V
```
如果python版本高于3.10，例如3.11、3.12、3.13，请使用conda（anaconda或miniconda）或者mamba创建环境（可能也可以用pyenv设置，暂未验证）
```bash
conda create -n webui python=3.10
conda activate webui
# 取消激活时使用 conda deactivate
conda install pip
```
3. 安装依赖
```bash
pip install -r requirements.txt
```
4. 首次运行，会安装大量模型、依赖等，需要一段时间
```bash
./webui.sh
```
5. 安装插件（可选）
- 汉化插件 https://github.com/hanamizuki-ai/stable-diffusion-webui-localization-zh_Hans.git 或 https://github.com/VinsonLaro/stable-diffusion-webui-chinese
- 超分辨率插件 https://github.com/Coyote-A/ultimate-upscale-for-automatic1111
- 提示词插件 https://github.com/Physton/sd-webui-prompt-all-in-one/tree/main

6. 以API方式启动webui
```bash
./webui.sh --listen --port 7860 --api      # 带webui的方式启动
#./webui.sh --listen --port 7860 --nowebui # 不带webui，仅API方式启动
```
### 【替代方案】使用绘世启动器启动 Stable diffusion
1.根据启动器视频教程或者文章教程位置配置好启动器设置，将要使用的模型放到启动器指定的目录里，并测试确保可以使用网页交互界面来生成图片。

2.在启动器设置界面勾选启动 API

3.在 Astrbot 的本插件配置界面的API栏中填写：`http://127.0.0.1:7860`（默认情况下已填写）

4.如果你的 Astrbot 和 Stable diffusion 软件不在同一个电脑（或系统）中运行，则需要输入运行 Stable diffusion 的系统的局域网 ip 地址（ip 前记得要写上 http://），并在后附上端口号 7860。需要确保你的两个系统在同一个局域网中（或者在同一个虚拟局域网中、一台电脑连接到另一台电脑发射的 wifi 等等），如果你没有相关的计算机网络知识，请去简单学习一下。

5.搞定配置后，尝试发送/sd conf 指令，看看机器人是否有响应

# 支持
QQ： 1259085392
- 请尽可能自己debug，实在无法解决的问题再寻求帮助
- 任何代码方面问题，请随时发issues
