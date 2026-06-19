"""
命令行接口
提供用户交互的命令行界面
"""

import json
import sys
from pathlib import Path
from typing import List, Optional

import click
from colorama import Fore, Style, init as colorama_init

from .change_detector import ChangeDetector
from .config import AppConfig, ConfigLoader
from .dependency_analyzer import DependencyAnalyzer, DependencyAnalysis
from .git_scanner import GitScanner, RepoScanResult
from .visualizer import DisplayConfig, TerminalVisualizer

colorama_init(autoreset=True)


class DepTrackerCLI:
    """变更追踪 CLI 主类"""

    def __init__(self):
        self.config: Optional[AppConfig] = None
        self.scanner: Optional[GitScanner] = None
        self.detector: Optional[ChangeDetector] = None
        self.analyzer: Optional[DependencyAnalyzer] = None
        self.visualizer: Optional[TerminalVisualizer] = None

    def load_config(self, config_path: Optional[str] = None) -> AppConfig:
        """加载配置"""
        if config_path:
            self.config = ConfigLoader.load(config_path)
        else:
            default_path = ConfigLoader.find_default_config()
            if not default_path:
                raise click.ClickException(
                    "未找到配置文件。请使用 --config 参数指定，或在当前目录创建 config.yaml"
                )
            self.config = ConfigLoader.load(default_path)
        
        return self.config

    def init_components(self):
        """初始化所有组件"""
        if not self.config:
            raise click.ClickException("配置未加载")
        
        self.scanner = GitScanner(max_workers=5)
        self.detector = ChangeDetector()
        self.analyzer = DependencyAnalyzer(self.config)
        self.visualizer = TerminalVisualizer()

    def run_analysis(
        self,
        issue_id: str,
        max_commits: int = 100
    ) -> tuple:
        """执行完整分析流程"""
        if not self.config or not self.scanner:
            raise click.ClickException("组件未初始化")

        click.echo(f"{Fore.CYAN}🔍 正在扫描 Issue: {issue_id}...{Style.RESET_ALL}")
        
        scan_results = self.scanner.scan_repositories(
            self.config.repositories,
            issue_id,
            max_commits
        )

        changed_results = [r for r in scan_results if r.has_changes]
        click.echo(f"{Fore.GREEN}✅ 扫描完成，找到 {len(changed_results)} 个有变更的仓库{Style.RESET_ALL}")
        click.echo("")

        repo_paths = {repo.name: repo.path for repo in self.config.repositories}
        change_analysis = self.detector.detect_all_changes(scan_results, repo_paths)

        click.echo(f"{Fore.CYAN}📊 正在分析依赖关系...{Style.RESET_ALL}")
        analysis = self.analyzer.analyze(issue_id, change_analysis)
        click.echo("")

        return analysis, scan_results

    def cleanup(self):
        """清理资源"""
        if self.scanner:
            self.scanner.close()


pass_cli = click.make_pass_decorator(DepTrackerCLI, ensure=True)


@click.group(invoke_without_command=True)
@click.option('--config', '-c', type=click.Path(exists=True, dir_okay=False),
              help='配置文件路径')
@click.option('--no-color', is_flag=True, help='禁用彩色输出')
@click.pass_context
def cli(ctx: click.Context, config: Optional[str], no_color: bool):
    """跨仓库变更依赖追踪工具
    
    用于追踪多个 Git 仓库之间的变更依赖关系，
    帮助避免因发布顺序错误导致的服务中断。
    """
    cli_instance = ctx.ensure_object(DepTrackerCLI)
    
    if no_color:
        colorama_init(strip=True, autoreset=True)
    
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())
        return

    if ctx.invoked_subcommand != 'init':
        try:
            cli_instance.load_config(config)
            cli_instance.init_components()
        except Exception as e:
            if ctx.invoked_subcommand == 'check':
                raise
            click.echo(f"{Fore.RED}❌ {str(e)}{Style.RESET_ALL}", err=True)
            sys.exit(1)


@cli.command()
@click.argument('issue_id')
@click.option('--max-commits', '-m', type=int, default=100,
              help='每个仓库最多扫描的提交数')
