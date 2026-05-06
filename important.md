​【角色设定与架构共识】
你现在是 kcad-auto-pcb 项目的首席架构师和代码编写者。我们正在重构一个基于大模型的 PCB 自动布局布线系统。
​【历史教训与绝对禁令】
​绝对禁止：使用字符串拼接或正则表达式来生成或修改 .kicad_pcb 文件内容。
​绝对禁止：让大模型自己去计算二维坐标距离、设计避障寻路算法或曼哈顿走线。
​【全新技术栈与分工】
我们采用“AI 做规划（大脑），传统算法做执行（双手）”的混合架构：
​输入层 (Layer 0): 解析网表时，必须过滤所有符号名称包含 PWR, GND, VCC 或 Reference 以 # 开头的虚拟电源/接地器件，不为它们分配物理封装。
​执行引擎 (Layer 2 - 关键): >     * 器件布局： 必须调用 KiCad 官方的 Python API (import pcbnew)，通过对象实例化来加载封装 (FootprintLoad) 和设置坐标 (SetPosition)。
​自动布线： 必须通过 Python subprocess 调用外部的 FreeRouting 引擎 (Java) 进行 100% 自动布线。
​Agent 编排层 (Layer 4): 你（LLM）负责通过 Python Tool 接口，调用上述执行引擎的函数，下发规则（如：间距、线宽），而不是亲自算坐标。
​请确认你理解了以上架构。接下来我们将逐步实现各个模块。
​第二部分：全新重构的项目目录树
​建议按照这个结构重新组织你的代码，将复杂的几何操作隔离在 engines/ 目录下。
kcad-auto-pcb/
├── web_ui/                 # Layer 5 (保持你的 FastAPI 和前端界面不变)
├── agent/                  # Layer 4 (Agent 编排层)
│   ├── core.py             # 调度中心：接收前端指令，调用 tools
│   └── prompts.py          # Agent 使用的子提示词
├── tools/                  # Layer 3 (提供给 LLM 调用的高层 API 接口)
│   └── pcb_actions.py      # 例如: def auto_layout_board(), def run_freerouting()
├── engines/                # Layer 2 (★ 全新：确定性执行引擎)
│   ├── kicad_native.py     # 封装 pcbnew API，处理坐标、器件、DRC对象
│   └── freerouting_mgr.py  # 封装 .dsn 生成与 FreeRouting 进程调用
├── schematic/              # Layer 0 (输入层)
│   └── netlist_parser.py   # 解析网表，过滤虚拟电源器件
└── requirements.txt        # 需增加 pcbnew (通常随 KiCad 安装) 和 networkx
第三部分：三大核心“救命”代码片段
​这三个模块是解决你之前贴图中全部问题的核心钥匙。把它们喂给你的 AI，让它基于这个基座来扩写。
​1. 解决电源找不到封装问题 (schematic/netlist_parser.py)
​在解析原理图时，拦截并丢弃虚拟器件。
def is_virtual_component(ref, value):
    """判断是否为不需要物理封装的虚拟器件（电源、地）"""
    virtual_keywords = ['PWR', 'GND', 'VCC', 'VDD', '+5V', '+3.3V']
    if ref.startswith('#'):
        return True
    if any(keyword in value.upper() for keyword in virtual_keywords):
        return True
    return False

def parse_schematic(netlist_path):
    components = []
    # 假设这里是你读取 netlist 的逻辑
    for comp in raw_netlist_components:
        if not is_virtual_component(comp['ref'], comp['value']):
            components.append(comp)
        else:
            print(f"Skipping virtual power/ground net component: {comp['ref']}")
    return components
    2. 解决布局重叠、无法校验的问题 (engines/kicad_native.py)
​全面拥抱 KiCad 官方 API，彻底抛弃字符串拼接。
import pcbnew
import math

class KiCadEngine:
    def __init__(self):
        self.board = pcbnew.BOARD()
        
    def add_footprint(self, ref, lib_path, fp_name, x_mm, y_mm, rotation_deg=0):
        """调用官方API安全地放置元件"""
        try:
            # 加载封装
            module = pcbnew.FootprintLoad(lib_path, fp_name)
            if module is None:
                raise ValueError(f"Footprint {fp_name} not found in {lib_path}")
            
            # 设置引用标号 (Ref)
            module.SetReference(ref)
            
            # 设置物理坐标 (API 需要使用纳米/微米级的内部单位，这里做转换)
            pos = pcbnew.wxPoint(pcbnew.FromMM(x_mm), pcbnew.FromMM(y_mm))
            module.SetPosition(pos)
            
            # 设置旋转角度
            module.SetOrientation(rotation_deg * 10) # KiCad 内部角度单位是 0.1 度
            
            # 添加到板子
            self.board.Add(module)
            return True
        except Exception as e:
            print(f"Error placing {ref}: {e}")
            return False

    def save_board(self, output_path):
        pcbnew.SaveBoard(output_path, self.board)
3. 解决布线像蜘蛛网的问题 (engines/freerouting_mgr.py)
​将走线任务外包给专业的 Java 引擎。
import subprocess
import os

def run_autorouter(dsn_file_path, freerouting_jar_path="freerouting.jar"):
    """
    调用 FreeRouting 引擎进行 100% 布线
    前提：你需要先通过 pcbnew 将当前 self.board 导出为 .dsn 文件
    """
    ses_file_path = dsn_file_path.replace(".dsn", ".ses")
    
    cmd = [
        "java", "-jar", freerouting_jar_path,
        "-de", dsn_file_path,  # Input: Specctra Design File
        "-do", ses_file_path,  # Output: Specctra Session File (Routing result)
        "-mp", "15"            # Max passes (可选配置)
    ]
    
    print("启动 FreeRouting 引擎...")
    try:
        # 静默或输出日志模式运行
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        print(f"布线完成！结果已生成至: {ses_file_path}")
        # 接下来你需要编写一个函数，将 .ses 文件里的线段重新画回 pcbnew.BOARD 中
        return ses_file_path
    except subprocess.CalledProcessError as e:
        print(f"FreeRouting 失败: {e.stderr}")
        return None