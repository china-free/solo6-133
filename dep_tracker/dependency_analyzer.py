"""
依赖分析模块
负责构建跨仓库依赖拓扑图，计算发布顺序
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import networkx as nx

from .change_detector import ChangeAnalysis
from .config import AppConfig, DependencyRule


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

    def analyze(
        self,
        issue_id: str,
        change_analysis: Dict[str, ChangeAnalysis]
    ) -> DependencyAnalysis:
        """执行依赖分析"""
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
        """根据配置的依赖规则构建边"""
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
            )
            
            existing_edge = topology.get_edge(rule.from_repo, rule.to_repo)
            if existing_edge:
                existing_edge.weight += 1
                existing_edge.has_api_change = existing_edge.has_api_change or edge.has_api_change
                existing_edge.has_model_change = existing_edge.has_model_change or edge.has_model_change
                existing_edge.has_breaking_change = existing_edge.has_breaking_change or edge.has_breaking_change
            else:
                topology.add_edge(edge)

    def _build_edges_from_changes(
        self,
        topology: TopologyGraph,
        change_analysis: Dict[str, ChangeAnalysis]
    ):
        """根据变更内容推断依赖关系"""
        changed_repos = [
            repo for repo, analysis in change_analysis.items()
            if analysis.has_changes
        ]

        for i, from_repo in enumerate(changed_repos):
            from_analysis = change_analysis[from_repo]
            
            for to_repo in changed_repos[i + 1:]:
                to_analysis = change_analysis[to_repo]
                
                shared_items = from_analysis.all_affected_items & to_analysis.all_affected_items
                if shared_items:
                    inferred_rule = self._find_matching_rule(from_repo, to_repo)
                    if inferred_rule:
                        edge = DependencyEdge(
                            from_repo=inferred_rule.from_repo,
                            to_repo=inferred_rule.to_repo,
                            reason=f"共享变更项: {', '.join(list(shared_items)[:3])}",
                            weight=2,
                            has_api_change=from_analysis.has_api_changes,
                            has_model_change=from_analysis.has_model_changes,
                            has_breaking_change=from_analysis.has_breaking_changes or to_analysis.has_breaking_changes,
                        )
                        
                        existing_edge = topology.get_edge(edge.from_repo, edge.to_repo)
                        if existing_edge:
                            existing_edge.weight += 1
                        else:
                            topology.add_edge(edge)

    def _find_matching_rule(self, repo_a: str, repo_b: str) -> Optional[DependencyRule]:
        """查找匹配的依赖规则方向"""
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