@click.option('--output', '-o', type=click.Choice(['text', 'json']), default='text',
              help='输出格式')
@click.option('--quiet', '-q', is_flag=True, help='只显示关键信息')
@click.option('--no-commits', is_flag=True, help='不显示提交详情')
@click.option('--no-changes', is_flag=True, help='不显示变更详情')
@pass_cli
def track(
    cli: DepTrackerCLI,
    issue_id: str,
    max_commits: int,
    output: str,
    quiet: bool,
    no_commits: bool,
    no_changes: bool
):
    """追踪指定 Issue 相关的跨仓库变更"""
    try:
        if quiet:
            cli.visualizer.config = DisplayConfig(
                show_commits=False,
                show_changes=False,
                show_reason=True,
            )
        else:
            cli.visualizer.config = DisplayConfig(
                show_commits=not no_commits,
                show_changes=not no_changes,
                show_reason=True,
            )

        analysis, scan_results = cli.run_analysis(issue_id, max_commits)

        if output == 'json':
            _output_json(analysis, scan_results)
        else:
            report = cli.visualizer.render_full_report(analysis, scan_results)
            click.echo(report)

        if analysis.has_circular_dependency:
            sys.exit(2)

        has_breaking = any(
            node.has_breaking_changes
            for node in analysis.topology.nodes.values()
        )
        if has_breaking:
            sys.exit(3)

    except KeyboardInterrupt:
        click.echo(f"\n{Fore.YELLOW}⚠️  操作已取消{Style.RESET_ALL}")
        sys.exit(130)
    except Exception as e:
        click.echo(f"{Fore.RED}❌ 执行失败: {str(e)}{Style.RESET_ALL}", err=True)
        sys.exit(1)
    finally:
        cli.cleanup()


@cli.command('list-repos')
@pass_cli
def list_repos(cli: DepTrackerCLI):
    """列出配置的所有仓库"""
    if not cli.config:
        return

    click.echo(f"{Fore.CYAN}📦 已配置的仓库列表:{Style.RESET_ALL}")
    click.echo("")

    for repo in cli.config.repositories:
        type_color = {
            'backend': Fore.CYAN,
            'frontend': Fore.MAGENTA,
            'database': Fore.YELLOW,
            'infrastructure': Fore.BLUE,
        }.get(repo.type, Fore.WHITE)

        click.echo(f"  {type_color}{repo.name}{Style.RESET_ALL}")
        click.echo(f"    路径: {repo.path}")
        click.echo(f"    类型: {repo.type}")
        if repo.api_contract_patterns:
            click.echo(f"    API 契约模式: {', '.join(repo.api_contract_patterns)}")
        if repo.shared_model_patterns:
            click.echo(f"    共享模型模式: {', '.join(repo.shared_model_patterns)}")
        click.echo("")

    click.echo(f"共 {len(cli.config.repositories)} 个仓库")


@cli.command('list-rules')
@pass_cli
def list_rules(cli: DepTrackerCLI):
    """列出配置的所有依赖规则"""
    if not cli.config:
        return

    click.echo(f"{Fore.CYAN}🔗 依赖规则列表:{Style.RESET_ALL}")
    click.echo("")

    for rule in cli.config.dependency_rules:
        click.echo(f"  {Fore.GREEN}{rule.from_repo}{Style.RESET_ALL} "
                   f"{Fore.LIGHTBLACK_EX}→{Style.RESET_ALL} "
                   f"{Fore.CYAN}{rule.to_repo}{Style.RESET_ALL}")
        if rule.reason:
            click.echo(f"    原因: {rule.reason}")
        click.echo("")

    click.echo(f"共 {len(cli.config.dependency_rules)} 条依赖规则")


@cli.command()
@click.argument('output_path', type=click.Path(), default='.dep-tracker-report')
@pass_cli
def export(cli: DepTrackerCLI, output_path: str):
    """导出分析结果到文件（功能演示）"""
    click.echo(f"{Fore.YELLOW}⚠️  此功能需要先执行 track 命令获取数据{Style.RESET_ALL}")
    click.echo(f"   示例: dep-tracker track JIRA-1024 --output json > report.json")
    click.echo("")
    click.echo(f"或者使用重定向:")
    click.echo(f"  dep-tracker track JIRA-1024 > {output_path}.txt")


