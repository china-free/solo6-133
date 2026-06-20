"""
依赖分析模块
负责构建跨仓库依赖拓扑图，计算发布顺序
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

import networkx as nx

from .change_detector import ChangeAnalysis
from .config import (
    AppConfig,
    DependencyRule,
    CHANGE_ROLE_PROVIDER,
    CHANGE_ROLE_CONSUMER,
    CHANGE_ROLE_BOTH,
    CHANGE_ROLE_UNKNOWN,
    INFERENCE_SOURCE_STATIC,
    INFERENCE_SOURCE_DYNAMIC,
    INFERENCE_SOURCE_HYBRID,
)


@dataclass
class DependencyEdge:
    """依赖边"""
    from_repo: str
    to_repo: str
    reason: str = ""
    weight: int = 1
    has_api_change: bool = False
    has_model_change: bool = False
    has_breaking_change: bool = False
    inference_source: str = INFERENCE_SOURCE_STATIC
    confidence: float = 1.0
    shared_items: Set[str] = field(default_factory=set)


@dataclass
class RepositoryNode:
    """仓库节点"""
    name: str
    repo_type: str = "backend"
    has_changes: bool = False
    has_api_changes: bool = False
    has_model_changes: bool = False
    has_breaking_changes: bool = False
    commit_count: int = 0
    affected_items: Set[str] = field(default_factory=set)


@dataclass
class TopologyGraph:
    """拓扑图"""
    nodes: Dict[str, RepositoryNode] = field(default_factory=dict)
    edges: List[DependencyEdge] = field(default_factory=list)
    graph: nx.DiGraph = field(default_factory=nx.DiGraph)

    def add_node(self, node: RepositoryNode):
        """添加节点"""
        self.nodes[node.name] = node
        if not self.graph.has_node(node.name):
            self.graph.add_node(node.name, **node.__dict__)

    def add_edge(self, edge: DependencyEdge):
        """添加边"""
        self.edges.append(edge)
        self.graph.add_edge(
            edge.from_repo,
            edge.to_repo,
            reason=edge.reason,
            weight=edge.weight,
            has_api_change=edge.has_api_change,
            has_model_change=edge.has_model_change,
            has_breaking_change=edge.has_breaking_change,
        )

    def get_edge(self, from_repo: str, to_repo: str) -> Optional[DependencyEdge]:
        """获取边"""
        for edge in self.edges:
            if edge.from_repo == from_repo and edge.to_repo == to_repo:
                return edge
        return None


@dataclass
class ReleaseStep:
    """发布步骤"""
    order: int
    repo_names: List[str]
    reason: str = ""
    requires: List[str] = field(default_factory=list)
    has_breaking_changes: bool = False
    notes: List[str] = field(default_factory=list)


@dataclass
class DependencyAnalysis:
    """依赖分析结果"""
    issue_id: str
    topology: TopologyGraph
    release_order: List[ReleaseStep]
    changed_repos: List[str]
    repos_without_changes: List[str]
    has_circular_dependency: bool = False
    circular_dependencies: List[List[str]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


class DependencyAnalyzer:
    """依赖分析器"""

    def __init__(self, config: AppConfig):
        self.config = config
        self._dynamic_warnings: List[str] = []

    def analyze(
        self,
        issue_id: str,
        change_analysis: Dict[str, ChangeAnalysis]
    ) -> DependencyAnalysis:
        """执行依赖分析"""
        self._dynamic_warnings = []
        
        topology = self._build_topology(change_analysis)
        
        has_circular = False
        cycles = []
        release_order: List[ReleaseStep] = []
        
        try:
            release_order = self._calculate_release_order(topology, change_analysis)
        except nx.NetworkXUnfeasible as e:
            has_circular = True
            cycles = self._find_cycles(topology.graph)
            topology, release_order = self._break_cycles_and_analyze(
                topology, change_analysis, cycles
            )

        changed_repos = [repo for repo, analysis in change_analysis.items() if len(analysis.commits) > 0]
        all_repo_names = {repo.name for repo in self.config.repositories}
        repos_without_changes = list(all_repo_names - set(changed_repos))

        warnings = self._generate_warnings(topology, change_analysis)
        
        for dyn_warning in self._dynamic_warnings:
            if dyn_warning not in warnings:
                warnings.append(dyn_warning)
        
        dynamic_edges = [
            edge for edge in topology.edges
            if edge.inference_source in (INFERENCE_SOURCE_DYNAMIC, INFERENCE_SOURCE_HYBRID)
        ]
        if dynamic_edges:
            info_msg = (
                f"动态推断了 {len(dynamic_edges)} 条依赖关系 "
                f"(动态: {sum(1 for e in dynamic_edges if e.inference_source == INFERENCE_SOURCE_DYNAMIC)}, "
                f"混合: {sum(1 for e in dynamic_edges if e.inference_source == INFERENCE_SOURCE_HYBRID)})"
            )
            warnings.insert(0, f"ℹ️  {info_msg}")

        return DependencyAnalysis(
            issue_id=issue_id,
            topology=topology,
            release_order=release_order,
            changed_repos=sorted(changed_repos),
            repos_without_changes=sorted(repos_without_changes),
            has_circular_dependency=has_circular,
            circular_dependencies=cycles,
            warnings=warnings,
        )

    def _build_topology(
        self,
        change_analysis: Dict[str, ChangeAnalysis]
    ) -> TopologyGraph:
        """构建拓扑图"""
        topology = TopologyGraph()

        for repo_config in self.config.repositories:
            analysis = change_analysis.get(repo_config.name)
            has_changes = analysis is not None and len(analysis.commits) > 0
            
            node = RepositoryNode(
                name=repo_config.name,
                repo_type=repo_config.type,
                has_changes=has_changes,
                has_api_changes=analysis.has_api_changes if analysis else False,
                has_model_changes=analysis.has_model_changes if analysis else False,
                has_breaking_changes=analysis.has_breaking_changes if analysis else False,
                commit_count=len(analysis.commits) if analysis else 0,
                affected_items=analysis.all_affected_items if analysis else set(),
            )
            topology.add_node(node)

        self._build_edges_from_rules(topology, change_analysis)
        self._build_edges_from_changes(topology, change_analysis)

        return topology

    def _build_edges_from_rules(
        self,
        topology: TopologyGraph,
        change_analysis: Dict[str, ChangeAnalysis]
    ):
        """根据静态配置的依赖规则构建边
        
        静态配置依赖与动态推断完全解耦，各自独立计算。
        """
        for rule in self.config.dependency_rules:
            from_analysis = change_analysis.get(rule.from_repo)
            to_analysis = change_analysis.get(rule.to_repo)
            
            from_has_changes = from_analysis is not None and from_analysis.has_changes
            to_has_changes = to_analysis is not None and to_analysis.has_changes
            
            if not (from_has_changes or to_has_changes):
                continue

            edge = DependencyEdge(
                from_repo=rule.from_repo,
                to_repo=rule.to_repo,
                reason=rule.reason,
                weight=1,
                has_api_change=from_analysis.has_api_changes if from_analysis else False,
                has_model_change=from_analysis.has_model_changes if from_analysis else False,
                has_breaking_change=from_analysis.has_breaking_changes if from_analysis else False,
                inference_source=INFERENCE_SOURCE_STATIC,
                confidence=1.0,
            )
            
            existing_edge = topology.get_edge(rule.from_repo, rule.to_repo)
            if existing_edge:
                if existing_edge.inference_source == INFERENCE_SOURCE_DYNAMIC:
                    existing_edge.inference_source = INFERENCE_SOURCE_HYBRID
                existing_edge.weight += 1
                existing_edge.confidence = min(1.0, existing_edge.confidence + 0.2)
                existing_edge.has_api_change = existing_edge.has_api_change or edge.has_api_change
                existing_edge.has_model_change = existing_edge.has_model_change or edge.has_model_change
                existing_edge.has_breaking_change = existing_edge.has_breaking_change or edge.has_breaking_change
                if rule.reason and rule.reason not in existing_edge.reason:
                    existing_edge.reason = f"{existing_edge.reason}; {rule.reason}" if existing_edge.reason else rule.reason
            else:
                topology.add_edge(edge)

    def _classify_repo_role(
        self,
        repo_name: str,
        analysis: ChangeAnalysis
    ) -> Tuple[str, Dict[str, Any]]:
        """根据变更特征分类仓库角色
        
        Args:
            repo_name: 仓库名称
            analysis: 变更分析结果
        
        Returns:
            (role, features) - 角色和特征字典
        """
        repo_config = self.config.get_repo(repo_name)
        features: Dict[str, Any] = {
            'repo_type': repo_config.type if repo_config else 'unknown',
            'has_api_changes': analysis.has_api_changes,
            'has_model_changes': analysis.has_model_changes,
            'has_breaking_changes': analysis.has_breaking_changes,
            'module_hierarchy': repo_config.module_hierarchy if repo_config else None,
            'provides_api': repo_config.provides_api if repo_config else False,
            'consumes_api': repo_config.consumes_api if repo_config else False,
            'affected_items': analysis.all_affected_items,
            'commit_count': len(analysis.commits),
        }
        
        api_keywords = ['Service:', 'RPC:', 'Path:', 'Schema:']
        model_keywords = ['Class:', 'Struct:', 'Type:', 'Message:']
        
        has_api_defs = any(
            any(kw in item for kw in api_keywords)
            for item in analysis.all_affected_items
        )
        
        has_model_defs = any(
            any(kw in item for kw in model_keywords)
            for item in analysis.all_affected_items
        )
        
        features['has_api_definitions'] = has_api_defs
        features['has_model_definitions'] = has_model_defs
        
        repo_type = features['repo_type']
        is_frontend = repo_type == 'frontend' or 'frontend' in repo_name.lower()
        is_backend = repo_type == 'backend' or 'service' in repo_name.lower()
        
        if features['provides_api'] or (is_backend and has_api_defs):
            if features['consumes_api'] or (is_frontend and has_model_defs and not has_api_defs):
                role = CHANGE_ROLE_BOTH
            else:
                role = CHANGE_ROLE_PROVIDER
        elif features['consumes_api'] or (is_frontend and has_model_defs and not has_api_defs):
            role = CHANGE_ROLE_CONSUMER
        else:
            role = CHANGE_ROLE_UNKNOWN
        
        features['is_frontend'] = is_frontend
        features['is_backend'] = is_backend
        
        return role, features

    def _calculate_direction_score(
        self,
        from_role: str,
        from_features: Dict[str, Any],
        to_role: str,
        to_features: Dict[str, Any],
        shared_items: Set[str]
    ) -> Tuple[int, float, str]:
        """计算依赖方向得分
        
        Args:
            from_role: 候选源仓库角色
            from_features: 候选源仓库特征
            to_role: 候选目标仓库角色
            to_features: 候选目标仓库特征
            shared_items: 共享变更项
        
        Returns:
            (direction, confidence, reason) - 方向: 1=from→to, -1=to→from, 0=无法确定
        """
        score_forward = 0.0
        score_backward = 0.0
        reasons = []
        
        if from_features['has_api_definitions'] and not to_features['has_api_definitions']:
            score_forward += 2.0
            reasons.append("源仓库定义API，目标仓库未定义API")
        elif to_features['has_api_definitions'] and not from_features['has_api_definitions']:
            score_backward += 2.0
            reasons.append("目标仓库定义API，源仓库未定义API")
        
        if from_role == CHANGE_ROLE_PROVIDER and to_role == CHANGE_ROLE_CONSUMER:
            score_forward += 3.0
            reasons.append("提供者→消费者")
        elif to_role == CHANGE_ROLE_PROVIDER and from_role == CHANGE_ROLE_CONSUMER:
            score_backward += 3.0
            reasons.append("消费者→提供者")
        
        if from_features['is_backend'] and to_features['is_frontend']:
            score_forward += 2.5
            reasons.append("后端→前端")
        elif to_features['is_backend'] and from_features['is_frontend']:
            score_backward += 2.5
            reasons.append("前端→后端")
        
        hierarchy_a = from_features.get('module_hierarchy')
        hierarchy_b = to_features.get('module_hierarchy')
        if hierarchy_a is not None and hierarchy_b is not None:
            if hierarchy_a < hierarchy_b:
                score_forward += 1.5
                reasons.append(f"模块层级 {hierarchy_a} → {hierarchy_b}")
            elif hierarchy_b < hierarchy_a:
                score_backward += 1.5
                reasons.append(f"模块层级 {hierarchy_b} → {hierarchy_a}")
        
        shared_api = sum(
            1 for item in shared_items
            if any(kw in item for kw in ['Service:', 'RPC:', 'Path:'])
        )
        shared_model = sum(
            1 for item in shared_items
            if any(kw in item for kw in ['Class:', 'Struct:', 'Type:', 'Message:'])
        )
        
        if shared_api > 0:
            if from_features['has_api_definitions']:
                score_forward += 1.0 * shared_api
            if to_features['has_api_definitions']:
                score_backward += 1.0 * shared_api
        
        if shared_model > 0:
            if from_features['has_model_definitions'] and not to_features['has_model_definitions']:
                score_forward += 0.5 * shared_model
            elif to_features['has_model_definitions'] and not from_features['has_model_definitions']:
                score_backward += 0.5 * shared_model
        
        from_commits = from_features.get('commit_count', 0)
        to_commits = to_features.get('commit_count', 0)
        if from_commits > to_commits and from_commits > 1:
            score_forward += 0.3
        elif to_commits > from_commits and to_commits > 1:
            score_backward += 0.3
        
        total_score = abs(score_forward - score_backward)
        max_score = max(score_forward, score_backward, 1.0)
        confidence = min(1.0, total_score / max_score) if max_score > 0 else 0.5
        
        if score_forward > score_backward and total_score >= 0.5:
            return 1, confidence, "; ".join(reasons) if reasons else "基于变更特征推断"
        elif score_backward > score_forward and total_score >= 0.5:
            return -1, confidence, "; ".join(reasons) if reasons else "基于变更特征推断"
        else:
            return 0, confidence, "无法确定方向，建议人工检查"

    def _infer_dependency_direction(
        self,
        repo_a: str,
        repo_b: str,
        analysis_a: ChangeAnalysis,
        analysis_b: ChangeAnalysis,
        shared_items: Set[str]
    ) -> Tuple[Optional[str], Optional[str], float, str]:
        """智能推断两个仓库之间的依赖方向
        
        完全基于变更特征，不依赖静态配置规则。
        
        Args:
            repo_a: 仓库A名称
            repo_b: 仓库B名称
            analysis_a: 仓库A的变更分析
            analysis_b: 仓库B的变更分析
            shared_items: 共享变更项
        
        Returns:
            (from_repo, to_repo, confidence, reason) - 推断出的依赖方向和置信度
        """
        role_a, features_a = self._classify_repo_role(repo_a, analysis_a)
        role_b, features_b = self._classify_repo_role(repo_b, analysis_b)
        
        direction, confidence, reason = self._calculate_direction_score(
            role_a, features_a,
            role_b, features_b,
            shared_items
        )
        
        if direction == 1:
            return repo_a, repo_b, confidence, reason
        elif direction == -1:
            return repo_b, repo_a, confidence, reason
        else:
            if features_a.get('module_hierarchy') and features_b.get('module_hierarchy'):
                if features_a['module_hierarchy'] < features_b['module_hierarchy']:
                    return repo_a, repo_b, 0.6, "基于模块层级默认推断"
                else:
                    return repo_b, repo_a, 0.6, "基于模块层级默认推断"
            
            if features_a.get('is_backend') and features_b.get('is_frontend'):
                return repo_a, repo_b, 0.7, "默认后端→前端"
            elif features_b.get('is_backend') and features_a.get('is_frontend'):
                return repo_b, repo_a, 0.7, "默认后端→前端"
            
            return None, None, confidence, reason

    def _normalize_item_name(self, item: str) -> str:
        """规范化项名称，移除前缀（Class:, Type:, Service:, RPC:, API:, etc.）
        
        这样可以跨仓库匹配相同的逻辑实体，即使前缀不同。
        """
        parts = item.split(': ', 1)
        if len(parts) == 2:
            return parts[1].strip()
        return item.strip()

    def _find_shared_items(
        self,
        analysis_a: ChangeAnalysis,
        analysis_b: ChangeAnalysis
    ) -> Set[str]:
        """智能查找两个仓库之间的共享变更项
        
        支持跨前缀匹配：Class: UserModel == Type: UserModel
        并优先返回analysis_a中的完整项名用于显示。
        """
        normalized_a = {}
        for item in analysis_a.all_affected_items:
            normalized = self._normalize_item_name(item)
            if normalized:
                normalized_a[normalized] = item
        
        normalized_b = {}
        for item in analysis_b.all_affected_items:
            normalized = self._normalize_item_name(item)
            if normalized:
                normalized_b[normalized] = item
        
        shared_names = set(normalized_a.keys()) & set(normalized_b.keys())
        
        shared_items = set()
        for name in shared_names:
            item_a = normalized_a.get(name, name)
            item_b = normalized_b.get(name, name)
            shared_items.add(item_a)
            shared_items.add(item_b)
        
        return shared_items

    def _build_edges_from_changes(
        self,
        topology: TopologyGraph,
        change_analysis: Dict[str, ChangeAnalysis]
    ):
        """基于变更特征动态推断依赖关系
        
        与静态配置完全解耦，不强制要求共享变更项必须命中配置规则。
        基于变更特征（API提供者/消费者、模块层级、仓库类型等）智能推断DAG方向。
        """
        changed_repos = [
            repo for repo, analysis in change_analysis.items()
            if analysis.has_changes
        ]

        dynamic_warnings = []

        for i, repo_a in enumerate(changed_repos):
            analysis_a = change_analysis[repo_a]
            
            for repo_b in changed_repos[i + 1:]:
                analysis_b = change_analysis[repo_b]
                
                shared_items = self._find_shared_items(analysis_a, analysis_b)
                if not shared_items:
                    continue
                
                existing_edge_ab = topology.get_edge(repo_a, repo_b)
                existing_edge_ba = topology.get_edge(repo_b, repo_a)
                has_static_edge = existing_edge_ab is not None or existing_edge_ba is not None
                
                if has_static_edge:
                    if existing_edge_ab and existing_edge_ab.inference_source == INFERENCE_SOURCE_STATIC:
                        existing_edge_ab.inference_source = INFERENCE_SOURCE_HYBRID
                        existing_edge_ab.shared_items.update(shared_items)
                    if existing_edge_ba and existing_edge_ba.inference_source == INFERENCE_SOURCE_STATIC:
                        existing_edge_ba.inference_source = INFERENCE_SOURCE_HYBRID
                        existing_edge_ba.shared_items.update(shared_items)
                
                from_repo, to_repo, confidence, reason = self._infer_dependency_direction(
                    repo_a, repo_b,
                    analysis_a, analysis_b,
                    shared_items
                )
                
                if from_repo is None or to_repo is None:
                    warning_msg = (
                        f"仓库 {repo_a} 和 {repo_b} 共享 {len(shared_items)} 个变更项，"
                        f"但无法确定依赖方向。建议添加静态依赖规则。"
                        f"共享项: {', '.join(list(shared_items)[:3])}"
                    )
                    dynamic_warnings.append(warning_msg)
                    continue
                
                edge = DependencyEdge(
                    from_repo=from_repo,
                    to_repo=to_repo,
                    reason=f"共享变更项: {', '.join(list(shared_items)[:3])}; {reason}",
                    weight=2,
                    has_api_change=analysis_a.has_api_changes or analysis_b.has_api_changes,
                    has_model_change=analysis_a.has_model_changes or analysis_b.has_model_changes,
                    has_breaking_change=analysis_a.has_breaking_changes or analysis_b.has_breaking_changes,
                    inference_source=INFERENCE_SOURCE_DYNAMIC if not has_static_edge else INFERENCE_SOURCE_HYBRID,
                    confidence=confidence,
                    shared_items=shared_items,
                )
                
                existing_edge = topology.get_edge(from_repo, to_repo)
                opposite_edge = topology.get_edge(to_repo, from_repo)
                
                if existing_edge:
                    existing_edge.weight += 1
                    if existing_edge.inference_source == INFERENCE_SOURCE_DYNAMIC and has_static_edge:
                        existing_edge.inference_source = INFERENCE_SOURCE_HYBRID
                    existing_edge.confidence = min(1.0, (existing_edge.confidence + confidence) / 2)
                    existing_edge.shared_items.update(shared_items)
                    if reason and reason not in existing_edge.reason:
                        existing_edge.reason = f"{existing_edge.reason}; {reason}" if existing_edge.reason else reason
                else:
                    topology.add_edge(edge)
        
        for warning in dynamic_warnings:
            if warning not in self._dynamic_warnings:
                self._dynamic_warnings.append(warning)

    def _find_matching_rule(self, repo_a: str, repo_b: str) -> Optional[DependencyRule]:
        """查找匹配的依赖规则方向（保留用于兼容，不强制使用）"""
        for rule in self.config.dependency_rules:
            if (rule.from_repo == repo_a and rule.to_repo == repo_b):
                return rule
            if (rule.from_repo == repo_b and rule.to_repo == repo_a):
                return rule
        return None

    def _calculate_release_order(
        self,
        topology: TopologyGraph,
        change_analysis: Dict[str, ChangeAnalysis]
    ) -> List[ReleaseStep]:
        """计算发布顺序（拓扑排序）"""
        changed_nodes = [
            name for name, node in topology.nodes.items()
            if node.has_changes
        ]

        if not changed_nodes:
            return []

        subgraph = topology.graph.subgraph(changed_nodes).copy()
        
        if len(subgraph.nodes()) == 0:
            return []

        levels = self._multilevel_topo_sort(subgraph)
        
        release_steps = []
        for i, level_repos in enumerate(levels, 1):
            step_breaking = False
            step_notes = []
            step_requires = []

            for repo in level_repos:
                node = topology.nodes.get(repo)
                if node and node.has_breaking_changes:
                    step_breaking = True
                
                predecessors = list(subgraph.predecessors(repo))
                for pred in predecessors:
                    if pred not in level_repos and pred not in step_requires:
                        step_requires.append(pred)

                analysis = change_analysis.get(repo)
                if analysis:
                    if analysis.has_api_changes:
                        api_count = sum(
                            len(c.affected_items) for c in analysis.api_contract_changes
                        )
                        step_notes.append(f"{repo}: {api_count} 个 API 变更")
                    if analysis.has_breaking_changes:
                        step_notes.append(f"{repo}: 包含破坏性变更，需特别注意")

            step = ReleaseStep(
                order=i,
                repo_names=level_repos,
                reason=f"第 {i} 批发布" if len(level_repos) > 1 else f"第 {i} 步发布",
                requires=step_requires,
                has_breaking_changes=step_breaking,
                notes=step_notes,
            )
            release_steps.append(step)

        return release_steps

    def _multilevel_topo_sort(self, graph: nx.DiGraph) -> List[List[str]]:
        """多层拓扑排序，将无依赖的节点放在同一层"""
        levels = []
        remaining = graph.copy()

        while remaining.nodes():
            current_level = [
                node for node in remaining.nodes()
                if remaining.in_degree(node) == 0
            ]
            
            if not current_level:
                raise nx.NetworkXUnfeasible("图中存在环，无法进行拓扑排序")
            
            levels.append(sorted(current_level))
            remaining.remove_nodes_from(current_level)

        return levels

    def _find_cycles(self, graph: nx.DiGraph) -> List[List[str]]:
        """查找图中的环"""
        try:
            cycles = list(nx.simple_cycles(graph))
            return cycles
        except Exception:
            return []

    def _break_cycles_and_analyze(
        self,
        topology: TopologyGraph,
        change_analysis: Dict[str, ChangeAnalysis],
        cycles: List[List[str]]
    ) -> Tuple[TopologyGraph, List[ReleaseStep]]:
        """尝试打破环并重新分析"""
        modified_topology = topology
        
        for cycle in cycles:
            for i in range(len(cycle)):
                from_repo = cycle[i]
                to_repo = cycle[(i + 1) % len(cycle)]
                
                edge = modified_topology.get_edge(from_repo, to_repo)
                if edge and not edge.has_breaking_change:
                    modified_topology.edges.remove(edge)
                    modified_topology.graph.remove_edge(from_repo, to_repo)
                    break

        try:
            release_order = self._calculate_release_order(modified_topology, change_analysis)
        except nx.NetworkXUnfeasible:
            release_order = []

        return modified_topology, release_order

    def _generate_warnings(
        self,
        topology: TopologyGraph,
        change_analysis: Dict[str, ChangeAnalysis]
    ) -> List[str]:
        """生成警告信息"""
        warnings = []

        for name, analysis in change_analysis.items():
            if not analysis.has_changes:
                continue

            node = topology.nodes.get(name)
            if not node:
                continue

            if analysis.has_breaking_changes:
                warnings.append(
                    f"警告: 仓库 {name} 包含破坏性变更，发布前需确认所有依赖方已准备好"
                )

            dependents = []
            for edge in topology.edges:
                if edge.from_repo == name:
                    dependents.append(edge.to_repo)
            
            if dependents and node.has_changes:
                warnings.append(
                    f"注意: 仓库 {name} 的变更会影响 {len(dependents)} 个依赖仓库: {', '.join(dependents)}"
                )

        return warnings
