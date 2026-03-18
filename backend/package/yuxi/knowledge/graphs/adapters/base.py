import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from neo4j import GraphDatabase as GD

from yuxi.utils import logger


@dataclass
class GraphQueryConfig:
    """图谱查询配置 (Graph Query Configuration)"""

    keyword: str = ""
    kb_id: str | None = None
    kgdb_name: str | None = None
    max_nodes: int = 50
    max_depth: int = 2
    hops: int = 2
    threshold: float = 0.9
    filters: dict = None
    context: dict = None

    def __post_init__(self):
        if self.filters is None:
            self.filters = {}
        if self.context is None:
            self.context = {}


@dataclass
class GraphMetadata:
    """图谱元数据 (Graph Metadata)"""

    graph_type: str
    id_field: str = "id"
    name_field: str = "name"
    supports_embedding: bool = False
    supports_threshold: bool = False


class GraphAdapter(ABC):
    """图谱适配器基类 (Base Graph Adapter)"""

    def __init__(self, config: dict[str, Any] = None):
        self.config = config or {}
        self.metadata = self._get_metadata()

    @abstractmethod
    def _get_metadata(self) -> GraphMetadata:
        """获取图谱元数据"""
        pass

    @abstractmethod
    async def query_nodes(self, keyword: str, **kwargs) -> dict[str, Any]:
        """查询节点 (Query nodes)"""
        pass

    @abstractmethod
    def normalize_node(self, raw_node: Any) -> dict[str, Any]:
        """标准化节点格式 (Normalize node format)"""
        pass

    @abstractmethod
    def normalize_edge(self, raw_edge: Any) -> dict[str, Any]:
        """标准化边格式 (Normalize edge format)"""
        pass

    @abstractmethod
    async def get_labels(self) -> list[str]:
        """获取所有标签 (Get all labels)"""
        pass

    async def get_stats(self, **kwargs) -> dict[str, Any]:
        """获取统计信息 (Get statistics)"""
        return {}

    def _create_query_config(self, **kwargs) -> GraphQueryConfig:
        """创建查询配置"""
        # 优先使用适配器的默认配置
        config_dict = self.config.copy()
        config_dict.update(kwargs)

        return GraphQueryConfig(
            keyword=config_dict.get("keyword", ""),
            kb_id=config_dict.get("kb_id") or self.config.get("kb_id"),
            kgdb_name=config_dict.get("kgdb_name") or self.config.get("kgdb_name", "neo4j"),
            max_nodes=config_dict.get("max_nodes", config_dict.get("limit", 50)),
            max_depth=config_dict.get("max_depth", 2),
            hops=config_dict.get("hops", 2),
            threshold=config_dict.get("threshold", 0.9),
            filters=config_dict.get("filters", {}),
            context=config_dict.get("context", {}),
        )

    def _create_standard_node(
        self,
        node_id: str,
        name: str,
        entity_type: str,
        labels: list[str],
        properties: dict[str, Any],
        source: str,
    ) -> dict[str, Any]:
        """
        Helper to create a standardized node dictionary.
        """
        return {
            "id": node_id,
            "name": name,
            "original_id": node_id,
            "type": entity_type,
            "labels": labels,
            "properties": properties,
            "normalized": {
                "name": name,
                "type": entity_type,
                "source": source,
            },
            "graph_type": source,
        }

    def _create_standard_edge(
        self,
        edge_id: str,
        source_id: str,
        target_id: str,
        edge_type: str,
        properties: dict[str, Any],
        direction: str = "directed",
    ) -> dict[str, Any]:
        """
        Helper to create a standardized edge dictionary.
        """
        return {
            "id": edge_id,
            "source_id": source_id,
            "target_id": target_id,
            "type": edge_type,
            "properties": properties,
            "normalized": {
                "type": edge_type,
                "direction": direction,
            },
        }


class Neo4jConnectionManager:
    """
    Neo4j 连接管理器
    专注于数据库连接管理，不包含业务逻辑
    """

    def __init__(self):
        self.driver = None
        self.status = "closed"
        if os.environ.get("LITE_MODE", "").lower() in ("true", "1"):
            logger.info("LITE_MODE enabled, skipping Neo4j connection")
            return
        self._connect()

    def _connect(self):
        """建立 Neo4j 连接"""
        if self.driver and self._is_connected():
            return

        uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
        username = os.environ.get("NEO4J_USERNAME", "neo4j")
        password = os.environ.get("NEO4J_PASSWORD", "0123456789")

        try:
            self.driver = GD.driver(uri, auth=(username, password))
            # 测试连接
            with self.driver.session() as session:
                session.run("RETURN 1")
            self.status = "open"
            logger.info("Successfully connected to Neo4j")
        except Exception as e:
            logger.error(f"Failed to connect to Neo4j: {e}")
            raise

    def _is_connected(self) -> bool:
        """检查连接是否有效"""
        if not self.driver:
            return False
        try:
            with self.driver.session() as session:
                session.run("RETURN 1")
            return True
        except Exception:
            return False

    def is_running(self):
        """检查图数据库是否正在运行"""
        return self.status == "open" or self.status == "processing"

    def close(self):
        """关闭数据库连接"""
        if self.driver:
            self.driver.close()
            self.driver = None
            self.status = "closed"