@cli.command()
@click.option('--force', '-f', is_flag=True, help='覆盖已存在的配置')
@click.option('--output', '-o', type=click.Path(), default='config.yaml',
              help='输出文件路径')
def init(force: bool, output: str):
    """生成示例配置文件"""
    output_path = Path(output).resolve()
    
    if output_path.exists() and not force:
        click.echo(f"{Fore.RED}❌ 配置文件已存在: {output_path}{Style.RESET_ALL}")
        click.echo(f"   使用 --force 参数覆盖，或指定其他输出路径")
        sys.exit(1)

    example_config = """# 跨仓库变更追踪工具配置文件

repositories:
  # 用户服务（后端）
  - name: user-service
    path: ../user-service
    type: backend
    api_contract_patterns:
      - "*.proto"
      - "api/**/*.yaml"
      - "api/**/*.yml"
      - "openapi/*.json"
    shared_model_patterns:
      - "models/**/*.py"
      - "dto/**/*.py"
      - "schemas/**/*.py"

  # 订单服务（后端）
  - name: order-service
    path: ../order-service
    type: backend
    api_contract_patterns:
      - "*.proto"
      - "api/**/*.yaml"
      - "api/**/*.yml"
    shared_model_patterns:
      - "models/**/*.go"
      - "schemas/**/*.go"

  # Web 前端
  - name: web-frontend
    path: ../web-frontend
    type: frontend
    api_contract_patterns:
      - "src/api/**/*.ts"
      - "openapi/*.json"
    shared_model_patterns:
      - "src/types/**/*.ts"
      - "src/models/**/*.ts"

# 依赖规则定义
# from: 被依赖的仓库
# to: 依赖的仓库（必须在 from 之后发布）
dependency_rules:
  - from: user-service
    to: order-service
    reason: "订单服务依赖用户服务的用户信息API"
  
  - from: user-service
    to: web-frontend
    reason: "前端依赖用户服务的登录和用户信息接口"

  - from: order-service
    to: web-frontend
    reason: "前端依赖订单服务的订单查询和创建接口"
"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(example_config, encoding='utf-8')
    
    click.echo(f"{Fore.GREEN}✅ 示例配置已生成: {output_path}{Style.RESET_ALL}")
    click.echo("")
    click.echo("请根据实际情况修改配置文件中的:")
    click.echo("  1. 仓库路径 (path)")
    click.echo("  2. API 契约文件匹配模式 (api_contract_patterns)")
    click.echo("  3. 共享模型文件匹配模式 (shared_model_patterns)")
    click.echo("  4. 仓库间的依赖规则 (dependency_rules)")


@cli.command()
@pass_cli
def check(cli: DepTrackerCLI):
    """检查配置和仓库连接状态"""
    if not cli.config:
        click.echo(f"{Fore.RED}❌ 配置加载失败{Style.RESET_ALL}")
        sys.exit(1)

    click.echo(f"{Fore.CYAN}🔍 检查配置...{Style.RESET_ALL}")
    click.echo("")

    all_ok = True

    click.echo(f"{Fore.BLUE}📋 配置文件: {cli.config.config_file}{Style.RESET_ALL}")
    click.echo("")

    click.echo(f"{Fore.CYAN}📦 仓库检查:{Style.RESET_ALL}")
    for repo in cli.config.repositories:
        path = Path(repo.path)
        
        if not path.exists():
            click.echo(f"  ❌ {repo.name}: {Fore.RED}路径不存在{Style.RESET_ALL}")
            all_ok = False
            continue

        git_dir = path / '.git'
        if not git_dir.exists():
            click.echo(f"  ⚠️  {repo.name}: {Fore.YELLOW}不是 Git 仓库{Style.RESET_ALL}")
            all_ok = False
            continue

        try:
            from git import Repo
            repo_instance = Repo(str(path))
            branch = repo_instance.active_branch.name
            click.echo(f"  ✅ {repo.name}: {Fore.GREEN}正常{Style.RESET_ALL} (分支: {branch})")
        except Exception as e:
            click.echo(f"  ❌ {repo.name}: {Fore.RED}Git 仓库异常: {str(e)}{Style.RESET_ALL}")
            all_ok = False

    click.echo("")
    click.echo(f"{Fore.CYAN}🔗 依赖规则检查:{Style.RESET_ALL}")
    
    repo_names = {repo.name for repo in cli.config.repositories}
    for rule in cli.config.dependency_rules:
        if rule.from_repo not in repo_names:
            click.echo(f"  ❌ 规则 {rule.from_repo} → {rule.to_repo}: {Fore.RED}from 仓库不存在{Style.RESET_ALL}")
            all_ok = False
        elif rule.to_repo not in repo_names:
            click.echo(f"  ❌ 规则 {rule.from_repo} → {rule.to_repo}: {Fore.RED}to 仓库不存在{Style.RESET_ALL}")
            all_ok = False
        else:
            click.echo(f"  ✅ {rule.from_repo} → {rule.to_repo}")

    click.echo("")

    if all_ok:
        click.echo(f"{Fore.GREEN}✅ 所有检查通过！{Style.RESET_ALL}")
    else:
        click.echo(f"{Fore.YELLOW}⚠️  存在问题，请修复后重试{Style.RESET_ALL}")
        sys.exit(1)


def _output_json(analysis: DependencyAnalysis, scan_results: List[RepoScanResult]):
    """输出 JSON 格式结果"""
    import json
    from datetime import datetime

    def default_serializer(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        if hasattr(obj, '__dict__'):
            return obj.__dict__
        return str(obj)

    result = {
        'issue_id': analysis.issue_id,
        'generated_at': datetime.now().isoformat(),
        'scan_results': [
            {
                'repo_name': r.repo_name,
                'repo_path': r.repo_path,
                'repo_type': r.repo_type,
                'has_changes': r.has_changes,
                'commit_count': len(r.commits),
                'api_contract_changes': r.api_contract_changes,
                'shared_model_changes': r.shared_model_changes,
                'error': r.error,
                'commits': [
                    {
                        'hash': c.commit_hash,
                        'short_hash': c.short_hash,
                        'message': c.message,
                        'author': c.author,
                        'date': c.date,
                        'changed_files': c.changed_files,
                    }
                    for c in r.commits
                ],
            }
            for r in scan_results
        ],
        'dependency_analysis': {
            'changed_repos': analysis.changed_repos,
            'repos_without_changes': analysis.repos_without_changes,
            'has_circular_dependency': analysis.has_circular_dependency,
            'circular_dependencies': analysis.circular_dependencies,
            'warnings': analysis.warnings,
            'release_order': [
                {
                    'order': s.order,
                    'repo_names': s.repo_names,
                    'reason': s.reason,
                    'requires': s.requires,
                    'has_breaking_changes': s.has_breaking_changes,
                    'notes': s.notes,
                }
                for s in analysis.release_order
            ],
            'topology': {
                'nodes': [
                    {
                        'name': n.name,
                        'repo_type': n.repo_type,
                        'has_changes': n.has_changes,
                        'has_api_changes': n.has_api_changes,
                        'has_model_changes': n.has_model_changes,
                        'has_breaking_changes': n.has_breaking_changes,
                        'commit_count': n.commit_count,
                    }
                    for n in analysis.topology.nodes.values()
                ],
                'edges': [
                    {
                        'from': e.from_repo,
                        'to': e.to_repo,
                        'reason': e.reason,
                        'has_api_change': e.has_api_change,
                        'has_model_change': e.has_model_change,
                        'has_breaking_change': e.has_breaking_change,
                    }
                    for e in analysis.topology.edges
                ],
            },
        },
    }

    click.echo(json.dumps(result, indent=2, default=default_serializer, ensure_ascii=False))


def main():
    """主入口函数"""
    cli(obj=DepTrackerCLI())


if __name__ == '__main__':
    main()
