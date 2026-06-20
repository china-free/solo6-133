"""
可视化模块
负责在终端渲染依赖拓扑图和发布顺序
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Set

from colorama import Fore, Style, init

from .dependency_analyzer import (
    DependencyAnalysis,
    DependencyEdge,
    ReleaseStep,
    RepositoryNode,
    TopologyGraph,
)
from .config import (
    INFERENCE_SOURCE_STATIC,
    INFERENCE_SOURCE_DYNAMIC,
    INFERENCE_SOURCE_HYBRID,
)
from .git_scanner import RepoScanResult

init(autoreset=True)


@dataclass
class DisplayConfig:
    """显示配置"""
    show_commits: bool = True
    show_changes: bool = True
    show_reason: bool = True
    max_width: int = 120
    use_color: bool = True


class TerminalVisualizer:
    """终端可视化器"""

    BOX_CHARS = {
        'horizontal': '─',
        'vertical': '│',
        'corner_tl': '┌',
        'corner_tr': '┐',
        'corner_bl': '└',
        'corner_br': '┘',
        'tee_left': '┤',
        'tee_right': '├',
        'tee_up': '┴',
        'tee_down': '┬',
        'cross': '┼',
        'arrow': '→',
        'arrow_bold': '⇒',
    }

    TYPE_COLORS = {
        'backend': Fore.CYAN,
        'frontend': Fore.MAGENTA,
        'database': Fore.YELLOW,
        'infrastructure': Fore.BLUE,
    }

    STATUS_COLORS = {
        'changed': Fore.GREEN,
        'breaking': Fore.RED,
        'unchanged': Fore.LIGHTBLACK_EX,
    }

    def __init__(self, config: Optional[DisplayConfig] = None):
        self.config = config or DisplayConfig()

    def _color(self, text: str, color: str) -> str:
        """应用颜色"""
        if not self.config.use_color:
            return text
        return f"{color}{text}{Style.RESET_ALL}"

    def _format_repo_name(self, node: RepositoryNode) -> str:
        """格式化仓库名称显示"""
        type_color = self.TYPE_COLORS.get(node.repo_type, Fore.WHITE)
        
        if node.has_changes:
            if node.has_breaking_changes:
                status_color = self.STATUS_COLORS['breaking']
                status_icon = '⚠️ '
            else:
                status_color = self.STATUS_COLORS['changed']
                status_icon = '✓ '
        else:
            status_color = self.STATUS_COLORS['unchanged']
            status_icon = '○ '

        return f"{status_icon}{self._color(node.name, type_color)}"

    def render_header(self, issue_id: str) -> str:
        """渲染标题"""
        lines = []
        title = f"跨仓库变更依赖分析 - Issue: {issue_id}"
        separator = self.BOX_CHARS['horizontal'] * len(title)
        
        lines.append(self._color(separator, Fore.LIGHTBLACK_EX))
        lines.append(self._color(title, Fore.YELLOW + Style.BRIGHT))
        lines.append(self._color(separator, Fore.LIGHTBLACK_EX))
        lines.append("")
        
        return "\n".join(lines)

    def render_scan_summary(self, scan_results: List[RepoScanResult]) -> str:
        """渲染扫描摘要"""
        lines = []
        lines.append(self._color("📊 仓库扫描摘要", Fore.BLUE + Style.BRIGHT))
        lines.append("")

        total_repos = len(scan_results)
        changed_repos = [r for r in scan_results if r.has_changes]
        error_repos = [r for r in scan_results if r.error]

        lines.append(f"  总仓库数: {total_repos}")
        lines.append(f"  有变更: {self._color(str(len(changed_repos)), Fore.GREEN)}")
        if error_repos:
            lines.append(f"  扫描失败: {self._color(str(len(error_repos)), Fore.RED)}")
        lines.append("")

        for result in scan_results:
            status_icon = "✅" if result.has_changes else "○"
            if result.error:
                status_icon = "❌"
            
            type_label = f"[{result.repo_type}]"
            type_color = self.TYPE_COLORS.get(result.repo_type, Fore.WHITE)
            
            line = f"  {status_icon} {self._color(result.repo_name, type_color)} {self._color(type_label, Fore.LIGHTBLACK_EX)}"
            
            if result.has_changes:
                line += f" - {len(result.commits)} 个提交"
                if result.api_contract_changes:
                    line += f" | API: {len(result.api_contract_changes)}"
                if result.shared_model_changes:
                    line += f" | 模型: {len(result.shared_model_changes)}"
            
            if result.error:
                line += f" - {self._color(result.error, Fore.RED)}"
            
            lines.append(line)
        
        lines.append("")
        return "\n".join(lines)

    def render_topology_graph(self, topology: TopologyGraph) -> str:
        """渲染拓扑图（ASCII 艺术）"""
        lines = []
        lines.append(self._color("🔗 变更依赖拓扑图", Fore.BLUE + Style.BRIGHT))
        lines.append("")

        changed_nodes = {name: node for name, node in topology.nodes.items() if node.has_changes}
        
        if not changed_nodes:
            lines.append("  " + self._color("没有检测到相关变更", Fore.LIGHTBLACK_EX))
            lines.append("")
            return "\n".join(lines)

        repo_positions = self._calculate_node_positions(topology)
        max_level = max(p[1] for p in repo_positions.values()) if repo_positions else 0
        
        level_repos: Dict[int, List[str]] = {}
        for repo, (col, level) in repo_positions.items():
            if level not in level_repos:
                level_repos[level] = []
            level_repos[level].append(repo)

        for level in range(max_level + 1):
            repos_in_level = level_repos.get(level, [])
            if not repos_in_level:
                continue

            level_header = f"  层级 {level + 1}: "
            lines.append(level_header)

            node_lines = []
            for repo in sorted(repos_in_level):
                node = topology.nodes.get(repo)
                if not node:
                    continue
                
                node_display = self._format_repo_name(node)
                if node.commit_count > 0:
                    node_display += f" ({node.commit_count} commits)"
                
                prefix = "    " + self.BOX_CHARS['corner_tl'] + self.BOX_CHARS['horizontal'] + " "
                node_lines.append(prefix + node_display)
                node_lines.append("    " + self.BOX_CHARS['vertical'] + "  ")

            lines.extend(node_lines)

            if level < max_level:
                next_repos = level_repos.get(level + 1, [])
                if next_repos:
                    edge_lines = self._render_edges(topology, repos_in_level, next_repos, level)
                    lines.extend(edge_lines)

        lines.append("")
        
        lines.append(self._color("  图例:", Fore.LIGHTBLACK_EX))
        lines.append(f"    {self._color('✓', Fore.GREEN)} 有变更  {self._color('⚠️', Fore.RED)} 含破坏性变更  {self._color('○', Fore.LIGHTBLACK_EX)} 无变更")
        lines.append(f"    {self.BOX_CHARS['arrow']} 依赖关系  {self.BOX_CHARS['arrow_bold']} 含 API/模型变更的依赖")
        lines.append(f"    {self._color('[S]', Fore.LIGHTBLACK_EX)} 静态配置  {self._color('[D]', Fore.CYAN)} 动态推断  {self._color('[H]', Fore.MAGENTA)} 混合模式")
        lines.append("")

        return "\n".join(lines)

    def _calculate_node_positions(self, topology: TopologyGraph) -> Dict[str, tuple]:
        """计算节点在图中的位置"""
        positions = {}
        changed_nodes = [name for name, node in topology.nodes.items() if node.has_changes]
        
        if not changed_nodes:
            return positions

        subgraph = topology.graph.subgraph(changed_nodes)
        
        in_degree = {node: subgraph.in_degree(node) for node in subgraph.nodes()}
        levels = {}
        current_level = 0
        remaining = set(subgraph.nodes())
        
        while remaining:
            level_nodes = [node for node in remaining if in_degree[node] == 0]
            if not level_nodes:
                for node in remaining:
                    levels[node] = current_level
                break
            
            for i, node in enumerate(sorted(level_nodes)):
                levels[node] = current_level
                positions[node] = (i, current_level)
                
                for successor in subgraph.successors(node):
                    if successor in in_degree:
                        in_degree[successor] -= 1
            
            remaining -= set(level_nodes)
            current_level += 1
        
        return positions

    def _render_edges(
        self,
        topology: TopologyGraph,
        from_repos: List[str],
        to_repos: List[str],
        level: int
    ) -> List[str]:
        """渲染节点之间的边"""
        lines = []
        prefix = "    "

        for from_repo in sorted(from_repos):
            edges_to_draw = []
            
            for to_repo in sorted(to_repos):
                edge = topology.get_edge(from_repo, to_repo)
                if edge:
                    edges_to_draw.append((to_repo, edge))
            
            if edges_to_draw:
                for to_repo, edge in edges_to_draw:
                    arrow = self.BOX_CHARS['arrow_bold'] if (edge.has_api_change or edge.has_model_change) else self.BOX_CHARS['arrow']
                    edge_color = Fore.RED if edge.has_breaking_change else Fore.LIGHTBLACK_EX
                    
                    source_tag = self._get_inference_source_tag(edge)
                    
                    line = f"{prefix}{self.BOX_CHARS['vertical']}"
                    line += f" {self._color(arrow, edge_color)} "
                    line += self._color(to_repo, Fore.WHITE)
                    line += f" {source_tag}"
                    
                    if hasattr(edge, 'confidence') and edge.confidence < 1.0:
                        confidence_pct = int(edge.confidence * 100)
                        if confidence_pct < 70:
                            conf_color = Fore.YELLOW
                        else:
                            conf_color = Fore.LIGHTBLACK_EX
                        line += self._color(f" [{confidence_pct}%]", conf_color)
                    
                    if self.config.show_reason and edge.reason:
                        reason_text = edge.reason
                        if len(reason_text) > 50:
                            reason_text = reason_text[:47] + "..."
                        line += self._color(f"  ({reason_text})", Fore.LIGHTBLACK_EX)
                    
                    lines.append(line)
                
                lines.append(f"{prefix}{self.BOX_CHARS['vertical']}")
        
        return lines

    def _get_inference_source_tag(self, edge: DependencyEdge) -> str:
        """获取推断来源标签"""
        if not hasattr(edge, 'inference_source'):
            return ""
        
        source = edge.inference_source
        if source == INFERENCE_SOURCE_STATIC:
            return self._color("[S]", Fore.LIGHTBLACK_EX)
        elif source == INFERENCE_SOURCE_DYNAMIC:
            return self._color("[D]", Fore.CYAN)
        elif source == INFERENCE_SOURCE_HYBRID:
            return self._color("[H]", Fore.MAGENTA)
        else:
            return ""

    def render_release_order(self, release_steps: List[ReleaseStep]) -> str:
        """渲染发布顺序"""
        lines = []
        lines.append(self._color("🚀 建议发布顺序", Fore.BLUE + Style.BRIGHT))
        lines.append("")

        if not release_steps:
            lines.append("  " + self._color("无需发布（无相关变更）", Fore.LIGHTBLACK_EX))
            lines.append("")
            return "\n".join(lines)

        for step in release_steps:
            step_header = f"  第 {step.order} 步: "
            
            repo_names = ", ".join(
                self._color(repo, self.TYPE_COLORS.get(
                    self._get_repo_type(repo, release_steps), Fore.WHITE
                ))
                for repo in step.repo_names
            )
            
            if step.has_breaking_changes:
                step_header += self._color("⚠️  ", Fore.RED)
            
            lines.append(step_header + repo_names)
            
            if self.config.show_reason and step.requires:
                requires_text = "    依赖: " + ", ".join(step.requires)
                lines.append(self._color(requires_text, Fore.LIGHTBLACK_EX))
            
            if step.notes:
                for note in step.notes:
                    note_color = Fore.RED if "破坏性" in note else Fore.YELLOW
                    lines.append(f"    {self._color('•', note_color)} {note}")
            
            lines.append("")

        return "\n".join(lines)

    def _get_repo_type(self, repo_name: str, steps: List[ReleaseStep]) -> str:
        """获取仓库类型（简化实现）"""
        if "frontend" in repo_name.lower() or "web" in repo_name.lower():
            return "frontend"
        return "backend"

    def render_warnings(self, warnings: List[str]) -> str:
        """渲染警告信息"""
        if not warnings:
            return ""
        
        lines = []
        lines.append(self._color("⚠️  注意事项", Fore.YELLOW + Style.BRIGHT))
        lines.append("")
        
        for warning in warnings:
            color = Fore.RED if "破坏性" in warning or "警告" in warning else Fore.YELLOW
            lines.append(f"  {self._color('•', color)} {warning}")
        
        lines.append("")
        return "\n".join(lines)

    def render_circular_dependencies(self, cycles: List[List[str]]) -> str:
        """渲染循环依赖警告"""
        if not cycles:
            return ""
        
        lines = []
        lines.append(self._color("❌ 检测到循环依赖！", Fore.RED + Style.BRIGHT))
        lines.append("")
        
        for i, cycle in enumerate(cycles, 1):
            cycle_str = " → ".join(cycle + [cycle[0]])
            lines.append(f"  循环 {i}: {self._color(cycle_str, Fore.RED)}")
        
        lines.append("")
        lines.append(self._color("  已尝试自动打破循环，但建议人工检查依赖配置。", Fore.YELLOW))
        lines.append("")
        return "\n".join(lines)

    def render_commit_details(self, scan_results: List[RepoScanResult]) -> str:
        """渲染提交详情"""
        if not self.config.show_commits:
            return ""
        
        lines = []
        lines.append(self._color("📝 提交详情", Fore.BLUE + Style.BRIGHT))
        lines.append("")

        has_commits = False
        for result in scan_results:
            if not result.commits:
                continue
            
            has_commits = True
            type_color = self.TYPE_COLORS.get(result.repo_type, Fore.WHITE)
            lines.append(f"  {self._color(result.repo_name, type_color)} ({len(result.commits)} 个提交):")
            lines.append("")
            
            for commit in result.commits:
                short_msg = commit.message.split('\n')[0][:80]
                date_str = commit.date.strftime("%Y-%m-%d %H:%M")
                
                lines.append(f"    {self._color(commit.short_hash, Fore.GREEN)}  {short_msg}")
                lines.append(f"      {self._color(date_str, Fore.LIGHTBLACK_EX)}  {commit.author}")
                
                if self.config.show_changes and commit.changed_files:
                    for file_path in commit.changed_files[:5]:
                        lines.append(f"      {self._color('•', Fore.LIGHTBLACK_EX)} {file_path}")
                    if len(commit.changed_files) > 5:
                        lines.append(f"      {self._color(f'... 还有 {len(commit.changed_files) - 5} 个文件', Fore.LIGHTBLACK_EX)}")
                
                lines.append("")
        
        if not has_commits:
            lines.append("  " + self._color("没有找到相关提交", Fore.LIGHTBLACK_EX))
            lines.append("")
        
        return "\n".join(lines)

    def render_change_details(self, analysis: DependencyAnalysis) -> str:
        """渲染变更详情"""
        if not self.config.show_changes:
            return ""
        
        from .change_detector import ChangeAnalysis
        
        lines = []
        lines.append(self._color("📋 API/模型变更详情", Fore.BLUE + Style.BRIGHT))
        lines.append("")
        
        has_changes = False
        
        for repo_name in analysis.changed_repos:
            change_analysis = None
            for result in analysis.topology.nodes.values():
                if result.name == repo_name:
                    has_changes = True
                    
                    type_color = self.TYPE_COLORS.get(result.repo_type, Fore.WHITE)
                    lines.append(f"  {self._color(repo_name, type_color)}:")
                    
                    if result.has_api_changes:
                        lines.append(f"    {self._color('API 契约变更:', Fore.CYAN)}")
                        for item in list(result.affected_items)[:10]:
                            if "Service:" in item or "RPC:" in item or "Path:" in item or "Schema:" in item:
                                lines.append(f"      {self._color('•', Fore.LIGHTBLACK_EX)} {item}")
                    
                    if result.has_model_changes:
                        lines.append(f"    {self._color('共享模型变更:', Fore.MAGENTA)}")
                        for item in list(result.affected_items)[:10]:
                            if "Class:" in item or "Struct:" in item or "Type:" in item or "Message:" in item:
                                lines.append(f"      {self._color('•', Fore.LIGHTBLACK_EX)} {item}")
                    
                    if result.affected_items:
                        other_items = [
                            item for item in result.affected_items
                            if not any(prefix in item for prefix in 
                                ["Service:", "RPC:", "Path:", "Schema:", 
                                 "Class:", "Struct:", "Type:", "Message:"])
                        ]
                        if other_items:
                            lines.append(f"    {self._color('其他变更:', Fore.LIGHTBLACK_EX)}")
                            for item in other_items[:5]:
                                lines.append(f"      {self._color('•', Fore.LIGHTBLACK_EX)} {item}")
                    
                    lines.append("")
        
        if not has_changes:
            lines.append("  " + self._color("没有检测到 API 或模型变更", Fore.LIGHTBLACK_EX))
            lines.append("")
        
        return "\n".join(lines)

    def render_full_report(
        self,
        analysis: DependencyAnalysis,
        scan_results: List[RepoScanResult]
    ) -> str:
        """渲染完整报告"""
        sections = []
        
        sections.append(self.render_header(analysis.issue_id))
        sections.append(self.render_scan_summary(scan_results))
        
        if analysis.has_circular_dependency:
            sections.append(self.render_circular_dependencies(analysis.circular_dependencies))
        
        sections.append(self.render_topology_graph(analysis.topology))
        sections.append(self.render_release_order(analysis.release_order))
        sections.append(self.render_warnings(analysis.warnings))
        sections.append(self.render_change_details(analysis))
        sections.append(self.render_commit_details(scan_results))
        
        return "\n".join(sections)
