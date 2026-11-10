"""知识库检索层。

与 app/agents/ 分开：入库脚本只需要切块和向量库，不应加载 LangGraph。
依赖方向单向：rag 不依赖 agents，agents.retriever 依赖 rag。

包内分工：policy（常量）、chunking（切块）、vector_store（连接/DDL）、
types（Hit/投影）、sparse（BGE-M3 稀疏头）、rewrite（Query 改写）、
assemble（上下文组装）、pipeline（串起检索）。

真正入口是 app/agents/retriever.py 的 search_full()，降级判断也在那里。
"""
