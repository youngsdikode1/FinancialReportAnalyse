import inspect
import json

def GetCaller():
    """获取上层业务调用者信息，用于日志，仅调试模式使用"""
    stack = inspect.stack()
    # stack[0]:GetCaller
    # stack[1]:调用函数
    # stack[2]:业务上层调用函数
    if len(stack) >=3:
        #frame.filename
        #frame.function
        #frame.lineno
        frame = stack[2]
        return frame
    return None


EMOJI = "✅ ❌ ⚠️ 🚧 ⏳ 🚫 🟢 🟡 🔴 ✨ 🐛 ♻️ ⚡ 🔥 🔧 🧪 📝 🚀 🎨 ℹ️ 💡 ❓ 🚨 🔔 📄 📊 📁 💾 🌐 🔄 🔒 🗃️"    


def PrettyJson(obj, ensure_ascii: bool = False) -> str:
    """
    美化格式化输出JSON字符串
    :param obj: dict / list 字典对象，或者json字符串
    :param ensure_ascii: False 中文不转unicode编码
    :return: 格式化后的json文本
    """
    if isinstance(obj, str):
        # 如果传入的是json字符串，先loads解析
        data = json.loads(obj)
    else:
        data = obj
    return json.dumps(data, indent=2, ensure_ascii=ensure_ascii)
