path = r'D:\agent学习\ragent\rag-pro\readme.md'

with open(path, 'rb') as f:
    data = f.read()

# Find first occurrence of "##" in UTF-8 bytes
marker = b'##'
pos = data.find(marker)

if pos != -1:
    # Find the newline after this position
    nl_pos = data.find(b'\n', pos)
    if nl_pos != -1:
        tail = data[nl_pos + 1:]
    else:
        tail = b''
    
    header = '<div align="center">\n\n# ragent\n\n###  **当 RAG 只会机械匹配，你需要一个懂上下文的 Agent**\n\n[![ragent](https://img.shields.io/badge/ragent-0.4.0-purple.svg)](https://github.com/yourname/ragent)\n[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://python.org)\n[![LangGraph](https://img.shields.io/badge/LangGraph-0.2.x-blue.svg)](https://langchain-ai.github.io/langgraph/)\n[![LangChain](https://img.shields.io/badge/LangChain-0.3.x-blue.svg)](https://python.langchain.com/)\n[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com/)\n[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)\n[![Tests](https://img.shields.io/badge/Tests-Passing-brightgreen.svg)](tests/)\n\n**下一代认知检索架构** · Next-Gen Cognitive Retrieval Architecture\n\n[\U0001f680 快速开始](#\u5feb\u901f\u5f00\u59cb) · [\U0001f3d7\ufe0f 核心架构](#\u7cfb\u7edf\u67b6\u6784) · [\u26a1 功能特性](#\u529f\u80fd\u7279\u6027\u8be6\u89e3) · [\U0001f4e1 API 文档](#api-\u6587\u6863)\n\n</div>\n\n---\n\n> \U0001f916 **你的知识库只会关键词搬运？ragent 让企业知识真正被理解**\n>\n> \U0001f4a1 **核心差异**：传统 RAG 匹配即结束，ragent 从意图理解到混合检索、从记忆延续到工具增强，完成完整认知闭环。\n\n---\n\n## 目录\n\n'
    
    with open(path, 'wb') as f:
        f.write(header.encode('utf-8'))
        f.write(tail)
    
    print('OK')
else:
    print('FAIL')
