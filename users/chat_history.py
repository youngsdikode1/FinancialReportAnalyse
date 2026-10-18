import json
import os
from typing import List
from users.message import CMessage
from utils.config import CConfigManager
from utils.logs import LogStack, LogInfo

class CChatHistory:
    def __init__(self, session_id: str):
        self.m_session_id: str = session_id
        # 主聊天面板：用户、bot对话消息
        self.m_user_history: List[CMessage] = []
        # 右侧状态栏监控消息：工作流、进度、日志（不持久化）
        self.m_monitor_history: List[CMessage] = []

    def _get_save_dir(self) -> str:
        """
        【内部私有方法】
        返回该会话存档目录，自动逐级创建目录，只返回目录路径，不带文件名
        返回: .../datas/users/{session_id}/
        """
        data_dir = CConfigManager.GetInstance().GetDataDir()
        user_root = os.path.join(data_dir, "users")
        user_dir = os.path.join(user_root, self.m_session_id)
        os.makedirs(user_dir, exist_ok=True)
        return user_dir

    def AddUserHistory(self, msg: CMessage) -> None:
        """添加主聊天面板消息（用户 / bot）"""
        self.m_user_history.append(msg)

    def GetUserHistoryDicts(self) -> List[dict]:
        """获取序列化字典列表，用于websocket推送给前端聊天面板"""
        return [item.ToDict() for item in self.m_user_history]

    def AddMonitorHistory(self, msg: CMessage) -> None:
        """添加状态栏监控消息"""
        self.m_monitor_history.append(msg)

    def GetMonitorHistoryDicts(self) -> List[dict]:
        """获取序列化字典列表，用于websocket推送给前端状态栏"""
        return [item.ToDict() for item in self.m_monitor_history]

    def GetLLMUserHistory(self, num: int = 50) -> str:
        """
        获取LLM可读对话上下文文本，只取user/bot消息，过滤monitor日志
        首尾增加分割线，内部保持标准 user:/assistant: 格式
        【现阶段上层CAgentAdvisor暂时不调用，留待后续重构】
        :param num: 取最近多少条记录
        :return: 拼接好的上下文字符串
        """
        use_items = self.m_user_history[-num:]
        buf = []
        for msg in use_items:
            if msg.m_type == "TYPE_CHAT_USER":
                buf.append(f"user: {msg.m_content}")
            elif msg.m_type == "TYPE_CHAT_BOT":
                buf.append(f"assistant: {msg.m_content}")
        body = "\n".join(buf)
        split_line = "-------- 用户聊天记录分割线 --------"
        return f"{split_line}\n{body}\n{split_line}"

    def Save(self) -> bool:
        """
        保存：同时生成 chat_history.json(正式存档) + chat_history.txt(调试可读文本)
        - chat_history.json：程序读档唯一来源
        - chat_history.txt：仅人工调试查看，程序不会读取
        txt写入失败不会影响json，返回值只代表json保存结果
        :return: True json保存成功，False json发生异常
        """
        save_dir = self._get_save_dir()
        json_path = os.path.join(save_dir, "chat_history.json")
        txt_path = os.path.join(save_dir, "chat_history.txt")
        # 1. 保存结构化json
        json_ok = False
        try:
            data = {
                "user_history": self.GetUserHistoryDicts()
            }
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            json_ok = True
        except Exception as e:
            LogStack(e, f"CChatHistory.Save json保存失败 session_id={self.m_session_id}")
        # 2. 输出可读调试txt，单独捕获异常，txt失败不影响主流程
        try:
            txt_content = self.GetLLMUserHistory(1000)
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(txt_content)
        except Exception as e:
            LogStack(e, f"CChatHistory.Save txt辅助文件写入失败 session_id={self.m_session_id}")
        return json_ok

    def ClearUserHistory(self) -> None:
        """
        清空用户聊天历史
        优化：清空前先备份可读txt为 chat_history_del_时间戳.txt，用于后台排查问题
        1. 先Save确保当前状态落盘
        2. 复制chat_history.txt → chat_history_del_yyyyMMdd_HHmmss.txt 备份
        3. 清空内存m_user_history
        4. Save()写入空记录到正式chat_history.json / chat_history.txt
        monitor监控日志不做处理，保留内存
        """
        import shutil
        from datetime import datetime
        save_dir = self._get_save_dir()
        src_txt = os.path.join(save_dir, "chat_history.txt")

        # step1：先把当前最新状态保存一次磁盘
        self.Save()

        # step2：如果txt文件存在，复制一份带删除时间戳的备份
        if os.path.exists(src_txt):
            now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            bak_txt_name = f"chat_history_del_{now_str}.txt"
            dst_txt = os.path.join(save_dir, bak_txt_name)
            try:
                shutil.copy2(src_txt, dst_txt)
                LogInfo(f"CChatHistory.ClearUserHistory 已备份删除前聊天记录: {bak_txt_name}, sid={self.m_session_id}")
            except Exception as e:
                LogStack(e, f"CChatHistory.ClearUserHistory 备份chat_history_del文件失败 sid={self.m_session_id}")

        # step3：清空内存聊天列表
        self.m_user_history.clear()

        # step4：保存空数据，覆盖正式chat_history.json / chat_history.txt
        self.Save()

    def Load(self) -> bool:
        """
        只从 chat_history.json 加载，完全忽略chat_history.txt
        文件不存在、json损坏、字段缺失全部返回False，不抛异常，不污染现有内存
        ✅反序列化统一使用 CMessage.Load(d)
        :return: True加载成功；False失败
        """
        save_dir = self._get_save_dir()
        json_path = os.path.join(save_dir, "chat_history.json")
        if not os.path.exists(json_path):
            return False
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                root = json.load(f)
            arr = root.get("user_history", [])
            msg_list: List[CMessage] = []
            for d in arr:
                msg = CMessage.Load(d)
                msg_list.append(msg)
            self.m_user_history = msg_list
            return True
        except Exception as e:
            LogStack(e, f"CChatHistory.Load 读取失败 session_id={self.m_session_id} path={json_path}")
            return False