class BaseNeo4jAdapter:
    """
    Neo4j 公共操作类，提供基础的数据库连接和查询方法
    专注于图谱本身的管理，与 upload 解耦
    """

    def __init__(self):
        self.connection = Neo4jConnectionManager()

    @property
    def driver(self):
        """获取数据库驱动（向后兼容）"""
        return self.connection.driver

    def _is_connected(self) -> bool:
        """检查连接是否有效"""
        return self.connection._is_connected()

    def _process_record_props(self, record: dict) -> dict:
        """
        处理记录中的属性：扁平化 properties 并移除 embedding
        """
        if record is None:
            return None

        # 复制一份以避免修改原字典
        data = dict(record)
        props = data.pop("properties", {}) or {}

        # 移除 embedding (节省传输带宽)
        if "embedding" in props:
            del props["embedding"]

        # 合并属性（优先保留原字典中的 id, name, type 等核心字段）
        return {**props, **data}

    def _get_sample_nodes_with_connections(self, num: int = 50, label_filter: str = None) -> dict[str, list]:
        """
        获取连通的节点子图，优先返回连通的节点
        Args:
            num: 返回的节点数量
            label_filter: 节点标签过滤器 (例如: "kb_123")
        """
        if not self._is_connected():
            raise Exception("Neo4j connection is not available")

        label_clause = f":{label_filter}" if label_filter else ""

        def query(tx, num):
            # 连通子图查询
            query_str = f"""
                // 获取高度数节点作为种子节点
                MATCH (seed{label_clause})
                WITH seed, COUNT{{(seed)-[]->()}} + COUNT{{(seed)<-[]-()}} as degree
                WHERE degree > 0
                ORDER BY degree DESC
                LIMIT 5

                // 为每个种子节点收集更多邻居节点
                UNWIND seed as s
                MATCH (s)-[*1..1]-(neighbor{label_clause})
                WITH s, neighbor, COUNT{{(s)-[]->()}} + COUNT{{(s)<-[]-()}} as s_degree
                WITH s, s_degree, collect(DISTINCT neighbor) as neighbors
                WITH s, s_degree, neighbors[0..toInteger($num * 0.15)] as limited_neighbors

                // 从邻居节点扩展到二跳节点
                UNWIND limited_neighbors as neighbor
                OPTIONAL MATCH (neighbor)-[*1..1]-(second_hop{label_clause})
                WHERE second_hop <> s
                WITH s, limited_neighbors, neighbor, collect(DISTINCT second_hop)[0..5] as second_hops

                // 收集所有连通节点
                WITH collect(DISTINCT s) as seeds,
                    collect(DISTINCT neighbor) as first_hop_nodes,
                    reduce(acc = [], x IN collect(second_hops) | acc + x) as second_hop_nodes
                WITH seeds + first_hop_nodes + second_hop_nodes as connected_nodes

                // 确保不会超过请求的节点数量
                WITH connected_nodes[0..$num] as final_nodes

                // 获取这些节点之间的关系，避免双向边
                UNWIND final_nodes as n
                OPTIONAL MATCH (n)-[rel]-(m)
                WHERE m IN final_nodes AND elementId(n) < elementId(m)
                RETURN
                    {{id: elementId(n), name: n.name, properties: properties(n)}} AS h,
                    CASE WHEN rel IS NOT NULL THEN
                        {{
                            id: elementId(rel),
                            type: rel.type,
                            source_id: elementId(startNode(rel)),
                            target_id: elementId(endNode(rel)),
                            properties: properties(rel)
                        }}
                    ELSE null END AS r,
                    CASE WHEN m IS NOT NULL THEN
                        {{id: elementId(m), name: m.name, properties: properties(m)}}
                    ELSE null END AS t
            """

            try:
                results = tx.run(query_str, num=int(num))
                formatted_results = {"nodes": [], "edges": []}
                node_ids = set()

                for item in results:
                    h_node = self._process_record_props(item["h"])
                    if h_node and h_node["id"] not in node_ids:
                        formatted_results["nodes"].append(h_node)
                        node_ids.add(h_node["id"])

                    if item["r"] is not None and item["t"] is not None:
                        t_node = self._process_record_props(item["t"])
                        r_edge = self._process_record_props(item["r"])

                        if t_node and t_node["id"] not in node_ids:
                            formatted_results["nodes"].append(t_node)
                            node_ids.add(t_node["id"])

                        if r_edge:
                            formatted_results["edges"].append(r_edge)

                # 如果节点数不足，补充更多节点
                if len(formatted_results["nodes"]) < num:
                    remaining_count = num - len(formatted_results["nodes"])
                    supplement_query = f"""
                    MATCH (n{label_clause})
                    WHERE NOT elementId(n) IN $existing_ids
                    RETURN {{id: elementId(n), name: n.name, properties: properties(n)}} AS node
                    LIMIT $count
                    """
                    supplement_results = tx.run(supplement_query, existing_ids=list(node_ids), count=remaining_count)
                    for item in supplement_results:
                        node = self._process_record_props(item["node"])
                        if node:
                            formatted_results["nodes"].append(node)

                return formatted_results

            except Exception as e:
                logger.warning(f"Connected subgraph query failed, using fallback: {e}")
                # 简单的备选查询
                fallback_query = f"""
                MATCH (n{label_clause})-[r]-(m{label_clause})
                WHERE elementId(n) < elementId(m)
                RETURN
                    {{id: elementId(n), name: n.name, properties: properties(n)}} AS h,
                    {{
                        id: elementId(r),
                        type: r.type,
                        source_id: elementId(startNode(r)),
                        target_id: elementId(endNode(r)),
                        properties: properties(r)
                    }} AS r,
                    {{id: elementId(m), name: m.name, properties: properties(m)}} AS t
                LIMIT $num
                """
                results = tx.run(fallback_query, num=int(num))
                formatted_results = {"nodes": [], "edges": []}
                node_ids = set()

                for item in results:
                    h_node = self._process_record_props(item["h"])
                    t_node = self._process_record_props(item["t"])
                    r_edge = self._process_record_props(item["r"])

                    if h_node and h_node["id"] not in node_ids:
                        formatted_results["nodes"].append(h_node)
                        node_ids.add(h_node["id"])
                    if t_node and t_node["id"] not in node_ids:
                        formatted_results["nodes"].append(t_node)
                        node_ids.add(t_node["id"])
                    if r_edge:
                        formatted_results["edges"].append(r_edge)

                return formatted_results

        with self.driver.session() as session:
            return session.execute_read(query, num)

    def _get_graph_stats(self, label_filter: str = None) -> dict[str, Any]:
        """
        获取图统计信息
        Args:
            label_filter: 节点标签过滤器 (例如: "kb_123")
        """
        if not self._is_connected():
            return {"total_nodes": 0, "total_edges": 0, "entity_types": []}

        label_clause = f":{label_filter}" if label_filter else ""

        def query(tx):
            # 统计节点
            node_query = f"MATCH (n{label_clause}) RETURN count(n) as node_count"
            node_count = tx.run(node_query).single()["node_count"]

            # 统计边
            edge_query = f"MATCH (n{label_clause})-[r]-(m{label_clause}) RETURN count(r) as edge_count"
            edge_count = tx.run(edge_query).single()["edge_count"]

            # 统计标签分布 (排除系统标签)
            label_dist_query = f"""
            MATCH (n{label_clause})
            UNWIND labels(n) as label
            WHERE label <> 'Entity' AND NOT label STARTS WITH 'kb_'
            WITH label, count(*) as count
            RETURN label, count
            ORDER BY count DESC
            """
            label_stats = tx.run(label_dist_query)
            entity_types = [{"type": record["label"], "count": record["count"]} for record in label_stats]

            return {
                "total_nodes": node_count,
                "total_edges": edge_count,
                "entity_types": entity_types,
            }

        try:
            with self.driver.session() as session:
                return session.execute_read(query)
        except Exception as e:
            logger.error(f"Failed to get graph stats: {e}")
            return {"total_nodes": 0, "total_edges": 0, "entity_types": []}

    def _get_all_labels(self, exclude_system_labels: bool = True) -> list[str]:
        """
        获取所有标签
        Args:
            exclude_system_labels: 是否排除系统标签 (kb_ 开头)
        """
        if not self._is_connected():
            return []

        def query(tx):
            result = tx.run("CALL db.labels() YIELD label RETURN collect(label) AS labels")
            labels = result.single()["labels"]

            if exclude_system_labels:
                labels = [label for label in labels if not label.startswith("kb_")]

            return labels

        try:
            with self.driver.session() as session:
                return session.execute_read(query)
        except Exception as e:
            logger.error(f"Failed to get labels: {e}")
            return []

    def close(self):
        """关闭数据库连接"""
        self.connection.close()
