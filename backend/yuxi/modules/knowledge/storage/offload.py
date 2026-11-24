"""同步存储查询的有界卸载；取消等待不提前归还线程容量。"""

import asyncio
import weakref


class BoundedOffload:
    """按事件循环隔离预算，运行中的线程始终占有容量。"""

    def __init__(self, limit: int):
        self.limit = limit
        self._semaphores = weakref.WeakValueDictionary()

    async def __call__(self, func, /, *args, **kwargs):
        """执行同步 I/O，直到线程退出才归还容量。"""
        loop = asyncio.get_running_loop()
        semaphore = self._semaphores.get(loop)
        if semaphore is None:
            semaphore = asyncio.Semaphore(self.limit)
            self._semaphores[loop] = semaphore
        await semaphore.acquire()
        task = asyncio.create_task(asyncio.to_thread(func, *args, **kwargs))

        def release_capacity(completed_task):
            """消费异常并释放实际完成的线程容量。"""
            semaphore.release()
            if not completed_task.cancelled():
                completed_task.exception()

        task.add_done_callback(release_capacity)
        return await asyncio.shield(task)


run_milvus_query_io = BoundedOffload(8)
run_neo4j_query_io = BoundedOffload(8)
# PageRank 是 CPU 计算，不占用 Neo4j 查询槽，避免一次大图排序堵住图读取。
run_graph_rank_io = BoundedOffload(2)
